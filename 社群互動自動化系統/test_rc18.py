import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace

import 圖形介面
import 日誌 as logger_module


base = Path(__file__).resolve().parent
assert (base / "VERSION.txt").read_text(encoding="utf-8").strip().endswith(("RC18", "RC19"))

task_source = (base / "notification_task.py").read_text(encoding="utf-8")
parser_source = (base / "notification_parser.py").read_text(encoding="utf-8")
assert "aria-pressed" in task_source
assert "control.click()" in task_source
assert "clickable.click()" not in task_source
assert parser_source.count("minarkahan bilang nabasa") == 2
assert parser_source.count("minarkahan bilang hindi pa nababasa") == 2
assert "'bago'" in parser_source


class FakeListbox:
    def curselection(self):
        return (0, 2)

    def index(self, _which):
        return 2


fake_app = SimpleNamespace(
    list=FakeListbox(),
    filtered=[
        SimpleNamespace(name="888"),
        SimpleNamespace(name="889"),
        SimpleNamespace(name="900"),
    ],
)
ordered = gui.App.selected(fake_app)
assert [profile.name for profile in ordered] == ["888", "900"]


old_log_dir = logger_module.LOG_DIR
old_profile_dir = logger_module.PROFILE_DIR
with tempfile.TemporaryDirectory() as temp_dir:
    logger_module.LOG_DIR = Path(temp_dir)
    logger_module.PROFILE_DIR = Path(temp_dir) / "profiles"
    logger_module.PROFILE_DIR.mkdir()
    received = []
    monitor = logger_module.setup_logger(received.append)
    profile, _ = logger_module.profile_logger("900")
    profile.info("profile-log-visible-in-gui")
    assert any("profile-log-visible-in-gui" in line for line in received)
    assert any(
        path.stat().st_size > 0
        for path in logger_module.LOG_DIR.glob("monitor_*.log")
    )
    assert any(
        path.stat().st_size > 0
        for path in logger_module.PROFILE_DIR.glob("*.log")
    )
    for active_logger in (profile, monitor):
        for handler in list(active_logger.handlers):
            handler.close()
            active_logger.removeHandler(handler)
logger_module.LOG_DIR = old_log_dir
logger_module.PROFILE_DIR = old_profile_dir

# Do not leave handlers from this standalone test attached to global logging.
logging.shutdown()

print("RC18 unread, ordering, and GUI log tests passed")
