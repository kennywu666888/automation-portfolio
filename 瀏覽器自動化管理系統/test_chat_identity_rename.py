from pathlib import Path

from 聊天室查詢任務 import ChatQueryTask
from 聊天室回覆任務 import ChatReplyTask
from 訊息選擇器 import (
    chat_muted_profile_name,
    has_chat_identity_restriction,
    suppress_messenger_restore_prompts,
)


IDENTITY_TEXT = "Confirm your identity to send messages"


class FakeBody:
    text = IDENTITY_TEXT


class FakeDriver:
    current_url = "https://www.facebook.com/messages"

    def get(self, url):
        self.current_url = url

    def find_element(self, _by, _value):
        return FakeBody()


class DummyRepository:
    pass


class FakeRestoreDriver:
    def execute_script(self, _script):
        return {"hiddenDialogs": 2, "hiddenVeils": 1, "visibleRestore": 0}


assert has_chat_identity_restriction(IDENTITY_TEXT)
assert has_chat_identity_restriction("確認身分才能傳送訊息")
assert chat_muted_profile_name("私訊891", "id") == "聊天室禁言私訊891"
assert (
    chat_muted_profile_name("聊天室禁言私訊891", "id")
    == "聊天室禁言私訊891"
)
assert suppress_messenger_restore_prompts(FakeRestoreDriver()) == {
    "hidden_dialogs": 2,
    "hidden_veils": 1,
    "visible_restore": 0,
}

query_names = []
query = ChatQueryTask(
    FakeDriver(),
    DummyRepository(),
    profile_id="k1ejskdj",
    profile_name="私訊891",
    rename_callback=lambda name: query_names.append(name) or True,
)
query_result = query.run()
assert query_result.status == "restricted"
assert query_result.restricted_count == 1
assert query_result.detail == IDENTITY_TEXT
assert query_names == ["聊天室禁言私訊891"]

reply_names = []
reply = ChatReplyTask(
    FakeDriver(),
    DummyRepository(),
    profile_id="k1ejskdj",
    profile_name="私訊891",
    text_file="unused.txt",
    rename_callback=lambda name: reply_names.append(name) or True,
)
assert reply._rename_chat_identity_restricted(IDENTITY_TEXT)
assert reply_names == ["聊天室禁言私訊891"]
assert reply._rename_chat_identity_restricted(IDENTITY_TEXT)
assert reply_names == ["聊天室禁言私訊891"]

base = Path(__file__).parent
main_source = (base / "main.py").read_text(encoding="utf-8")
reply_source = (base / "chat_reply_task.py").read_text(encoding="utf-8")
selector_source = (base / "messenger_selectors.py").read_text(encoding="utf-8")
assert main_source.count(
    "if has_chat_identity_restriction(task_result.detail):"
) == 2
assert 'ctrl, profile.name, "聊天室禁言更名後", stop_event' in main_source
assert "summary.finish(success=False, reason=reason)" in main_source
assert "環境已標記聊天室禁言，未執行回覆" in reply_source
assert "data-codex-restore-hidden" in selector_source
assert "data-codex-restore-veil" in selector_source
assert "don't restore messages" in selector_source.lower()

print("Chat identity restriction rename tests passed")
