from __future__ import annotations

import tempfile
from pathlib import Path

from 多行文字 import load_text_blocks, random_text_block
from 短影音 import read_description


def test_rc19_multiline_blocks() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "captions.txt"
        path.write_text(
            "# comment\n第一行 😊\n第二行\n\n第四行 👍\n---\n另一篇 🚀\n第二行 ✨\n",
            encoding="utf-8",
        )
        expected = ["第一行 😊\n第二行\n\n第四行 👍", "另一篇 🚀\n第二行 ✨"]
        assert load_text_blocks(path) == expected
        selected, count = random_text_block(path)
        assert count == 2 and selected in expected
        # Reels no longer maps profile suffix to a fixed text line.
        assert read_description(str(path), 999) in expected


def test_rc19_legacy_one_caption_per_line() -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "captions.txt"
        path.write_text("# ignored\n 第一篇 😊 \n\n第二篇  內容\n", encoding="utf-8")
        assert load_text_blocks(path) == ["第一篇 😊", "第二篇 內容"]


def test_wiring_and_unicode_input() -> None:
    root = Path(__file__).resolve().parent
    behavior = (root / "behavior.py").read_text(encoding="utf-8")
    gui = (root / "gui.py").read_text(encoding="utf-8")
    main = (root / "main.py").read_text(encoding="utf-8")
    assert "ord(ch) <= 0xFFFF" not in behavior
    assert "copy_to_windows_clipboard(safe_content)" in behavior
    assert "generate_filipino_post(post_text_file)" in behavior
    assert "post_text_file=self.post_text_file.get().strip()" in gui
    assert "post_text_file=gui_settings.post_text_file" in main


if __name__ == "__main__":
    test_rc19_multiline_blocks()
    test_rc19_legacy_one_caption_per_line()
    test_wiring_and_unicode_input()
    print("random multiline caption tests passed")
