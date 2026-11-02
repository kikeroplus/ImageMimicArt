"""アプリアイコンの描画。ウィンドウ用アイコンと EXE 用 .ico の両方で共有する。

元画像 (後ろの写真) から、似た絵 (前の写真) を作る様子: 2 枚の写真と、きらめきの印。
"""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _photo(painter: QPainter, rect: QRectF, s: float, sky: str, ground: str, sun: str):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ffffff"))
    painter.drawRoundedRect(rect, s * 0.04, s * 0.04)
    inner = rect.adjusted(s * 0.035, s * 0.035, -s * 0.035, -s * 0.035)
    painter.save()
    clip = QPainterPath()
    clip.addRect(inner)
    painter.setClipPath(clip)
    painter.fillRect(inner, QColor(sky))
    painter.setBrush(QColor(sun))
    r = inner.width() * 0.13
    painter.drawEllipse(QPointF(inner.right() - inner.width() * 0.25, inner.top() + inner.height() * 0.28), r, r)
    hill = QPainterPath()
    hill.moveTo(inner.left(), inner.bottom())
    hill.lineTo(inner.left(), inner.top() + inner.height() * 0.72)
    hill.lineTo(inner.left() + inner.width() * 0.38, inner.top() + inner.height() * 0.42)
    hill.lineTo(inner.left() + inner.width() * 0.62, inner.top() + inner.height() * 0.66)
    hill.lineTo(inner.left() + inner.width() * 0.78, inner.top() + inner.height() * 0.54)
    hill.lineTo(inner.right(), inner.top() + inner.height() * 0.70)
    hill.lineTo(inner.right(), inner.bottom())
    hill.closeSubpath()
    painter.setBrush(QColor(ground))
    painter.drawPath(hill)
    painter.restore()


def _sparkle(painter: QPainter, center: QPointF, radius: float, color: str):
    path = QPainterPath()
    thin = radius * 0.28
    path.moveTo(center.x(), center.y() - radius)
    path.quadTo(center.x() + thin * 0.3, center.y() - thin * 0.3, center.x() + radius, center.y())
    path.quadTo(center.x() + thin * 0.3, center.y() + thin * 0.3, center.x(), center.y() + radius)
    path.quadTo(center.x() - thin * 0.3, center.y() + thin * 0.3, center.x() - radius, center.y())
    path.quadTo(center.x() - thin * 0.3, center.y() - thin * 0.3, center.x(), center.y() - radius)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawPath(path)


def draw_icon(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)

    background = QLinearGradient(QPointF(0, 0), QPointF(0, s))
    background.setColorAt(0.0, QColor("#3a3a3a"))
    background.setColorAt(1.0, QColor("#161616"))
    painter.setPen(QPen(QColor("#E0A000"), max(1.0, s * 0.03)))
    painter.setBrush(QBrush(background))
    painter.drawRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), s * 0.2, s * 0.2)

    # 後ろの写真 (元画像): 少し傾けて、落ち着いた色
    painter.save()
    painter.translate(s * 0.40, s * 0.42)
    painter.rotate(-10)
    _photo(painter, QRectF(-s * 0.25, -s * 0.20, s * 0.48, s * 0.38), s, "#8aa0b4", "#5d7363", "#d9d2b0")
    painter.restore()

    # 前の写真 (生成した絵): 明るい色
    _photo(painter, QRectF(s * 0.34, s * 0.42, s * 0.50, s * 0.40), s, "#4fa3e0", "#3f9a58", "#ffd54f")

    # きらめき
    _sparkle(painter, QPointF(s * 0.78, s * 0.24), s * 0.13, "#FFE84D")
    if size >= 32:
        _sparkle(painter, QPointF(s * 0.63, s * 0.16), s * 0.06, "#FFE84D")
    painter.end()
    return image


def make_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(draw_icon(size)))
    return icon
