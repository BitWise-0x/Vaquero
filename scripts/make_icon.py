"""Draws the Vaquero art: assets/Vaquero.icns for the bundle, assets/Vaquero.png for the window header,
assets/buffalo.png for the log banner (the buffalo_l model pack's namesake).

Run: uv run python scripts/make_icon.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

SIZE = 1024
OUT = Path(__file__).resolve().parent.parent / "assets" / "Vaquero.icns"
PNG = OUT.with_suffix(".png")
PNG_SIZE = 256
BUFFALO = OUT.with_name("buffalo.png")
BUFFALO_SIZE = 128
AMBER = "#efb146"
BUFFALO_IN_FRAME = 0.54  # the mascot's scale inside the icon's viewfinder


def draw() -> QImage:
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # macOS icon grid: 824 px body centred on 1024, corner radius about 22.5% of the body
    body = QRectF(100, 100, 824, 824)
    bg = QLinearGradient(body.topLeft(), body.bottomRight())
    bg.setColorAt(0, QColor("#1b221f"))
    bg.setColorAt(1, QColor("#0c0f0e"))
    shape = QPainterPath()
    shape.addRoundedRect(body, 185, 185)
    p.fillPath(shape, QBrush(bg))
    p.strokePath(shape, QPen(QColor("#2e3733"), 6))

    # viewfinder brackets in the bitwise0x.com signal amber
    pen = QPen(QColor("#efb146"), 44, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    inset, arm = 230, 130
    lo, hi = inset, SIZE - inset
    for x, y, dx, dy in ((lo, lo, 1, 1), (hi, lo, -1, 1), (lo, hi, 1, -1), (hi, hi, -1, -1)):
        p.drawPolyline([QPointF(x, y + dy * arm), QPointF(x, y), QPointF(x + dx * arm, y)])

    # the buffalo_l mascot inside the frame
    p.save()
    p.translate(512, 540)
    p.scale(BUFFALO_IN_FRAME, BUFFALO_IN_FRAME)
    p.translate(-512, -550)
    paint_buffalo(p)
    p.restore()
    p.end()
    return img


def draw_buffalo() -> QImage:
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_buffalo(p)
    p.end()
    return img


def paint_buffalo(p: QPainter) -> None:
    """Front-facing buffalo head on the 1024 grid: light hooked horns, shaggy mane and beard, amber face."""
    horn = QPen(QColor("#e7ece8"), 78, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(horn)
    for side in (-1, 1):
        path = QPainterPath(QPointF(512 + side * 150, 340))
        path.cubicTo(QPointF(512 + side * 340, 470), QPointF(512 + side * 470, 380), QPointF(512 + side * 400, 180))
        p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(AMBER))
    for side in (-1, 1):  # ears
        p.save()
        p.translate(512 + side * 255, 420)
        p.rotate(side * -20)
        p.drawEllipse(QPointF(0, 0), 95, 48)
        p.restore()
    head = QPainterPath(QPointF(330, 300))
    head.cubicTo(QPointF(420, 250), QPointF(604, 250), QPointF(694, 300))
    head.cubicTo(QPointF(700, 520), QPointF(660, 700), QPointF(620, 800))
    head.cubicTo(QPointF(590, 880), QPointF(434, 880), QPointF(404, 800))
    head.cubicTo(QPointF(364, 700), QPointF(324, 520), QPointF(330, 300))
    p.drawPath(head)
    p.setBrush(QColor("#7a4a12"))  # mane over the forehead, beard under the chin
    for x, y, r in ((400, 290, 80), (470, 250, 88), (554, 250, 88), (624, 290, 80), (512, 320, 90),
                    (470, 880, 52), (554, 880, 52), (512, 905, 58)):
        p.drawEllipse(QPointF(x, y), r, r)
    p.setBrush(QColor("#a96a19"))  # muzzle
    p.drawEllipse(QPointF(512, 760), 118, 92)
    p.setBrush(QColor("#0c0f0e"))
    for side in (-1, 1):
        p.drawEllipse(QPointF(512 + side * 92, 480), 30, 36)  # eyes
        p.drawEllipse(QPointF(512 + side * 48, 770), 20, 28)  # nostrils


def main() -> None:
    QGuiApplication(sys.argv)
    master = draw()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Vaquero.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                master.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(
                    str(iconset / name)
                )
        OUT.parent.mkdir(exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)], check=True)
    master.scaled(PNG_SIZE, PNG_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation).save(
        str(PNG)
    )
    draw_buffalo().scaled(
        BUFFALO_SIZE, BUFFALO_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    ).save(str(BUFFALO))
    print(f"wrote {OUT}, {PNG} and {BUFFALO}")


if __name__ == "__main__":
    main()
