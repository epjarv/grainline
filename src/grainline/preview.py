"""Draw the temperature schedule on a tkinter canvas."""

from __future__ import annotations

import tkinter as tk

from grainline.engine import Inspection, Settings, held_layer_temps, held_time_temps
from grainline.theme import INPUT, MUTED, TEXT, wood_hex


def preview_series(settings: Settings, inspection: Inspection | None) -> tuple[list[float], list[int], str]:
    """Return x positions, held temperatures, and whether x is layers or seconds."""
    if settings.mode == "time":
        duration = 15 * 60
        if inspection is not None and inspection.estimated_seconds > 1:
            duration = inspection.estimated_seconds
        points = held_time_temps(duration, settings)
        return [point[0] for point in points], [point[1] for point in points], "time"
    count = inspection.layer_count if inspection is not None and inspection.layer_count else 180
    temps = held_layer_temps(count, settings)
    return [float(index) for index in range(len(temps))], temps, "layers"


def draw_preview(canvas: tk.Canvas, settings: Settings, inspection: Inspection | None) -> tuple[int, int]:
    """Paint the grain curve. Returns the coolest and hottest temperatures drawn."""
    canvas.delete("all")
    width = max(canvas.winfo_width(), 10)
    height = max(canvas.winfo_height(), 10)
    canvas.configure(bg=INPUT)
    xs, temps, _axis = preview_series(settings, inspection)
    if not temps:
        return int(settings.temp_min), int(settings.temp_max)

    if len(xs) > 900:
        stride = max(1, len(xs) // 900)
        xs = xs[::stride]
        temps = temps[::stride]

    pad_left, pad_right, pad_top, pad_bottom = 52, 18, 16, 28
    plot_w = max(1, width - pad_left - pad_right)
    plot_h = max(1, height - pad_top - pad_bottom)
    low = settings.temp_min
    high = settings.temp_max
    span = max(1e-6, high - low)
    x0 = xs[0]
    x_span = max(1e-6, xs[-1] - xs[0])

    def px(x: float) -> float:
        return pad_left + (x - x0) / x_span * plot_w

    def py(temp: float) -> float:
        return pad_top + (1 - (temp - low) / span) * plot_h

    top = pad_top
    bottom = pad_top + plot_h
    canvas.create_line(pad_left, top, pad_left, bottom, fill="#3a2c24")
    canvas.create_line(pad_left, bottom, pad_left + plot_w, bottom, fill="#3a2c24")
    for fraction in (0, 0.5, 1):
        y = pad_top + (1 - fraction) * plot_h
        canvas.create_line(pad_left, y, pad_left + plot_w, y, fill="#2a211b")
        label = int(round(low + fraction * span))
        canvas.create_text(pad_left - 8, y, text=f"{label}°", fill=MUTED, anchor="e", font=("Segoe UI", 10))

    polygon = [pad_left, bottom]
    points: list[tuple[float, float]] = []
    previous_y = py(temps[0])
    for index, (x_value, temp) in enumerate(zip(xs, temps)):
        x = px(x_value)
        y = py(temp)
        if index == 0:
            points.append((x, y))
            polygon.extend((x, y))
        else:
            points.append((x, previous_y))
            points.append((x, y))
            polygon.extend((x, previous_y, x, y))
        previous_y = y
    polygon.extend((points[-1][0], bottom))
    if len(polygon) >= 6:
        canvas.create_polygon(polygon, fill="#3a2a1c", outline="")

    for start, end in zip(points, points[1:]):
        amount = 1 - ((start[1] + end[1]) / 2 - pad_top) / plot_h
        canvas.create_line(*start, *end, fill=wood_hex(amount), width=2.4, capstyle=tk.ROUND)

    end_label = "sample length" if inspection is None else "end of print"
    canvas.create_text(pad_left, height - 8, text="start", fill=MUTED, anchor="w", font=("Segoe UI", 10))
    canvas.create_text(width - pad_right, height - 8, text=end_label, fill=MUTED, anchor="e", font=("Segoe UI", 10))
    canvas.create_text(
        pad_left + 8,
        pad_top + 4,
        text="darker is hotter",
        fill=TEXT,
        anchor="nw",
        font=("Segoe UI", 10),
    )
    return min(temps), max(temps)
