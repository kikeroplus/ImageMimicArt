"""中央のメイン表示 (ui-standard の 1・11 章)。

選んだ元画像を画面に合わせて大きく出す。Ctrl+ホイールでマウスの位置を中心に拡大・縮小 (1 段 ×1.25、最大 ×16)、
拡大中はドラッグで移動、ダブルクリックで全体表示に戻す。普段は画面に合わせた大きさで読み込み、
拡大したときに元の解像度で読み直す。ホイールは前へ / 次へ。前後の画像は先読みする。
生成結果を選ぶと、左右に分けて元画像と並べて見せる (並べている間は拡大しない)。
"""

import queue
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QImageReader, QPainter
from PySide6.QtWidgets import QWidget

from .theme import ACCENT_LIGHT, MAIN_VIEW

ZOOM_STEP = 1.25
MAX_ZOOM = 16.0
CACHE_LIMIT = 10
GAP = 10  # 並べて見せるときの間


class _Loaded:
    def __init__(self, image: QImage, original: QSize, full: bool):
        self.image = image
        self.original = original
        self.full = full


class ImageLoader(QObject):
    """裏のスレッドで画像を読む。今見たいもの → 先読みの順。読んだものは少しだけ覚えておく。"""

    loaded = Signal(str)  # パス (cache に入った)

    def __init__(self):
        super().__init__()
        self.cache: "OrderedDict[str, _Loaded]" = OrderedDict()
        self._lock = threading.Lock()
        self._queue: "queue.Queue[int]" = queue.Queue()
        self._wanted: list[tuple[str, bool]] = []  # (パス, 元の解像度で)
        screens = QGuiApplication.screens()
        self.fit = max((int(max(s.size().width(), s.size().height()) * s.devicePixelRatio()) for s in screens),
                       default=2560)
        threading.Thread(target=self._run, daemon=True).start()

    def get(self, path: str) -> Optional[_Loaded]:
        with self._lock:
            item = self.cache.get(path)
            if item is not None:
                self.cache.move_to_end(path)
            return item

    def request(self, paths: list[tuple[str, bool]]):
        """paths を前から順に読む (前の依頼は捨てる)。"""
        with self._lock:
            self._wanted = [p for p in paths if not self._has(p[0], p[1])]
        self._queue.put(1)

    def forget(self, path: str):
        with self._lock:
            self.cache.pop(path, None)

    def _has(self, path: str, full: bool) -> bool:
        item = self.cache.get(path)
        return item is not None and (item.full or not full)

    def _run(self):
        while True:
            self._queue.get()
            while True:
                with self._lock:
                    if not self._wanted:
                        break
                    path, full = self._wanted.pop(0)
                    if self._has(path, full):
                        continue
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                original = reader.size()
                if not full and original.isValid() and max(original.width(), original.height()) > self.fit:
                    reader.setScaledSize(original.scaled(self.fit, self.fit, Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if image.isNull():
                    continue
                with self._lock:
                    self.cache[path] = _Loaded(image, self._oriented(original, image), full)
                    self.cache.move_to_end(path)
                    while len(self.cache) > CACHE_LIMIT:
                        self.cache.popitem(last=False)
                try:
                    self.loaded.emit(path)
                except RuntimeError:
                    return

    @staticmethod
    def _oriented(original: QSize, image: QImage) -> QSize:
        """回転 (EXIF) を反映した元の大きさ。読んだ画像の縦横の向きに合わせる。"""
        if not original.isValid():
            return image.size()
        if (image.width() > image.height()) != (original.width() > original.height()) and image.width() != image.height():
            return original.transposed()
        return original


class MainView(QWidget):
    step_requested = Signal(int)  # ホイールで前 (-1) / 次 (+1)
    context_requested = Signal(QPoint)
    zoom_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(200, 150)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.loader = ImageLoader()
        self.loader.loaded.connect(self._on_loaded, Qt.ConnectionType.QueuedConnection)
        self.path: Optional[str] = None
        self.compare_path: Optional[str] = None
        self.message = ""
        self.info = ""
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self._drag: Optional[QPointF] = None
        self._preload: list[str] = []

    # ------------------------------------------------------------------
    # 表示するもの
    # ------------------------------------------------------------------
    def show_image(self, path: Optional[str], preload: Optional[list[str]] = None, message: str = ""):
        """path を表示する (None なら message を中央に出す)。別のファイルに移ったら拡大は戻す。"""
        if path != self.path:
            self.zoom = 1.0
            self.offset = QPointF(0, 0)
            self.compare_path = None
        self.path = path
        self.message = message
        self._preload = preload or []
        self._request()
        self.update()
        self.zoom_changed.emit()

    def set_compare(self, path: Optional[str]):
        """生成結果を元画像と並べて見せる (None ならやめる)。"""
        self.compare_path = path or None
        if self.compare_path:
            self.zoom = 1.0
            self.offset = QPointF(0, 0)
        self._request()
        self.update()
        self.zoom_changed.emit()

    def set_info(self, text: str):
        self.info = text
        self.update()

    def refresh_file(self, path: str):
        """ファイルが書き換わった・消えたので読み直す。"""
        self.loader.forget(path)
        self._request()
        self.update()

    def _request(self):
        wanted = []
        if self.path:
            wanted.append((self.path, False))
            if self.zoom > 1.0 and not self.compare_path:
                wanted.append((self.path, True))
        if self.compare_path:
            wanted.append((self.compare_path, False))
        wanted.extend((p, False) for p in self._preload)
        self.loader.request(wanted)

    def _on_loaded(self, path: str):
        if path in (self.path, self.compare_path):
            self.update()
            if path == self.path:
                self.zoom_changed.emit()

    def original_size(self) -> Optional[QSize]:
        item = self.loader.get(self.path) if self.path else None
        return item.original if item is not None else None

    # ------------------------------------------------------------------
    # 描画
    # ------------------------------------------------------------------
    def _fit_rect(self, size: QSize, area: QRectF) -> QRectF:
        if size.width() <= 0 or size.height() <= 0:
            return QRectF()
        scale = min(area.width() / size.width(), area.height() / size.height())
        w, h = size.width() * scale, size.height() * scale
        return QRectF(area.center().x() - w / 2, area.center().y() - h / 2, w, h)

    def _display_rect(self, item: _Loaded) -> QRectF:
        fit = self._fit_rect(item.original, QRectF(self.rect()))
        w, h = fit.width() * self.zoom, fit.height() * self.zoom
        center = QPointF(self.width() / 2, self.height() / 2) + self.offset
        return QRectF(center.x() - w / 2, center.y() - h / 2, w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(MAIN_VIEW))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.path is None:
            painter.setPen(QColor("#999999"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            painter.end()
            return
        if self.compare_path:
            half = (self.width() - GAP) / 2
            left = QRectF(0, 26, half, self.height() - 26)
            right = QRectF(half + GAP, 26, half, self.height() - 26)
            painter.fillRect(QRectF(half, 0, GAP, self.height()), QColor("#000000"))
            self._draw_fit(painter, self.path, left, "元画像")
            self._draw_fit(painter, self.compare_path, right, "生成結果")
        else:
            item = self.loader.get(self.path)
            if item is None:
                painter.setPen(QColor("#999999"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "読み込み中...")
            else:
                painter.drawImage(self._display_rect(item), item.image)
        self._draw_info(painter)
        painter.end()

    def _draw_fit(self, painter: QPainter, path: str, area: QRectF, caption: str):
        item = self.loader.get(path)
        label = f"{caption}  {Path(path).name}"
        if item is not None:
            label += f"  {item.original.width()} × {item.original.height()}"
        font = QFont(painter.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(ACCENT_LIGHT) if caption == "生成結果" else QColor("#DDDDDD"))
        text_rect = QRect(int(area.left()) + 8, 4, int(area.width()) - 16, 20)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideMiddle, text_rect.width()))
        painter.setFont(QFont())
        if item is None:
            painter.setPen(QColor("#999999"))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "読み込み中...")
            return
        painter.drawImage(self._fit_rect(item.original, area.adjusted(4, 4, -4, -4)), item.image)

    def _draw_info(self, painter: QPainter):
        if not self.info:
            return
        metrics = painter.fontMetrics()
        width = min(metrics.horizontalAdvance(self.info) + 20, self.width() - 16)
        box = QRect(8, self.height() - metrics.height() - 18, width, metrics.height() + 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 150))
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(QColor("#EEEEEE"))
        painter.drawText(box.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(self.info, Qt.TextElideMode.ElideRight, box.width() - 20))

    # ------------------------------------------------------------------
    # マウス
    # ------------------------------------------------------------------
    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if not self.compare_path:
                self._zoom_at(event.position(), ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP)
        else:
            self.step_requested.emit(-1 if delta > 0 else 1)
        event.accept()

    def _zoom_at(self, pos: QPointF, factor: float):
        item = self.loader.get(self.path) if self.path else None
        if item is None:
            return
        new_zoom = min(max(self.zoom * factor, 1.0), MAX_ZOOM)
        if new_zoom == self.zoom:
            return
        # マウスの下の点が動かないように、中心からのずれを拡大率に合わせて動かす
        center = QPointF(self.width() / 2, self.height() / 2)
        ratio = new_zoom / self.zoom
        self.offset = (self.offset - (pos - center)) * ratio + (pos - center)
        self.zoom = new_zoom
        if self.zoom == 1.0:
            self.offset = QPointF(0, 0)
        self._clamp()
        self._request()  # 拡大したら元の解像度で読み直す
        self.update()
        self.zoom_changed.emit()

    def _clamp(self):
        item = self.loader.get(self.path) if self.path else None
        if item is None:
            return
        rect = self._display_rect(item)
        max_x = max(0.0, (rect.width() - self.width()) / 2)
        max_y = max(0.0, (rect.height() - self.height()) / 2)
        self.offset = QPointF(min(max(self.offset.x(), -max_x), max_x), min(max(self.offset.y(), -max_y), max_y))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.zoom > 1.0 and not self.compare_path:
            self._drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.offset += event.position() - self._drag
            self._drag = event.position()
            self._clamp()
            self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        if self.zoom != 1.0:
            self.zoom = 1.0
            self.offset = QPointF(0, 0)
            self.update()
            self.zoom_changed.emit()

    def contextMenuEvent(self, event):
        if self.path:
            self.context_requested.emit(event.globalPos())

    def resizeEvent(self, event):
        self._clamp()
        super().resizeEvent(event)
