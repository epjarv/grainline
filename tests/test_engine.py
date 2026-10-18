"""Tests for Grainline's G-code temperature injection."""

from __future__ import annotations

import hashlib
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grainline.cli import main
from grainline.engine import (
    GcodeError,
    Settings,
    inspect_gcode,
    output_path,
    process_file,
    process_gcode,
    process_path,
    temperature_for,
)
from grainline.threemf import inspect_archive


def smooth(**overrides) -> Settings:
    values = dict(
        temp_min=200,
        temp_max=220,
        style="smooth",
        spacing_layers=10,
        every_n_layers=1,
        randomness=0,
        seed=1,
        mode="layers",
        replace_existing=True,
    )
    values.update(overrides)
    return Settings(**values)


class TemperatureTests(unittest.TestCase):
    def test_smooth_wave_hits_known_temperatures(self) -> None:
        settings = smooth()
        self.assertEqual(temperature_for(0, settings), 210)
        self.assertEqual(temperature_for(2, settings), 220)
        self.assertEqual(temperature_for(5, settings), 210)
        self.assertEqual(temperature_for(7, settings), 200)

        shift = math.radians(1)
        signal = math.sin(2 * math.pi * 1 / 10 + shift)
        expected = int(math.floor(210 + 10 * signal + 0.5))
        self.assertEqual(temperature_for(1, settings), expected)

    def test_seed_moves_the_wave(self) -> None:
        self.assertNotEqual(temperature_for(2, smooth(seed=1)), temperature_for(2, smooth(seed=90)))

    def test_values_stay_inside_the_range(self) -> None:
        for style in ("natural", "smooth", "rings", "figure"):
            settings = smooth(style=style, randomness=0.85, seed=42, temp_min=205, temp_max=236)
            for position in range(0, 400, 3):
                temp = temperature_for(position, settings)
                self.assertGreaterEqual(temp, 205)
                self.assertLessEqual(temp, 236)

    def test_low_must_be_below_high(self) -> None:
        with self.assertRaises(GcodeError):
            process_gcode(";LAYER:0\nG1 X1 Y1 E0.1\n", smooth(temp_min=220, temp_max=200))


