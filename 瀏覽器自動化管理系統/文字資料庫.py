"""UTF-8 TXT 文案庫；忽略空白行，文一與文二由呼叫端分開指定。"""

from __future__ import annotations

import random
from pathlib import Path


class TextLibrary:
    def __init__(self, file_path: str | Path, label: str = "文案"):
        self.file_path = Path(file_path)
        self.label = label
        self.lines: list[str] = []
        self.reload()

    def reload(self) -> None:
        if not self.file_path.is_file():
            raise FileNotFoundError(f"找不到{self.label}檔案：{self.file_path}")
        text = self.file_path.read_text(encoding="utf-8-sig")
        self.lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not self.lines:
            raise ValueError(f"{self.label}沒有可用內容：{self.file_path}")

    def random_text(self) -> str:
        return random.choice(self.lines)
