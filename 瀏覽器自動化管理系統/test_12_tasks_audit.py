"""Offline audit for all 12 task integrations. No AdsPower/Facebook login required."""
from __future__ import annotations
import ast, json, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TASKS = {
    "professional_mode": ["professional_mode.py"],
    "avatar": ["avatar_pin.py"],
    "messenger_pin": ["avatar_pin.py"],
    "confirm_friend": ["behavior.py"],
    "post": ["behavior.py", "facebook.py", "text_library.py", "文案.xlsx"],
    "reels": ["reels.py", "reels_settings.json", "reels_history.json"],
    "reels_comment": ["reels_comment.py", "reels_comment.txt"],
    "browse_like": ["behavior.py"],
    "add_friend": ["behavior.py"],
    "fanpage_message": ["fanpage_message_task.py", "kolurl.txt", "文二.txt"],
    "query_chats": ["chat_query_task.py", "messenger_selectors.py", "chat_repository.py"],
    "reply_chats": ["chat_reply_task.py", "chat_repository.py", "文一.txt"],
}

def assert_python_parses() -> None:
    for path in ROOT.glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

def assert_required_files() -> None:
    missing = []
    for files in TASKS.values():
        for name in files:
            if not (ROOT / name).exists():
                missing.append(name)
    assert not missing, f"Missing required files: {sorted(set(missing))}"

def assert_gui_and_main_hooks() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    gui = (ROOT / "gui.py").read_text(encoding="utf-8")
    assert "ReelsCommentTask" in main
    assert "enable_reels_comment" in main
    assert "reels_comment" in gui
    assert "十二項獨立任務設定" in gui

def assert_json() -> None:
    for name in ["reels_settings.json", "reels_history.json", "schedules.json"]:
        json.loads((ROOT / name).read_text(encoding="utf-8"))

def assert_db_init() -> None:
    path = ROOT / "_audit_chat_tasks.db"
    try:
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY)")
        conn.commit(); conn.close()
    finally:
        if path.exists(): path.unlink()

def assert_profile_only_reels_comment() -> None:
    text = (ROOT / "reels_comment.py").read_text(encoding="utf-8")
    assert "_open_reels_tab" not in text
    assert "facebook.com/reel/" not in text
    assert "FeedInteractor" in text and "try_like" in text

def assert_reels_audience_and_foreground_guards() -> None:
    reels = (ROOT / "reels.py").read_text(encoding="utf-8")
    browser = (ROOT / "browser.py").read_text(encoding="utf-8")
    assert '"tapos na"' in reels.casefold()
    assert "Audience 視窗底部結構" in reels
    assert "GetForegroundWindow" in browser
    assert "AttachThreadInput" in browser

if __name__ == "__main__":
    assert_python_parses()
    assert_required_files()
    assert_gui_and_main_hooks()
    assert_json()
    assert_db_init()
    assert_profile_only_reels_comment()
    assert_reels_audience_and_foreground_guards()
    print("12-task offline audit passed")
