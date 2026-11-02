"""右の枠の下の「生成結果」: 表示中の元画像から生成した画像のサムネールを横一列に並べる。

クリックで中央に元画像と並べて表示 (もう一度クリック / Esc / 空きをクリックで戻す。← → で切り替え)。
ダブルクリック / Enter で関連付けアプリで開く。右クリックで「生成時の設定」
「エクスプローラーで開く」「ごみ箱へ削除」。他のアプリへドラッグするとファイルとして渡す (コピーのみ)。
PromptWordCrafter の results_view.py を引き継いだもの (大きく見る小窓の代わりに、元画像と並べて見せる)。
"""

import os
import queue
import threading
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QItemSelectionModel, QMimeData, QObject, QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from . import results

THUMB_SIZE = 112
CELL_WIDTH = THUMB_SIZE + 14
CELL_HEIGHT = THUMB_SIZE + 30
PATH_ROLE = Qt.ItemDataRole.UserRole
PLACEHOLDER_COLOR = QColor("#333333")
OLD_COLOR = QColor("#8a6d00")
COMPARED_COLOR = QColor("#FFE84D")  # 並べて見せているもの (表示中の枠と同じ色)


class ElidedLabel(QLabel):
    """入りきらない文字を「…」で省く 1 行の見出し。長い文字でも枠の幅を押し広げない。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setText(text)

    def setText(self, text: str):
        self._full = text
        self._elide()

    def text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(0, super().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 0))
        super().setText(shown)


class ThumbnailLoader(QObject):
    """裏のスレッドで 1 枚ずつサムネールを作る。新しい依頼が来たら古い依頼は捨てる。"""

    loaded = Signal(int, str, QImage)  # 依頼番号, パス, 画像

    def __init__(self, size: int):
        super().__init__()
        self.size = size
        self._queue: "queue.Queue[tuple[int, list[str]]]" = queue.Queue()
        self._ticket = 0
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, paths: list[str]) -> int:
        self._ticket += 1
        self._queue.put((self._ticket, paths))
        return self._ticket

    def _run(self):
        results.init_thread_com()
        while True:
            ticket, paths = self._queue.get()
            for path in paths:
                if ticket != self._ticket:
                    break  # 別の画像に移った
                try:
                    image = results.make_thumbnail(Path(path), self.size)
                except Exception:  # noqa: BLE001 - 作れないものは灰色のまま
                    image = None
                if image is not None and not image.isNull():
                    try:
                        self.loaded.emit(ticket, path, image)
                    except RuntimeError:
                        return  # ウィンドウが閉じられた


class _ResultList(QListWidget):
    """横一列のサムネール。ホイールで横にスクロールし、ドラッグでファイルを渡す。"""

    open_requested = Signal()
    delete_requested = Signal()
    escape_pressed = Signal()
    clicked = Signal(object)  # 修飾キーなしの左クリック (ドラッグは除く) をした項目 (空きなら None)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.setGridSize(QSize(CELL_WIDTH, CELL_HEIGHT))
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setFixedHeight(CELL_HEIGHT + self.horizontalScrollBar().sizeHint().height() + 6)
        self._press: Optional[QPoint] = None
        self.compared_at_press: Optional[str] = None  # 押したときに並べて見せていたもの
        self.compared: Optional[str] = None  # 今並べて見せているもの (ResultsStrip が入れる)

    def mousePressEvent(self, event):
        plain = not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))
        left = event.button() == Qt.MouseButton.LeftButton
        self._press = event.position().toPoint() if left and plain else None
        self.compared_at_press = self.compared
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        press, self._press = self._press, None
        if press is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if (event.position().toPoint() - press).manhattanLength() >= QApplication.startDragDistance():
            return
        self.clicked.emit(self.itemAt(press))

    def mouseDoubleClickEvent(self, event):
        self._press = None  # ダブルクリックの 2 回目の離しでは並べ直さない
        super().mouseDoubleClickEvent(event)

    def supportedDragActions(self) -> Qt.DropAction:
        return Qt.DropAction.CopyAction  # 移動させると一覧と食い違うのでコピーのみ

    def mimeData(self, items):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(item.data(PATH_ROLE)) for item in items])
        return mime

    def wheelEvent(self, event):
        bar = self.horizontalScrollBar()
        delta = event.angleDelta().y() or event.angleDelta().x()
        bar.setValue(bar.value() - delta)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.open_requested.emit()
            return
        if event.key() == Qt.Key.Key_Delete:
            if not event.isAutoRepeat():  # 押しっぱなしで続けて消えないように
                self.delete_requested.emit()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            return
        super().keyPressEvent(event)


class ResultsStrip(QWidget):
    """表示中の元画像から生成したファイルの一覧。"""

    status = Signal(str)
    info_requested = Signal(object)  # そのファイルを作ったときの記録 (comfy.GenRecord)
    compare_requested = Signal(str)  # 元画像と並べて見せる生成結果 (空なら並べるのをやめる)
    changed = Signal()  # 一覧の中身が変わった (消したなど。サムネール一覧の印を直すため)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: Optional[Path] = None
        self._caption: Optional[str] = None
        self._ticket = 0
        self._images: dict[str, QImage] = {}  # パスと更新日時ごとのサムネール
        self._items: dict[str, QListWidgetItem] = {}
        self._records: dict = {}  # パス → 生成の記録 (記録のないものは入れない)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(2)
        self.header = ElidedLabel()
        layout.addWidget(self.header)
        self.list = _ResultList(self)
        layout.addWidget(self.list)

        self._loader = ThumbnailLoader(THUMB_SIZE)
        self._loader.loaded.connect(self._on_loaded, Qt.ConnectionType.QueuedConnection)
        self._rebuilding = False
        self.list.clicked.connect(self._toggle_compare)
        self.list.currentItemChanged.connect(self._follow_current)
        self.list.itemDoubleClicked.connect(self._on_double_clicked)
        self.list.open_requested.connect(self._open_current)
        self.list.delete_requested.connect(self.delete_selected)
        self.list.escape_pressed.connect(lambda: self.set_compared(None, notify=True))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.clear()

    @property
    def compared(self) -> Optional[str]:
        return self.list.compared

    # ------------------------------------------------------------------
    # 表示
    # ------------------------------------------------------------------
    def clear(self):
        self.set_compared(None)
        self.source = None
        self._caption = None
        self._ticket = self._loader.request([])
        self.list.clear()
        self._items.clear()
        self._records.clear()
        self.header.setText("生成結果")

    def show_for(self, source: Optional[Path], caption: Optional[str]):
        """その元画像の生成結果を出す。同じ画像なら選択とスクロール位置を残す。"""
        if source is None:
            self.clear()
            return
        same = self.source is not None and results.same_path(self.source, source)
        selected = {item.data(PATH_ROLE) for item in self.list.selectedItems()} if same else set()
        scroll = self.list.horizontalScrollBar().value() if same else 0
        if not same:
            self.set_compared(None)
        self.source = source
        self._caption = caption

        found = results.find_results(source, caption)
        self._rebuilding = True  # 作り直しの間に「表示中」が動いても並べ直さない
        self.list.clear()
        self._items.clear()
        self._records = {str(r.path): r.record for r in found if r.record is not None}
        pending = []
        for result in found:
            path = str(result.path)
            item = QListWidgetItem(result.path.name)
            item.setData(PATH_ROLE, path)
            item.setData(PATH_ROLE + 1, result.is_video)
            item.setData(PATH_ROLE + 2, result.old)
            item.setToolTip(self._tooltip(result))
            item.setSizeHint(QSize(CELL_WIDTH, CELL_HEIGHT))
            image = self._images.get(self._cache_key(result.path))
            item.setIcon(self._icon(image, result.is_video, result.old, path == self.compared))
            self.list.addItem(item)
            self._items[path] = item
            if path in selected:
                item.setSelected(True)
            if image is None:
                pending.append(path)
        compared = self._items.get(self.compared) if self.compared else None
        if compared is not None:
            self.list.setCurrentItem(compared, QItemSelectionModel.SelectionFlag.NoUpdate)
        elif self.compared:
            self.set_compared(None, notify=True)  # 並べていたものが消えた
        self._rebuilding = False
        self.list.horizontalScrollBar().setValue(scroll)

        old = sum(1 for r in found if r.old)
        if not found:
            self.header.setText("生成結果: この画像から生成したものはまだありません")
        else:
            note = f" (うち {old} 件は前の説明プロンプトで生成)" if old else ""
            self.header.setText(f"生成結果 ({len(found)} 件){note}  クリックで元画像と並べて表示")
        self._ticket = self._loader.request(pending)

    def refresh(self, caption: Optional[str] = None):
        if self.source is not None:
            self.show_for(self.source, self._caption if caption is None else caption)

    def count(self) -> int:
        return self.list.count()

    def _cache_key(self, path: Path) -> str:
        return f"{results._key(path)}|{results._mtime(path)}"

    def _tooltip(self, result: results.ResultFile) -> str:
        lines = [str(result.path)]
        if result.record is not None and result.record.time:
            request = result.record.request
            lines.append(f"生成した日時: {result.record.time}")
            lines.append(f"ワークフロー: {request.workflow_name}")
            if request.reader is not None:
                lines.append(f"読み取りプリセット: {request.reader_name or '(名前なし)'}")
            if request.enhancer is not None:
                lines.append(f"エンハンサー: {request.enhancer_name or '(名前なし)'}")
        elif result.record is None:
            lines.append("(生成の記録なし。ファイル名から見つけたもの)")
        if result.old:
            lines.append("※ 前の説明プロンプトで生成したもの (今の説明プロンプトとは違います)")
        lines.append("クリックで元画像と並べて表示 / ダブルクリックで開く / 右クリックで生成時の設定")
        return "\n".join(lines)

    def _on_loaded(self, ticket: int, path: str, image: QImage):
        self._images[self._cache_key(Path(path))] = image
        if ticket != self._ticket:
            return
        item = self._items.get(path)
        if item is not None:
            self._repaint_item(item, image)

    def _repaint_item(self, item: QListWidgetItem, image: Optional[QImage] = None):
        path = item.data(PATH_ROLE)
        if image is None:
            image = self._images.get(self._cache_key(Path(path)))
        item.setIcon(self._icon(image, item.data(PATH_ROLE + 1), item.data(PATH_ROLE + 2), path == self.compared))

    def _icon(self, image: Optional[QImage], is_video: bool, old: bool, compared: bool) -> QIcon:
        """サムネールに印を重ねる (動画は左下に ▶、前の説明で作ったものは左上に「旧」、並べているものは黄枠)。"""
        canvas = QPixmap(THUMB_SIZE, THUMB_SIZE)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if image is None:
            painter.fillRect(QRect(8, 8, THUMB_SIZE - 16, THUMB_SIZE - 16), PLACEHOLDER_COLOR)
        else:
            x = (THUMB_SIZE - image.width()) // 2
            y = (THUMB_SIZE - image.height()) // 2
            painter.drawImage(x, y, image)
        if is_video:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 160))
            painter.drawEllipse(QRect(6, THUMB_SIZE - 34, 28, 28))
            painter.setBrush(QColor("white"))
            painter.drawPolygon(QPolygon([QPoint(16, THUMB_SIZE - 27), QPoint(16, THUMB_SIZE - 13),
                                          QPoint(27, THUMB_SIZE - 20)]))
        if old:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(OLD_COLOR)
            painter.drawRoundedRect(QRect(4, 4, 26, 20), 4, 4)
            painter.setPen(QColor("white"))
            font = QFont(painter.font())
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRect(4, 4, 26, 20), Qt.AlignmentFlag.AlignCenter, "旧")
        if compared:
            painter.setPen(COMPARED_COLOR)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for inset in range(3):
                painter.drawRect(QRect(inset, inset, THUMB_SIZE - 1 - inset * 2, THUMB_SIZE - 1 - inset * 2))
        painter.end()
        return QIcon(canvas)

    # ------------------------------------------------------------------
    # 元画像と並べる
    # ------------------------------------------------------------------
    def set_compared(self, path: Optional[str], notify: bool = False):
        """並べて見せているものを path にする (None ならやめる)。notify なら compare_requested で知らせる。"""
        old, self.list.compared = self.list.compared, path
        for key in {old, path} - {None}:
            item = self._items.get(key)
            if item is not None:
                self._repaint_item(item)
        if notify:
            self.compare_requested.emit(path or "")

    def compare_newest(self):
        """いちばん新しいもの (先頭) を並べる (1 件だけの生成が終わったとき)。"""
        if self.list.count():
            item = self.list.item(0)
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item)
            self.set_compared(item.data(PATH_ROLE), notify=True)

    def _toggle_compare(self, item: Optional[QListWidgetItem]):
        """クリック: 並べて表示。並べているものをもう一度クリックしたら (空きをクリックしても) やめる。"""
        if item is None or self.list.compared_at_press == item.data(PATH_ROLE):
            self.set_compared(None, notify=True)
        elif self.compared != item.data(PATH_ROLE):
            self.set_compared(item.data(PATH_ROLE), notify=True)

    def _follow_current(self, current: Optional[QListWidgetItem], _previous=None):
        """並べているときは、← → などで動いた先を並べる。"""
        if self._rebuilding or current is None or self.compared is None:
            return
        if self.compared != current.data(PATH_ROLE):
            self.list.scrollToItem(current)
            self.set_compared(current.data(PATH_ROLE), notify=True)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _selected_paths(self) -> list[Path]:
        return [Path(item.data(PATH_ROLE)) for item in self.list.selectedItems()]

    def _on_double_clicked(self, item: QListWidgetItem):
        self._open(Path(item.data(PATH_ROLE)))

    def _open_current(self):
        item = self.list.currentItem()
        if item is not None:
            self._open(Path(item.data(PATH_ROLE)))

    def _open(self, path: Path):
        try:
            os.startfile(str(path))  # 関連付けられたアプリで開く
        except OSError as exc:
            self.status.emit(f"開けませんでした: {path.name} ({exc})")

    def _show_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        paths = self._selected_paths()
        clicked = Path(item.data(PATH_ROLE))
        menu = QMenu(self)
        open_action = menu.addAction("開く\tEnter")
        open_action.triggered.connect(lambda: self._open(clicked))
        record = self._records.get(str(clicked))
        info = menu.addAction("生成時の設定...")
        info.setEnabled(record is not None)
        if record is None:
            info.setToolTip("生成の記録がありません (ファイル名から見つけたもの)")
        info.triggered.connect(lambda: self.info_requested.emit(record))
        menu.setToolTipsVisible(True)
        explorer = menu.addAction("エクスプローラーで開く")
        explorer.triggered.connect(lambda: results.show_in_explorer(clicked))
        menu.addSeparator()
        if len(paths) > 1:
            delete = menu.addAction(f"選択中の {len(paths)} 個のファイルを削除 (ごみ箱へ)\tDel")
        else:
            delete = menu.addAction("ファイルを削除 (ごみ箱へ)\tDel")
        delete.triggered.connect(self.delete_selected)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def delete_selected(self):
        """選択中のファイルをごみ箱へ。1 つなら確認なし、複数かごみ箱に入らないときは確認する。"""
        paths = self._selected_paths()
        if not paths:
            return
        comparing = self.compared is not None
        recyclable = all(results.can_recycle(p) for p in paths)
        if len(paths) > 1 or not recyclable:
            what = paths[0].name if len(paths) == 1 else f"選択中の {len(paths)} 個のファイル"
            text = f"{what}を削除 (ごみ箱へ) しますか？"
            if not recyclable:
                text = f"{what}を削除しますか？\n\nこの場所のファイルはごみ箱に入らず、完全に消えます。"
            if QMessageBox.question(self, "ファイルを削除", text) != QMessageBox.StandardButton.Yes:
                return
        first_row = min(self.list.row(item) for item in self.list.selectedItems())
        if comparing:
            self.set_compared(None, notify=True)  # 表示中のファイルを手放してから消す
        error = results.send_to_recycle_bin(paths)
        gone = [p for p in paths if not p.exists()]
        self.refresh()
        # 消したあとは、同じ位置 (= 次) のものを選ぶ
        if self.list.count():
            row = min(first_row, self.list.count() - 1)
            self.list.setCurrentRow(row)
            if comparing:
                self.set_compared(self.list.currentItem().data(PATH_ROLE), notify=True)  # 並べていたら次のもの
        self.changed.emit()
        if error and len(gone) < len(paths):
            self.status.emit(f"{error}: {len(paths) - len(gone)} 個 / ごみ箱へ移動 {len(gone)} 個")
        elif len(gone) == 1:
            self.status.emit(f"ごみ箱へ移動しました: {gone[0].name}")
        else:
            self.status.emit(f"{len(gone)} 個のファイルをごみ箱へ移動しました")
