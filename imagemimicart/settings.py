"""画面まわりの設定 (前回のフォルダ・並び順・ツリーの幅・窓の位置など)。

ui-standard の 15 章どおり、%APPDATA%\\ImageMimicArt\\ に設定 1 つにつき小さなテキストファイル 1 つで置く。
読めない・壊れているときは既定値で動かす。

LLM の接続 (サーバーURL・モデル・送る画像の大きさ) は 3 つのアプリで共有するので、gencommon.settings にある
(ここからも同じ名前で呼べる)。
"""

from typing import Optional

from gencommon import store
from gencommon.settings import (  # noqa: F401 - 画面から settings.llm_url() などで呼ぶ
    DEFAULT_MAX_SIDE,
    enhance_model,
    llm_url,
    max_side,
    reader_model,
    set_enhance_model,
    set_llm_url,
    set_max_side,
    set_reader_model,
)


def get(name: str, default: str = "") -> str:
    value = store.load(name)
    return default if value is None else value


def put(name: str, value) -> None:
    store.save(name, str(value))


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