class InjectionTests(unittest.TestCase):
    CURA = "\n".join(
        [
            "; generated",
            "M140 S60",
            "M104 S215",
            "M109 S215",
            "G28",
            "G1 Z0.2 F1000",
            ";LAYER:0",
            "G1 X0 Y0 F3000",
            "G1 X10 Y0 E1 F1200",
            "M104 S215",
            "M140 S60",
            "M190 S60",
            ";LAYER:1",
            "G1 X10 Y10 E2",
            ";LAYER:2",
            "G1 X0 Y10 E3",
            "M104 S210",
            ";End of Gcode",
            "M104 S0",
            "M104 S180",
            "M140 S0",
            "",
        ]
    )

    def test_cura_layers_keep_warmup_and_cooldown(self) -> None:
        settings = smooth()
        body, stats = process_gcode(self.CURA, settings)
        self.assertEqual(stats.flavor, "layer-number")
        self.assertEqual(stats.layers, 3)
        self.assertEqual(stats.injected, 3)
        self.assertTrue(body.startswith("; Grainline "))
        for layer in (0, 1, 2):
            temp = temperature_for(layer, settings)
            self.assertIn(f"M104 S{temp}", body)
            self.assertLess(body.index(f"M104 S{temp}"), body.index(f";LAYER:{layer}"))
        lines = body.splitlines()
        self.assertIn("M104 S215", lines)
        self.assertIn("M109 S215", lines)
        self.assertIn("M140 S60", lines)
        self.assertIn("M190 S60", lines)
        self.assertIn("; Grainline disabled M104 S215", lines)
        self.assertIn("; Grainline disabled M104 S210", lines)
        self.assertIn("M104 S0", lines)
        self.assertIn("M104 S180", lines)
        self.assertEqual(stats.replaced, 2)

    def test_prusa_layer_change_and_footer(self) -> None:
        text = "\n".join(
            [
                "M109 S210",
                ";LAYER_CHANGE",
                ";Z:0.2",
                "G1 X1 Y1 E0.1",
                ";LAYER_CHANGE",
                ";Z:0.4",
                "G1 X2 Y2 E0.2",
                "M104 S0",
                "; prusaslicer_config = begin",
                "",
            ]
        )
        _body, stats = process_gcode(text, smooth())
        self.assertEqual(stats.flavor, "layer-change")
        self.assertEqual(stats.layers, 2)
        self.assertEqual(stats.injected, 2)
        self.assertIn("M104 S0", _body.splitlines())

    def test_simplify3d_markers(self) -> None:
        text = "\n".join(
            [
                "; layer 1, Z = 0.200",
                "G1 X1 Y1 E0.1",
                "; layer 2, Z = 0.400",
                "G1 X2 Y2 E0.2",
                "",
            ]
        )
        _body, stats = process_gcode(text, smooth(every_n_layers=2))
        self.assertEqual(stats.flavor, "simplify3d")
        self.assertEqual(stats.layers, 2)
        self.assertEqual(stats.injected, 1)

    def test_every_other_layer_skips_the_ones_between(self) -> None:
        text = "\n".join(f";LAYER:{index}\nG1 X{index} Y0 E0.{index + 1}" for index in range(4)) + "\n"
        settings = smooth(temp_min=180, temp_max=250, every_n_layers=2)
        body, stats = process_gcode(text, settings)
        self.assertEqual(stats.injected, 2)
        self.assertEqual(body.count("; Grainline "), 3)  # header plus two temperature notes

    def test_wait_uses_m109(self) -> None:
        text = ";LAYER:0\nG1 X1 Y1 E0.1\n"
        body, _stats = process_gcode(text, smooth(wait_for_temp=True))
        self.assertIn("M109 S210", body)
        self.assertNotIn("M104 S210", body)

    def test_other_tool_is_left_alone(self) -> None:
        text = "\n".join(
            [
                ";LAYER:0",
                "M104 S200 T0",
                "M104 S200 T1",
                "M104 S200",
                "G1 X1 Y1 E0.1",
                "SET_HEATER_TEMPERATURE HEATER=extruder1 TARGET=205",
                "",
            ]
        )
        body, stats = process_gcode(text, smooth(temp_min=210, temp_max=240, tool=1))
        lines = body.splitlines()
        self.assertIn("M104 S200 T0", lines)
        self.assertIn("M104 S200", lines)
        self.assertIn("; Grainline disabled M104 S200 T1", lines)
        self.assertIn("; Grainline disabled SET_HEATER_TEMPERATURE HEATER=extruder1 TARGET=205", lines)
        self.assertEqual(stats.replaced, 2)
        self.assertTrue(any(line.startswith("M104 S") and "T1" in line and "disabled" not in line for line in lines))

    def test_time_mode_follows_motion(self) -> None:
        text = "\n".join(
            [
                "G28",
                "G90",
                "M83",
                "G1 F600",
                ";LAYER:0",
                "G1 X0 Y0",
                "G1 X60 Y0 E1",
                "G1 X120 Y0 E2",
                ";LAYER:1",
                "G1 X180 Y0 E3",
                ";End of Gcode",
                "M104 S0",
                "",
            ]
        )
        settings = smooth(mode="time", every_n_seconds=5, cycle_seconds=30)
        _body, stats = process_gcode(text, settings)
        self.assertEqual(stats.injected, 3)
        self.assertIn("M104 S0", _body.splitlines())

    def test_refuses_a_second_pass(self) -> None:
        body, _stats = process_gcode(";LAYER:0\nG1 X1 Y1 E0.1\n", smooth())
        with self.assertRaises(GcodeError):
            process_gcode(body, smooth())

    def test_crlf_is_preserved(self) -> None:
        text = ";LAYER:0\r\nG1 X1 Y1 E0.1\r\n"
        body, _stats = process_gcode(text, smooth())
        self.assertIn("\r\n", body)
        self.assertEqual(body.replace("\r\n", "").count("\n"), 0)

    def test_z_layers_ignore_hops(self) -> None:
        text = "\n".join(
            [
                "G90",
                "G1 Z0.2 F3000",
                "G1 X0 Y0 E0.1 F1000",
                "G1 X10 Y0 E0.2",
                "G1 Z0.4",
                "G1 X0 Y0 E0.3",
                "G1 Z0.8",
                "G1 X5 Y5",
                "G1 Z0.4",
                "G1 X12 Y0 E0.5",
                "G1 Z0.6",
                "G1 X0 Y10 E0.7",
                "",
            ]
        )
        _body, stats = process_gcode(text, smooth())
        self.assertEqual(stats.flavor, "z-height")
        self.assertEqual(stats.layers, 3)

    def test_unretract_during_a_hop_is_not_a_layer(self) -> None:
        text = "\n".join(
            [
                "G90",
                "M83",
                "G1 Z0.2 F3000",
                "G1 X0 Y0 E0.1 F1000",
                "G1 Z0.6",
                "G1 X10 Y10",
                "G1 E0.4",
                "G1 Z0.2",
                "G1 X20 Y0 E0.2",
                "G1 Z0.4",
                "G1 X0 Y10 E0.2",
                "",
            ]
        )
        _body, stats = process_gcode(text, smooth())
        self.assertEqual(stats.layers, 2)

    def test_arc_time_is_longer_than_the_straight_chord(self) -> None:
        inspection = inspect_gcode("G1 X0 Y0 F600\nG3 X10 Y0 I5 J0\n")
        self.assertAlmostEqual(inspection.estimated_seconds, math.pi / 2, delta=0.05)

    def test_process_file_leaves_the_original(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "benchy.gcode"
            original = ";LAYER:0\nG1 X1 Y1 E0.1\n"
            source.write_text(original, encoding="utf-8")
            dest = output_path(source, "_grainline")
            stats = process_file(source, dest, smooth())
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertTrue(dest.is_file())
            self.assertIn("M104 S", dest.read_text(encoding="utf-8"))
            self.assertEqual(Path(stats.output), dest)

    def test_suffix_is_added_without_clobbering_the_original(self) -> None:
        path = Path("benchy.gcode")
        self.assertEqual(output_path(path, "wood").name, "benchy_wood.gcode")
        self.assertEqual(output_path(path, "_grainline").name, "benchy_grainline.gcode")
        self.assertEqual(output_path(path, "").name, "benchy_grainline.gcode")
        sliced = Path("benchy.gcode.3mf")
        self.assertEqual(output_path(sliced, "_grainline").name, "benchy_grainline.gcode.3mf")
        self.assertEqual(output_path(Path("model.3mf"), "_grainline").name, "model_grainline.gcode.3mf")
        with self.assertRaises(GcodeError):
            output_path(path, "bad:name")


class CommandLineTests(unittest.TestCase):
    def test_cli_writes_a_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "part.gcode"
            source.write_text(";LAYER:0\nG1 X2 Y2 E0.2\n", encoding="utf-8")
            dest = Path(folder) / "part_out.gcode"
            code = main(
                [
                    str(source),
                    "-o",
                    str(dest),
                    "--min",
                    "210",
                    "--max",
                    "230",
                    "--style",
                    "smooth",
                    "--randomness",
                    "0",
                    "--seed",
                    "1",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("M104 S", dest.read_text(encoding="utf-8"))
            self.assertNotIn("Grainline", source.read_text(encoding="utf-8"))


PLATE = ";LAYER:0\nG1 X1 Y1 E0.1\n;LAYER:1\nG1 X2 Y2 E0.1\n"
EMPTY_PLATE = "; no sliced geometry\n"


def _write_archive(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


class SlicedArchiveTests(unittest.TestCase):
    def test_round_trip_keeps_the_model_and_updates_the_checksum(self) -> None:
        gcode = PLATE.encode("utf-8")
        model = b"<model>keep me</model>"
        thumbnail = b"\x89PNG thumbnail"
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "benchy.gcode.3mf"
            _write_archive(
                source,
                {
                    "[Content_Types].xml": b"<Types/>",
                    "3D/3dmodel.model": model,
                    "Metadata/plate_1.png": thumbnail,
                    "Metadata/plate_1.gcode": gcode,
                    "Metadata/plate_1.gcode.md5": hashlib.md5(gcode).hexdigest().encode("ascii") + b"\n",
                },
            )
            original = source.read_bytes()
            dest = output_path(source, "_grainline")
            stats = process_path(source, dest, smooth())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(dest.name, "benchy_grainline.gcode.3mf")
            self.assertEqual(stats.plates, 1)
            with zipfile.ZipFile(dest) as archive:
                names = set(archive.namelist())
                self.assertIn("3D/3dmodel.model", names)
                self.assertEqual(archive.read("3D/3dmodel.model"), model)
                self.assertEqual(archive.read("Metadata/plate_1.png"), thumbnail)
                rewritten = archive.read("Metadata/plate_1.gcode")
                self.assertIn(b"M104 S", rewritten)
                self.assertIn(b"Grainline", rewritten)
                digest = archive.read("Metadata/plate_1.gcode.md5").strip()
                self.assertEqual(digest, hashlib.md5(rewritten).hexdigest().encode("ascii"))

    def test_every_plate_is_updated_and_an_empty_plate_is_copied(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "two.gcode.3mf"
            _write_archive(
                source,
                {
                    "Metadata/plate_1.gcode": PLATE.encode("utf-8"),
                    "Metadata/plate_2.gcode": PLATE.replace("X1", "X8").encode("utf-8"),
                    "Metadata/plate_3.gcode": EMPTY_PLATE.encode("utf-8"),
                    "3D/3dmodel.model": b"model",
                },
            )
            dest = output_path(source, "_grainline")
            stats = process_path(source, dest, smooth())
            self.assertEqual(stats.plates, 2)
            self.assertIn("copied unchanged", stats.note)
            with zipfile.ZipFile(dest) as archive:
                self.assertIn(b"M104 S", archive.read("Metadata/plate_1.gcode"))
                self.assertIn(b"M104 S", archive.read("Metadata/plate_2.gcode"))
                self.assertEqual(archive.read("Metadata/plate_3.gcode"), EMPTY_PLATE.encode("utf-8"))
                self.assertEqual(archive.read("3D/3dmodel.model"), b"model")

    def test_one_plate_can_be_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "two.gcode.3mf"
            second = PLATE.replace("X1", "X8").encode("utf-8")
            _write_archive(
                source,
                {
                    "Metadata/plate_1.gcode": PLATE.encode("utf-8"),
                    "Metadata/plate_2.gcode": second,
                },
            )
            dest = output_path(source, "_grainline")
            stats = process_path(source, dest, smooth(), plates=[2])
            self.assertEqual(stats.plates, 1)
            with zipfile.ZipFile(dest) as archive:
                self.assertEqual(archive.read("Metadata/plate_1.gcode"), PLATE.encode("utf-8"))
                self.assertIn(b"M104 S", archive.read("Metadata/plate_2.gcode"))

    def test_a_project_without_sliced_gcode_explains_the_export(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "project.3mf"
            _write_archive(source, {"3D/3dmodel.model": b"<model/>"})
            with self.assertRaises(GcodeError) as caught:
                inspect_archive(source)
            self.assertIn("Export plate sliced file", str(caught.exception))
            dest = output_path(source, "_grainline")
            self.assertTrue(dest.name.endswith(".gcode.3mf"))
            with self.assertRaises(GcodeError):
                process_path(source, dest, smooth())

    def test_a_processed_archive_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "done.gcode.3mf"
            _write_archive(
                source,
                {"Metadata/plate_1.gcode": ("; Grainline 1.1.0: already\n" + PLATE).encode("utf-8")},
            )
            with self.assertRaises(GcodeError) as caught:
                inspect_archive(source)
            self.assertIn("already run through Grainline", str(caught.exception))

    def test_cli_writes_a_gcode_3mf(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "part.gcode.3mf"
            _write_archive(source, {"Metadata/plate_1.gcode": PLATE.encode("utf-8"), "3D/3dmodel.model": b"m"})
            dest = Path(folder) / "part_grainline.gcode.3mf"
            code = main([str(source), "-o", str(dest), "--min", "210", "--max", "230", "--style", "smooth"])
            self.assertEqual(code, 0)
            with zipfile.ZipFile(dest) as archive:
                self.assertIn(b"M104 S", archive.read("Metadata/plate_1.gcode"))
                self.assertEqual(archive.read("3D/3dmodel.model"), b"m")


if __name__ == "__main__":
    unittest.main()
