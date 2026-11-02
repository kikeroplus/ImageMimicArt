"""右の枠: 説明プロンプト (photo.txt) の表示・手直し・保存、読み取り、エンハンサー、生成結果。

- 説明プロンプトは直接直せて、Ctrl+S で保存する (UTF-8)。保存していないまま別の画像に移る・閉じるときは聞く。
- 「読み取る」(Ctrl+R) で表示中の画像を LLM に読ませる。すでに .txt があれば上書きしてよいか聞く。
  読み取っている間は、その画像の説明の欄を書き換えられない。終わったら欄に入れて保存する。
- 「盛る」は説明プロンプト (保存前の編集も含む) をエンハンサーで盛って、下の欄に出すだけ (ファイルには書かない)。
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import enhancer, llm, presets, reader, settings
from .results import same_path as _same
from .results_view import ResultsStrip


def _header(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


class CaptionPanel(QWidget):
    status = Signal(str)
    caption_saved = Signal(str, bool)  # 元画像のパス, 説明があるか (一覧の印を直す)

    _read_done = Signal(object, object)  # 結果, エラー文
    _enhance_done = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: Optional[Path] = None  # 表示中の元画像
        self._reading: Optional[Path] = None  # 読み取り中の元画像
        self._enhancing = False
        self._enhance_source = ""
        self._enhance_name = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 説明プロンプト
        top = QHBoxLayout()
        self.title = _header("説明プロンプト")
        top.addWidget(self.title, 1)
        self.save_button = QPushButton("保存")
        self.save_button.setToolTip("説明プロンプトを .txt に保存します (Ctrl+S)")
        self.save_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        top.addWidget(self.save_button)
        layout.addLayout(top)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("まだ読み取っていません (読み取る: Ctrl+R)")
        layout.addWidget(self.editor, 3)

        reader_row = QHBoxLayout()
        reader_row.addWidget(QLabel("読み取り"))
        self.reader_combo = QComboBox()
        self.reader_combo.setToolTip("使う読み取りプリセット (画像をどう説明させるか)")
        self.reader_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.reader_combo.setMinimumContentsLength(8)
        reader_row.addWidget(self.reader_combo, 1)
        self.read_button = QPushButton("読み取る")
        self.read_button.setToolTip("表示中の画像を LLM に読ませて、説明プロンプトを作ります (Ctrl+R)")
        self.read_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reader_row.addWidget(self.read_button)
        reader_settings = QPushButton("設定")
        reader_settings.setToolTip("読み取りの設定 (サーバーURL、モデル、読み取りプリセット)")
        reader_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reader_row.addWidget(reader_settings)
        layout.addLayout(reader_row)

        enhance_row = QHBoxLayout()
        enhance_row.addWidget(QLabel("エンハンサー"))
        self.enhancer_combo = QComboBox()
        self.enhancer_combo.setToolTip("使うエンハンサー (生成 AI のモデルごとの書き方)")
        self.enhancer_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.enhancer_combo.setMinimumContentsLength(8)
        enhance_row.addWidget(self.enhancer_combo, 1)
        self.enhance_button = QPushButton("盛る")
        self.enhance_button.setToolTip("説明プロンプトを AI で盛って、下の欄に出します (ファイルは変えません)")
        self.enhance_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        enhance_row.addWidget(self.enhance_button)
        enhancer_settings = QPushButton("設定")
        enhancer_settings.setToolTip("エンハンサーの設定 (指示文の登録、LLM のモデル)")
        enhancer_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        enhance_row.addWidget(enhancer_settings)
        layout.addLayout(enhance_row)

        enhanced_top = QHBoxLayout()
        self.enhanced_title = _header("盛ったプロンプト")
        enhanced_top.addWidget(self.enhanced_title, 1)
        self._copy_icon = presets.copy_icon()
        self._check_icon = presets.check_icon()
        self.copy_button = QToolButton()
        self.copy_button.setProperty("flat", True)
        self.copy_button.setIcon(self._copy_icon)
        self.copy_button.setIconSize(QSize(16, 16))
        self.copy_button.setToolTip("盛ったプロンプトをコピー")
        self.copy_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        enhanced_top.addWidget(self.copy_button)
        layout.addLayout(enhanced_top)
        self.enhanced = QPlainTextEdit()
        self.enhanced.setReadOnly(True)
        self.enhanced.setPlaceholderText("「盛る」を押すと、ここに出します (生成するときは、ジョブの中で改めて盛ります)")
        layout.addWidget(self.enhanced, 2)
        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.timeout.connect(self._reset_copy_button)

        self.results = ResultsStrip(self)
        self.results.status.connect(self.status)
        layout.addWidget(self.results)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        self.reload_presets()
        self.save_button.clicked.connect(self.save)
        self.read_button.clicked.connect(self.read)
        self.enhance_button.clicked.connect(self.enhance)
        reader_settings.clicked.connect(self.show_reader_settings)
        enhancer_settings.clicked.connect(self.show_enhancer_settings)
        self.copy_button.clicked.connect(self._copy_enhanced)
        self.editor.modificationChanged.connect(self._update_title)
        self.reader_combo.currentIndexChanged.connect(
            lambda i: presets.save_index(reader.PANEL_FILE, i) if i >= 0 else None)
        self.enhancer_combo.currentIndexChanged.connect(
            lambda i: presets.save_index(enhancer.PANEL_FILE, i) if i >= 0 else None)
        self._read_done.connect(self._on_read_done, Qt.ConnectionType.QueuedConnection)
        self._enhance_done.connect(self._on_enhance_done, Qt.ConnectionType.QueuedConnection)
        self.show_for(None)

    # ------------------------------------------------------------------
    # 表示中の画像
    # ------------------------------------------------------------------
    def show_for(self, path: Optional[Path]):
        """その元画像の説明プロンプトと生成結果を出す (保存の確認は呼び出し側で済ませておく)。"""
        self.path = path
        text = reader.read_caption(path) if path is not None else None
        self.editor.setPlainText(text or "")
        self.editor.document().setModified(False)
        self._update_state()
        self.results.show_for(path, text if text and text.strip() else None)

    def is_dirty(self) -> bool:
        return self.path is not None and self.editor.document().isModified()

    def caption_text(self) -> str:
        return self.editor.toPlainText()

    def _update_state(self):
        reading_here = self._reading is not None and self.path is not None and _same(self._reading, self.path)
        has_path = self.path is not None
        self.editor.setReadOnly(not has_path or reading_here)
        self.editor.setPlaceholderText(
            "読み取っています..." if reading_here
            else "まだ読み取っていません (読み取る: Ctrl+R)" if has_path else "画像を選んでください"
        )
        self.save_button.setEnabled(has_path and not reading_here)
        self.read_button.setEnabled(has_path and self._reading is None)
        caption_exists = has_path and reader.caption_path(self.path).is_file()
        self.read_button.setText("読み直す" if caption_exists else "読み取る")
        self.enhance_button.setEnabled(has_path and not self._enhancing)
        self._update_title()

    def _update_title(self, *_args):
        if self.path is None:
            self.title.setText("説明プロンプト")
            return
        mark = " *" if self.editor.document().isModified() else ""
        reading = "  (読み取っています...)" if self._reading is not None and _same(self._reading, self.path) else ""
        self.title.setText(f"説明プロンプト ({reader.caption_path(self.path).name}){mark}{reading}")
        self.title.setToolTip(str(reader.caption_path(self.path)))

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def save(self) -> bool:
        """表示中の説明プロンプトを .txt に書く。書けなければ False (知らせてある)。"""
        if self.path is None:
            return True
        text = self.editor.toPlainText()
        try:
            reader.write_caption(self.path, text)
        except OSError as exc:
            QMessageBox.warning(self, "保存", f"説明プロンプトを保存できません。\n{reader.caption_path(self.path)}\n\n{exc}")
            return False
        self.editor.document().setModified(False)
        self.status.emit(f"保存しました: {reader.caption_path(self.path).name}")
        self.caption_saved.emit(str(self.path), bool(text.strip()))
        self._update_state()
        self.results.refresh(text if text.strip() else None)
        return True

    def confirm_leave(self) -> bool:
        """保存していない編集があれば、保存するか聞く。続けてよければ True。"""
        if not self.is_dirty():
            return True
        answer = QMessageBox.question(
            self.window(), "説明プロンプト",
            f"{reader.caption_path(self.path).name} の変更を保存しますか？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            self.editor.document().setModified(False)
            return True
        return False

    def caption_written(self, path: Path, text: str):
        """生成ジョブが .txt を書いた。表示中なら (編集中でなければ) 欄を入れ替える。"""
        if self.path is None or not _same(path, self.path):
            return
        if self.editor.document().isModified():
            self.status.emit(f"{path.name} を読み取って .txt に書きました (編集中の内容は欄に残しています。保存すると上書きします)")
            return
        self.show_for(self.path)

    # ------------------------------------------------------------------
    # 読み取る
    # ------------------------------------------------------------------
    def reload_presets(self):
        presets.fill_combo(self.reader_combo, reader.load_presets(), presets.load_index(reader.PANEL_FILE))
        presets.fill_combo(self.enhancer_combo, enhancer.load_presets(), presets.load_index(enhancer.PANEL_FILE))

    def read(self):
        if self.path is None or self._reading is not None:
            return
        preset = reader.load_presets()[max(self.reader_combo.currentIndex(), 0)]
        if preset.is_empty:
            QMessageBox.information(self, "読み取る", "この読み取りプリセットは未登録です。「設定」で指示文を登録してください。")
            return
        path = self.path
        if reader.caption_path(path).is_file() or self.editor.toPlainText().strip():
            if QMessageBox.question(
                self.window(), "読み直す",
                f"{reader.caption_path(path).name} を、読み取った説明で上書きします。よろしいですか？",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Ok:
                return
        self._reading = path
        self._update_state()
        self.status.emit(f"読み取っています: {path.name} ({preset.name or 'プリセット'})")
        base_url, model, max_side = settings.llm_url(), settings.reader_model(), settings.max_side()
        llm.run_in_thread(lambda: reader.read_image(base_url, model, preset, path, max_side), self._read_done)

    def _on_read_done(self, result, error):
        path, self._reading = self._reading, None
        if path is None:
            return
        if error is not None:
            self._update_state()
            self.status.emit(f"読み取れませんでした: {path.name}")
            QMessageBox.warning(self.window(), "読み取る", f"{path.name}\n\n読み取れませんでした。\n{error}")
            return
        try:
            reader.write_caption(path, result)
        except OSError as exc:
            self._update_state()
            QMessageBox.warning(self.window(), "読み取る", f"説明プロンプトを保存できません。\n{reader.caption_path(path)}\n\n{exc}")
            return
        self.caption_saved.emit(str(path), True)
        if self.path is not None and _same(path, self.path):
            self.show_for(self.path)
        else:
            self._update_state()
        self.status.emit(f"読み取って保存しました: {reader.caption_path(path).name}" + reader.sharing_note(path))

    def show_reader_settings(self):
        dialog = reader.ReaderSettingsDialog(self.reader_combo.currentIndex(), self.window())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        index = dialog.editor.shown if accepted else self.reader_combo.currentIndex()
        presets.fill_combo(self.reader_combo, reader.load_presets(), index)  # プリセットは窓の中で保存済み
        presets.save_index(reader.PANEL_FILE, self.reader_combo.currentIndex())

    def show_enhancer_settings(self):
        dialog = enhancer.EnhancerSettingsDialog(self.enhancer_combo.currentIndex(), self.window())
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        index = dialog.editor.shown if accepted else self.enhancer_combo.currentIndex()
        presets.fill_combo(self.enhancer_combo, enhancer.load_presets(), index)
        presets.save_index(enhancer.PANEL_FILE, self.enhancer_combo.currentIndex())

    # ------------------------------------------------------------------
    # 盛る
    # ------------------------------------------------------------------
    def enhance(self):
        if self.path is None or self._enhancing:
            return
        preset = enhancer.load_presets()[max(self.enhancer_combo.currentIndex(), 0)]
        text = self.editor.toPlainText()
        if preset.is_empty:
            self.enhanced.setPlainText("このエンハンサーは未登録です。「設定」で指示文を登録してください。")
            return
        if not text.strip():
            self.enhanced.setPlainText("説明プロンプトが空です。先に読み取ってください。")
            return
        self._enhancing = True
        self.enhance_button.setEnabled(False)
        name = preset.name or f"{self.enhancer_combo.currentIndex() + 1} 番"
        self.enhanced_title.setText(f"盛ったプロンプト ({name}): 盛っています...")
        self.enhanced.setPlainText("AI が書き足しています...")
        self._enhance_source = self.path.name
        self._enhance_name = name
        base_url, model = settings.llm_url(), settings.enhance_model()
        llm.run_in_thread(lambda: enhancer.enhance(base_url, model, preset, text), self._enhance_done)

    def _on_enhance_done(self, result, error):
        self._enhancing = False
        self.enhance_button.setEnabled(self.path is not None)
        if error is None:
            self.enhanced_title.setText(f"盛ったプロンプト ({self._enhance_name}): {self._enhance_source}")
            self.enhanced.setPlainText(result or "")
        else:
            self.enhanced_title.setText(f"盛れませんでした ({self._enhance_name})")
            self.enhanced.setPlainText(error)

    def _copy_enhanced(self):
        QGuiApplication.clipboard().setText(self.enhanced.toPlainText())
        self.copy_button.setIcon(self._check_icon)
        self.copy_button.setToolTip("コピーしました")
        self._copied_timer.start(presets.COPIED_MS)

    def _reset_copy_button(self):
        self.copy_button.setIcon(self._copy_icon)
        self.copy_button.setToolTip("盛ったプロンプトをコピー")
