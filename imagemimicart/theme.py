"""見た目 (ui-standard の 14 章): 暗い配色、強調色は黄。"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BACKGROUND = "#202020"
BAR = "#2B2B2B"  # ツールバーとステータスバー
MAIN_VIEW = "#111111"
LIST = "#181818"
BUTTON = "#3A3A3A"
BUTTON_HOVER = "#505050"
ACCENT = "#E0A000"  # 選択中の枠・押してある切り替えボタン
ACCENT_LIGHT = "#FFE84D"  # 表示中の枠
TEXT = "#E6E6E6"
TEXT_DIM = "#9A9A9A"

# ダイアログの結果表示など
OK_COLOR = "#66BB6A"
ERROR_COLOR = "#EF5350"
HINT_COLOR = TEXT_DIM

STYLE = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BACKGROUND}; }}
QToolBar {{ background: {BAR}; border: none; spacing: 4px; padding: 3px; }}
QToolBar::separator {{ background: #444; width: 1px; margin: 4px 6px; }}
QStatusBar {{ background: {BAR}; }}
QStatusBar QLabel {{ color: {TEXT}; }}
QPushButton, QToolButton {{
    background: {BUTTON}; border: 1px solid #4A4A4A; border-radius: 3px; padding: 4px 10px;
}}
QPushButton:hover, QToolButton:hover {{ background: {BUTTON_HOVER}; }}
QPushButton:disabled, QToolButton:disabled {{ color: #6A6A6A; background: #2E2E2E; }}
QToolButton:checked, QPushButton:checked {{ background: {ACCENT}; color: black; }}
QToolButton[flat="true"] {{ background: transparent; border: none; padding: 2px; }}
QToolButton[flat="true"]:hover {{ background: {BUTTON_HOVER}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #161616; border: 1px solid #4A4A4A; border-radius: 3px;
    selection-background-color: #6B5200; selection-color: white;
}}
QPlainTextEdit:read-only {{ background: #1C1C1C; }}
QComboBox QAbstractItemView {{ background: #262626; selection-background-color: #6B5200; }}
QTreeView, QListView, QTreeWidget, QListWidget {{
    background: {LIST}; border: none; selection-background-color: #4A3A00; selection-color: white;
}}
QHeaderView::section {{ background: {BAR}; border: none; padding: 3px 6px; }}
QTabWidget::pane {{ border: 1px solid #444; }}
QTabBar::tab {{ background: {BUTTON}; padding: 5px 12px; border: 1px solid #444; }}
QTabBar::tab:selected {{ background: {BUTTON_HOVER}; }}
QMenu {{ background: #2A2A2A; border: 1px solid #555; }}
QMenu::item {{ padding: 4px 24px 4px 20px; }}
QMenu::item:selected {{ background: #4A3A00; }}
QMenu::item:disabled {{ color: #6A6A6A; }}
QMenu::separator {{ height: 1px; background: #444; margin: 3px 8px; }}
QToolTip {{ background: #2A2A2A; color: {TEXT}; border: 1px solid #555; }}
QSplitter::handle {{ background: #2A2A2A; }}
QLabel[hint="true"] {{ color: {TEXT_DIM}; }}
QScrollBar {{ background: {LIST}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BACKGROUND),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, "#161616"),
        (QPalette.ColorRole.AlternateBase, "#222222"),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, BUTTON),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, "#6B5200"),
        (QPalette.ColorRole.HighlightedText, "#FFFFFF"),
        (QPalette.ColorRole.ToolTipBase, "#2A2A2A"),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, "#777777"),
        (QPalette.ColorRole.Link, ACCENT_LIGHT),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6A6A6A"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6A6A6A"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#6A6A6A"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
