"""不需登入 Facebook 的核心回歸測試。"""

from pathlib import Path
from tempfile import TemporaryDirectory

from 聊天室資料庫 import ChatRepository, make_message_hash
from 文字資料庫 import TextLibrary

try:
    from 訊息選擇器 import chat_id_from_url, chat_item_name, is_chat_url
except ModuleNotFoundError:
    chat_id_from_url = chat_item_name = is_chat_url = None


class FakeChatItem:
    def __init__(self, text="", aria_label="", title=""):
        self.text = text
        self.values = {"aria-label": aria_label, "title": title}

    def get_attribute(self, name):
        return self.values.get(name, "")


def run() -> None:
    if is_chat_url is not None:
        assert is_chat_url("https://www.facebook.com/messages/t/123")
        assert is_chat_url("https://www.facebook.com/messages/e2ee/t/456")
        assert not is_chat_url("https://www.facebook.com/messages")
        assert chat_id_from_url("https://www.facebook.com/messages/e2ee/t/456") == "456"
        assert chat_item_name(
            FakeChatItem("", "Messenger", ""),
            "https://www.facebook.com/messages/t/123",
        ) == "聊天室 123"
        assert chat_item_name(
            FakeChatItem("", "客戶名稱", ""),
            "https://www.facebook.com/messages/t/123",
        ) == "客戶名稱"

    with TemporaryDirectory() as folder:
        db = ChatRepository(Path(folder) / "jobs.db")
        args = dict(
            profile_id="profile-1", profile_name="測試", chat_id="chat-1",
            chat_name="客戶", chat_url="https://facebook.com/messages/t/chat-1",
            message_text="Hello", is_unread=True, max_retries=2,
        )
        job_id, created = db.enqueue(**args)
        same_id, duplicate_created = db.enqueue(**args)
        assert created and not duplicate_created and job_id == same_id
        jobs = db.claim("profile-1", 1, "worker-1")
        assert len(jobs) == 1
        assert db.mark_replied(jobs[0], "Reply")
        assert db.counts() == {"replied": 1}

        failed_args = dict(args)
        failed_args.update(chat_id="chat-2", chat_url="u2", message_text="New")
        db.enqueue(**failed_args)
        failed_job = db.claim("profile-1", 1, "worker-2")[0]
        assert db.mark_failed(failed_job, "temporary")
        assert db.counts()["pending"] == 1
        failed_job = db.claim("profile-1", 1, "worker-3")[0]
        assert db.mark_failed(failed_job, "final")
        assert db.counts()["failed"] == 1

        released_args = dict(args)
        released_args.update(chat_id="chat-3", chat_url="u3", message_text="Hold")
        db.enqueue(**released_args)
        released_job = db.claim("profile-1", 1, "worker-4")[0]
        assert db.release(released_job, "user stopped")
        released_row = db.get(released_job.id)
        assert released_row is not None
        assert released_row["status"] == "pending"
        assert released_row["retry_count"] == 0
        assert released_row["locked_by"] == ""

        # V4.0.9 舊資料沒有 direction_verified：升級後不可直接領取，
        # 經新版嚴格確認並重新 enqueue 後才可恢復為 pending。
        legacy_path = Path(folder) / "legacy.db"
        legacy = ChatRepository(legacy_path)
        with legacy.transaction() as conn:
            message_hash = make_message_hash("legacy-profile", "legacy-chat", "Old")
            conn.execute(
                """INSERT INTO chat_jobs (
                   profile_id,profile_name,chat_id,chat_name,chat_url,message_text,
                   message_hash,message_direction,direction_verified,is_unread,
                   status,query_time,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,'incoming',0,1,'pending',?,?,?)""",
                ("legacy-profile", "舊環境", "legacy-chat", "舊聊天室",
                 "https://facebook.com/messages/t/legacy-chat", "Old",
                 message_hash, "now", "now", "now"),
            )
        assert legacy.claim("legacy-profile", 1) == []
        revived_id, revived = legacy.enqueue(
            profile_id="legacy-profile", profile_name="舊環境",
            chat_id="legacy-chat", chat_name="舊聊天室",
            chat_url="https://facebook.com/messages/t/legacy-chat",
            message_text="Old", is_unread=True,
        )
        assert revived and revived_id > 0
        assert len(legacy.claim("legacy-profile", 1, "verified-worker")) == 1

        text_file = Path(folder) / "文案.txt"
        text_file.write_text("\n第一句\n\n第二句\n", encoding="utf-8")
        assert TextLibrary(text_file).random_text() in {"第一句", "第二句"}


if __name__ == "__main__":
    run()
    print("core tests passed")
