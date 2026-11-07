"""メインウィンドウ: ツールバー、フォルダツリー、メイン表示、サムネール一覧、右の枠、ステータスバー、グリッド一覧。

画面と操作は ui-standard (J-Rate) に合わせる。採用した章は 1〜5・7・9〜11・13〜16 (評価とコピー・移動は入れない)。
キーはウインドウ全体で受ける。ただし文字を入力する欄 (説明プロンプトなど) にいるときはその欄に効き、
Esc で一覧に戻る。生成結果のサムネールにいるときは、← → で生成結果を切り替える。
"""

import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QTextEdit,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gencommon import comfy, reader
from gencommon.generation import GenerationController

from . import orphans, results, settings
from .caption_panel import CaptionPanel
from .folder_tree import FolderTree, same_path
from .image_list import BASE_THUMB, PATH_ROLE, SORT_MODES, ImageModel, ThumbView, is_image
from .image_view import MainView
from .theme import BAR

APP_TITLE = "ImageMimicArt"
STRIP_TILE = 120
GRID_TILES = (160, 240, 320)  # 小 / 中 / 大
HELP_TEXT = ("← →・ホイール: 移動 / Home End: 先頭・末尾 / Del: ごみ箱へ削除 / Ctrl・Shift+クリック: 複数選択 / "
             "Ctrl+D: 複数選択を解除 / Ctrl+ホイール: 拡大縮小 / Ctrl+R: 読み取る / Ctrl+S: 説明を保存 / "
             "Ctrl+O: フォルダを開く / Ctrl+G: グリッド一覧 / Ctrl+J: 生成ジョブ / Esc: 並べるのをやめる・入力欄から戻る")

NEXT_KEYS = {Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space}
PREV_KEYS = {Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace}


def _is_text_input(widget) -> bool:
    if isinstance(widget, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)):
        return True
    return isinstance(widget, QComboBox) and widget.isEditable()


class GridWindow(QWidget):
    """グリッド一覧 (ui-standard の 5 章)。親子関係のない独立したウインドウ。選択と表示中はメインと共有する。"""

    def __init__(self, main: "MainWindow"):
        super().__init__(None, Qt.WindowType.Window)
        self.main = main
        self.setWindowTitle(f"グリッド一覧 - {APP_TITLE}")
        self.setWindowIcon(main.windowIcon())
        self.tile_index = settings.get_int("grid_tile.txt", 0, 0, len(GRID_TILES) - 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("gridBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        bar.setStyleSheet(f"#gridBar {{ background: {BAR}; }}")  # 子のボタンには効かせない
        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 3, 6, 3)
        row.addWidget(QLabel("タイルの大きさ"))
        self.size_buttons = []
        for i, name in enumerate(("小", "中", "大")):
            button = QToolButton()
            button.setText(name)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(f"タイルの幅 {GRID_TILES[i]} (Ctrl+ホイールでも変えられます)")
            button.clicked.connect(lambda _checked, i=i: self.set_tile(i))
            row.addWidget(button)
            self.size_buttons.append(button)
        row.addStretch(1)
        layout.addWidget(bar)
        self.view = ThumbView(main.model, True, GRID_TILES[self.tile_index], self)
        self.view.setSelectionModel(main.selection)
        layout.addWidget(self.view, 1)
        self.view.activated_item.connect(lambda _row: main.bring_to_front())
        self.view.context_requested.connect(main.show_context_menu)
        self.view.tile_wheel.connect(lambda step: self.set_tile(self.tile_index + step))
        self.view.verticalScrollBar().valueChanged.connect(lambda _v: self._request_large())
        for key, func in (("Ctrl+O", main.choose_folder), ("Ctrl+D", main.clear_multi), ("Ctrl+R", main.read_current),
                          ("Ctrl+J", main.gen.show_jobs), ("Ctrl+S", main.panel.save)):
            QShortcut(QKeySequence(key), self).activated.connect(func)
        self._restore()
        self.set_tile(self.tile_index)

    def _restore(self):
        rect = settings.get_ints("grid_window.txt", 5)
        if rect and _on_screen(QRect(*rect[:4])):
            self.setGeometry(*rect[:4])
            if rect[4]:
                self.setWindowState(Qt.WindowState.WindowMaximized)
        else:
            self.resize(1000, 700)

    def save_settings(self):
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        settings.put("grid_window.txt", f"{g.x()},{g.y()},{g.width()},{g.height()},{int(self.isMaximized())}")
        settings.put("grid_tile.txt", self.tile_index)

    def set_tile(self, index: int):
        index = min(max(index, 0), len(GRID_TILES) - 1)
        self.tile_index = index
        for i, button in enumerate(self.size_buttons):
            button.setChecked(i == index)
        self.view.set_tile(GRID_TILES[index])
        self.scroll_to_current()
        self._request_large()

    def _request_large(self):
        """中・大では、その大きさのサムネールを裏で作り直す (見えているところから)。"""
        tile = GRID_TILES[self.tile_index]
        if tile <= BASE_THUMB or not self.isVisible():
            self.main.model.drop_large()
            return
        first = self.view.indexAt(QPoint(10, 10))
        self.main.model.request_large(tile, first.row() if first.isValid() else 0)

    def scroll_to_current(self):
        index = self.main.selection.currentIndex()
        if index.isValid():
            self.view.scrollTo(index)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._request_large)

    def closeEvent(self, event):
        self.save_settings()
        self.main.model.drop_large()  # 閉じたら大きいサムネールは捨てる
        super().closeEvent(event)


