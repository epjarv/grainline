"""Read and write Bambu Studio and OrcaSlicer sliced .gcode.3mf files.

A .gcode.3mf file is a zip archive. The sliced instructions live in
Metadata/plate_1.gcode, alongside the model and thumbnails. Grainline
replaces that G-code and leaves every other file in the archive alone, so
Bambu Studio can import the result.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from grainline.engine import (
    GcodeError,
    Inspection,
    ProcessStats,
    Settings,
    already_processed,
    decode_gcode,
    inspect_gcode,
    process_gcode,
)

_PLATE_RE = re.compile(r"(?:^|/)plate_(\d+)\.gcode$", re.IGNORECASE)
_EXPORT_HINT = (
    "This file doesn't contain sliced G-code. In Bambu Studio use "
    "File > Export > Export plate sliced file, which saves a .gcode.3mf."
)


@dataclass(frozen=True)
class PlateInfo:
    number: int
    inspection: Inspection


def is_sliced_archive(path: Path) -> bool:
    return path.name.lower().endswith(".3mf")


def read_plates(path: Path) -> list[tuple[int, str, str]]:
    """Return (plate number, zip member name, gcode text) for every sliced plate."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise GcodeError("This .3mf file couldn't be opened. Export a .gcode.3mf from Bambu Studio.") from exc
    with archive:
        found: list[tuple[int, str]] = []
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            match = _PLATE_RE.search(normalized)
            if match:
                found.append((int(match.group(1)), info.filename))
        found.sort(key=lambda item: item[0])
        plates: list[tuple[int, str, str]] = []
        for number, member in found:
            plates.append((number, member, decode_gcode(archive.read(member))))
    return plates


def inspect_archive(path: Path) -> list[PlateInfo]:
    usable: list[PlateInfo] = []
    for number, _member, text in read_plates(path):
        if already_processed(text):
            raise GcodeError(
                "This sliced file was already run through Grainline. Export it again from Bambu Studio."
            )
        inspection = inspect_gcode(text)
        if inspection.layer_count > 0:
            usable.append(PlateInfo(number, inspection))
    if not usable:
        raise GcodeError(_EXPORT_HINT)
    return usable


def process_3mf(
    source: Path,
    dest: Path,
    settings: Settings,
    plates: list[int] | None = None,
    progress=None,
) -> ProcessStats:
    if source.resolve() == dest.resolve():
        raise GcodeError("Grainline won't overwrite the original file.")
    extracted = read_plates(source)
    if not extracted:
        raise GcodeError(_EXPORT_HINT)
    if any(already_processed(text) for _number, _member, text in extracted):
        raise GcodeError(
            "This sliced file was already run through Grainline. Export it again from Bambu Studio."
        )

    chosen = {number for number, _member, _text in extracted} if plates is None else set(plates)
    targets = [(number, member, text) for number, member, text in extracted if number in chosen]
    if not targets:
        raise GcodeError("That plate isn't in this file.")
    with zipfile.ZipFile(source) as archive:
        archive_names = [info.filename for info in archive.infolist()]

    replacements: dict[str, bytes] = {}
    combined: ProcessStats | None = None
    skipped = 0
    written = 0
    total = len(targets)
    for index, (_number, member, text) in enumerate(targets):
        if inspect_gcode(text).layer_count == 0:
            skipped += 1
            continue

        def plate_progress(fraction: float, start: int = index) -> None:
            if progress:
                progress((start + fraction) / total)

        rewritten, stats = process_gcode(text, settings, plate_progress)
        data = rewritten.encode("utf-8")
        replacements[member.replace("\\", "/")] = data
        md5_member = _md5_member(member, archive_names)
        if md5_member is not None:
            previous = _read_member(source, md5_member)
            replacements[md5_member.replace("\\", "/")] = _md5_bytes(data, previous)
        combined = _merge_stats(combined, stats)
        written += 1

    if combined is None or written == 0:
        raise GcodeError(_EXPORT_HINT)

    _repack(source, dest, replacements)
    combined.output = str(dest)
    combined.plates = written
    if skipped:
        combined.note = "Plates without sliced G-code were copied unchanged."
    return combined


def _md5_member(gcode_member: str, members: list[str]) -> str | None:
    wanted = gcode_member.replace("\\", "/") + ".md5"
    for member in members:
        if member.replace("\\", "/") == wanted:
            return member
    return None


def _read_member(path: Path, member: str) -> bytes | None:
    with zipfile.ZipFile(path) as archive:
        names = {info.filename.replace("\\", "/"): info.filename for info in archive.infolist()}
        actual = names.get(member.replace("\\", "/"))
        if actual is None:
            return None
        return archive.read(actual)


def _md5_bytes(gcode: bytes, previous: bytes | None) -> bytes:
    digest = hashlib.md5(gcode).hexdigest().encode("ascii")
    if previous is not None and previous.endswith((b"\n", b"\r")):
        newline = b"\r\n" if previous.endswith(b"\r\n") else b"\n"
        return digest + newline
    return digest


def _merge_stats(current: ProcessStats | None, extra: ProcessStats) -> ProcessStats:
    if current is None:
        return extra
    return ProcessStats(
        flavor=current.flavor,
        layers=current.layers + extra.layers,
        injected=current.injected + extra.injected,
        replaced=current.replaced + extra.replaced,
        lowest=min(current.lowest, extra.lowest),
        highest=max(current.highest, extra.highest),
        estimated_seconds=current.estimated_seconds + extra.estimated_seconds,
    )


def _repack(source: Path, dest: Path, replacements: dict[str, bytes]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    try:
        with zipfile.ZipFile(source, "r") as incoming, zipfile.ZipFile(partial, "w") as outgoing:
            outgoing.comment = incoming.comment
            for info in incoming.infolist():
                key = info.filename.replace("\\", "/")
                data = replacements.get(key, incoming.read(info))
                outgoing.writestr(_copy_info(info), data)
        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _copy_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    copied = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
    copied.compress_type = info.compress_type
    if copied.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        copied.compress_type = zipfile.ZIP_DEFLATED
    copied.external_attr = info.external_attr
    copied.create_system = info.create_system
    copied.flag_bits = info.flag_bits & 0x800
    return copied
