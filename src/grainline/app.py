"""Grainline window."""

from __future__ import annotations

import random
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox

import customtkinter as ctk

from grainline.engine import (
    FLAVOR_LABELS,
    STYLE_LABELS,
    GcodeError,
    Inspection,
    Settings,
    already_processed,
    held_layer_temps,
    held_time_temps,
    inspect_gcode,
    output_path,
    process_path,
    read_gcode,
)
from grainline.threemf import PlateInfo, inspect_archive, is_sliced_archive
from grainline.prefs import load_prefs, save_prefs
from grainline.preview import draw_preview
from grainline.theme import AMBER, AMBER_DARK, BG, CARD, INPUT, LINE, MUTED, TEXT, WARN
from grainline.version import __version__

PRESETS = (("Subtle", 215, 230), ("Classic", 210, 240), ("Strong", 200, 250))
STYLE_HELP = {
    "natural": "Uneven bands, closer to real grain.",
    "smooth": "A steady wave from pale to dark.",
    "rings": "Longer pale stretches with a darker line, like growth rings.",
    "figure": "Wandering color that doesn't repeat as evenly.",
}
LABEL_TO_STYLE = {label: key for key, label in STYLE_LABELS.items()}


def run(open_path: str | None = None) -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    app = GrainlineApp(open_path)
    app.mainloop()


def _asset(name: str) -> Path | None:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS"))
    else:
        root = Path(__file__).resolve().parents[2]
    path = root / "assets" / name
    return path if path.is_file() else None


def _parse_int(text: str) -> int | None:
    try:
        return int(text.strip())
    except ValueError:
        return None


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} sec"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        if remainder == 0:
            return f"{minutes} min"
        return f"{minutes} min {remainder} sec"
    hours, minutes = divmod(minutes, 60)
    if minutes == 0:
        return f"{hours} hr"
    return f"{hours} hr {minutes} min"


