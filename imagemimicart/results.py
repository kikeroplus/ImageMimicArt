"""元画像と、そこから生成した画像の結び付け。サムネール・ごみ箱・エクスプローラーの小道具。

出来たファイルは出力フォルダにあるので、結び付けは生成の記録 (comfy_history.txt) を正にする。
記録のないものは「出力フォルダ\\(親フォルダ名)_(元画像のフォルダ名)\\(元の名前)_mimic*.png」の名前で拾う。
記録に残っている説明プロンプトと今の説明プロンプトを比べ、前の説明で作ったものには印を付ける。
PromptWordCrafter の results.py を引き継いだもの。
"""

import ctypes
import os
import re
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader

from gencommon import comfy

from . import profile

MOVIE_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".mkv"}
MEDIA_EXTS = MOVIE_EXTS | {".png", ".jpg", ".jpeg", ".webp", ".gif"}


@dataclass
class ResultFile:
    path: Path
    is_video: bool
    old: bool  # 今の説明プロンプトとは違う (前の) 説明で生成した
    time: float  # 並べ替え用 (更新日時)
    record: Optional[comfy.GenRecord] = None


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def same_path(a, b) -> bool:
    return _key(a) == _key(b)


def _normalize(text: str) -> str:
    """改行と空白の揺れをそろえる (NBSP→空白、各種改行→LF)。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return text.replace(" ", "\n").replace(" ", "\n").replace(" ", " ").strip()


def _same_text(a: str, b: str) -> bool:
    return _normalize(a) == _normalize(b)


def _name_pattern(stem: str) -> re.Pattern:
    """その元画像の名前で保存した結果 (「名前_mimic (2).png」など)。"""
    suffix = re.escape(profile.OUTPUT_SUFFIX)
    return re.compile(rf"^{re.escape(stem)}{suffix}(?: \(\d+\))?\.[^.]+$", re.IGNORECASE)


def expected_dir(source: Path) -> Optional[Path]:
    """今の出力フォルダでの、その元画像の出力先 (出力フォルダが決まっていなければ None)。"""
    root = comfy.output_folder()
    return profile.output_dir_for(str(source), root) if root else None


def find_results(source: Path, caption: Optional[str], records: Optional[list] = None) -> list[ResultFile]:
    """その元画像から生成したファイル (新しい順)。

    - 記録の元画像がこの画像のもの
    - 元画像がもう見つからない記録で、出力先がこの画像の出力先と同じで、送った説明が今の説明と同じもの
      (エクスプローラーで元画像の名前を変えたときの保険)
    - 記録はないが、出力先に「(元の名前)_mimic」で始まる名前であるもの
    caption は今の説明プロンプト (まだなければ None。「旧」の印は付けない)。
    """
    if records is None:
        records = comfy.read_history()
    source_key = _key(source)
    out_dir = expected_dir(source)
    found: dict[str, ResultFile] = {}

    for record in reversed(records):  # 新しい記録を優先
        output = record.output
        if not output:
            continue
        key = _key(output)
        if key in found:
            continue
        mine = bool(record.source) and _key(record.source) == source_key
        if not mine:
            # 元画像があるかの確認 (exists) は最後に回す。記録の元がつながっていない
            # ネットワークドライブなどにあると 1 回ごとに待たされ、画像を選ぶたびに遅くなる
            mine = (
                bool(record.source)
                and out_dir is not None
                and caption is not None
                and record.request.prompt is not None
                and _key(Path(output).parent) == _key(out_dir)
                and _same_text(record.request.prompt, caption)
                and not Path(record.source).exists()
            )
        if not mine:
            continue
        path = Path(output)
        if not path.is_file():
            continue
        old = (caption is not None and record.request.prompt is not None
               and not _same_text(record.request.prompt, caption))
        found[key] = ResultFile(path, _is_video(path), old, _mtime(path), record)

    if out_dir is not None:
        try:
            pattern = _name_pattern(source.stem)
            for path in out_dir.iterdir():
                if path.suffix.lower() in MEDIA_EXTS and pattern.match(path.name) and _key(path) not in found:
                    found[_key(path)] = ResultFile(path, _is_video(path), False, _mtime(path))
        except OSError:
            pass

    return sorted(found.values(), key=lambda r: r.time, reverse=True)


def result_counts(sources: list[Path]) -> dict[str, int]:
    """一覧の印に出す、元画像ごとの生成結果の数 ({_key(元画像): 数}。0 のものは入れない)。

    フォルダを開いたときに裏のスレッドで呼ぶ。記録の出来たファイルがまだあるかと、
    出力先にある「(元の名前)_mimic*」を数える。
    """
    if not sources:
        return {}
    wanted = {_key(s): s for s in sources}
    outputs: dict[str, set] = {}
    for record in comfy.read_history():
        if record.source and record.output:
            key = _key(record.source)
            if key in wanted:
                outputs.setdefault(key, set()).add(_key(record.output))
    for key, paths in outputs.items():
        outputs[key] = {p for p in paths if os.path.isfile(p)}  # 消したものは数えない
    out_dir = expected_dir(sources[0])  # 一覧は同じフォルダの画像なので、出力先も同じ
    if out_dir is not None:
        try:
            names = [p for p in out_dir.iterdir() if p.suffix.lower() in MEDIA_EXTS]
        except OSError:
            names = []
        if names:
            by_stem: dict[str, list[str]] = {}
            for key, source in wanted.items():
                by_stem.setdefault(source.stem.lower(), []).append(key)
            suffix = profile.OUTPUT_SUFFIX.lower()
            for path in names:
                lower = path.stem.lower()
                cut = lower.rfind(suffix)
                if cut <= 0:
                    continue
                for key in by_stem.get(lower[:cut], []):
                    if _name_pattern(wanted[key].stem).match(path.name):
                        outputs.setdefault(key, set()).add(_key(path))
    return {key: len(paths) for key, paths in outputs.items() if paths}


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in MOVIE_EXTS


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ----------------------------------------------------------------------
# エクスプローラー・ごみ箱
# ----------------------------------------------------------------------
def show_in_explorer(path: Path) -> None:
    """そのファイルを選択した状態でフォルダを開く。"""
    subprocess.Popen(["explorer", "/select,", str(path)])


def can_recycle(path: Path) -> bool:
    """ごみ箱に入るか (ネットワーク上のファイルは入らずに消える)。"""
    text = str(path)
    if text.startswith("\\\\"):
        return False
    drive = os.path.splitdrive(os.path.abspath(text))[0] + "\\"
    try:
        return ctypes.windll.kernel32.GetDriveTypeW(drive) != 4  # DRIVE_REMOTE
    except (AttributeError, OSError):
        return True


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def send_to_recycle_bin(paths: list[Path]) -> Optional[str]:
    """ごみ箱へ送る。失敗したときは説明を返す (うまくいけば None)。"""
    if not paths:
        return None
    op = _SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = "".join(str(p) + "\0" for p in paths) + "\0"
    # FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
    op.fFlags = 0x0004 | 0x0010 | 0x0040 | 0x0400
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        return f"削除できませんでした (コード 0x{result:X})"
    if op.fAnyOperationsAborted:
        return "削除が中断されました"
    return None


# ----------------------------------------------------------------------
# サムネール (エクスプローラーと同じ Windows のサムネールを使う。動画もこれで取れる)
# ----------------------------------------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD),
                ("bmBitsPixel", wintypes.WORD), ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


_IID_IShellItemImageFactory = "{bcc18b79-ba16-442f-80c4-8a59c30c463b}"
_GetImageProto = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, _SIZE, ctypes.c_int,
                                    ctypes.POINTER(wintypes.HBITMAP))
_ReleaseProto = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def init_thread_com() -> None:
    """サムネールを作るスレッドで最初に 1 回呼ぶ。"""
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    except (AttributeError, OSError):
        pass


def _shell_thumbnail(path: Path, size: int) -> Optional[QImage]:
    try:
        shell32 = ctypes.windll.shell32
        gdi32 = ctypes.windll.gdi32
    except AttributeError:
        return None
    factory = ctypes.c_void_p()
    iid = _guid(_IID_IShellItemImageFactory)
    shell32.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                    ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    if shell32.SHCreateItemFromParsingName(str(path), None, ctypes.byref(iid), ctypes.byref(factory)) != 0:
        return None
    vtable = ctypes.cast(factory, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    hbitmap = wintypes.HBITMAP()
    try:
        get_image = _GetImageProto(vtable[3])
        try:
            get_image(factory, _SIZE(size, size), 0, ctypes.byref(hbitmap))  # SIIGBF_RESIZETOFIT
        except OSError:
            return None
    finally:
        _ReleaseProto(vtable[2])(factory)
    if not hbitmap:
        return None
    try:
        bmp = _BITMAP()
        gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
        if not gdi32.GetObjectW(hbitmap, ctypes.sizeof(bmp), ctypes.byref(bmp)):
            return None
        width, height = bmp.bmWidth, abs(bmp.bmHeight)
        header = _BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth = width
        header.biHeight = -height  # 上から下へ
        header.biPlanes = 1
        header.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        dc = ctypes.windll.user32.GetDC(None)
        try:
            gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
            lines = gdi32.GetDIBits(dc, hbitmap, 0, height, buffer, ctypes.byref(header), 0)
        finally:
            ctypes.windll.user32.ReleaseDC(None, dc)
        if lines != height:
            return None
        raw = buffer.raw
        # アルファがすべて 0 なら不透明の画像として扱う (動画のサムネールなど)
        has_alpha = any(raw[i] for i in range(3, len(raw), 4 * 97))
        fmt = QImage.Format.Format_ARGB32_Premultiplied if has_alpha else QImage.Format.Format_RGB32
        return QImage(raw, width, height, width * 4, fmt).copy()
    finally:
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.DeleteObject(hbitmap)


def make_thumbnail(path: Path, size: int) -> Optional[QImage]:
    """size × size に収まるサムネール。画像は自分で読み、読めないもの・動画は Windows に頼む。"""
    if not _is_video(path):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid() and original.width() > 0 and original.height() > 0:
            reader.setScaledSize(original.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if not image.isNull():
            return image
    image = _shell_thumbnail(path, size)
    if image is not None and (image.width() > size or image.height() > size):
        image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return image


def load_preview(path: Path, width: int, height: int) -> tuple[Optional[QImage], Optional[QSize]]:
    """width × height に収まる大きさで読む (元より大きくはしない)。元の大きさも返す (動画は None)。"""
    if not _is_video(path):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        original = reader.size()
        if original.isValid() and original.width() > 0 and original.height() > 0:
            if original.width() > width or original.height() > height:
                reader.setScaledSize(original.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if not image.isNull():
                return image, original
    return make_thumbnail(path, min(width, height)), None
