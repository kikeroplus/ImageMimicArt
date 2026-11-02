"""左のフォルダツリー (ui-standard の 3 章)。

エクスプローラー風に、上からデスクトップ・ピクチャ・PC (ドライブ → フォルダ)・ネットワーク (PC → 共有 → フォルダ)。
階層は開いたときに裏のスレッドで読む (先読みしない)。ドライブ一覧はドライブ文字だけで先に出し、
名前とアイコンは 1 台ずつ後から埋める。ネットワークは J-Rate と同じくシェルの名前空間で列挙する。
クリックでそのフォルダを開く。右クリックしただけでは選択を変えない。ツリーはマウス専用 (フォーカスを取らない)。
"""

import ctypes
import functools
import os
import stat
import string
import threading
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QFileInfo, QObject, QStandardPaths, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QFileIconProvider, QMenu, QTreeWidget, QTreeWidgetItem

COMPUTER, FOLDER, NETWORK, SERVER, PLACEHOLDER = range(5)
KIND_ROLE = Qt.ItemDataRole.UserRole
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
LOADED_ROLE = Qt.ItemDataRole.UserRole + 2
HIDDEN_COLOR = QColor(255, 255, 255, 110)  # 隠しフォルダは薄く

try:
    _StrCmpLogicalW = ctypes.windll.shlwapi.StrCmpLogicalW
    _StrCmpLogicalW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    _StrCmpLogicalW.restype = ctypes.c_int
    natural_key = functools.cmp_to_key(_StrCmpLogicalW)  # エクスプローラーと同じ並び (2 が 10 より前)
except (AttributeError, OSError):
    natural_key = str.lower


def same_path(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(a.rstrip("\\/")) == os.path.normcase(b.rstrip("\\/"))


# ----------------------------------------------------------------------
# 一覧を作る (裏のスレッドで呼ぶ)
# ----------------------------------------------------------------------
def _list_drives() -> list[tuple]:
    """ドライブ文字だけで作る。ディスクには触れないので、休止中の HDD などを待たない。"""
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    return [(FOLDER, f"({letter}:)", f"{letter}:\\", False)
            for i, letter in enumerate(string.ascii_uppercase) if mask & (1 << i)]


def _list_folders(folder: str) -> list[tuple]:
    entries = []
    with os.scandir(folder) as it:
        for entry in it:
            try:
                if not entry.is_dir():
                    continue
                attrs = entry.stat().st_file_attributes
            except OSError:
                continue
            hidden = bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)
            if hidden and attrs & stat.FILE_ATTRIBUTE_SYSTEM:
                continue  # 保護されたシステムフォルダ ($Recycle.Bin など) は出さない
            entries.append((FOLDER, entry.name, entry.path, hidden))
    entries.sort(key=lambda e: natural_key(e[1]))
    return entries


def _list_shell(namespace, kind: int) -> list[tuple]:
    """シェルの名前空間を列挙する (ネットワーク上の PC と、その共有フォルダ)。STA のスレッドで呼ぶ。"""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        folder = shell.NameSpace(namespace)
        entries = []
        if folder is not None:
            for item in folder.Items():
                try:
                    path = str(item.Path or "")
                    if item.IsFolder and path.startswith("\\\\"):
                        entries.append((kind, str(item.Name), path, False))
                except Exception:  # noqa: BLE001 - 読めない項目は飛ばす
                    continue
        entries.sort(key=lambda e: natural_key(e[1]))
        return entries
    finally:
        pythoncom.CoUninitialize()


def _drive_name(path: str) -> str:
    """「ローカル ディスク (C:)」のような表示名 (エクスプローラーと同じ)。"""
    try:
        from win32com.shell import shell, shellcon

        _flags, info = shell.SHGetFileInfo(path, 0, shellcon.SHGFI_DISPLAYNAME)
        return info[3] or ""
    except Exception:  # noqa: BLE001 - 取れなければドライブ文字のまま
        return ""


CSIDL_NETWORK = 0x12


class _Loader(QObject):
    loaded = Signal(object, object, str)  # item, entries, error
    drive_named = Signal(object, str)  # item, 表示名


