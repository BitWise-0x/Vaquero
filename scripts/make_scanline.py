"""Renders assets/Vaquero-scan.webp: the app icon with an amber line sweeping down inside its viewfinder corners,
looping, for the README and the website. The app draws the same sweep live in its window header (vaquero/app.py,
ScanIcon); both follow the recipe below.

Every size is a fraction of the icon edge. The line is a sharp core plus a soft halo, blended toward the accent
color by up to 90%, kept inside the corners (14% to 86% of the edge), fading in at the top and out at the bottom.
It sweeps for 2.4 s, then the plain icon holds for 1.6 s: a 4 s loop at 30 fps.

Run: uv run python scripts/make_scanline.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICNS = ASSETS / "Vaquero.icns"
OUT = ASSETS / "Vaquero-scan.webp"
SIZE = 480  # shown at up to 240 css px on the website, so sharp on 2x screens
QUALITY = 82

AMBER = np.array([239, 177, 70], dtype=np.float32)
FPS = 30
PERIOD = 4 * FPS
SWEEP = int(2.4 * FPS)
INSET = 0.14
CORE, HALO, SPILL = 3 / 480, 26 / 480, 40 / 480


def sweep_alpha(ys: np.ndarray, y: float, edge: float, p: float) -> np.ndarray:
    """The line's blend weight per row at sweep progress p (0..1), centered on row y."""
    fade = np.sin(np.pi * p) ** 0.6
    band = 0.85 * np.exp(-(((ys - y) / (edge * CORE)) ** 2)) + 0.22 * np.exp(-(((ys - y) / (edge * HALO)) ** 2))
    return band * fade


def icon_1024() -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "icon.png"
        subprocess.run(["sips", "-s", "format", "png", "-Z", "1024", str(ICNS), "--out", str(png)], check=True, capture_output=True)
        return Image.open(png).convert("RGBA").copy()


def main() -> None:
    icon = icon_1024().resize((SIZE, SIZE), Image.LANCZOS)
    base = np.asarray(icon, dtype=np.float32)
    rgb, alpha = base[..., :3], base[..., 3:]
    lo, hi = int(SIZE * INSET), SIZE - int(SIZE * INSET)
    pad = int(SIZE * SPILL)
    rows = slice(max(lo - pad, 0), min(hi + pad, SIZE))
    ys = np.arange(SIZE, dtype=np.float32)[rows, None]

    frames: list[Image.Image] = []
    for t in range(PERIOD):
        f = rgb.copy()
        if t < SWEEP:
            p = t / (SWEEP - 1)
            a = sweep_alpha(ys, lo + p * (hi - lo), SIZE, p)
            band = f[rows, lo:hi]
            band += (AMBER - band) * a[:, :, None] * 0.9
        frames.append(Image.fromarray(np.clip(np.concatenate([f, alpha], axis=2), 0, 255).astype(np.uint8), "RGBA"))

    first, *rest = frames
    # whole-millisecond frame times that add up to exactly the loop length
    durations = [round((t + 1) * 1000 / FPS) - round(t * 1000 / FPS) for t in range(PERIOD)]
    first.save(OUT, save_all=True, append_images=rest, duration=durations, loop=0, quality=QUALITY, method=6)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(frames)} frames)", file=sys.stderr)


if __name__ == "__main__":
    main()
