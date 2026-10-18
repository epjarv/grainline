"""Draw the Grainline icon."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets"


def draw(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    margin = max(1, int(size * 0.06))
    radius = int(size * 0.22)
    pen.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(28, 18, 12, 255),
    )
    center_x = size * 0.50
    center_y = size * 0.52
    rings = (
        (0.38, (232, 196, 138, 255), 0.02),
        (0.31, (176, 122, 64, 255), 0.015),
        (0.245, (232, 196, 138, 255), -0.01),
        (0.185, (112, 70, 36, 255), 0.012),
        (0.13, (214, 168, 104, 255), -0.008),
        (0.08, (74, 44, 22, 255), 0.0),
        (0.035, (240, 214, 170, 255), 0.0),
    )
    for scale, color, shift in rings:
        radius = size * scale
        offset = size * shift
        pen.ellipse(
            [center_x + offset - radius, center_y - radius, center_x + offset + radius, center_y + radius],
            fill=color,
        )
    return image


def main() -> None:
    OUT.mkdir(exist_ok=True)
    image = draw(256)
    image.save(OUT / "grainline.png")
    image.save(
        OUT / "grainline.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


if __name__ == "__main__":
    main()
