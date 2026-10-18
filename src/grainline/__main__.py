"""Launch the Grainline window, or the command line when a file is passed."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _prepare_windows() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> int:
    _prepare_windows()
    args = [arg for arg in sys.argv[1:] if arg != "--gui"]
    frozen = bool(getattr(sys, "frozen", False))
    open_path = None
    if frozen and args and not args[0].startswith("-"):
        open_path = args[0]
        args = []
    if frozen or not args:
        try:
            from grainline.app import run

            run(open_path)
        except Exception:
            _report_crash()
            return 1
        return 0
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from grainline.cli import main as cli_main

    return cli_main(args)


def _report_crash() -> None:
    details = traceback.format_exc()
    try:
        from grainline.prefs import app_dir

        log = app_dir() / "crash.log"
        log.write_text(details, encoding="utf-8")
    except Exception:
        log = None
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        extra = f"\n\nDetails were saved to {log}" if log else ""
        messagebox.showerror("Grainline", details[-1800:] + extra)
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
