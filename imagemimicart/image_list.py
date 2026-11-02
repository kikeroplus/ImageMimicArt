"""元画像の一覧 (ui-standard の 4・5 章)。

ImageModel: フォルダの画像・サムネール・印 (読み取り済み / 生成結果の数) を持つ。
ThumbView: メインの横一列とグリッド一覧の両方で使う一覧。モデルと選択 (QItemSelectionModel) を共有するので、
選択と表示中 (= current) は両方で自然にそろう。
選択中は細い黄枠、表示中は太く明るい黄枠。読み取り済みは左上に「txt」、生成結果があれば右上に数を出す。
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QApplication, QListView, QStyledItemDelegate

from . import results
from .folder_tree import natural_key
from .results_view import ThumbnailLoader
from .theme import ACCENT, ACCENT_LIGHT

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".jpe", ".webp", ".bmp"}
BASE_THUMB = 160  # 一覧用に作るサムネールの大きさ (グリッドの「小」と同じ)
SORT_MODES = ("名前 (昇順)", "名前 (降順)", "更新日時 (古い順)", "更新日時 (新しい順)")
PATH_ROLE = Qt.ItemDataRole.UserRole
CAPTION_COLOR = QColor("#2E7D6B")
COUNT_COLOR = QColor("#3C5A9A")


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


@dataclass
class Entry:
    path: Path
    mtime: float
    has_caption: bool = False
    count: int = 0  # 生成結果の数


class ImageModel(QAbstractListModel):
    """開いているフォルダの画像。"""

    loaded = Signal(str)  # フォルダを読み終わった (エラーの説明。うまくいけば空)
    thumbs_progress = Signal(int, int)  # 作ったサムネールの数, 全体
    _folder_done = Signal(int, object, object, str)  # 依頼番号, entries, 相手のいない .txt, エラー
    _counts_done = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder: Optional[Path] = None
        self.entries: list[Entry] = []
        self.orphans: list[Path] = []  # 同じ名前の画像がない .txt (説明だけ残っているもの)
        self.sort_mode = 0
        self.loading = False
        self._ticket = 0
        self._thumbs: dict[str, QImage] = {}
        self._large: dict[str, QImage] = {}  # グリッドの中・大に合わせたサムネール
        self._large_size = 0
        self._thumb_done = 0
        self._loader = ThumbnailLoader(BASE_THUMB)
        self._loader.loaded.connect(self._on_thumb, Qt.ConnectionType.QueuedConnection)
        self._large_loader: Optional[ThumbnailLoader] = None
        self._thumb_ticket = 0
        self._large_ticket = 0
        self._folder_done.connect(self._on_folder_done, Qt.ConnectionType.QueuedConnection)
        self._counts_done.connect(self._on_counts_done, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------------
    # モデル
    # ------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.entries):
            return None
        entry = self.entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.path.name
        if role == PATH_ROLE:
            return str(entry.path)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [entry.path.name]
            lines.append("説明プロンプトあり (読み取り済み)" if entry.has_caption else "まだ読み取っていません")
            if entry.count:
                lines.append(f"生成結果 {entry.count} 件")
            return "\n".join(lines)
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def thumbnail(self, row: int, size: int) -> Optional[QImage]:
        key = str(self.entries[row].path)
        if size > BASE_THUMB and key in self._large:
            return self._large[key]
        return self._thumbs.get(key)

    def row_of(self, path) -> int:
        target = results._key(path)
        for i, entry in enumerate(self.entries):
            if results._key(entry.path) == target:
                return i
        return -1

    def paths(self) -> list[Path]:
        return [e.path for e in self.entries]

    # ------------------------------------------------------------------
    # フォルダを読む
    # ------------------------------------------------------------------
    def open_folder(self, folder: Path):
        """裏で読む。読み終わったら loaded。途中で別のフォルダを開いたら前の読み込みは捨てる。"""
        self._ticket += 1
        ticket = self._ticket
        self.beginResetModel()
        self.folder = folder
        self.entries = []
        self.orphans = []
        self.endResetModel()
        self.loading = True
        self._thumb_ticket = self._loader.request([])
        self.drop_large()

        def work():
            entries, orphans, error = [], [], ""
            try:
                txt: dict[str, Path] = {}
                with os.scandir(folder) as it:
                    for item in it:
                        try:
                            if not item.is_file():
                                continue
                        except OSError:
                            continue
                        path = Path(item.path)
                        suffix = path.suffix.lower()
                        if suffix == ".txt":
                            txt[path.stem.lower()] = path
                        elif suffix in IMAGE_EXTS:
                            try:
                                mtime = item.stat().st_mtime
                            except OSError:
                                mtime = 0.0
                            entries.append(Entry(path, mtime))
                for entry in entries:
                    entry.has_caption = entry.path.stem.lower() in txt
                stems = {entry.path.stem.lower() for entry in entries}
                orphans = sorted((p for stem, p in txt.items() if stem not in stems), key=lambda p: p.name.lower())
            except OSError as exc:
                error = str(exc)
            self._folder_done.emit(ticket, entries, orphans, error)

        threading.Thread(target=work, daemon=True).start()

    def _on_folder_done(self, ticket: int, entries: list, orphans: list, error: str):
        if ticket != self._ticket:
            return
        self.beginResetModel()
        self.entries = entries
        self.orphans = orphans
        self._sort_entries()
        self.endResetModel()
        self.loading = False
        self.loaded.emit(error)
        self._request_thumbs()
        self._request_counts()

    def _request_thumbs(self):
        pending = [str(e.path) for e in self.entries if str(e.path) not in self._thumbs]
        self._thumb_done = len(self.entries) - len(pending)
        self._thumb_ticket = self._loader.request(pending)
        self.thumbs_progress.emit(self._thumb_done, len(self.entries))

    def _on_thumb(self, ticket: int, path: str, image: QImage):
        self._thumbs[path] = image
        if ticket != self._thumb_ticket:
            return
        self._thumb_done += 1
        self.thumbs_progress.emit(self._thumb_done, len(self.entries))
        row = self.row_of(path)
        if row >= 0:
            index = self.index(row)
            self.dataChanged.emit(index, index)

    def _request_counts(self):
        ticket = self._ticket
        paths = self.paths()

        def work():
            try:
                counts = results.result_counts(paths)
            except Exception:  # noqa: BLE001 - 数えられなければ印を出さないだけ
                counts = {}
            self._counts_done.emit(ticket, counts)

        threading.Thread(target=work, daemon=True).start()

    def _on_counts_done(self, ticket: int, counts: dict):
        if ticket != self._ticket:
            return
        for entry in self.entries:
            entry.count = counts.get(results._key(entry.path), 0)
        if self.entries:
            self.dataChanged.emit(self.index(0), self.index(len(self.entries) - 1))

    # ------------------------------------------------------------------
    # 並び順・更新
    # ------------------------------------------------------------------
    def _sort_entries(self):
        mode = self.sort_mode
        if mode in (2, 3):
            self.entries.sort(key=lambda e: (e.mtime, natural_key(e.path.name)), reverse=mode == 3)
        else:
            self.entries.sort(key=lambda e: natural_key(e.path.name), reverse=mode == 1)

    def set_sort(self, mode: int):
        """並べ替える。表示中と選択は呼び出し側が持ち直す (layoutChanged で永続インデックスは付いていく)。"""
        self.sort_mode = mode
        self.layoutAboutToBeChanged.emit()
        old = [e.path for e in self.entries]
        self._sort_entries()
        mapping = {results._key(p): i for i, p in enumerate(e.path for e in self.entries)}
        persistent = self.persistentIndexList()
        new = [self.index(mapping[results._key(old[i.row()])]) if i.row() < len(old) else QModelIndex()
               for i in persistent]
        self.changePersistentIndexList(persistent, new)
        self.layoutChanged.emit()

    def set_caption_state(self, path, has_caption: bool):
        row = self.row_of(path)
        if row >= 0 and self.entries[row].has_caption != has_caption:
            self.entries[row].has_caption = has_caption
            self.dataChanged.emit(self.index(row), self.index(row))

    def set_count(self, path, count: int):
        row = self.row_of(path)
        if row >= 0 and self.entries[row].count != count:
            self.entries[row].count = count
            self.dataChanged.emit(self.index(row), self.index(row))

    def remove_files(self, paths: list[Path]):
        keys = {results._key(p) for p in paths}
        for row in reversed(range(len(self.entries))):
            if results._key(self.entries[row].path) in keys:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self.entries[row]
                self.endRemoveRows()
        # 消した画像の .txt は、同じ名前の画像がもうなければ「説明だけ残っているもの」になる
        stems = {e.path.stem.lower() for e in self.entries}
        known = {results._key(p) for p in self.orphans}
        for path in paths:
            caption = path.with_suffix(".txt")
            if path.stem.lower() not in stems and results._key(caption) not in known and caption.is_file():
                self.orphans.append(caption)
                known.add(results._key(caption))

    # ------------------------------------------------------------------
    # グリッドの中・大のサムネール (表示中の順に作る。小に戻すか閉じたら捨てる)
    # ------------------------------------------------------------------
    def request_large(self, size: int, first_row: int = 0):
        if size <= BASE_THUMB:
            self.drop_large()
            return
        if size != self._large_size:
            self._large.clear()
            self._large_size = size
            if self._large_loader is not None:
                self._large_loader.request([])
            self._large_loader = ThumbnailLoader(size)
            self._large_loader.loaded.connect(self._on_large, Qt.ConnectionType.QueuedConnection)
        order = self.entries[first_row:] + self.entries[:first_row]
        pending = [str(e.path) for e in order if str(e.path) not in self._large]
        self._large_ticket = self._large_loader.request(pending)

    def drop_large(self):
        self._large.clear()
        self._large_size = 0
        if self._large_loader is not None:
            self._large_loader.request([])

    def _on_large(self, ticket: int, path: str, image: QImage):
        if ticket != self._large_ticket or not self._large_size:
            return
        self._large[path] = image
        row = self.row_of(path)
        if row >= 0:
            self.dataChanged.emit(self.index(row), self.index(row))


# ----------------------------------------------------------------------
# 描画
# ----------------------------------------------------------------------
class ThumbDelegate(QStyledItemDelegate):
    """サムネール・名前・印・枠を描く。"""

    NAME_HEIGHT = 18
    PAD = 6

    def __init__(self, model: ImageModel, tile: int, parent=None):
        super().__init__(parent)
        self.model = model
        self.tile = tile  # サムネールの幅 (枠と名前を除く)
        self.current_row = -1

    def cell_size(self) -> QSize:
        return QSize(self.tile + self.PAD * 2, self.tile + self.NAME_HEIGHT + self.PAD * 2)

    def sizeHint(self, option, index) -> QSize:
        return self.cell_size()

    def paint(self, painter: QPainter, option, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cell = option.rect
        row = index.row()
        view = option.widget
        selected = view is not None and view.selectionModel().isSelected(index)
        current = view is not None and view.selectionModel().currentIndex() == index
        area = QRect(cell.left() + self.PAD, cell.top() + self.PAD, self.tile, self.tile)

        image = self.model.thumbnail(row, self.tile)
        if image is None or image.isNull():
            painter.fillRect(area.adjusted(8, 8, -8, -8), QColor("#2A2A2A"))
        else:
            size = image.size().scaled(area.size(), Qt.AspectRatioMode.KeepAspectRatio)
            if size.width() > image.width() and max(image.width(), image.height()) < BASE_THUMB:
                size = image.size()  # 元画像が小さいもの (サムネールが小さい) は引き伸ばさない
            target = QRect(0, 0, size.width(), size.height())
            target.moveCenter(area.center())
            painter.drawImage(target, image)

        entry = self.model.entries[row]
        font = QFont(painter.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() * 0.85))
        painter.setFont(font)
        if entry.has_caption:
            badge = QRect(area.left() + 3, area.top() + 3, 26, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(CAPTION_COLOR)
            painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "txt")
        if entry.count:
            text = str(entry.count)
            width = max(18, painter.fontMetrics().horizontalAdvance(text) + 10)
            badge = QRect(area.right() - width - 2, area.top() + 3, width, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(COUNT_COLOR)
            painter.drawRoundedRect(badge, 8, 8)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

        name_rect = QRect(cell.left() + 2, area.bottom() + 3, cell.width() - 4, self.NAME_HEIGHT)
        painter.setPen(QColor("#CCCCCC"))
        name = painter.fontMetrics().elidedText(entry.path.name, Qt.TextElideMode.ElideMiddle, name_rect.width())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, name)

        # 枠: 表示中は太く明るい黄 (太くした分は内側へ)、選択中は細い黄
        frame = cell.adjusted(1, 1, -1, -1)
        if current:
            pen = QPen(QColor(ACCENT_LIGHT), 4)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(frame.adjusted(2, 2, -2, -2))
        elif selected:
            pen = QPen(QColor(ACCENT), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(frame.adjusted(1, 1, -1, -1))
        painter.restore()


# ----------------------------------------------------------------------
# 一覧
# ----------------------------------------------------------------------
class ThumbView(QListView):
    """メインの横一列 (grid=False) とグリッド一覧 (grid=True) で使う一覧。

    マウスの選択は ui-standard の 4.2: 選択していないものをクリック → それだけを選んで表示、
    選択中のものをクリック → 選択はそのままで表示だけ切り替え、Ctrl / Shift で複数選択。
    選択中のものをドラッグすると、選択中のすべてをファイルとして渡す (コピーのみ)。
    """

    activated_item = Signal(int)  # ダブルクリック (グリッド: メインを手前へ)
    context_requested = Signal(QPoint)  # 画面上の位置
    tile_wheel = Signal(int)  # Ctrl+ホイール (グリッド: タイルの大きさ)

    def __init__(self, model: ImageModel, grid: bool, tile: int, parent=None):
        super().__init__(parent)
        self.grid = grid
        self.setModel(model)
        self.delegate = ThumbDelegate(model, tile, self)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)  # 選択は自分で扱う
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setMouseTracking(False)
        if grid:
            self.setFlow(QListView.Flow.LeftToRight)
            self.setWrapping(True)
            self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        else:
            self.setFlow(QListView.Flow.LeftToRight)
            self.setWrapping(False)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.set_tile(tile)
        self._press_pos: Optional[QPoint] = None
        self._press_index = QModelIndex()

    def set_tile(self, tile: int):
        self.delegate.tile = tile
        size = self.delegate.cell_size()
        self.setGridSize(size)
        if not self.grid:
            self.setFixedHeight(size.height() + self.horizontalScrollBar().sizeHint().height() + 4)
        self.doItemsLayout()

    # --- 選択 ---------------------------------------------------------
    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        sel = self.selectionModel()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        self.setFocus()
        if not index.isValid():
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if not sel.isSelected(index):
                sel.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if ctrl:
            if sel.isSelected(index):
                if len(sel.selectedIndexes()) <= 1:
                    return  # 最後の 1 つは外さない (何も表示されない状態にしない)
                sel.select(index, QItemSelectionModel.SelectionFlag.Deselect)
                if sel.currentIndex() == index:
                    rest = sorted(sel.selectedIndexes(), key=lambda i: abs(i.row() - index.row()))
                    sel.setCurrentIndex(rest[0], QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                sel.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.Select)
        elif shift and sel.currentIndex().isValid():
            anchor = sel.currentIndex().row()
            low, high = sorted((anchor, index.row()))
            model = self.model()
            selection = QItemSelection(model.index(low), model.index(high))
            sel.select(selection, QItemSelectionModel.SelectionFlag.Select)
            sel.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
        elif sel.isSelected(index):
            sel.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)  # 表示だけ切り替える
        else:
            sel.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self._press_pos = event.position().toPoint()
        self._press_index = index
        self.viewport().update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            return
        self._press_pos = None
        sel = self.selectionModel()
        if not self._press_index.isValid() or not sel.isSelected(self._press_index):
            return
        rows = sorted(i.row() for i in sel.selectedIndexes())
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(self.model().data(self.model().index(r), PATH_ROLE)) for r in rows])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)  # 移動させると一覧と食い違うのでコピーのみ

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and event.button() == Qt.MouseButton.LeftButton:
            self.activated_item.emit(index.row())
        event.accept()

    def contextMenuEvent(self, event):
        if self.indexAt(event.pos()).isValid():
            self.context_requested.emit(event.globalPos())

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if self.grid:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if delta:
                    self.tile_wheel.emit(1 if delta > 0 else -1)
                event.accept()
                return
            super().wheelEvent(event)
            return
        bar = self.horizontalScrollBar()  # 横一列: ホイールで横スクロール
        bar.setValue(bar.value() - delta)
        event.accept()

    def keyPressEvent(self, event):
        event.ignore()  # キーはウインドウ全体で受ける (メインウインドウが扱う)

    def columns(self) -> int:
        """グリッドの 1 行に並ぶ数 (↑ ↓ で動く数)。"""
        cell = self.gridSize().width()
        return max(1, self.viewport().width() // cell) if cell > 0 else 1
