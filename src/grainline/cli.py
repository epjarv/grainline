"""Command-line entry for Grainline. The window is the normal way to use it."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from grainline.engine import STYLES, GcodeError, Settings, output_path, process_file
from grainline.version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grainline",
        description="Add wood-grain hotend temperatures to sliced G-code.",
    )
    parser.add_argument("gcode", nargs="?", help="Sliced G-code file")
    parser.add_argument("-o", "--output", help="Where to write the new G-code")
    parser.add_argument("--min", dest="temp_min", type=float, default=210)
    parser.add_argument("--max", dest="temp_max", type=float, default=240)
    parser.add_argument("--style", choices=STYLES, default="natural")
    parser.add_argument("--spacing", type=float, default=36, help="Layers in one pale-to-dark cycle")
    parser.add_argument("--every", type=int, default=1, help="Layers between temperature commands")
    parser.add_argument("--randomness", type=float, default=0.3, help="0 to 1")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--time", action="store_true", help="Schedule by print time instead of layers")
    parser.add_argument("--every-seconds", type=float, default=20)
    parser.add_argument("--cycle-seconds", type=float, default=180)
    parser.add_argument("--wait", action="store_true", help="Pause at each change with M109")
    parser.add_argument("--keep-temps", action="store_true", help="Leave the slicer's temperature lines in place")
    parser.add_argument("--tool", type=int, default=0)
    parser.add_argument("--suffix", default="_grainline")
    parser.add_argument("--version", action="version", version=f"Grainline {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.gcode:
        parser.print_help()
        return 2
    source = Path(args.gcode)
    if not source.is_file():
        print(f"Can't find {source}", file=sys.stderr)
        return 1
    settings = Settings(
        temp_min=args.temp_min,
        temp_max=args.temp_max,
        style=args.style,
        spacing_layers=args.spacing,
        every_n_layers=args.every,
        randomness=args.randomness,
        seed=args.seed,
        mode="time" if args.time else "layers",
        every_n_seconds=args.every_seconds,
        cycle_seconds=args.cycle_seconds,
        wait_for_temp=args.wait,
        replace_existing=not args.keep_temps,
        tool=args.tool,
    )
    try:
        dest = Path(args.output) if args.output else output_path(source, args.suffix)
        stats = process_file(source, dest, settings)
    except GcodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
        print(
            f"Wrote {stats.output}\n"
            f"{stats.layers} layers, {stats.injected} temperature commands, "
            f"{stats.lowest}-{stats.highest} C"
        )
    return 0
