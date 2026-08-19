from __future__ import annotations

import tempfile
from pathlib import Path

from 圖形介面 import SettingsWindow
from 媒體來源 import MediaPool, media_kind


def test_random_and_fixed_media_match_rc19() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        nested = root / "nested"
        nested.mkdir()
        photo = root / "one.jpg"
        video = nested / "two.mp4"
        ignored = root / "ignored.txt"
        photo.write_bytes(b"photo")
        video.write_bytes(b"video")
        ignored.write_text("ignored", encoding="utf-8")

        pool = MediaPool.from_settings({
            "post_media_mode": "random",
            "post_random_media_dir": str(root),
        })
        assert pool.photo_count == 1
        assert pool.video_count == 1
        claimed = {pool.claim(), pool.claim()}
        assert claimed == {photo.resolve(), video.resolve()}

        fixed = MediaPool.from_settings({
            "post_media_mode": "fixed",
            "post_fixed_media_file": str(video),
        })
        assert fixed.claim() == video.resolve()
        assert fixed.claim() == video.resolve()
        assert media_kind(fixed.claim()) == "video"


def test_gui_settings_backward_compatible() -> None:
    old = SettingsWindow._settings_from_dict({})
    assert old.post_media_enabled is False
    assert old.post_media_mode == "random"
    new = SettingsWindow._settings_from_dict({
        "post_media_enabled": True,
        "post_media_mode": "fixed",
        "post_fixed_media_file": "C:/media/test.mp4",
    })
    assert new.post_media_enabled is True
    assert new.post_media_mode == "fixed"
    encoded = SettingsWindow._settings_to_dict(new)
    assert encoded["post_media_enabled"] is True
    assert encoded["post_media_mode"] == "fixed"


def test_post_media_wiring() -> None:
    root = Path(__file__).resolve().parent
    gui = (root / "gui.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    behavior = (root / "behavior.py").read_text(encoding="utf-8")
    assert "加相片／影片" in gui
    assert "相片／影片隨機" in gui and "固定相片／影片" in gui
    assert "post_media_enabled=gui_settings.post_media_enabled" in main
    assert "_attach_post_media(editor, media_path)" in behavior
    assert "upload_input.send_keys(str(path))" in behavior
    assert "處理逾時" in behavior


if __name__ == "__main__":
    test_random_and_fixed_media_match_rc19()
    test_gui_settings_backward_compatible()
    test_post_media_wiring()
    print("post photo/video tests passed")
