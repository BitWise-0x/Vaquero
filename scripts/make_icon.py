"""Draws the FaceSort app icon: assets/FaceSort.icns for the bundle, assets/FaceSort.png for the window header.

Run: uv run python scripts/make_icon.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

SIZE = 1024
OUT = Path(__file__).resolve().parent.parent / "assets" / "FaceSort.icns"
PNG = OUT.with_suffix(".png")
PNG_SIZE = 256


def draw() -> QImage:
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # macOS icon grid: 824 px body centred on 1024, corner radius about 22.5% of the body
    body = QRectF(100, 100, 824, 824)
    bg = QLinearGradient(body.topLeft(), body.bottomRight())
    bg.setColorAt(0, QColor("#1b2a4a"))
    bg.setColorAt(1, QColor("#0e5a6b"))
    shape = QPainterPath()
    shape.addRoundedRect(body, 185, 185)
    p.fillPath(shape, QBrush(bg))

    # viewfinder brackets
    pen = QPen(QColor("#ffffff"), 44, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    inset, arm = 230, 130
    lo, hi = inset, SIZE - inset
    for x, y, dx, dy in ((lo, lo, 1, 1), (hi, lo, -1, 1), (lo, hi, 1, -1), (hi, hi, -1, -1)):
        p.drawPolyline([QPointF(x, y + dy * arm), QPointF(x, y), QPointF(x + dx * arm, y)])

    # face: head and shoulders, clipped to the frame
    face = QLinearGradient(QPointF(512, 330), QPointF(512, 790))
    face.setColorAt(0, QColor("#5eead4"))
    face.setColorAt(1, QColor("#22d3ee"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(face))
    p.drawEllipse(QPointF(512, 450), 120, 132)
    shoulders = QPainterPath()
    shoulders.moveTo(322, 790)
    shoulders.cubicTo(330, 650, 420, 610, 512, 610)
    shoulders.cubicTo(604, 610, 694, 650, 702, 790)
    shoulders.closeSubpath()
    p.drawPath(shoulders)

    # match badge
    p.setBrush(QBrush(QColor("#30a46c")))
    p.setPen(QPen(QColor("#0e3a46"), 18))
    p.drawEllipse(QPointF(735, 735), 92, 92)
    p.setPen(QPen(QColor("#ffffff"), 30, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPolyline([QPointF(692, 738), QPointF(724, 770), QPointF(782, 704)])
    p.end()
    return img


def main() -> None:
    QGuiApplication(sys.argv)
    master = draw()
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "FaceSort.iconset"
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
    print(f"wrote {OUT} and {PNG}")


if __name__ == "__main__":
    main()
