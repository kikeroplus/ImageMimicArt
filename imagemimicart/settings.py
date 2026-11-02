"""画面まわりの設定 (前回のフォルダ・並び順・ツリーの幅・窓の位置など) と、LLM の接続先。

ui-standard の 15 章どおり、%APPDATA%\\ImageMimicArt\\ に設定 1 つにつき小さなテキストファイル 1 つで置く。
読めない・壊れているときは既定値で動かす。

初めて起動したときは、PromptWordCrafter の設定 (LLM のサーバーURL・モデル、エンハンサー、
生成の設定) を引き継ぐ。
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from . import comfy

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

DEFAULT_LLM_URL = "http://192.168.11.6:1234/v1"  # PromptWordCrafter の既定と同じ
DEFAULT_LLM_MODEL = "qwen2.5-14b-instruct-uncensored"
DEFAULT_MAX_SIDE = 1024  # 読み取りで画像を送る前に縮める大きさ (長い辺のピクセル数)

# 引き継ぎ元 (PromptWordCrafter は同じ WindowsApp フォルダに並べて置いている)
PWC_SETTINGS = APP_DIR.parent / "PromptWordCrafter" / "promptwordcrafter_settings.json"
PWC_DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "PromptWordCrafter"


def get(name: str, default: str = "") -> str:
    value = comfy.load(name)
    return default if value is None else value


def put(name: str, value) -> None:
    comfy.save(name, str(value))


def get_int(name: str, default: int, low: Optional[int] = None, high: Optional[int] = None) -> int:
    try:
        value = int(float(get(name)))
    except ValueError:
        return default
    if (low is not None and value < low) or (high is not None and value > high):
        return default
    return value


def get_ints(name: str, count: int) -> Optional[list[int]]:
    """「x,y,w,h」のような数の並び。数が合わなければ None。"""
    try:
        values = [int(float(v)) for v in get(name).split(",")]
    except ValueError:
        return None
    return values if len(values) == count else None


# ----------------------------------------------------------------------
# LLM (読み取りとエンハンサーで同じサーバーを使う。モデルは別々に選ぶ)
# ----------------------------------------------------------------------
def llm_url() -> str:
    return get("llm_url.txt") or DEFAULT_LLM_URL


def set_llm_url(url: str) -> None:
    put("llm_url.txt", url.strip())


def reader_model() -> str:
    return get("reader_model.txt") or DEFAULT_LLM_MODEL


def set_reader_model(model: str) -> None:
    put("reader_model.txt", model.strip())


def enhance_model() -> str:
    return get("enhance_model.txt") or reader_model()


def set_enhance_model(model: str) -> None:
    put("enhance_model.txt", model.strip())


def max_side() -> int:
    return get_int("reader_max_side.txt", DEFAULT_MAX_SIDE, 256, 4096)


def set_max_side(value: int) -> None:
    put("reader_max_side.txt", value)


# ----------------------------------------------------------------------
# 初めて起動したときの引き継ぎ
# ----------------------------------------------------------------------
_INHERIT_SKIP = {"comfy_history.txt", "comfy_jobs.json", "comfy_inherit.txt"}


def inherit_from_promptwordcrafter() -> list[str]:
    """ImageMimicArt の設定がまだ何もなければ、PromptWordCrafter の設定を写す。

    写すもの: LLM のサーバーURL とモデル、エンハンサーのプリセット、生成の設定
    (接続先・ワークフロー・ベースプロンプト・セット)。生成の記録とジョブは写さない。
    戻り値は写したものの説明 (何もしなかったら空)。
    """
    if comfy.DATA_DIR.exists() and any(comfy.DATA_DIR.iterdir()):
        return []
    done = []
    try:
        data = json.loads(PWC_SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        url = str(data.get("translate_url") or "")
        model = str(data.get("enhance_model") or data.get("translate_model") or "")
        if url:
            set_llm_url(url)
        if model:
            set_reader_model(model)
            set_enhance_model(model)
        if url or model:
            done.append("LLM のサーバーURL とモデル")
    if PWC_DATA_DIR.is_dir():
        comfy.DATA_DIR.mkdir(parents=True, exist_ok=True)
        copied = []
        for path in PWC_DATA_DIR.iterdir():
            name = path.name
            wanted = (name.startswith("comfy_") and name.endswith(".txt")) or name == "enhancer_presets.json"
            if not wanted or name in _INHERIT_SKIP or not path.is_file():
                continue
            try:
                shutil.copy2(path, comfy.DATA_DIR / name)
                copied.append(name)
            except OSError:
                pass
        if "enhancer_presets.json" in copied:
            done.append("エンハンサー")
        if any(name.startswith("comfy_") for name in copied):
            done.append("生成の設定")
    return done
