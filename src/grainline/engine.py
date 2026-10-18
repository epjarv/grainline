"""Inject wood-grain hotend temperatures into sliced G-code.

The original file is never modified. Temperature lines are inserted at layer
changes, or along the print when scheduling by time. M104 is the default so
the hotend can drift while the printer keeps moving; that lag is what turns a
temperature range into soft grain instead of hard stripes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from grainline.version import __version__

STYLES = ("natural", "smooth", "rings", "figure")

STYLE_LABELS = {
    "natural": "Natural grain",
    "smooth": "Smooth waves",
    "rings": "Growth rings",
    "figure": "Wild figure",
}

FLAVOR_LABELS = {
    "layer-change": "PrusaSlicer, OrcaSlicer, or Bambu Studio",
    "layer-number": "Cura or IdeaMaker",
    "simplify3d": "Simplify3D",
    "z-height": "Z moves (this file has no layer comments)",
    "none": "no layers found",
}

_LAYER_CHANGE_RE = re.compile(r"^\s*;\s*(?:LAYER_CHANGE|CHANGE_LAYER)\b", re.IGNORECASE)
_LAYER_NUM_RE = re.compile(r"^\s*;\s*LAYER\s*:\s*(-?\d+)\b", re.IGNORECASE)
_SIMPLIFY_RE = re.compile(r"^\s*;\s*layer\s+(\d+)\s*,", re.IGNORECASE)
_FOOTER_RES = (
    re.compile(r"^\s*;\s*prusaslicer_config\b", re.IGNORECASE),
    re.compile(r"^\s*;\s*superslicer_config\b", re.IGNORECASE),
    re.compile(r"^\s*;\s*CONFIG_BLOCK_START\b", re.IGNORECASE),
    re.compile(r"^\s*;\s*EXECUTABLE_BLOCK_END\b", re.IGNORECASE),
    re.compile(r"^\s*;\s*END_OF_GCODE\b", re.IGNORECASE),
    re.compile(r"^\s*;\s*End of Gcode\b", re.IGNORECASE),
    re.compile(r"^\s*;\s*SETTING_3\b", re.IGNORECASE),
)
_ALREADY_RE = re.compile(r"^\s*;\s*Grainline\s+\d")
_HOTEND_RE = re.compile(r"^\s*(M104|M109)\b([^;\r\n]*)", re.IGNORECASE)
_KLIPPER_RE = re.compile(r"^\s*SET_HEATER_TEMPERATURE\b([^;\r\n]*)", re.IGNORECASE)
_S_RE = re.compile(r"\bS([+-]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
_T_RE = re.compile(r"\bT(\d+)\b", re.IGNORECASE)
_HEATER_RE = re.compile(r"\bHEATER=extruder(\d*)", re.IGNORECASE)
_TARGET_RE = re.compile(r"\bTARGET=([+-]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
_PARAM_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
_INVALID_NAME = '<>:"/\\|?*'


class GcodeError(Exception):
    """A problem the user can fix by picking another file or setting."""


@dataclass
class Settings:
    temp_min: float = 210
    temp_max: float = 240
    style: str = "natural"
    spacing_layers: float = 36
    every_n_layers: int = 1
    randomness: float = 0.3
    seed: int = 1
    mode: str = "layers"
    every_n_seconds: float = 20
    cycle_seconds: float = 180
    wait_for_temp: bool = False
    replace_existing: bool = True
    tool: int = 0


@dataclass
class Inspection:
    flavor: str
    layer_count: int
    layer_lines: list[int]
    estimated_seconds: float


@dataclass
class ProcessStats:
    flavor: str
    layers: int
    injected: int
    replaced: int
    lowest: int
    highest: int
    estimated_seconds: float
    output: str = ""
    plates: int = 1
    note: str = ""


@dataclass
class MotionEvent:
    z: float | None
    z_written: float | None
    xy_extrude: bool


class MotionClock:
    """Track position and print time well enough to schedule temperature changes."""

    def __init__(self) -> None:
        self.xyz_absolute = True
        self.e_absolute = True
        self.e_explicit = False
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.e = 0.0
        self.f = 0.0
        self.seconds = 0.0
        self.have_xyz = False
        self.have_z = False

    def observe(self, line: str) -> MotionEvent:
        parsed = _split_command(line)
        if parsed is None:
            return MotionEvent(self.z if self.have_z else None, None, False)
        cmd, rest = parsed
        params = _params(rest)
        if cmd == "G90":
            self.xyz_absolute = True
            if not self.e_explicit:
                self.e_absolute = True
        elif cmd == "G91":
            self.xyz_absolute = False
            if not self.e_explicit:
                self.e_absolute = False
        elif cmd == "M82":
            self.e_absolute = True
            self.e_explicit = True
        elif cmd == "M83":
            self.e_absolute = False
            self.e_explicit = True
        elif cmd == "G92":
            self._apply_g92(params)
        elif cmd == "G28":
            self.have_xyz = False
            self.have_z = False
            return MotionEvent(None, None, False)
        elif cmd == "G4":
            self._dwell(params)
        elif cmd in ("G0", "G1", "G2", "G3"):
            return self._move(cmd, params)
        return MotionEvent(self.z if self.have_z else None, None, False)

    def _apply_g92(self, params: dict[str, float]) -> None:
        if "X" in params:
            self.x = params["X"]
        if "Y" in params:
            self.y = params["Y"]
        if "Z" in params:
            self.z = params["Z"]
            self.have_z = True
        if "E" in params:
            self.e = params["E"]
        if any(axis in params for axis in "XYZ"):
            self.have_xyz = True

    def _dwell(self, params: dict[str, float]) -> None:
        if "S" in params:
            self.seconds += max(0.0, params["S"])
        elif "P" in params:
            self.seconds += max(0.0, params["P"]) / 1000.0

    def _move(self, cmd: str, params: dict[str, float]) -> MotionEvent:
        if "F" in params and params["F"] > 0:
            self.f = params["F"]
        nx, ny, nz = self.x, self.y, self.z
        if "X" in params:
            nx = params["X"] if self.xyz_absolute else self.x + params["X"]
        if "Y" in params:
            ny = params["Y"] if self.xyz_absolute else self.y + params["Y"]
        if "Z" in params:
            nz = params["Z"] if self.xyz_absolute else self.z + params["Z"]

        de = 0.0
        if "E" in params:
            if self.e_absolute:
                de = params["E"] - self.e
                self.e = params["E"]
            else:
                de = params["E"]
                self.e += params["E"]

        wrote_z = "Z" in params
        z_written = nz if wrote_z else None
        dist = 0.0
        if self.have_xyz:
            if cmd in ("G2", "G3"):
                dist = _arc_length(self.x, self.y, nx, ny, nz - self.z, params, cmd == "G2")
            else:
                dist = math.dist((self.x, self.y, self.z), (nx, ny, nz))
        if dist < 1e-9 and abs(de) > 0 and "X" not in params and "Y" not in params:
            dist = abs(de)
        if self.f > 1e-6 and dist > 0:
            self.seconds += dist / self.f * 60.0

        self.x, self.y, self.z = nx, ny, nz
        if any(axis in params for axis in "XYZ"):
            self.have_xyz = True
        if wrote_z:
            self.have_z = True
        xy_extrude = de > 1e-6 and ("X" in params or "Y" in params)
        return MotionEvent(self.z if self.have_z else None, z_written, xy_extrude)


def validate_settings(settings: Settings) -> None:
    if settings.mode not in ("layers", "time"):
        raise GcodeError("Choose layer timing or print-time timing.")
    if settings.style not in STYLES:
        raise GcodeError("Choose a grain look.")
    if settings.temp_min >= settings.temp_max:
        raise GcodeError("The low temperature has to be below the high temperature.")
    if settings.temp_min < 150 or settings.temp_max > 320:
        raise GcodeError("Use temperatures between 150 and 320 °C.")
    if settings.spacing_layers <= 0 or settings.cycle_seconds <= 0:
        raise GcodeError("Grain width has to be greater than zero.")
    if settings.every_n_layers < 1 or settings.every_n_seconds <= 0:
        raise GcodeError("The time between temperature changes has to be greater than zero.")
    if not 0 <= settings.randomness <= 1:
        raise GcodeError("Variation has to be between 0 and 100%.")
    if settings.tool < 0:
        raise GcodeError("Tool number can't be negative.")


def temperature_for(position: float, settings: Settings) -> int:
    """Map a layer index or a print time, in seconds, onto the temperature range."""
    spacing = settings.cycle_seconds if settings.mode == "time" else settings.spacing_layers
    if spacing <= 0:
        spacing = 1.0
    signal = grain_signal(position, spacing, settings.style, settings.seed, settings.randomness)
    span = settings.temp_max - settings.temp_min
    value = settings.temp_min + (signal + 1.0) / 2.0 * span
    value = min(settings.temp_max, max(settings.temp_min, value))
    rounded = int(math.floor(value + 0.5))
    low = int(math.floor(settings.temp_min + 0.5))
    high = int(math.floor(settings.temp_max + 0.5))
    return min(high, max(low, rounded))


def held_layer_temps(layer_count: int, settings: Settings) -> list[int]:
    """Temperature that will be active on each layer."""
    every = max(1, int(settings.every_n_layers))
    temps: list[int] = []
    last: int | None = None
    for index in range(max(0, layer_count)):
        if last is None or index % every == 0:
            last = temperature_for(float(index), settings)
        temps.append(last)
    return temps


def held_time_temps(duration: float, settings: Settings) -> list[tuple[float, int]]:
    step = max(0.1, float(settings.every_n_seconds))
    duration = max(step, float(duration))
    points: list[tuple[float, int]] = []
    moment = 0.0
    guard = 0
    while moment <= duration + 1e-6 and guard < 20000:
        points.append((moment, temperature_for(moment, settings)))
        moment += step
        guard += 1
    return points


def grain_signal(position: float, spacing: float, style: str, seed: int, randomness: float) -> float:
    """Return a grain wave in the range -1 to 1."""
    if spacing <= 0:
        spacing = 1.0
    randomness = min(1.0, max(0.0, randomness))
    shift = math.radians(seed % 360)
    phase = position / spacing
    angle = 2 * math.pi * phase + shift
    if style == "smooth":
        base = math.sin(angle)
    elif style == "rings":
        base = _ring_wave(phase + (seed % 360) / 360.0)
    elif style == "figure":
        base = _figure(phase, seed)
    else:
        base = _natural(angle, seed)
    if randomness > 0 and style == "figure":
        extra = _smooth_noise(phase * 6.0, seed + 53)
        base = (1 - 0.45 * randomness) * base + 0.45 * randomness * extra
    elif randomness > 0:
        noise = _smooth_noise(phase * 3.0 + 2.0, seed + 11)
        base = (1 - randomness) * base + randomness * noise
    return max(-1.0, min(1.0, base))


def output_path(source: Path, suffix: str) -> Path:
    cleaned = (suffix or "").strip() or "_grainline"
    if any(char in cleaned for char in _INVALID_NAME):
        raise GcodeError("The file name suffix uses a character Windows can't put in a file name.")
    if cleaned[0] not in "_-.":
        cleaned = "_" + cleaned
    name = source.name
    lower = name.lower()
    if lower.endswith(".gcode.3mf"):
        base, ext = name[: -len(".gcode.3mf")], name[-len(".gcode.3mf") :]
    elif lower.endswith(".3mf"):
        base, ext = source.stem, ".gcode.3mf"
    else:
        base, ext = source.stem, source.suffix
    dest = source.with_name(base + cleaned + ext)
    if dest.resolve() == source.resolve():
        raise GcodeError("That name would overwrite the original. Add a suffix.")
    return dest


def inspect_gcode(text: str) -> Inspection:
    return inspect_lines(text.splitlines())


def already_processed(text: str) -> bool:
    return any(_ALREADY_RE.match(line) for line in text.splitlines()[:40])


def inspect_lines(lines: list[str]) -> Inspection:
    changes: list[int] = []
    numbered: list[int] = []
    simplify: list[int] = []
    z_layers: list[int] = []
    clock = MotionClock()
    layer_z: float | None = None
    candidate: tuple[int, float] | None = None

    for index, line in enumerate(lines):
        if _LAYER_CHANGE_RE.match(line):
            changes.append(index)
        elif _LAYER_NUM_RE.match(line):
            numbered.append(index)
        elif _SIMPLIFY_RE.match(line):
            simplify.append(index)

        event = clock.observe(line)
        if event.z_written is not None:
            new_z = event.z_written
            if layer_z is None or new_z > layer_z + 0.012:
                candidate = (index, new_z)
            elif layer_z is not None and abs(new_z - layer_z) <= 0.015:
                candidate = None
        if event.xy_extrude and candidate is not None and event.z is not None:
            if abs(event.z - candidate[1]) <= 0.03:
                if layer_z is None or candidate[1] > layer_z + 0.012:
                    z_layers.append(candidate[0])
                    layer_z = candidate[1]
                candidate = None

    if changes:
        flavor, indexes = "layer-change", changes
    elif numbered:
        flavor, indexes = "layer-number", numbered
    elif simplify:
        flavor, indexes = "simplify3d", simplify
    elif z_layers:
        flavor, indexes = "z-height", z_layers
    else:
        flavor, indexes = "none", []
    return Inspection(flavor, len(indexes), indexes, clock.seconds)


def process_gcode(text: str, settings: Settings, progress=None) -> tuple[str, ProcessStats]:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines, stats = process_lines(text.splitlines(), settings, progress)
    body = newline.join(lines)
    if text.endswith(("\n", "\r")):
        body += newline
    return body, stats


def process_lines(lines: list[str], settings: Settings, progress=None) -> tuple[list[str], ProcessStats]:
    validate_settings(settings)
    if any(_ALREADY_RE.match(line) for line in lines[:40]):
        raise GcodeError(
            "This G-code was already run through Grainline. Open the original file from the slicer."
        )
    inspection = inspect_lines(lines)
    if inspection.layer_count == 0:
        raise GcodeError("No layers were found. Open a G-code file exported by your slicer.")

    layer_at = {line_index: ordinal for ordinal, line_index in enumerate(inspection.layer_lines)}
    last_layer_line = inspection.layer_lines[-1]
    every = max(1, int(settings.every_n_layers))
    out = [describe(settings)]
    clock = MotionClock()
    printing = False
    footer = False
    origin: float | None = None
    next_time = 0.0
    last_temp: int | None = None
    injected = 0
    replaced = 0
    lowest = 10**9
    highest = -10**9
    total = max(1, len(lines))

    def emit(position: float) -> list[str]:
        nonlocal last_temp, injected, lowest, highest
        temp = temperature_for(position, settings)
        if temp == last_temp:
            return []
        last_temp = temp
        injected += 1
        lowest = min(lowest, temp)
        highest = max(highest, temp)
        command = "M109" if settings.wait_for_temp else "M104"
        tool = f" T{settings.tool}" if settings.tool else ""
        return [f"; Grainline {temp} C", f"{command} S{temp}{tool}"]

    for index, line in enumerate(lines):
        if progress and index % 40000 == 0:
            progress(index / total)
        prefix: list[str] = []
        if printing and not footer and (
            _is_footer(line) or (index > last_layer_line and _is_hotend_off(line))
        ):
            footer = True
        if index in layer_at and not footer:
            printing = True
            if origin is None:
                origin = clock.seconds
            if settings.mode == "layers" and layer_at[index] % every == 0:
                prefix.extend(emit(float(layer_at[index])))
        if settings.mode == "time" and printing and not footer and origin is not None and _has_xy(line):
            elapsed = clock.seconds - origin
            if elapsed + 1e-9 >= next_time:
                prefix.extend(emit(elapsed))
                step = float(settings.every_n_seconds)
                next_time = (math.floor(elapsed / step) + 1) * step

        written = line
        if settings.replace_existing and printing and not footer:
            commented = disable_hotend_temp(line, settings.tool)
            if commented is not None:
                written = commented
                replaced += 1
        out.extend(prefix)
        out.append(written)
        clock.observe(line)

    if progress:
        progress(1.0)
    if injected == 0:
        raise GcodeError("No temperature commands were added. Widen the temperature range and try again.")
    return out, ProcessStats(
        flavor=inspection.flavor,
        layers=inspection.layer_count,
        injected=injected,
        replaced=replaced,
        lowest=lowest,
        highest=highest,
        estimated_seconds=inspection.estimated_seconds,
    )


def process_file(source: Path, dest: Path, settings: Settings, progress=None) -> ProcessStats:
    if source.resolve() == dest.resolve():
        raise GcodeError("Grainline won't overwrite the original G-code.")
    text = read_gcode(source)
    rewritten, stats = process_gcode(text, settings, progress)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    try:
        partial.write_bytes(rewritten.encode("utf-8"))
        partial.replace(dest)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    stats.output = str(dest)
    return stats


def decode_gcode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8", errors="replace")


def read_gcode(path: Path) -> str:
    return decode_gcode(path.read_bytes())


def process_path(
    source: Path,
    dest: Path,
    settings: Settings,
    plates: list[int] | None = None,
    progress=None,
) -> ProcessStats:
    """Process a plain G-code file, or a Bambu/Orca .gcode.3mf sliced archive."""
    if source.name.lower().endswith(".3mf"):
        from grainline.threemf import process_3mf

        return process_3mf(source, dest, settings, plates, progress)
    return process_file(source, dest, settings, progress)


def describe(settings: Settings) -> str:
    if settings.mode == "time":
        pace = f"every {settings.every_n_seconds:g}s, cycle {settings.cycle_seconds:g}s"
    else:
        pace = f"every {int(settings.every_n_layers)} layer(s), cycle {settings.spacing_layers:g} layers"
    command = "M109" if settings.wait_for_temp else "M104"
    return (
        f"; Grainline {__version__}: {settings.temp_min:g}-{settings.temp_max:g} C, "
        f"{settings.style}, {pace}, variation {settings.randomness:.2f}, "
        f"seed {settings.seed}, {command}, T{settings.tool}"
    )


def disable_hotend_temp(line: str, tool: int) -> str | None:
    """Comment out a printing hotend temperature, leaving cooldown and bed lines alone."""
    hotend = _HOTEND_RE.match(line)
    if hotend:
        temp_match = _S_RE.search(hotend.group(2))
        tool_match = _T_RE.search(hotend.group(2))
        explicit = int(tool_match.group(1)) if tool_match else None
        temp = float(temp_match.group(1)) if temp_match else None
    else:
        klipper = _KLIPPER_RE.match(line)
        if not klipper:
            return None
        heater = _HEATER_RE.search(klipper.group(1))
        temp_match = _TARGET_RE.search(klipper.group(1))
        if heater is None or temp_match is None:
            return None
        digits = heater.group(1)
        explicit = int(digits) if digits else 0
        temp = float(temp_match.group(1))
    if temp is None or temp < 100:
        return None
    if explicit is None:
        if tool != 0:
            return None
    elif explicit != tool:
        return None
    stripped = line.strip()
    return f"; Grainline disabled {stripped}"


def _is_footer(line: str) -> bool:
    return any(pattern.match(line) for pattern in _FOOTER_RES)


def _is_hotend_off(line: str) -> bool:
    """True when a line turns the hotend down, which means the print is over."""
    hotend = _HOTEND_RE.match(line)
    if hotend:
        match = _S_RE.search(hotend.group(2))
        return match is not None and float(match.group(1)) < 100
    klipper = _KLIPPER_RE.match(line)
    if not klipper:
        return False
    heater = _HEATER_RE.search(klipper.group(1))
    match = _TARGET_RE.search(klipper.group(1))
    return heater is not None and match is not None and float(match.group(1)) < 100


def _has_xy(line: str) -> bool:
    parsed = _split_command(line)
    if parsed is None:
        return False
    cmd, rest = parsed
    if cmd not in ("G0", "G1", "G2", "G3"):
        return False
    params = _params(rest)
    return "X" in params or "Y" in params


def _split_command(line: str) -> tuple[str, str] | None:
    body = line.split(";", 1)[0].strip()
    if not body:
        return None
    body = re.sub(r"\*\s*\d+\s*$", "", body).strip()
    body = re.sub(r"^N\d+\s*", "", body, flags=re.IGNORECASE)
    match = re.match(r"^([A-Za-z])(\d+)\s*(.*)$", body)
    if match is None:
        return None
    return (match.group(1) + match.group(2)).upper(), match.group(3)


def _params(rest: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for axis, value in _PARAM_RE.findall(rest):
        found[axis.upper()] = float(value)
    return found


def _natural(angle: float, seed: int) -> float:
    wobble = 0.15 * math.sin(angle * 0.37 + (seed % 19))
    base = 0.70 * math.sin(angle) + 0.22 * math.sin(angle * 2.17 + 0.6) + wobble
    return max(-1.0, min(1.0, base / 0.92))


def _ring_wave(phase: float) -> float:
    x = phase % 1.0
    if x < 0.8:
        return math.cos(math.pi * x / 0.8)
    return -math.cos(math.pi * (x - 0.8) / 0.2)


def _figure(phase: float, seed: int) -> float:
    first = _smooth_noise(phase * 0.85, seed)
    second = _smooth_noise(phase * 2.4 + 4, seed + 17)
    third = _smooth_noise(phase * 0.33 + 8, seed + 29)
    return max(-1.0, min(1.0, (0.62 * first + 0.28 * second + 0.10 * third) * 1.45))


def _smooth_noise(x: float, seed: int) -> float:
    whole = math.floor(x)
    blend = x - whole
    blend = blend * blend * (3 - 2 * blend)
    left = _hash_unit(int(whole), seed) * 2 - 1
    right = _hash_unit(int(whole) + 1, seed) * 2 - 1
    return left * (1 - blend) + right * blend


def _hash_unit(index: int, seed: int) -> float:
    value = (index * 374761393 + seed * 668265263) & 0xFFFFFFFF
    value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
    value ^= value >> 16
    return (value & 0xFFFFFF) / float(0xFFFFFF)


def _arc_length(
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    dz: float,
    params: dict[str, float],
    clockwise: bool,
) -> float:
    chord = math.hypot(ex - sx, ey - sy)
    if "I" not in params and "J" not in params:
        return math.sqrt(chord * chord + dz * dz)
    center_x = sx + params.get("I", 0.0)
    center_y = sy + params.get("J", 0.0)
    radius = math.hypot(params.get("I", 0.0), params.get("J", 0.0))
    if radius < 1e-6:
        return math.sqrt(chord * chord + dz * dz)
    start = math.atan2(sy - center_y, sx - center_x)
    end = math.atan2(ey - center_y, ex - center_x)
    sweep = end - start
    if clockwise:
        if sweep >= 0:
            sweep -= 2 * math.pi
    elif sweep <= 0:
        sweep += 2 * math.pi
    if chord < 1e-6:
        sweep = -2 * math.pi if clockwise else 2 * math.pi
    length = abs(sweep) * radius
    return math.sqrt(length * length + dz * dz)
