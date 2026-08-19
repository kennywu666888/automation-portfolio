from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".avi", ".mkv", ".3gp"}
MEDIA_EXTENSIONS = PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
MEDIA_MODES = {"none", "random", "fixed"}


def _media_files(folder: str | Path, extensions: set[str]) -> list[Path]:
    root = Path(folder).expanduser()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"找不到媒體資料夾：{root}")
    files = sorted(
        (
            path.resolve()
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix.casefold() in extensions
            and path.stat().st_size > 0
        ),
        key=lambda path: path.name.casefold(),
    )
    return files


@dataclass
class MediaPool:
    mode: str
    files: list[Path]

    @classmethod
    def from_settings(cls, settings: dict) -> "MediaPool":
        mode = str(settings.get("group_comment_media_mode", "none")).strip().lower()
        # Compatibility for the first local RC19 media prototype.
        if mode in {"photo", "video"}:
            mode = "random"
        if mode not in MEDIA_MODES:
            mode = "none"
        if mode == "none":
            return cls(mode, [])

        if mode == "random":
            folder = (
                settings.get("group_comment_random_media_dir")
                or settings.get("group_comment_photo_dir")
                or (Path.home() / "Desktop" / "view")
            )
            files = _media_files(folder, MEDIA_EXTENSIONS)
            if not files:
                raise RuntimeError("隨機媒體資料夾沒有可用的相片或影片")
            random.shuffle(files)
            return cls(mode, files)

        fixed_value = str(settings.get("group_comment_fixed_media_file") or "").strip()
        if not fixed_value:
            raise RuntimeError("固定相片／影片模式尚未選取媒體檔案")
        fixed_path = Path(fixed_value).expanduser().resolve()
        if not fixed_path.is_file() or fixed_path.stat().st_size <= 0:
            raise FileNotFoundError(f"固定媒體檔不存在或為空：{fixed_path}")
        if fixed_path.suffix.casefold() not in MEDIA_EXTENSIONS:
            raise RuntimeError(f"固定媒體格式不支援：{fixed_path.suffix}")
        return cls(mode, [fixed_path])

    @property
    def photo_count(self) -> int:
        return sum(media_kind(path) == "photo" for path in self.files)

    @property
    def video_count(self) -> int:
        return sum(media_kind(path) == "video" for path in self.files)

    def claim(self) -> Optional[Path]:
        if self.mode == "none":
            return None
        if not self.files:
            raise RuntimeError("本次執行的媒體檔案已用完")
        if self.mode == "fixed":
            return self.files[0]
        return self.files.pop()


def media_kind(path: str | Path | None) -> str:
    if not path:
        return "none"
    suffix = Path(path).suffix.casefold()
    if suffix in PHOTO_EXTENSIONS:
        return "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return "unknown"
