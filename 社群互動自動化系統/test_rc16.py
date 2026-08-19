import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import Telegram回報
from 留言回覆讀取 import ReplyData
from 診斷工具 import mask
from notification_repository import NotificationRepository
from notification_task import NotificationTask
from 任務結果 import TaskResult
from Telegram回報 import TelegramReporter, TelegramTargets


candidate = SimpleNamespace(
    key="notification-1",
    text="Customer replied",
    url="https://www.facebook.com/notification-1",
)
reply = ReplyData(
    reply_user="Customer",
    reply_text="Hello",
    original_comment="Original",
    post_author="Author",
    notification_time="Now",
    facebook_url="https://www.facebook.com/reply-1",
)

with tempfile.TemporaryDirectory() as folder:
    repo = NotificationRepository(Path(folder) / "history.db")
    repo.mark(
        "profile-1",
        "Profile",
        candidate,
        "facebook_replied",
        reply,
        reply_sent=True,
        sent_reply_text="Thanks",
        incoming_telegram_sent=True,
    )
    # A later reporting failure must not clear the persisted Facebook state.
    repo.mark(
        "profile-1",
        "Profile",
        candidate,
        "failed",
        error="Telegram unavailable",
    )
    state = repo.get_state(candidate.key)
    assert state["facebook_reply_sent"] == 1
    assert state["incoming_telegram_sent"] == 1
    assert state["sent_reply_text"] == "Thanks"
    assert state["reply_user"] == "Customer"
    assert len(repo.pending_replied("profile-1")) == 1

    class Reporter:
        def __init__(self):
            self.replied = []

        def send_replied(self, profile, stored_reply, reply_text, retries=3):
            self.replied.append((profile, stored_reply.reply_user, reply_text, retries))
            return True

    class Logger:
        def info(self, *_args):
            pass

        def exception(self, *_args):
            pass

    reporter = Reporter()
    task = NotificationTask(
        None,
        SimpleNamespace(profile_id="profile-1", name="Profile"),
        {},
        repo,
        reporter,
        Logger(),
        threading.Event(),
    )
    result = TaskResult("SKIPPED")
    attempted = task._retry_pending_reports(result)
    assert attempted == {candidate.key}
    assert reporter.replied == [("Profile", "Customer", "Thanks", 3)]
    assert result.reported == 1 and result.failed == 0
    assert repo.is_reported(candidate.key)
    assert repo.pending_replied("profile-1") == []


secret = "123456:TEST_SECRET"
original_post = telegram_reporter.requests.post


def failing_post(url, **_kwargs):
    raise RuntimeError(f"request failed for {url}")


telegram_reporter.requests.post = failing_post
try:
    reporter = TelegramReporter(secret, TelegramTargets("1", "2"), True)
    try:
        reporter.send_incoming("Profile", reply, retries=1)
        raise AssertionError("Expected Telegram failure")
    except RuntimeError as exc:
        message = str(exc)
        assert secret not in message
        assert "bot***/sendMessage" in message
finally:
    telegram_reporter.requests.post = original_post

assert secret not in mask(f"https://api.telegram.org/bot{secret}/sendMessage")
print("RC16 safety and idempotency tests passed")