class GrainlineApp(ctk.CTk):
    def __init__(self, open_path: str | None = None) -> None:
        super().__init__()
        self.prefs = load_prefs()
        self.path: Path | None = None
        self.inspection: Inspection | None = None
        self.output: Path | None = None
        self.blocked = ""
        self._scan_id = 0
        self._events: Queue = Queue()
        self._ready = False
        self._applying = False
        self._save_job = None
        self._last_good: Settings | None = None
        self._canvas_size = (0, 0)
        self._seed = int(self.prefs.get("seed", random.randint(1, 99999)))
        self._layer_spacing = float(self.prefs.get("spacing_layers", 36))
        self._layer_every = int(self.prefs.get("every_n_layers", 1))
        self._time_cycle = float(self.prefs.get("cycle_seconds", 180))
        self._time_every = float(self.prefs.get("every_n_seconds", 20))

        self.title("Grainline")
        self.configure(fg_color=BG)
        self.minsize(1040, 860)
        self._center(1140, 1000)
        icon = _asset("grainline.ico")
        if icon is not None:
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

        self.font = ctk.CTkFont(family="Segoe UI", size=14)
        self.font_small = ctk.CTkFont(family="Segoe UI", size=12)
        self.font_title = ctk.CTkFont(family="Segoe UI", size=26, weight="bold")
        self.font_button = ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        self._build()
        self._ready = True
        self._refresh()
        self.bind("<Control-o>", lambda _event: self._browse())
        self.bind("<Control-Return>", lambda _event: self._write())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(50, self._poll_events)
        self.after(200, self._hook_drop)
        start = open_path or self.prefs.get("last_file")
        if start and Path(start).is_file():
            self.after(100, lambda: self._load_path(str(start)))

    def _center(self, width: int, height: int) -> None:
        self.update_idletasks()
        left, top, screen_w, screen_h = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                rect = wintypes.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    left, top = rect.left, rect.top
                    screen_w, screen_h = rect.right - rect.left, rect.bottom - rect.top
            except Exception:
                pass
        width = min(width, max(980, screen_w - 40))
        height = min(height, max(700, screen_h - 40))
        x = left + max(0, (screen_w - width) // 2)
        y = top + max(0, (screen_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=0, minsize=430)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, fg_color=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(16, 8), pady=12)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)
        controls = ctk.CTkScrollableFrame(
            left,
            fg_color=BG,
            scrollbar_button_color=LINE,
            scrollbar_button_hover_color=AMBER_DARK,
        )
        controls.grid(row=0, column=0, sticky="nsew")
        actions = ctk.CTkFrame(left, fg_color=BG)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        right = ctk.CTkFrame(self, fg_color=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 18), pady=16)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=1)

        self._build_header(controls)
        self._build_file(controls)
        self._build_temperature(controls)
        self._build_grain(controls)
        self._build_more(controls)
        self._build_actions(actions)
        self._build_preview(right)
        self._apply_mode_sliders()

    def _card(self, parent, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12)
        card.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(card, text=title, text_color=TEXT, font=self.font, anchor="w").pack(
            fill="x", padx=12, pady=(8, 0)
        )
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=12, pady=(4, 10))
        return body

    def _build_header(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        logo = self._logo()
        if logo is not None:
            ctk.CTkLabel(row, text="", image=logo).pack(side="left", padx=(0, 10))
        words = ctk.CTkFrame(row, fg_color="transparent")
        words.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(words, text="Grainline", text_color=TEXT, font=self.font_title, anchor="w").pack(fill="x")
        ctk.CTkLabel(
            words,
            text=f"Wood-grain color for sliced G-code  ·  {__version__}",
            text_color=MUTED,
            font=self.font_small,
            anchor="w",
        ).pack(fill="x")

    def _logo(self):
        png = _asset("grainline.png")
        if png is None:
            return None
        try:
            from PIL import Image

            self._logo_image = ctk.CTkImage(Image.open(png), size=(48, 48))
            return self._logo_image
        except Exception:
            return None

    def _build_file(self, parent) -> None:
        body = self._card(parent, "G-code file")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")
        self.path_var = tk.StringVar()
        entry = ctk.CTkEntry(
            row,
            textvariable=self.path_var,
            fg_color=INPUT,
            border_color=LINE,
            text_color=TEXT,
            font=self.font_small,
        )
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        entry.configure(state="disabled")
        ctk.CTkButton(
            row,
            text="Browse",
            width=90,
            fg_color=AMBER,
            hover_color=AMBER_DARK,
            text_color=BG,
            font=self.font,
            command=self._browse,
        ).pack(side="left")
        ctk.CTkLabel(
            body,
            text="Drop a .gcode or Bambu .gcode.3mf file here, or press Ctrl+O.",
            text_color=MUTED,
            font=self.font_small,
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(fill="x", pady=(8, 0))
        self.meta = ctk.CTkLabel(
            body,
            text="No file yet. The preview is a sample until you open a print.",
            text_color=MUTED,
            font=self.font_small,
            anchor="w",
            wraplength=360,
            justify="left",
        )
        self.meta.pack(fill="x", pady=(6, 0))
        self.plates: list[PlateInfo] = []
        self.plate_menu = ctk.CTkOptionMenu(
            body,
            values=["All plates"],
            command=self._on_plate,
            fg_color=INPUT,
            button_color=AMBER_DARK,
            button_hover_color=AMBER,
            text_color=TEXT,
            font=self.font_small,
        )

    def _build_temperature(self, parent) -> None:
        body = self._card(parent, "Temperature")
        self.min_var = tk.StringVar(value=str(int(self.prefs.get("temp_min", 210))))
        self.max_var = tk.StringVar(value=str(int(self.prefs.get("temp_max", 240))))
        self.min_slider, self.min_value = self._temp_row(body, "Low", self.min_var, self._on_min_slider)
        self.max_slider, self.max_value = self._temp_row(body, "High", self.max_var, self._on_max_slider)
        self.min_slider.set(self._saved_temp(self.min_var, 210))
        self.max_slider.set(self._saved_temp(self.max_var, 240))
        presets = ctk.CTkFrame(body, fg_color="transparent")
        presets.pack(fill="x", pady=(10, 0))
        self.preset_buttons = {}
        for name, low, high in PRESETS:
            button = ctk.CTkButton(
                presets,
                text=name,
                width=90,
                fg_color=INPUT,
                hover_color=AMBER_DARK,
                text_color=TEXT,
                font=self.font_small,
                command=lambda lo=low, hi=high: self._apply_preset(lo, hi),
            )
            button.pack(side="left", padx=(0, 8))
            self.preset_buttons[name] = button
        self.temp_note = ctk.CTkLabel(
            body,
            text="",
            text_color=MUTED,
            font=self.font_small,
            anchor="w",
            wraplength=360,
            justify="left",
        )
        self.temp_note.pack(fill="x", pady=(8, 0))

    def _temp_row(self, parent, caption: str, variable: tk.StringVar, command):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(row, text=caption, text_color=TEXT, font=self.font, width=46, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(
            row,
            textvariable=variable,
            width=64,
            fg_color=INPUT,
            border_color=LINE,
            text_color=TEXT,
            font=self.font,
            justify="center",
        )
        entry.pack(side="right")
        entry.bind("<KeyRelease>", lambda _event: self._refresh())
        entry.bind("<FocusOut>", lambda _event, var=variable: self._commit_temp(var))
        entry.bind("<Return>", lambda _event, var=variable: self._commit_temp(var))
        slider = ctk.CTkSlider(
            row,
            from_=150,
            to=320,
            command=command,
            progress_color=AMBER,
            button_color=AMBER,
            button_hover_color=AMBER_DARK,
            fg_color=INPUT,
        )
        slider.pack(side="left", fill="x", expand=True, padx=(4, 8))
        return slider, entry

    def _build_grain(self, parent) -> None:
        body = self._card(parent, "Grain")
        labels = list(STYLE_LABELS.values())
        saved = self.prefs.get("style", "natural")
        current = STYLE_LABELS.get(saved, saved if saved in labels else "Natural grain")
        self.style_menu = ctk.CTkOptionMenu(
            body,
            values=labels,
            command=lambda _value: self._refresh(),
            fg_color=INPUT,
            button_color=AMBER_DARK,
            button_hover_color=AMBER,
            text_color=TEXT,
            font=self.font,
        )
        self.style_menu.set(current)
        self.style_menu.pack(fill="x")
        self.style_help = ctk.CTkLabel(
            body, text="", text_color=MUTED, font=self.font_small, anchor="w", wraplength=360, justify="left"
        )
        self.style_help.pack(fill="x", pady=(4, 8))
        self.mode_switch = ctk.CTkSegmentedButton(
            body,
            values=["By layer", "By time"],
            command=lambda _value: self._on_mode(),
            selected_color=AMBER,
            selected_hover_color=AMBER_DARK,
            unselected_color=INPUT,
            unselected_hover_color=CARD,
            text_color=TEXT,
            font=self.font_small,
        )
        self.mode_switch.set("By time" if self.prefs.get("mode") == "time" else "By layer")
        self.mode_switch.pack(fill="x", pady=(0, 4))
        self.width_slider, self.width_detail = self._slider(body, "Grain width", 8, 160, self._layer_spacing)
        self.every_slider, self.every_detail = self._slider(body, "New temperature every", 1, 30, self._layer_every)
        self.variation_slider, self.variation_detail = self._slider(
            body, "Variation", 0, 100, float(self.prefs.get("randomness", 0.3)) * 100
        )
        seed_row = ctk.CTkFrame(body, fg_color="transparent")
        seed_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(seed_row, text="Pattern", text_color=TEXT, font=self.font).pack(side="left")
        self.seed_var = tk.StringVar(value=str(self._seed))
        seed_entry = ctk.CTkEntry(
            seed_row,
            textvariable=self.seed_var,
            width=90,
            fg_color=INPUT,
            border_color=LINE,
            text_color=TEXT,
            font=self.font,
            justify="center",
        )
        seed_entry.pack(side="left", padx=8)
        seed_entry.bind("<KeyRelease>", lambda _event: self._refresh())
        ctk.CTkButton(
            seed_row,
            text="Shuffle",
            width=80,
            fg_color=INPUT,
            hover_color=AMBER_DARK,
            text_color=TEXT,
            font=self.font_small,
            command=self._shuffle,
        ).pack(side="left")

    def _slider(self, parent, caption: str, low: float, high: float, initial: float):
        detail = ctk.CTkLabel(
            parent, text=caption, text_color=MUTED, font=self.font_small, anchor="w", wraplength=380, justify="left"
        )
        detail.pack(fill="x", pady=(8, 0))
        slider = ctk.CTkSlider(
            parent,
            from_=low,
            to=high,
            command=lambda _value: self._refresh(),
            progress_color=AMBER,
            button_color=AMBER,
            button_hover_color=AMBER_DARK,
            fg_color=INPUT,
        )
        slider.set(initial)
        slider.pack(fill="x", pady=(4, 0))
        return slider, detail

    def _build_more(self, parent) -> None:
        holder = ctk.CTkFrame(parent, fg_color="transparent")
        holder.pack(fill="x", pady=(0, 8))
        self.more_btn = ctk.CTkButton(
            holder,
            text="More options",
            fg_color=CARD,
            hover_color=LINE,
            text_color=TEXT,
            font=self.font,
            anchor="w",
            command=self._toggle_more,
        )
        self.more_btn.pack(fill="x")
        self.more_frame = ctk.CTkFrame(holder, fg_color=CARD, corner_radius=14)
        inner = ctk.CTkFrame(self.more_frame, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=14)
        ctk.CTkLabel(inner, text="When the temperature changes", text_color=TEXT, font=self.font, anchor="w").pack(
            fill="x"
        )
        self.wait_switch = ctk.CTkSegmentedButton(
            inner,
            values=["Drift while printing", "Pause until hot"],
            command=lambda _value: self._refresh(),
            selected_color=AMBER,
            selected_hover_color=AMBER_DARK,
            unselected_color=INPUT,
            unselected_hover_color=CARD,
            text_color=TEXT,
            font=self.font_small,
        )
        self.wait_switch.set("Pause until hot" if self.prefs.get("wait_for_temp") else "Drift while printing")
        self.wait_switch.pack(fill="x", pady=(6, 4))
        self.wait_help = ctk.CTkLabel(
            inner, text="", text_color=MUTED, font=self.font_small, anchor="w", wraplength=360, justify="left"
        )
        self.wait_help.pack(fill="x")
        self.replace_var = tk.BooleanVar(value=bool(self.prefs.get("replace_existing", True)))
        ctk.CTkCheckBox(
            inner,
            text="Replace slicer temperatures during the print",
            variable=self.replace_var,
            command=self._refresh,
            text_color=TEXT,
            font=self.font_small,
            fg_color=AMBER,
            hover_color=AMBER_DARK,
            border_color=LINE,
            checkmark_color=BG,
        ).pack(anchor="w", pady=(10, 0))
        ctk.CTkLabel(
            inner,
            text="Warmup at the start and cooldown at the end are left alone. Turn this off only if your slicer should stay in control.",
            text_color=MUTED,
            font=self.font_small,
            anchor="w",
            wraplength=360,
            justify="left",
        ).pack(fill="x", pady=(4, 8))
        tool_row = ctk.CTkFrame(inner, fg_color="transparent")
        tool_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(tool_row, text="Nozzle tool", text_color=TEXT, font=self.font).pack(side="left")
        self.tool_var = tk.StringVar(value=str(int(self.prefs.get("tool", 0))))
        tool_entry = ctk.CTkEntry(
            tool_row,
            textvariable=self.tool_var,
            width=50,
            fg_color=INPUT,
            border_color=LINE,
            text_color=TEXT,
            font=self.font,
            justify="center",
        )
        tool_entry.pack(side="left", padx=8)
        tool_entry.bind("<KeyRelease>", lambda _event: self._refresh())
        ctk.CTkLabel(tool_row, text="Leave at 0 for one nozzle.", text_color=MUTED, font=self.font_small).pack(
            side="left"
        )
        suffix_row = ctk.CTkFrame(inner, fg_color="transparent")
        suffix_row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(suffix_row, text="File suffix", text_color=TEXT, font=self.font).pack(side="left")
        self.suffix_var = tk.StringVar(value=str(self.prefs.get("suffix", "_grainline")))
        suffix_entry = ctk.CTkEntry(
            suffix_row,
            textvariable=self.suffix_var,
            width=130,
            fg_color=INPUT,
            border_color=LINE,
            text_color=TEXT,
            font=self.font,
        )
        suffix_entry.pack(side="left", padx=8)
        suffix_entry.bind("<KeyRelease>", lambda _event: self._refresh())

    def _build_actions(self, parent) -> None:
        self.output_label = ctk.CTkLabel(
            parent,
            text="The new file is saved next to the original.",
            text_color=MUTED,
            font=self.font_small,
            anchor="w",
            wraplength=390,
            justify="left",
        )
        self.output_label.pack(fill="x", pady=(0, 8))
        self.write_btn = ctk.CTkButton(
            parent,
            text="Make grain file",
            height=46,
            fg_color=AMBER,
            hover_color=AMBER_DARK,
            text_color=BG,
            font=self.font_button,
            command=self._write,
        )
        self.write_btn.pack(fill="x")
        ctk.CTkLabel(
            parent,
            text="Your original file is not changed.",
            text_color=MUTED,
            font=self.font_small,
            anchor="w",
        ).pack(fill="x", pady=(6, 0))

    def _build_preview(self, parent) -> None:
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(card, text="Temperature preview", text_color=TEXT, font=self.font, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=14, pady=(12, 0)
        )
        self.shade_label = ctk.CTkLabel(
            card, text="", text_color=MUTED, font=self.font_small, anchor="w", wraplength=560, justify="left"
        )
        self.shade_label.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 0))
        self.canvas = tk.Canvas(card, height=260, highlightthickness=0, bd=0, bg=INPUT)
        self.canvas.grid(row=2, column=0, sticky="nsew", padx=12, pady=(8, 4))
        self.canvas.bind("<Configure>", self._on_canvas)
        self.hint_label = ctk.CTkLabel(
            card, text="", text_color=WARN, font=self.font_small, anchor="w", wraplength=560, justify="left"
        )
        self.hint_label.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))

        log_card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14)
        log_card.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        ctk.CTkLabel(log_card, text="Result", text_color=TEXT, font=self.font, anchor="w").pack(
            fill="x", padx=14, pady=(12, 0)
        )
        self.log = ctk.CTkTextbox(
            log_card,
            height=120,
            fg_color=INPUT,
            text_color=TEXT,
            font=self.font_small,
            wrap="word",
            activate_scrollbars=True,
        )
        self.log.pack(fill="x", padx=14, pady=(6, 8))
        self._set_log(
            "Open a G-code file, or a .gcode.3mf exported from Bambu Studio. Set the temperature range, check the preview, then make a new file."
        )
        self.folder_btn = ctk.CTkButton(
            log_card,
            text="Show the new file",
            fg_color=INPUT,
            hover_color=AMBER_DARK,
            text_color=TEXT,
            font=self.font,
            command=self._show_output,
        )
        self.folder_btn.pack(fill="x", padx=14, pady=(0, 14))
        self.folder_btn.pack_forget()

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "scanned":
                    _, path, scan_id, inspection, error, plates = event
                    self._scanned(path, scan_id, inspection, error, plates)
                elif kind == "progress":
                    self.write_btn.configure(text=f"Writing… {event[1]}%")
                elif kind == "wrote":
                    self._write_done(event[1])
                elif kind == "write_failed":
                    self._write_failed(event[1])
        except Empty:
            pass
        if self.winfo_exists():
            self.after(40, self._poll_events)

    def _hook_drop(self) -> None:
        try:
            import windnd

            windnd.hook_dropfiles(self, self._dropped, force_unicode=True)
        except Exception:
            return

    def _dropped(self, paths) -> None:
        copied = []
        for item in paths:
            if isinstance(item, bytes):
                copied.append(item.decode("utf-8", errors="replace"))
            else:
                copied.append(str(item))
        if copied:
            self.after(0, lambda: self._accept_drop(copied))

    def _accept_drop(self, paths: list[str]) -> None:
        self._load_path(paths[0])
        if len(paths) > 1:
            self._set_log("Used the first dropped file.")

    def _browse(self) -> None:
        folder = self.prefs.get("last_dir") or str(Path.home())
        chosen = filedialog.askopenfilename(
            parent=self,
            initialdir=folder if Path(folder).is_dir() else str(Path.home()),
            title="Open sliced G-code",
            filetypes=[
                ("Sliced G-code", "*.gcode *.gcode.3mf *.gco *.g *.nc"),
                ("Bambu sliced file", "*.gcode.3mf"),
                ("All files", "*.*"),
            ],
        )
        if chosen:
            self._load_path(chosen)

    def _load_path(self, path_str: str) -> None:
        path = Path(path_str)
        if not path.is_file():
            messagebox.showerror("Grainline", f"Can't find {path}", parent=self)
            return
        self.path = path
        self.path_var.set(str(path))
        self.blocked = ""
        self.inspection = None
        self.plates = []
        self.plate_menu.pack_forget()
        self.folder_btn.pack_forget()
        self.meta.configure(text="Reading layers…", text_color=MUTED)
        self.write_btn.configure(state="disabled")
        self._scan_id += 1
        scan_id = self._scan_id
        threading.Thread(target=self._scan, args=(path, scan_id), daemon=True).start()

    def _scan(self, path: Path, scan_id: int) -> None:
        try:
            plates: list[PlateInfo] = []
            if is_sliced_archive(path):
                plates = inspect_archive(path)
                inspection = plates[0].inspection
                error = ""
            else:
                text = read_gcode(path)
                if already_processed(text):
                    raise GcodeError(
                        "This G-code was already run through Grainline. Open the original file from the slicer."
                    )
                inspection = inspect_gcode(text)
                error = ""
                if inspection.layer_count == 0:
                    error = "No layers were found. Open a G-code file, or a .gcode.3mf exported from Bambu Studio."
        except GcodeError as exc:
            inspection = None
            plates = []
            error = str(exc)
        except OSError as exc:
            inspection = None
            plates = []
            error = f"Couldn't read that file. {exc}"
        self._events.put(("scanned", path, scan_id, inspection, error, plates))

    def _scanned(
        self,
        path: Path,
        scan_id: int,
        inspection: Inspection | None,
        error: str,
        plates: list[PlateInfo],
    ) -> None:
        if scan_id != self._scan_id or self.path != path:
            return
        self.inspection = inspection
        self.plates = plates
        self.blocked = error
        if len(plates) > 1:
            labels = [f"Plate {plate.number}" for plate in plates]
            self.plate_menu.configure(values=["All plates", *labels])
            self.plate_menu.set("All plates")
            self.plate_menu.pack(fill="x", pady=(8, 0))
        else:
            self.plate_menu.pack_forget()
        if error:
            self.meta.configure(text=error, text_color=WARN)
        else:
            self._describe_open_file()
        self.prefs["last_dir"] = str(path.parent)
        self.prefs["last_file"] = str(path)
        self._refresh()

    def _on_plate(self, value: str) -> None:
        if not self._ready or not self.plates:
            return
        if value == "All plates":
            chosen = self.plates[0]
        else:
            number = int(value.rsplit(" ", 1)[-1])
            chosen = next(plate for plate in self.plates if plate.number == number)
        self.inspection = chosen.inspection
        self._describe_open_file()
        self._refresh()

    def _describe_open_file(self) -> None:
        inspection = self.inspection
        if inspection is None:
            return
        time_bit = ""
        if inspection.estimated_seconds >= 30:
            time_bit = f" · about {format_duration(inspection.estimated_seconds)}"
        flavor = FLAVOR_LABELS.get(inspection.flavor, inspection.flavor)
        if self.plates:
            if len(self.plates) == 1:
                where = f"Plate {self.plates[0].number}"
            elif self.plate_menu.get() == "All plates":
                where = f"{len(self.plates)} plates"
            else:
                where = self.plate_menu.get()
            layers = inspection.layer_count
            if len(self.plates) > 1 and self.plate_menu.get() == "All plates":
                layers = sum(plate.inspection.layer_count for plate in self.plates)
            word = "layer" if layers == 1 else "layers"
            text = f"Sliced 3MF · {where} · {layers} {word}{time_bit} · {flavor}"
        else:
            word = "layer" if inspection.layer_count == 1 else "layers"
            text = f"{inspection.layer_count} {word}{time_bit} · {flavor}"
        self.meta.configure(text=text, text_color=MUTED)

    def _plate_numbers(self) -> list[int] | None:
        if not self.plates:
            return None
        if self.plate_menu.get() == "All plates":
            return [plate.number for plate in self.plates]
        number = int(self.plate_menu.get().rsplit(" ", 1)[-1])
        return [number]

    def _settings(self) -> Settings | None:
        low = _parse_int(self.min_var.get())
        high = _parse_int(self.max_var.get())
        if low is None or high is None:
            return None
        mode = "time" if self.mode_switch.get() == "By time" else "layers"
        try:
            tool = max(0, int(self.tool_var.get().strip() or "0"))
        except ValueError:
            return None
        try:
            seed = int(self.seed_var.get().strip())
        except ValueError:
            seed = self._seed
        style = LABEL_TO_STYLE.get(self.style_menu.get(), "natural")
        return Settings(
            temp_min=low,
            temp_max=high,
            style=style,
            spacing_layers=max(1.0, round(self._layer_spacing)),
            every_n_layers=max(1, int(round(self._layer_every))),
            randomness=min(1.0, max(0.0, self.variation_slider.get() / 100)),
            seed=seed,
            mode=mode,
            every_n_seconds=max(1.0, round(self._time_every, 1)),
            cycle_seconds=max(1.0, round(self._time_cycle, 1)),
            wait_for_temp=self.wait_switch.get() == "Pause until hot",
            replace_existing=bool(self.replace_var.get()),
            tool=tool,
        )

    def _on_min_slider(self, value: float) -> None:
        self.min_var.set(str(int(round(float(value)))))
        self._refresh()

    def _on_max_slider(self, value: float) -> None:
        self.max_var.set(str(int(round(float(value)))))
        self._refresh()

    def _commit_temp(self, variable: tk.StringVar) -> None:
        parsed = _parse_int(variable.get())
        if parsed is None:
            slider = self.min_slider if variable is self.min_var else self.max_slider
            variable.set(str(int(round(slider.get()))))
        else:
            parsed = min(320, max(150, parsed))
            variable.set(str(parsed))
            slider = self.min_slider if variable is self.min_var else self.max_slider
            slider.set(parsed)
        self._refresh()

    def _saved_temp(self, variable: tk.StringVar, fallback: int) -> int:
        parsed = _parse_int(variable.get())
        return fallback if parsed is None else min(320, max(150, parsed))

    def _apply_preset(self, low: int, high: int) -> None:
        self.min_var.set(str(low))
        self.max_var.set(str(high))
        self.min_slider.set(low)
        self.max_slider.set(high)
        self._refresh()

    def _on_mode(self) -> None:
        if not self._ready:
            return
        self._apply_mode_sliders()
        self._refresh()

    def _apply_mode_sliders(self) -> None:
        self._applying = True
        try:
            self._set_mode_slider_range()
        finally:
            self._applying = False

    def _set_mode_slider_range(self) -> None:
        if self.mode_switch.get() == "By time":
            self.width_slider.configure(from_=30, to=1200)
            self.every_slider.configure(from_=5, to=180)
            self.width_slider.set(min(1200, max(30, self._time_cycle)))
            self.every_slider.set(min(180, max(5, self._time_every)))
        else:
            self.width_slider.configure(from_=8, to=160)
            self.every_slider.configure(from_=1, to=30)
            self.width_slider.set(min(160, max(8, self._layer_spacing)))
            self.every_slider.set(min(30, max(1, self._layer_every)))

    def _capture_sliders(self) -> None:
        if self.mode_switch.get() == "By time":
            self._time_cycle = float(self.width_slider.get())
            self._time_every = float(self.every_slider.get())
        else:
            self._layer_spacing = float(self.width_slider.get())
            self._layer_every = float(self.every_slider.get())

    def _shuffle(self) -> None:
        self._seed = random.randint(1, 99999)
        self.seed_var.set(str(self._seed))
        self._refresh()

    def _toggle_more(self) -> None:
        if self.more_frame.winfo_ismapped():
            self.more_frame.pack_forget()
            self.more_btn.configure(text="More options")
        else:
            self.more_frame.pack(fill="x", pady=(8, 0))
            self.more_btn.configure(text="Fewer options")

    def _on_canvas(self, event) -> None:
        if not self._ready:
            return
        size = (event.width, event.height)
        if size != self._canvas_size and event.width > 20 and event.height > 20:
            self._canvas_size = size
            self._draw()

    def _refresh(self) -> None:
        if not self._ready or self._applying:
            return
        self._capture_sliders()
        settings = self._settings()
        if settings is not None and settings.temp_min < settings.temp_max:
            self._last_good = settings
        self._paint_presets(settings)
        self._paint_copy(settings)
        self._draw()
        self._sync_write(settings)
        self._save_soon()

    def _paint_presets(self, settings: Settings | None) -> None:
        for name, low, high in PRESETS:
            selected = settings is not None and settings.temp_min == low and settings.temp_max == high
            button = self.preset_buttons[name]
            button.configure(fg_color=AMBER if selected else INPUT, text_color=BG if selected else TEXT)

    def _paint_copy(self, settings: Settings | None) -> None:
        if settings is None:
            self.temp_note.configure(text="Enter a low and a high temperature.")
            return
        if settings.temp_max >= 250:
            note = "That's hot for a lot of wood PLA. Check the range on the spool."
        elif settings.temp_min < 190:
            note = "That's cool for a lot of wood PLA. Layers may not bond if you go too low."
        elif settings.temp_min >= settings.temp_max:
            note = "The low temperature has to be below the high temperature."
        else:
            note = "Stay inside the range printed on your filament."
        self.temp_note.configure(text=note, text_color=WARN if settings.temp_min >= settings.temp_max else MUTED)
        style = settings.style
        self.style_help.configure(text=STYLE_HELP.get(style, ""))
        if settings.mode == "time":
            self.width_detail.configure(
                text=f"One pale-to-dark cycle takes about {format_duration(settings.cycle_seconds)}."
            )
            self.every_detail.configure(
                text=f"Sends a new temperature about every {format_duration(settings.every_n_seconds)}."
            )
        else:
            width = int(settings.spacing_layers)
            every = int(settings.every_n_layers)
            self.width_detail.configure(text=f"One pale-to-dark cycle is about {width} layers tall.")
            if every == 1:
                every_text = "Sends a temperature every layer, so the shade can drift."
            else:
                every_text = f"Holds each temperature for {every} layers, which makes wider stripes."
            self.every_detail.configure(text=every_text)
        percent = int(round(settings.randomness * 100))
        if percent < 15:
            variation = f"{percent}% — even, repeating grain."
        elif percent < 55:
            variation = f"{percent}% — mostly even, with some irregular streaks."
        else:
            variation = f"{percent}% — wilder grain that doesn't repeat as cleanly."
        self.variation_detail.configure(text=variation)
        if settings.wait_for_temp:
            wait = "The printer pauses until the hotend reaches each new temperature. Stripes get harder, and the print takes longer."
        else:
            wait = "The hotend drifts toward the next temperature while the printer keeps moving. That's the softer wood look."
        self.wait_help.configure(text=wait)
        if self.path is None:
            self.output_label.configure(text="The new file is saved next to the original.", text_color=MUTED)
            return
        try:
            dest = output_path(self.path, self.suffix_var.get())
        except GcodeError as exc:
            self.output_label.configure(text=str(exc), text_color=WARN)
            return
        self.output_label.configure(text=f"Writes {dest.name} next to the original.", text_color=MUTED)

    def _draw(self) -> None:
        settings = self._settings()
        if settings is None or settings.temp_min >= settings.temp_max:
            settings = self._last_good
        if settings is None:
            self.canvas.delete("all")
            self.shade_label.configure(text="")
            self.hint_label.configure(text="")
            return
        low, high = draw_preview(self.canvas, settings, self.inspection)
        if self.inspection is None:
            self.shade_label.configure(text=f"Sample shape, about {low}–{high} °C. Open a print to match its length.")
        else:
            self.shade_label.configure(text=f"This print would run about {low}–{high} °C.")
        self.hint_label.configure(text=self._hint(settings, low, high))

    def _hint(self, settings: Settings, low: int, high: int) -> str:
        if self.inspection is None or self.blocked:
            return ""
        if self.inspection.layer_count == 1 and settings.mode == "layers":
            return "This file has one layer, so layer changes can only set one temperature. Switch to By time to vary the color during the print."
        span = settings.temp_max - settings.temp_min
        if span > 0 and (high - low) < span * 0.35:
            if settings.mode == "layers":
                return "The color barely moves across this print. Make the grain width smaller if you want more bands."
            return "The color barely moves across this print. Shorten the cycle if you want more bands."
        if self.inspection.flavor == "z-height":
            return "This file has no layer comments, so Grainline is using Z moves. Re-slice with layer comments on if the bands land in the wrong place."
        if len(self.plates) > 1 and self.plate_menu.get() == "All plates":
            return "Showing the first plate. Every plate in the file gets the same grain settings."
        return ""

    def _sync_write(self, settings: Settings | None) -> None:
        ready = (
            self.path is not None
            and self.inspection is not None
            and self.inspection.layer_count > 0
            and not self.blocked
            and settings is not None
            and settings.temp_min < settings.temp_max
        )
        if ready:
            self.write_btn.configure(state="normal", fg_color=AMBER, text_color=BG)
        else:
            self.write_btn.configure(state="disabled", fg_color="#4a3b30", text_color=MUTED)

    def _save_soon(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(500, self._save_prefs)

    def _save_prefs(self) -> None:
        self._save_job = None
        settings = self._settings()
        if settings is None:
            return
        self._seed = settings.seed
        data = {
            "temp_min": settings.temp_min,
            "temp_max": settings.temp_max,
            "style": settings.style,
            "spacing_layers": self._layer_spacing,
            "every_n_layers": self._layer_every,
            "randomness": settings.randomness,
            "seed": settings.seed,
            "mode": settings.mode,
            "every_n_seconds": self._time_every,
            "cycle_seconds": self._time_cycle,
            "wait_for_temp": settings.wait_for_temp,
            "replace_existing": settings.replace_existing,
            "tool": settings.tool,
            "suffix": self.suffix_var.get(),
            "last_dir": self.prefs.get("last_dir", ""),
            "last_file": str(self.path) if self.path else self.prefs.get("last_file", ""),
        }
        try:
            save_prefs(data)
            self.prefs.update(data)
        except OSError:
            return

    def _write(self) -> None:
        if str(self.write_btn.cget("state")) == "disabled" or self.path is None:
            return
        settings = self._settings()
        if settings is None:
            return
        try:
            dest = output_path(self.path, self.suffix_var.get())
        except GcodeError as exc:
            messagebox.showerror("Grainline", str(exc), parent=self)
            return
        if dest.exists():
            replace = messagebox.askyesno("Grainline", f"{dest.name} already exists. Replace it?", parent=self)
            if not replace:
                return
        self.write_btn.configure(state="disabled", text="Writing…")
        self._set_log("Writing temperature changes…")
        plates = self._plate_numbers()
        threading.Thread(target=self._write_worker, args=(self.path, dest, settings, plates), daemon=True).start()

    def _write_worker(self, source: Path, dest: Path, settings: Settings, plates: list[int] | None) -> None:
        def progress(fraction: float) -> None:
            self._events.put(("progress", int(fraction * 100)))

        try:
            stats = process_path(source, dest, settings, plates, progress)
        except Exception as exc:
            self._events.put(("write_failed", exc))
            return
        self._events.put(("wrote", stats))

    def _write_failed(self, exc: Exception) -> None:
        self.write_btn.configure(state="normal", text="Make grain file")
        message = str(exc) if isinstance(exc, GcodeError) else f"Couldn't write the file.\n{exc}"
        self._set_log(message)
        self._sync_write(self._settings())
        messagebox.showerror("Grainline", message, parent=self)

    def _write_done(self, stats) -> None:
        self.output = Path(stats.output)
        self.write_btn.configure(state="normal", text="Make grain file")
        lines = [
            f"Wrote {stats.output}",
            f"{stats.layers} layers · {stats.injected} temperature commands · {stats.lowest}–{stats.highest} °C",
            "The original file was not changed.",
        ]
        if str(stats.output).lower().endswith(".gcode.3mf"):
            lines.append("Import this .gcode.3mf back into Bambu Studio.")
        if stats.plates > 1:
            lines.append(f"Updated {stats.plates} plates.")
        if stats.replaced:
            noun = "line" if stats.replaced == 1 else "lines"
            lines.append(f"Set aside {stats.replaced} slicer temperature {noun} inside the print.")
        if stats.note:
            lines.append(stats.note)
        if stats.flavor == "z-height":
            lines.append("Layers were read from Z moves because the file had no layer comments.")
        self._set_log("\n".join(lines))
        self.folder_btn.pack(fill="x", padx=14, pady=(0, 14))
        self._save_prefs()

    def _show_output(self) -> None:
        if self.output is None or not self.output.exists():
            return
        subprocess.run(["explorer", f"/select,{self.output}"], check=False)

    def _set_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", text)
        self.log.configure(state="disabled")

    def _close(self) -> None:
        try:
            self._save_prefs()
        finally:
            self.destroy()
