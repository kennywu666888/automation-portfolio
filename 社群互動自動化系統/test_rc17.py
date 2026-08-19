from types import SimpleNamespace

import notification_task
from notification_parser import NotificationCandidate, click_candidate
from notification_task import NotificationTask, _open_unread_tab


class Logger:
    def info(self, *_args):
        pass

    def warning(self, *_args):
        pass


class SequenceDriver:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        return self.responses.pop(0)


class ClickElement:
    def __init__(self):
        self.clicks = 0

    def click(self):
        self.clicks += 1


logger = Logger()
disabled = SequenceDriver([])
assert _open_unread_tab(disabled, logger, False)
assert disabled.calls == []

already_selected = SequenceDriver([
    {"found": True, "clicked": False, "selected": True},
])
assert _open_unread_tab(already_selected, logger, True)

clicked_and_verified = SequenceDriver([
    {"found": True, "selected": False, "pressed": "false", "element": None},
])
unread_button = ClickElement()
clicked_and_verified.responses[0]["element"] = unread_button
clicked_and_verified.responses.append(
    {"found": True, "selected": True, "pressed": "true", "element": unread_button}
)
original_sleep = notification_task.time.sleep
notification_task.time.sleep = lambda _seconds: None
try:
    assert _open_unread_tab(clicked_and_verified, logger, True)
finally:
    notification_task.time.sleep = original_sleep
assert unread_button.clicks == 1
assert "aria-pressed" in clicked_and_verified.calls[0][0]

missing = SequenceDriver([
    {"found": False, "clicked": False, "selected": False},
])
assert not _open_unread_tab(missing, logger, True)

candidate = NotificationCandidate(
    text="Customer replied to your comment",
    url="https://www.facebook.com/notification",
    unread=True,
    kind="comment_reply",
)
candidate.section = "new"
candidate.occurrence = 1

not_unread = SequenceDriver([
    {"clicked": False, "reason": "not_unread_at_click"},
])
clicked, reason = click_candidate(not_unread, candidate, require_unread=True)
assert not clicked and reason == "not_unread_at_click"
script, args = not_unread.calls[0]
assert args[-1] is True
assert "mark as read" in script
assert "mark as unread" in script


class EmptyRepo:
    def pending_replied(self, _profile_id):
        return []


class FailClosedDriver:
    current_url = "https://www.facebook.com/notifications"

    def __init__(self):
        self.urls = []

    def get(self, url):
        self.urls.append(url)


driver = FailClosedDriver()
task = NotificationTask(
    driver,
    SimpleNamespace(profile_id="p1", name="Profile"),
    {"only_unread": True},
    EmptyRepo(),
    None,
    logger,
    SimpleNamespace(is_set=lambda: False),
)
original_open = notification_task._open_unread_tab
original_sleep = notification_task.time.sleep
notification_task._open_unread_tab = lambda *_args: False
notification_task.time.sleep = lambda _seconds: None
try:
    result = task.run()
finally:
    notification_task._open_unread_tab = original_open
    notification_task.time.sleep = original_sleep
assert result.status == "SKIPPED"
assert result.reported == 0
assert "Unread" in result.issues[0]

print("RC17 strict unread tests passed")
