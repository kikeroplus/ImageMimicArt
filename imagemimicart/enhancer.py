"""プロンプトエンハンサー (AI で盛る)。

説明プロンプトをローカル LLM に渡して、生成 AI のモデルに合った書き方に書き足してもらう。
モデルごとに推奨される書き方が違うので、指示文 (system)・送る文の雛形 (user)・temperature を
「エンハンサー」のプリセットとして登録し、選んで使う。

PromptWordCrafter のエンハンサーを引き継いだもの。プリセットのファイル (enhancer_presets.json) の形式も同じ。
接続先のサーバーは読み取りと同じものを使い、LLM のモデルだけ別に選べる。
"""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from . import llm, presets, settings
from .presets import PROMPT_MARK, LlmPreset

PRESETS_FILE = "enhancer_presets.json"  # %APPDATA%\ImageMimicArt に置く (comfy_*.txt と同じ場所)
GENERATE_FILE = "enhancer_generate.txt"  # 生成の画面で前回選んだエンハンサーの番号 (0 = なし)
PANEL_FILE = "enhance_preset.txt"  # 右の枠で選んでいるエンハンサーの番号

# 初めて使うときに 1・2 番に入れておく例。モデルごとの推奨の指示文に差し替えて使う
_EXAMPLES = [
    LlmPreset(
        name="例: 自然文 (英語)",
        system=(
            "You are a prompt engineer for text-to-image and text-to-video models.\n"
            "Rewrite the user's prompt into one richly detailed English description in natural sentences: "
            "subject, appearance, action, setting, lighting, camera and style.\n"
            "- Keep every subject and the intent of the original. Do not change who or what is depicted.\n"
            "- The input may be in Japanese or a list of tags; the output must always be English prose.\n"
            "- Keep weighting syntax such as (word:1.2) and special tags such as <lora:...> exactly as written.\n"
            "Output only the rewritten prompt, with no explanation, title or quotation marks."
        ),
        template=PROMPT_MARK,
    ),
    LlmPreset(
        name="例: タグ (カンマ区切り)",
        system=(
            "You are a prompt engineer for anime-style text-to-image models that use comma-separated tags.\n"
            "Expand the user's prompt into a comma-separated list of English tags: keep the original tags first, "
            "then add tags for details, composition, lighting and quality.\n"
            "- Keep every subject and the intent of the original.\n"
            "- The input may be in Japanese; translate it into tags.\n"
            "- Keep weighting syntax such as (word:1.2) and special tags such as <lora:...> exactly as written.\n"
            "Output only the tag list on one line, with no explanation."
        ),
        template=PROMPT_MARK,
    ),
]


def load_presets() -> list[LlmPreset]:
    return presets.load_presets(PRESETS_FILE, _EXAMPLES, PROMPT_MARK)


def last_generate_index() -> int:
    """生成の画面で前回選んだエンハンサー (0 始まり。なしなら -1)。"""
    return presets.load_index(GENERATE_FILE)


def set_last_generate_index(index: int) -> None:
    presets.save_index(GENERATE_FILE, index)


def snapshot(preset: LlmPreset) -> dict:
    """ジョブに持たせる、登録した時点のエンハンサーの写し (接続先とモデルも今のものを入れる)。"""
    return presets.snapshot(preset, settings.llm_url(), settings.enhance_model())


def user_message(template: str, text: str) -> str:
    """雛形の {prompt} を元の文に置き換える。目印がなければ雛形のあとに付ける。"""
    if PROMPT_MARK in template:
        return template.replace(PROMPT_MARK, text)
    return template.rstrip() + "\n\n" + text if template.strip() else text


def enhance(base_url: str, model: str, preset: LlmPreset, text: str) -> str:
    reply = llm.chat(base_url, model, preset.system, user_message(preset.template, text), preset.temperature)
    return presets.clean(reply)


def enhance_with(snap: dict, text: str) -> str:
    """snapshot() の写しで盛る (生成ジョブから使う)。"""
    preset = presets.from_snapshot(snap, PROMPT_MARK)
    return enhance(str(snap.get("url", "")), str(snap.get("model", "")), preset, text)


class EnhancerSettingsDialog(QDialog):
    """エンハンサー用の LLM のモデルと、エンハンサーのプリセット。モデルは OK で決まる。"""

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("エンハンサーの設定")
        self.resize(680, 620)
        layout = QVBoxLayout(self)

        self.status_label = QLabel(f"接続先は「読み取りの設定」のサーバーURL ({settings.llm_url()}) を使います。")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("hint", True)
        form = QFormLayout()
        self.model = presets.ModelPicker(settings.enhance_model(), settings.llm_url, self.status_label.setText)
        form.addRow("LLM のモデル", self.model)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addSpacing(6)

        self.editor = presets.PresetEditor(
            "エンハンサーのプリセット (生成 AI のモデルごとに登録します)",
            load_presets(), PRESETS_FILE, PROMPT_MARK,
            "指示文 (system) — モデルごとに推奨されている指示文を貼ります",
            f"送る文の雛形 (user) — {PROMPT_MARK} が説明プロンプトに置き換わります",
            f"ふつうは {PROMPT_MARK} だけで十分です。{PROMPT_MARK} がないときは、雛形のあとに説明プロンプトを付けて送ります。",
            "高いほど毎回違う盛り方になります (目安 0.5〜1.0)",
            self._swapped, index,
        )
        layout.addWidget(self.editor, 1)

        ok_cancel = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_cancel.accepted.connect(self._accept)
        ok_cancel.rejected.connect(self.reject)
        layout.addWidget(ok_cancel)

    @staticmethod
    def _swapped(a: int, b: int):
        presets.swap_index(GENERATE_FILE, a, b)
        presets.swap_index(PANEL_FILE, a, b)

    def _accept(self):
        if self.model.model():
            settings.set_enhance_model(self.model.model())
        self.accept()
