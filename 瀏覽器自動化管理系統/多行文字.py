"""RC19-compatible random multiline text blocks and Unicode clipboard."""
from __future__ import annotations

import random
import time
from pathlib import Path


BLOCK_SEPARATOR = "---"


def _clean_block(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(line.rstrip() for line in lines).strip()


def load_text_blocks(path: str | Path) -> list[str]:
    """Load captions using RC19 rules.

    Without a separator, every non-empty, non-comment line is one caption.
    With a line containing only ``---``, every separated block is one caption;
    internal newlines, blank lines and emoji are preserved.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"文字檔不存在：{source}")
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"文字檔必須使用 UTF-8 編碼：{source}") from exc

    if not any(line.strip() == BLOCK_SEPARATOR for line in lines):
        usable = [
            " ".join(line.split())
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
    else:
        usable: list[str] = []
        current: list[str] = []
        for line in lines:
            if line.strip() == BLOCK_SEPARATOR:
                block = _clean_block(current)
                if block:
                    usable.append(block)
                current = []
                continue
            if line.lstrip().startswith("#"):
                continue
            current.append(line)
        block = _clean_block(current)
        if block:
            usable.append(block)

    if not usable:
        raise RuntimeError(f"文字檔沒有可用文案：{source}")
    return usable


def random_text_block(path: str | Path) -> tuple[str, int]:
    blocks = load_text_blocks(path)
    return random.choice(blocks), len(blocks)


def copy_to_windows_clipboard(text: str) -> None:
    """Place multiline/non-BMP Unicode text on the Windows clipboard."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = pointer
    kernel32.GlobalLock.argtypes = (pointer,)
    kernel32.GlobalLock.restype = pointer
    kernel32.GlobalUnlock.argtypes = (pointer,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = (pointer,)
    kernel32.GlobalFree.restype = pointer
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = (wintypes.UINT, pointer)
    user32.SetClipboardData.restype = pointer
    user32.CloseClipboard.restype = wintypes.BOOL

    data = (str(text) + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(0x0002, len(data))
    if not handle:
        raise OSError("GlobalAlloc 失敗")
    locked = kernel32.GlobalLock(handle)
    if not locked:
        kernel32.GlobalFree(handle)
        raise OSError("GlobalLock 失敗")
    try:
        ctypes.memmove(locked, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)

    opened = False
    for _ in range(10):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.03)
    if not opened:
        kernel32.GlobalFree(handle)
        raise OSError("OpenClipboard 失敗")
    try:
        if not user32.EmptyClipboard():
            raise OSError("EmptyClipboard 失敗")
        if not user32.SetClipboardData(13, handle):
            raise OSError("SetClipboardData 失敗")
        handle = None
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)