def _on_screen(rect: QRect) -> bool:
    return any(screen.availableGeometry().intersects(rect) for screen in QGuiApplication.screens())


class MainWindow(QMainWindow):
    def __init__(self, start_path: Optional[str] = None, notice: str = ""):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setAcceptDrops(True)
        self.grid: Optional[GridWindow] = None
        self._reverting = False
        self._pending_select: Optional[str] = None
        self._delete_key_down = False

        self.model = ImageModel(self)
        self.model.sort_mode = settings.get_int("sort.txt", 0, 0, len(SORT_MODES) - 1)
        self.selection = QItemSelectionModel(self.model, self)

        self.tree = FolderTree()
        self.view = MainView()
        self.strip = ThumbView(self.model, False, STRIP_TILE)
        self.strip.setSelectionModel(self.selection)
        self.panel = CaptionPanel()
        self.gen = GenerationController(self, self._caption_for, self.show_status, self._save_caption)

        self._build_toolbar()
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self.view, 1)
        center_layout.addWidget(self.strip)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(center)
        self.splitter.addWidget(self.panel)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self.splitter)
        self._build_statusbar()

        # 生成結果を元画像と並べる、記録を見る
        self.panel.status.connect(self.show_status)
        self.panel.caption_saved.connect(lambda path, has: self.model.set_caption_state(path, has))
        self.panel.results.compare_requested.connect(lambda path: self.view.set_compare(path or None))
        self.panel.results.info_requested.connect(lambda record: self.gen.show_record(record))
        self.panel.results.changed.connect(self._recount_shown)
        self.tree.folder_clicked.connect(lambda path: self.open_folder(path))
        self.tree.status.connect(lambda text: self.show_status(text) if text else None)
        self.view.step_requested.connect(self.move)
        self.view.context_requested.connect(self.show_context_menu)
        self.view.zoom_changed.connect(self._update_info)
        self.strip.context_requested.connect(self.show_context_menu)
        self.model.loaded.connect(self._on_folder_loaded)
        self.model.thumbs_progress.connect(self._on_thumbs_progress)
        self.model.rowsRemoved.connect(lambda *_: self._update_count())
        self.selection.currentChanged.connect(self._on_current_changed)
        self.selection.selectionChanged.connect(self._on_selection_changed)
        self.gen.jobs_changed.connect(self._update_jobs_button)
        self.gen.job_done.connect(self._on_job_done)

        for key, func in (("Ctrl+O", self.choose_folder), ("Ctrl+D", self.clear_multi), ("Ctrl+G", self.show_grid),
                          ("Ctrl+R", self.read_current), ("Ctrl+J", self.gen.show_jobs), ("Ctrl+S", self.panel.save)):
            QShortcut(QKeySequence(key), self).activated.connect(func)
        QApplication.instance().installEventFilter(self)

        self._restore_settings()
        self._update_actions()
        self.show_status(notice or HELP_TEXT)
        folder = start_path or settings.get("lastfolder.txt")
        if folder and os.path.exists(folder):
            QTimer.singleShot(0, lambda: self.open_path(folder))
        else:
            self.view.show_image(None, message="フォルダを開いてください (Ctrl+O・左のツリー・フォルダをドロップ)")
        QTimer.singleShot(300, self.gen.offer_resume)

    # ------------------------------------------------------------------
    # 画面の部品
    # ------------------------------------------------------------------
    def _build_toolbar(self):
        bar = QToolBar("ツールバー")
        bar.setMovable(False)
        bar.setFloatable(False)
        self.addToolBar(bar)

        def action(text: str, tip: str, func) -> QAction:
            act = QAction(text, self)
            act.setToolTip(tip)
            act.triggered.connect(func)
            bar.addAction(act)
            return act

        action("フォルダを開く", "フォルダを開きます (Ctrl+O)", self.choose_folder)
        bar.addSeparator()
        bar.addWidget(QLabel(" 並び順 "))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(SORT_MODES)
        self.sort_combo.setCurrentIndex(self.model.sort_mode)
        self.sort_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sort_combo.currentIndexChanged.connect(self._on_sort)
        bar.addWidget(self.sort_combo)
        self.deselect_action = action("選択解除", "複数選択を解除して、表示中の 1 つだけにします (Ctrl+D)", self.clear_multi)
        action("グリッド一覧", "一覧をタイルで並べた別のウインドウを開きます (Ctrl+G)", self.show_grid)
        self.newest_action = action("最新の結果と並べる",
                                    "画像を表示したら、いちばん新しい生成結果を元画像の横に並べます (Esc でその画像だけやめる)",
                                    self._toggle_newest)
        self.newest_action.setCheckable(True)
        self.newest_action.setChecked(settings.get("compare_newest.txt") != "0")
        bar.addSeparator()
        self.read_action = action("読み取る", "表示中の画像を LLM に読ませて、説明プロンプトを作ります (Ctrl+R)",
                                  self.read_current)
        self.generate_action = action("生成する", "表示中 (複数選択中は選択中) の画像から生成します", self.generate_selected)
        self.jobs_action = action("生成ジョブ", "生成ジョブの進み具合とキャンセル (Ctrl+J)", self.gen.show_jobs)
        action("生成の設定", "接続先・ワークフロー・ベースプロンプト・出力フォルダの登録", self.gen.show_settings)
        bar.addSeparator()
        action("読み取りの設定", "LLM のサーバーURL・モデル・読み取りプリセットの登録", self.panel.show_reader_settings)
        for button in bar.findChildren(QToolButton):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # ツールバーのボタンはフォーカスを取らない

    def _build_statusbar(self):
        status = self.statusBar()
        self.status_label = QLabel()
        self.status_label.setMinimumWidth(100)
        status.addWidget(self.status_label, 1)
        self.progress_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(100)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setTextVisible(False)
        status.addPermanentWidget(self.progress_label)
        status.addPermanentWidget(self.progress_bar)
        self.count_label = QLabel()
        status.addPermanentWidget(self.count_label)
        self.progress_label.hide()
        self.progress_bar.hide()

    def show_status(self, text: str):
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _restore_settings(self):
        rect = settings.get_ints("window.txt", 5)
        if rect and _on_screen(QRect(*rect[:4])):
            self.setGeometry(*rect[:4])
            if rect[4]:
                self.setWindowState(Qt.WindowState.WindowMaximized)
        else:
            self.resize(1500, 900)
        sizes = settings.get_ints("splitter.txt", 3)
        # 真ん中 (画像) が押しつぶされた幅は使わない
        ok = sizes and all(s > 0 for s in sizes) and sizes[1] >= 400
        self.splitter.setSizes(sizes if ok else [240, 900, 420])

    def _save_settings(self):
        g = self.normalGeometry() if self.isMaximized() else self.geometry()
        settings.put("window.txt", f"{g.x()},{g.y()},{g.width()},{g.height()},{int(self.isMaximized())}")
        settings.put("splitter.txt", ",".join(str(s) for s in self.splitter.sizes()))
        settings.put("sort.txt", self.model.sort_mode)
        if self.model.folder is not None:
            settings.put("lastfolder.txt", str(self.model.folder))

    # ------------------------------------------------------------------
    # フォルダを開く
    # ------------------------------------------------------------------
    def choose_folder(self):
        start = str(self.model.folder) if self.model.folder else ""
        folder = QFileDialog.getExistingDirectory(self, "フォルダを開く", start)
        if folder:
            self.open_folder(folder)

    def open_path(self, path: str):
        """フォルダならそれを開く。ファイルならそのフォルダを開いて、そのファイルを選ぶ。"""
        p = Path(path)
        if p.is_dir():
            self.open_folder(str(p))
        elif p.is_file():
            self.open_folder(str(p.parent), select=str(p))

    def open_folder(self, folder: str, select: Optional[str] = None):
        if not self.panel.confirm_leave():
            if self.model.folder is not None:
                self.tree.sync_to(str(self.model.folder))  # ツリーを今のフォルダに戻す
            return
        folder = str(Path(folder))
        if self.model.folder is not None and same_path(str(self.model.folder), folder) and not self.model.loading:
            if select:
                self._select_path(select)
            return
        self._pending_select = select
        self._reverting = True
        self.selection.clear()
        self._reverting = False
        self.panel.show_for(None)
        self.view.show_image(None, message="読み込み中...")
        self.model.open_folder(Path(folder))
        self.tree.sync_to(folder)
        self.setWindowTitle(f"{Path(folder).name or folder} - {APP_TITLE}")
        settings.put("lastfolder.txt", folder)
        self._update_count()
        self._update_actions()

    def _on_folder_loaded(self, error: str):
        folder = str(self.model.folder)
        if error:
            self.view.show_image(None, message=f"フォルダを開けません\n{folder}\n{error}")
            self.show_status(f"フォルダを開けません: {error}")
        elif not self.model.entries:
            self.view.show_image(None, message=f"このフォルダには画像がありません\n{folder}")
        else:
            row = self.model.row_of(self._pending_select) if self._pending_select else -1
            self.selection.setCurrentIndex(self.model.index(max(row, 0)),
                                           QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self._pending_select = None
        self._update_count()
        self._update_actions()
        if not error and self.model.orphans:
            self.show_status(f"このフォルダには、同じ名前の画像がない .txt が {len(self.model.orphans)} 個あります"
                             "  (右クリック →「説明だけ残っている .txt を整理」で確かめられます)")

    def _on_thumbs_progress(self, done: int, total: int):
        working = 0 < total and done < total
        self.progress_label.setVisible(working)
        self.progress_bar.setVisible(working)
        if working:
            self.progress_label.setText(f"サムネール作成中 {done} / {total}")
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(done)

    def _update_count(self):
        n = len(self.model.entries)
        orphan = f"  説明だけの .txt {len(self.model.orphans)} 個" if self.model.orphans else ""
        self.count_label.setText("" if self.model.folder is None else f"表示 {n} 枚{orphan}")

    def _on_sort(self, mode: int):
        self.model.set_sort(mode)
        settings.put("sort.txt", mode)
        self._scroll_to_current()
        self._update_info()

    # ------------------------------------------------------------------
    # 選択と表示中
    # ------------------------------------------------------------------
    def current_path(self) -> Optional[Path]:
        index = self.selection.currentIndex()
        return Path(index.data(PATH_ROLE)) if index.isValid() else None

    def selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.selection.selectedIndexes())

    def selected_paths(self) -> list[Path]:
        return [self.model.entries[r].path for r in self.selected_rows()]

    def _select_path(self, path: str):
        row = self.model.row_of(path)
        if row >= 0:
            self.selection.setCurrentIndex(self.model.index(row), QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _on_current_changed(self, current, previous):
        if self._reverting:
            return
        new = Path(current.data(PATH_ROLE)) if current.isValid() else None
        if self.panel.path is not None and (new is None or not results.same_path(new, self.panel.path)):
            if not self.panel.confirm_leave():
                # やめた: 前の画像に戻す
                self._reverting = True
                if previous.isValid():
                    self.selection.select(previous, QItemSelectionModel.SelectionFlag.Select)
                    self.selection.setCurrentIndex(previous, QItemSelectionModel.SelectionFlag.NoUpdate)
                self._reverting = False
                self._repaint_lists()
                return
        self._show_current()

    def _show_current(self):
        path = self.current_path()
        if path is None:
            if self.model.folder is not None and not self.model.loading and not self.model.entries:
                self.view.show_image(None, message="このフォルダには画像がありません")
            self.panel.show_for(None)
        else:
            row = self.selection.currentIndex().row()
            preload = [str(self.model.entries[r].path) for r in (row + 1, row - 1, row + 2)
                       if 0 <= r < len(self.model.entries)]
            self.view.show_image(str(path), preload)
            if self.panel.path is None or not results.same_path(path, self.panel.path):
                self.panel.show_for(path)
                if self.newest_action.isChecked():
                    self.panel.results.compare_newest()
        self._scroll_to_current()
        self._update_info()
        self._update_actions()
        self._repaint_lists()

    def _toggle_newest(self, checked: bool):
        settings.put("compare_newest.txt", int(checked))
        if checked:
            self.panel.results.compare_newest()
        else:
            self.panel.results.set_compared(None, notify=True)

    def _on_selection_changed(self, *_args):
        self._update_info()
        self._update_actions()
        self._repaint_lists()

    def _repaint_lists(self):
        self.strip.viewport().update()
        if self.grid is not None:
            self.grid.view.viewport().update()

    def _scroll_to_current(self):
        index = self.selection.currentIndex()
        if index.isValid():
            self.strip.scrollTo(index)
            if self.grid is not None and self.grid.isVisible():
                self.grid.view.scrollTo(index)

    def _update_info(self):
        index = self.selection.currentIndex()
        if not index.isValid():
            self.view.set_info("")
            return
        parts = [f"{index.row() + 1} / {len(self.model.entries)}", self.model.entries[index.row()].path.name]
        size = self.view.original_size()
        if size is not None:
            parts.append(f"{size.width()} × {size.height()}")
        if self.view.zoom != 1.0:
            parts.append(f"拡大 ×{self.view.zoom:.1f}")
        count = len(self.selection.selectedIndexes())
        if count > 1:
            parts.append(f"{count} 個を選択中")
        self.view.set_info("    ".join(parts))

    def _update_actions(self):
        has = self.current_path() is not None
        multi = len(self.selection.selectedIndexes()) > 1
        self.deselect_action.setEnabled(multi)
        self.deselect_action.setToolTip("複数選択を解除して、表示中の 1 つだけにします (Ctrl+D)"
                                        if multi else "複数選択していないときは使えません")
        self.read_action.setEnabled(has)
        self.generate_action.setEnabled(has)
        count = len(self.selection.selectedIndexes())
        self.generate_action.setText(f"選択中の {count} 枚を生成する" if multi else "生成する")
        self._update_jobs_button()

    def _update_jobs_button(self):
        active = self.gen.active_count
        if self.gen.paused:
            self.jobs_action.setText(f"生成ジョブ ({active}・一時停止中)")
        else:
            self.jobs_action.setText(f"生成ジョブ ({active})" if active else "生成ジョブ")

    def move(self, step: int):
        """前へ / 次へ。複数選択中は、選択を残したまま選択しているものの中だけを動く。端では止まる。"""
        index = self.selection.currentIndex()
        if not index.isValid():
            return
        rows = self.selected_rows()
        if len(rows) > 1:
            pos = rows.index(index.row()) if index.row() in rows else 0
            target = rows[min(max(pos + (1 if step > 0 else -1), 0), len(rows) - 1)]
            self.selection.setCurrentIndex(self.model.index(target), QItemSelectionModel.SelectionFlag.NoUpdate)
            return
        target = min(max(index.row() + step, 0), len(self.model.entries) - 1)
        if target != index.row():
            self.selection.setCurrentIndex(self.model.index(target), QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def move_to_end(self, last: bool):
        rows = self.selected_rows()
        if len(rows) > 1:
            target = rows[-1] if last else rows[0]
            self.selection.setCurrentIndex(self.model.index(target), QItemSelectionModel.SelectionFlag.NoUpdate)
        elif self.model.entries:
            target = len(self.model.entries) - 1 if last else 0
            self.selection.setCurrentIndex(self.model.index(target), QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def clear_multi(self):
        index = self.selection.currentIndex()
        if index.isValid() and len(self.selection.selectedIndexes()) > 1:
            self.selection.select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    # ------------------------------------------------------------------
    # キー (ウインドウ全体で受ける)
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Delete and not event.isAutoRepeat():
            self._delete_key_down = False
        if event.type() != QEvent.Type.KeyPress:
            return False
        active = QApplication.activeWindow()
        if active is not self and (self.grid is None or active is not self.grid):
            return False  # ダイアログなど
        if QApplication.activeModalWidget() is not None:
            return False
        focus = QApplication.focusWidget()
        key = event.key()
        if _is_text_input(focus):
            if key == Qt.Key.Key_Escape and focus is self.panel.editor:
                self.strip.setFocus()  # 説明の欄から一覧に戻る (矢印キーでまた画像を移動できる)
                return True
            return False
        results_list = self.panel.results.list
        if focus is not None and (focus is results_list or results_list.isAncestorOf(focus)):
            return False  # 生成結果のサムネールの中で動く (← → で切り替え、Enter で開く、Del で消す)
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            return False
        in_grid = self.grid is not None and active is self.grid
        if key in NEXT_KEYS or key in PREV_KEYS:
            step = 1 if key in NEXT_KEYS else -1
            if in_grid and key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and len(self.selected_rows()) <= 1:
                step *= self.grid.view.columns()  # グリッドの ↑ ↓ は 1 行分
            self.move(step)
            return True
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            self.move_to_end(key == Qt.Key.Key_End)
            return True
        if key == Qt.Key.Key_Delete:
            if not event.isAutoRepeat() and not self._delete_key_down:
                self._delete_key_down = True  # 押しっぱなしで続けて消えないように
                self.delete_selected()
            return True
        if key == Qt.Key.Key_Escape:
            if self.view.compare_path:
                self.panel.results.set_compared(None, notify=True)
            elif in_grid:
                self.grid.close()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and in_grid:
            self.bring_to_front()
            return True
        return False

    def bring_to_front(self):
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # 右クリックメニュー (メイン表示・サムネール一覧・グリッドで同じもの)
    # ------------------------------------------------------------------
    def show_context_menu(self, pos: QPoint):
        path = self.current_path()
        if path is None:
            if self.model.folder is not None and self.model.orphans:  # 画像がなくても .txt だけ残っていることがある
                menu = QMenu(self)
                menu.addAction(self._orphan_menu_text()).triggered.connect(self.show_orphans)
                menu.exec(pos)
            return
        selected = self.selected_paths()
        multi = len(selected) > 1
        menu = QMenu(self)
        menu.addAction("フォルダを表示").triggered.connect(lambda: self.tree.sync_to(str(path.parent), reveal=True))
        menu.addAction("エクスプローラーで開く").triggered.connect(lambda: results.show_in_explorer(path))
        menu.addSeparator()
        read_text = "読み直す" if reader.caption_path(path).is_file() else "読み取る"
        menu.addAction(f"{read_text} (表示中の画像)\tCtrl+R").triggered.connect(self.read_current)
        generate = menu.addAction(f"選択中の {len(selected)} 枚を生成する..." if multi else "生成する...")
        generate.triggered.connect(self.generate_selected)
        total = len(self.model.entries)
        menu.addAction(f"一覧の画像をまとめて生成する ({total} 枚)...").triggered.connect(self.generate_all)
        info = menu.addAction("生成時の設定...")
        info.setEnabled(self.gen.has_history(path))
        info.triggered.connect(lambda: self.gen.show_gen_info(path))
        menu.addAction("生成ジョブの一覧...\tCtrl+J").triggered.connect(self.gen.show_jobs)
        menu.addAction("生成の設定...").triggered.connect(self.gen.show_settings)
        menu.addSeparator()
        deselect = menu.addAction("複数選択を解除\tCtrl+D")
        deselect.setEnabled(multi)
        deselect.triggered.connect(self.clear_multi)
        menu.addSeparator()
        orphan = menu.addAction(self._orphan_menu_text())
        orphan.setEnabled(bool(self.model.orphans))
        orphan.triggered.connect(self.show_orphans)
        menu.addSeparator()
        text = f"選択中の {len(selected)} 個のファイルを削除 (ごみ箱へ)\tDel" if multi else "ファイルを削除 (ごみ箱へ)\tDel"
        menu.addAction(text).triggered.connect(self.delete_selected)
        menu.exec(pos)

    # ------------------------------------------------------------------
    # 説明だけ残っている .txt
    # ------------------------------------------------------------------
    def _orphan_menu_text(self) -> str:
        count = len(self.model.orphans)
        return f"説明だけ残っている .txt を整理 ({count} 個)..." if count else "説明だけ残っている .txt はありません"

    def show_orphans(self):
        folder = self.model.folder
        if folder is None:
            return
        self.model.orphans = orphans.find_orphans(folder)  # 開いたあとに変わっているかもしれないので数え直す
        self._update_count()
        if not self.model.orphans:
            self.show_status("説明だけ残っている .txt はありません")
            return
        dialog = orphans.OrphanDialog(folder, self.model.orphans, self)
        dialog.exec()
        if dialog.removed:
            gone = {results._key(p) for p in dialog.removed}
            self.model.orphans = [p for p in self.model.orphans if results._key(p) not in gone]
            self.show_status(f"説明だけ残っている .txt を {len(dialog.removed)} 個、ごみ箱へ送りました")
        self._update_count()

    # ------------------------------------------------------------------
    # 読み取り・生成
    # ------------------------------------------------------------------
    def read_current(self):
        if self.current_path() is not None:
            self.panel.read()

    def generate_selected(self):
        paths = self.selected_paths()
        if len(paths) > 1:
            self.gen.generate(paths, what="選択中の画像")
        elif self.current_path() is not None:
            self.gen.generate([self.current_path()])

    def generate_all(self):
        self.gen.generate(self.model.paths(), what="一覧の画像")

    def _caption_for(self, path: Path) -> str:
        """その元画像の今の説明プロンプト (表示中なら、保存前の編集も含めて右の枠の内容)。"""
        if self.panel.path is not None and results.same_path(path, self.panel.path):
            return self.panel.caption_text()
        return reader.read_caption(path) or ""

    def _save_caption(self, path: Path, text: str):
        """生成ジョブで読み取った説明プロンプトを .txt に書く。"""
        try:
            reader.write_caption(path, text)
        except OSError as exc:
            self.show_status(f"説明プロンプトを保存できません: {reader.caption_path(path)} ({exc})")
            return
        self.model.set_caption_state(path, True)
        note = reader.sharing_note(path)
        if note:
            self.show_status(f"読み取って保存しました: {reader.caption_path(path).name}{note}")
        self.panel.caption_written(path, text)

    def _on_job_done(self, job):
        source = Path(job.source)
        if self.model.row_of(source) >= 0:
            self.model.set_count(source, len(results.find_results(source, None)))
        shown = self.panel.path is not None and results.same_path(source, self.panel.path)
        if shown:
            self.panel.results.refresh()
        if job.batch or not comfy.open_result():
            return
        # 1 件だけの生成: 元画像と並べて見せる (複数選択中や、別のフォルダの画像なら動かさない)
        if not shown:
            if self.model.row_of(source) < 0 or len(self.selected_rows()) > 1 or self.panel.is_dirty():
                return
            self._select_path(str(source))
        self.panel.results.compare_newest()

    def _recount_shown(self):
        path = self.panel.path
        if path is not None:
            self.model.set_count(path, self.panel.results.count())

    # ------------------------------------------------------------------
    # 削除 (ごみ箱へ)
    # ------------------------------------------------------------------
    def delete_selected(self):
        paths = self.selected_paths()
        if not paths:
            return
        recyclable = all(results.can_recycle(p) for p in paths)
        if len(paths) > 1 or not recyclable:
            what = paths[0].name if len(paths) == 1 else f"選択中の {len(paths)} 個のファイル"
            text = f"{what}を削除 (ごみ箱へ) しますか？"
            if not recyclable:
                text = f"{what}を削除しますか？\n\nこの場所のファイルはごみ箱に入らず、完全に消えます。"
            if QMessageBox.question(self, "ファイルを削除", text) != QMessageBox.StandardButton.Yes:
                return
        shown = self.panel.path
        if shown is not None and any(results.same_path(p, shown) for p in paths) and self.panel.is_dirty():
            self.panel.editor.document().setModified(False)  # 消す画像の説明の編集は捨てる
        first_row = self.selected_rows()[0]
        self.view.set_compare(None)
        # 説明プロンプトの .txt も一緒に送る (同じ名前の別の画像が残るものは残す)。数えるのは画像だけ
        captions = reader.captions_to_remove(paths)
        error = results.send_to_recycle_bin(paths + captions)
        gone = [p for p in paths if not p.exists()]
        for p in gone:
            self.view.refresh_file(str(p))
        self._reverting = True
        self.model.remove_files(gone)
        self.selection.clear()
        self._reverting = False
        if self.model.entries:
            row = min(first_row, len(self.model.entries) - 1)  # 同じ位置 (= 次) のものを表示する
            self.selection.setCurrentIndex(self.model.index(row), QItemSelectionModel.SelectionFlag.ClearAndSelect)
        else:
            self.panel.show_for(None)
            self.view.show_image(None, message="このフォルダには画像がありません")
        self._update_count()
        if error and len(gone) < len(paths):
            self.show_status(f"{error}: {len(paths) - len(gone)} 個 / ごみ箱へ移動 {len(gone)} 個")
        elif len(gone) == 1:
            self.show_status(f"ごみ箱へ移動しました: {gone[0].name}")
        elif gone:
            self.show_status(f"{len(gone)} 個のファイルをごみ箱へ移動しました")
        else:
            self.show_status(error or "削除できませんでした")

    # ------------------------------------------------------------------
    # グリッド一覧
    # ------------------------------------------------------------------
    def show_grid(self):
        if self.grid is None:
            self.grid = GridWindow(self)
        self.grid.show()
        self.grid.raise_()
        self.grid.activateWindow()
        self.grid.scroll_to_current()

    # ------------------------------------------------------------------
    # ドロップ (フォルダ → 開く、ファイル → そのフォルダを開いて選ぶ)
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.source() is None and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.source() is not None:
            return  # 自分の一覧から自分へ落としたものは無視する
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isdir(path) or is_image(Path(path)):
                    self.open_path(path)
                elif os.path.isfile(path):
                    self.open_folder(str(Path(path).parent))
                event.acceptProposedAction()
                return

    # ------------------------------------------------------------------
    # 閉じる
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if not self.panel.confirm_leave() or not self.gen.confirm_close():
            event.ignore()
            return
        self._save_settings()
        if self.grid is not None:
            self.grid.close()
        QApplication.instance().removeEventFilter(self)
        event.accept()


def start_path_from_args() -> Optional[str]:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    return args[0] if args else None
