"""Renders the website's icons and link-preview image into site/public from assets/Vaquero.icns:
favicon.ico (16, 32, 48 px), apple-touch-icon.png (180 px, opaque), icon-192.png and icon-512.png for the web manifest,
icon-maskable-512.png (the icon inside the maskable safe zone on the app's background), and og-image.png
(1200x630: the icon beside the name and headline).

Run: uv run python scripts/make_web_icons.py
"""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ICNS = ROOT / "assets" / "Vaquero.icns"
OUT = ROOT / "site" / "public"
BG = (12, 15, 14)  # the app's background, #0c0f0e
FG = (231, 236, 232)  # the app's text, #e7ece8
AMBER = (239, 177, 70)
FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
HEADLINE = ("Find and round up", "every photo & video, by face")  # "every photo & video" stays on one line
TAGLINE = ("Free, open source, no account.", "Stays on your Mac.")


def icon_1024() -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "icon.png"
        subprocess.run(["sips", "-s", "format", "png", "-Z", "1024", str(ICNS), "--out", str(png)], check=True, capture_output=True)
        return Image.open(png).convert("RGBA").copy()


def on_background(icon: Image.Image, size: int, scale: float) -> Image.Image:
    """The icon centered at scale of size, on the opaque app background."""
    canvas = Image.new("RGBA", (size, size), BG + (255,))
    side = round(size * scale)
    canvas.alpha_composite(icon.resize((side, side), Image.LANCZOS), ((size - side) // 2, (size - side) // 2))
    return canvas.convert("RGB")


def og_image(icon: Image.Image) -> Image.Image:
    w, h = 1200, 630
    img = Image.new("RGB", (w, h), BG)
    side = 420
    small = icon.resize((side, side), Image.LANCZOS)
    img.paste(small, (50, (h - side) // 2), small)
    draw = ImageDraw.Draw(img)
    name = ImageFont.truetype(FONT, 76, index=1)
    bold = ImageFont.truetype(FONT, 50, index=1)  # the longer headline line fits with a margin to the right edge
    regular = ImageFont.truetype(FONT, 32, index=0)
    # text, font, color, space below; the block is centered on the icon
    lines = [("Vaquero", name, AMBER, 16), (HEADLINE[0], bold, FG, 8), (HEADLINE[1], bold, FG, 24)]
    lines += [(TAGLINE[0], regular, AMBER, 6), (TAGLINE[1], regular, AMBER, 0)]
    heights = [draw.textbbox((0, 0), text, font=font, anchor="lt")[3] for text, font, _, _ in lines]
    y = (h - sum(heights) - sum(gap for *_, gap in lines)) / 2
    for (text, font, color, gap), height in zip(lines, heights, strict=True):
        draw.text((465, y), text, font=font, fill=color, anchor="lt")
        y += height + gap
    return img


def main() -> None:
    icon = icon_1024()
    OUT.mkdir(parents=True, exist_ok=True)
    icon.resize((256, 256), Image.LANCZOS).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    on_background(icon, 180, 1.0).save(OUT / "apple-touch-icon.png")
    icon.resize((192, 192), Image.LANCZOS).save(OUT / "icon-192.png")
    icon.resize((512, 512), Image.LANCZOS).save(OUT / "icon-512.png")
    # maskable icons are cropped to a circle of 80% of the edge; the icon art fills about 82% of its own canvas
    on_background(icon, 512, 0.78).save(OUT / "icon-maskable-512.png")
    og_image(icon).save(OUT / "og-image.png", optimize=True)
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("*")) if p.suffix in {".ico", ".png"}))


if __name__ == "__main__":
    main()
