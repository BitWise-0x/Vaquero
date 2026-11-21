"""Renders the menu bar icon from assets/Vaquero.png: the viewfinder corners and the buffalo as a single-color
template image (black plus transparency), which macOS tints for a light or dark menu bar. Writes
assets/MenuBar-18.png and assets/MenuBar-36.png (18 pt at 1x and 2x). The app sweeps its scan line across this
image while it works (vaquero/menubar.py).

Run: uv run --with pillow python scripts/make_menubar_icon.py
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "Vaquero.png"
LUMINANCE = 90  # the icon's background is near black; the corners and the buffalo are bright


def main() -> None:
    icon = Image.open(SOURCE).convert("RGBA")
    mask = Image.new("L", icon.size, 0)
    src, out = icon.load(), mask.load()
    for y in range(icon.size[1]):
        for x in range(icon.size[0]):
            r, g, b, a = src[x, y]
            out[x, y] = 255 if a > 128 and 0.3 * r + 0.59 * g + 0.11 * b > LUMINANCE else 0
    mask = mask.crop(mask.getbbox())
    for size in (18, 36):
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        image.putalpha(mask.resize((size, size), Image.LANCZOS))
        image.save(ROOT / "assets" / f"MenuBar-{size}.png", optimize=True)
    print("wrote assets/MenuBar-18.png, assets/MenuBar-36.png")


if __name__ == "__main__":
    main()