class FolderTree(QTreeWidget):
    """フォルダを開く (folder_clicked) ためのツリー。"""

    folder_clicked = Signal(str)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # マウス専用 (キーはファイルの移動に使う)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, self.header().ResizeMode.ResizeToContents)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self._icons = QFileIconProvider()
        self._folder_icon = self._icons.icon(QFileIconProvider.IconType.Folder)
        self._loader = _Loader()
        self._loader.loaded.connect(self._on_loaded, Qt.ConnectionType.QueuedConnection)
        self._loader.drive_named.connect(self._on_drive_named, Qt.ConnectionType.QueuedConnection)
        self._waiting: dict[int, list[Callable]] = {}  # 読み込み中の item → 読み終わったら呼ぶもの
        self._sync_version = 0

        for location in (QStandardPaths.StandardLocation.DesktopLocation,
                         QStandardPaths.StandardLocation.PicturesLocation):
            path = QStandardPaths.writableLocation(location)
            if path and os.path.isdir(path):
                name = QStandardPaths.displayName(location)
                item = self._make_item(FOLDER, name, str(Path(path)), False)
                item.setIcon(0, self._icons.icon(QFileInfo(path)))
                self.addTopLevelItem(item)
        self.computer = self._make_item(COMPUTER, "PC", "", False)
        self.computer.setIcon(0, self._icons.icon(QFileIconProvider.IconType.Computer))
        self.addTopLevelItem(self.computer)
        self.network = self._make_item(NETWORK, "ネットワーク", "", False)
        self.network.setIcon(0, self._icons.icon(QFileIconProvider.IconType.Network))
        self.addTopLevelItem(self.network)

        self.itemExpanded.connect(lambda item: self.ensure_children(item))
        self.itemClicked.connect(self._on_clicked)
        self.computer.setExpanded(True)

    # ------------------------------------------------------------------
    # 項目
    # ------------------------------------------------------------------
    def _make_item(self, kind: int, name: str, path: str, hidden: bool) -> QTreeWidgetItem:
        item = QTreeWidgetItem([name])
        item.setData(0, KIND_ROLE, kind)
        item.setData(0, PATH_ROLE, path)
        item.setToolTip(0, path or name)
        if kind == FOLDER:
            item.setIcon(0, self._folder_icon)
        if hidden:
            item.setForeground(0, QBrush(HIDDEN_COLOR))
        if kind != PLACEHOLDER:
            placeholder = QTreeWidgetItem(["読み込み中..."])
            placeholder.setData(0, KIND_ROLE, PLACEHOLDER)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            item.addChild(placeholder)
        return item

    @staticmethod
    def kind(item: QTreeWidgetItem) -> int:
        return item.data(0, KIND_ROLE)

    @staticmethod
    def path(item: QTreeWidgetItem) -> str:
        return item.data(0, PATH_ROLE) or ""

    def ensure_children(self, item: QTreeWidgetItem, then: Optional[Callable[[], None]] = None):
        """子を (まだなら) 裏で読む。読み終わったら then() を呼ぶ。"""
        if item.data(0, LOADED_ROLE):
            if then is not None:
                then()
            return
        key = id(item)
        if key in self._waiting:
            if then is not None:
                self._waiting[key].append(then)
            return
        self._waiting[key] = [then] if then is not None else []
        kind, path = self.kind(item), self.path(item)

        def work():
            error = ""
            try:
                if kind == COMPUTER:
                    entries = _list_drives()
                elif kind == NETWORK:
                    entries = _list_shell(CSIDL_NETWORK, SERVER)
                elif kind == SERVER:
                    entries = _list_shell(path, FOLDER)
                else:
                    entries = _list_folders(path)
            except Exception as exc:  # noqa: BLE001 - 読めないフォルダは空にする
                entries, error = [], str(exc)
            try:
                self._loader.loaded.emit(item, entries, error)
            except RuntimeError:
                pass  # 閉じられた

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, item: QTreeWidgetItem, entries: list, error: str):
        item.takeChildren()
        children = [self._make_item(*entry) for entry in entries]
        server_icon = self._icons.icon(QFileIconProvider.IconType.Computer)
        for child in children:
            if self.kind(child) == SERVER:
                child.setIcon(0, server_icon)
        item.addChildren(children)
        item.setData(0, LOADED_ROLE, True)
        if self.kind(item) == COMPUTER:
            for child in children:
                self._resolve_drive(child)
        for then in self._waiting.pop(id(item), []):
            then()

    def _resolve_drive(self, item: QTreeWidgetItem):
        """ドライブの表示名とアイコンを後から取る (ドライブごとに別のスレッド。遅いドライブが他を待たせない)。"""
        path = self.path(item)

        def work():
            name = _drive_name(path)
            if name:
                try:
                    self._loader.drive_named.emit(item, name)
                except RuntimeError:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _on_drive_named(self, item: QTreeWidgetItem, name: str):
        item.setText(0, name)
        item.setIcon(0, self._icons.icon(QFileInfo(self.path(item))))

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()  # 右クリックしただけでは選択を変えない (フォルダを開かない)
            return
        h_scroll = self.horizontalScrollBar()
        preserved = h_scroll.value()  # 選んだ項目へ勝手に横スクロールしないように
        super().mousePressEvent(event)
        h_scroll.setValue(preserved)

    def _on_clicked(self, item: QTreeWidgetItem, _column: int):
        if self.kind(item) == FOLDER and self.path(item):
            self.folder_clicked.emit(self.path(item))

    def _show_menu(self, pos):
        item = self.itemAt(pos)
        if item is None or self.kind(item) not in (FOLDER, SERVER) or not self.path(item):
            return
        path = self.path(item)
        menu = QMenu(self)
        menu.addAction("エクスプローラーで開く").triggered.connect(lambda: os.startfile(path))
        menu.exec(self.viewport().mapToGlobal(pos))

    def sync_to(self, folder: str, reveal: bool = False):
        """そのフォルダまで展開して選択する。

        reveal = False: フォルダを開いたときの自動追従 (ネットワークのパスは時間がかかるので追わない)。
        reveal = True : 「フォルダを表示」。ネットワークも探し、見える位置までスクロールする。
        """
        self._sync_version += 1
        version = self._sync_version
        full = os.path.abspath(folder)
        unc = full.startswith("\\\\")
        current = self.currentItem()
        if not reveal and (unc or (current is not None and same_path(self.path(current), full))):
            if current is not None and not same_path(self.path(current), full):
                self.clearSelection()
            return
        root = os.path.splitdrive(full)[0] + "\\"
        rest = [part for part in full[len(root):].split("\\") if part]

        def finish(node: Optional[QTreeWidgetItem], found: bool):
            if version != self._sync_version:
                return
            h_scroll = self.horizontalScrollBar()
            preserved = h_scroll.value()  # 見える位置まで縦にだけスクロールする
            if found and node is not None:
                self.setCurrentItem(node)
                self.scrollToItem(node)
            elif not found:
                self.clearSelection()
                if node is not None and reveal:
                    self.scrollToItem(node)
            h_scroll.setValue(preserved)
            if unc:
                self.status.emit("")

        def walk(node: QTreeWidgetItem, parts: list[str]):
            if version != self._sync_version:
                return
            if not parts:
                finish(node, True)
                return

            def step():
                if version != self._sync_version:
                    return
                for i in range(node.childCount()):
                    child = node.child(i)
                    if os.path.basename(self.path(child).rstrip("\\")).lower() == parts[0].lower():
                        node.setExpanded(True)
                        walk(child, parts[1:])
                        return
                finish(node, False)  # ツリーに出していないフォルダ (システムフォルダなど)

            self.ensure_children(node, step)

        def find_child(node: QTreeWidgetItem, target: str, then: Callable[[Optional[QTreeWidgetItem]], None]):
            def step():
                if version != self._sync_version:
                    return
                node.setExpanded(True)
                for i in range(node.childCount()):
                    if same_path(self.path(node.child(i)), target):
                        then(node.child(i))
                        return
                then(None)

            self.ensure_children(node, step)

        if unc:
            self.status.emit("ネットワークを検索中...")
            server = "\\\\" + full[2:].split("\\")[0]
            share_root = os.path.splitdrive(full)[0]

            def on_server(node):
                if node is None:
                    finish(None, False)
                    return
                find_child(node, share_root, lambda share: walk(share, rest) if share else finish(node, False))

            find_child(self.network, server, on_server)
        else:
            find_child(self.computer, root, lambda drive: walk(drive, rest) if drive else finish(None, False))
