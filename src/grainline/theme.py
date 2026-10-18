"""Colors shared by the window and the temperature preview."""

BG = "#1b1410"
CARD = "#2a201a"
CARD_EDGE = "#3d2e24"
TEXT = "#f6ead8"
MUTED = "#b7a394"
AMBER = "#e2a04a"
AMBER_DARK = "#b8752e"
INPUT = "#140e0b"
LINE = "#8c6240"
WARN = "#e3a06a"
PALE = (240, 210, 160)
DARK = (74, 44, 22)


def wood_hex(amount: float) -> str:
    """Pale sapwood at 0, dark grain at 1."""
    amount = max(0.0, min(1.0, amount))
    red = int(PALE[0] + (DARK[0] - PALE[0]) * amount)
    green = int(PALE[1] + (DARK[1] - PALE[1]) * amount)
    blue = int(PALE[2] + (DARK[2] - PALE[2]) * amount)
    return f"#{red:02x}{green:02x}{blue:02x}"
