"""ImageMimicArt の、共通部品 (gencommon) に渡すアプリごとの決まり。

出来たファイルは「出力フォルダ\\(親フォルダ名)_(元画像のフォルダ名)\\(元の名前)_mimic.png」に保存する。
"""

from pathlib import Path

from gencommon import app, comfy

from . import theme

APP_NAME = "ImageMimicArt"
OUTPUT_SUFFIX = "_mimic"  # 出来たファイルの名前 = (元の名前)_mimic.png
OUTPUT_HINT = "生成した画像は「出力フォルダ\\(親フォルダ名)_(元画像のフォルダ名)\\(元の名前)_mimic.png」に保存します。"


def output_dir_for(source: str, root: str) -> Path:
    """元画像の出力先: 出力フォルダ\\(親フォルダ名)_(元画像のフォルダ名)。
    別の場所にある同じ名前のフォルダ (grok-image\\all と other\\all など) が混ざらないように、親の名前もつなげる。
    ドライブや共有の直下に近いときは、名前のない段の代わりに「D」「RAVEN_SharBox」のような名前を使う。"""

    def label(folder: Path) -> str:
        return folder.name or "_".join(part for part in folder.anchor.replace(":", "").split("\\") if part) or "root"

    parent = Path(source).parent
    if not parent.name:
        return Path(root) / label(parent)  # ドライブや共有の直下の画像
    return Path(root) / f"{label(parent.parent)}_{parent.name}"


def _output_path(source: Path, request, is_video: bool, ext: str) -> Path:
    if not request.output_dir:
        raise comfy.ComfyError("出力フォルダが決まっていません。「生成の設定」で出力フォルダを指定してください。")
    return output_dir_for(str(source), request.output_dir) / (source.stem + OUTPUT_SUFFIX + ext)


PROFILE = app.Profile(
    name=APP_NAME,
    reads=True,
    source_word="元画像",
    unit="枚",
    prompt_word="説明プロンプト",
    prompt_where="右の枠",
    column="元画像",
    output_path=_output_path,
    workflow_hint=(
        "元画像から読み取った説明プロンプト (盛ったときは盛ったもの) がワークフローのプロンプトに入ります "
        f"(文字欄に「{comfy.PROMPT_MARKER}」があればそこ。なければ prompt 入力、CLIPTextEncode の順。"
        "複数あるときはタイトルに Positive とあるもの)。"
        "プロンプトを入れる場所のないワークフローは登録できません。「Load Image」ノードがあれば元画像も送ります。"
        "seed は毎回ランダムになります。大きさはワークフローに書かれたままです。"
    ),
    output_folder=True,
    output_hint=OUTPUT_HINT,
    open_result_label="生成が終わったら、出来たファイルを元画像と並べて見せる (まとめて生成のときは見せません)",
    open_on_done=False,  # 並べて見せるのは画面が job_done で受ける
    colors=app.Colors(ok=theme.OK_COLOR, error=theme.ERROR_COLOR, hint=theme.HINT_COLOR,
                      paused="#ff9800", separator="#555555"),
)


def setup() -> list[str]:
    """起動したときに呼ぶ。戻り値は、共有の設定に移したときの知らせ。"""
    return app.setup(PROFILE)
