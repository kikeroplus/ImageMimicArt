"""説明だけ残っている .txt (同じ名前の画像がないもの) を見て、要らないものをごみ箱へ送る窓。

画像を消したり、エクスプローラーで画像だけ移したりすると、photo.txt が相手のいないまま残る。
このアプリと関係ないテキストも同じように見えるので、勝手には消さない。中身を見て、チェックしたものだけ送る。
"""

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import reader, results

PATH_ROLE = Qt.ItemDataRole.UserRole
PREVIEW_CHARS = 20000


def find_orphans(folder: Path) -> list[Path]:
    """folder の中の、同じ名前の画像がない .txt (名前順)。読めなければ空。"""
    txt: dict[str, Path] = {}
    stems = set()
    try:
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
                elif suffix in reader.IMAGE_EXTS:
                    stems.add(path.stem.lower())
    except OSError:
        return []
    return sorted((p for stem, p in txt.items() if stem not in stems), key=lambda p: p.name.lower())


class OrphanDialog(QDialog):
    """相手のいない .txt の一覧。閉じたあと removed に消したものが入る。"""

    def __init__(self, folder: Path, paths: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("説明だけ残っている .txt を整理")
        self.resize(900, 560)
        self.folder = folder
        self.removed: list[Path] = []

        layout = QVBoxLayout(self)
        note = QLabel(
            f"{folder}\n"
            "同じ名前の画像がない .txt です (画像を消した・移したあとに残った説明など)。"
            "このアプリと関係ないテキストも混ざることがあるので、中身を確かめて、要らないものにチェックしてください。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        splitter.addWidget(self.list)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.info = QLabel()
        right_layout.addWidget(self.info)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        right_layout.addWidget(self.preview, 1)
        splitter.addWidget(right)
        splitter.setSizes([320, 580])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        check_all = QPushButton("すべてチェック")
        uncheck_all = QPushButton("チェックを外す")
        buttons.addWidget(check_all)
        buttons.addWidget(uncheck_all)
        buttons.addStretch(1)
        self.delete_button = QPushButton()
        buttons.addWidget(self.delete_button)
        close = QPushButton("閉じる")
        buttons.addWidget(close)
        layout.addLayout(buttons)

        for path in paths:
            item = QListWidgetItem(path.name)
            item.setData(PATH_ROLE, str(path))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)  # 勝手に消さない: 最初はどれもチェックしない
            item.setToolTip(str(path))
            self.list.addItem(item)

        self.list.currentItemChanged.connect(self._show_preview)
        self.list.itemChanged.connect(lambda _item: self._update_button())
        check_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        uncheck_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        self.delete_button.clicked.connect(self._delete_checked)
        close.clicked.connect(self.accept)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.setFocus()
        self._update_button()

    def _checked(self) -> list[QListWidgetItem]:
        return [self.list.item(i) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _set_all(self, state: Qt.CheckState):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def _update_button(self):
        count = len(self._checked())
        self.delete_button.setText(f"チェックした {count} 個をごみ箱へ" if count else "チェックしたものをごみ箱へ")
        self.delete_button.setEnabled(count > 0)

    def _show_preview(self, item, _previous=None):
        if item is None:
            self.info.setText("")
            self.preview.setPlainText("")
            return
        path = Path(item.data(PATH_ROLE))
        try:
            stat = path.stat()
            when = datetime.fromtimestamp(stat.st_mtime).strftime("%Y/%m/%d %H:%M")
            self.info.setText(f"{path.name}    {stat.st_size:,} バイト    更新 {when}")
        except OSError as exc:
            self.info.setText(f"{path.name}    (読めません: {exc})")
        text = reader.read_caption(path) or ""
        if len(text) > PREVIEW_CHARS:
            text = text[:PREVIEW_CHARS] + "\n\n(長いので、ここまで)"
        self.preview.setPlainText(text)

    def _delete_checked(self):
        items = self._checked()
        if not items:
            return
        paths = [Path(item.data(PATH_ROLE)) for item in items]
        text = f"チェックした {len(paths)} 個の .txt をごみ箱へ送りますか？"
        if not all(results.can_recycle(p) for p in paths):
            text = f"チェックした {len(paths)} 個の .txt を削除しますか？\n\nこの場所のファイルはごみ箱に入らず、完全に消えます。"
        if QMessageBox.question(self, "説明だけ残っている .txt", text) != QMessageBox.StandardButton.Yes:
            return
        error = results.send_to_recycle_bin(paths)
        for item, path in zip(items, paths):
            if not path.exists():
                self.removed.append(path)
                self.list.takeItem(self.list.row(item))
        if error:
            QMessageBox.warning(self, "説明だけ残っている .txt", f"消せなかったものがあります。\n{error}")
        self._update_button()
