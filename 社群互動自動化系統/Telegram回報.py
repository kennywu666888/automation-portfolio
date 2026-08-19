import threading
import time
import re
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class TelegramTargets:
    incoming_chat_id: str
    replied_chat_id: str


class TelegramReporter:
    """Telegram reporter with two independent destinations.

    CHAT_ID1: incoming customer comment/reply notifications.
    CHAT_ID2: successfully sent Facebook replies.
    """

    _lock = threading.Lock()

    def __init__(self, token: str, targets: TelegramTargets, enabled: bool = True):
        self.token = (token or "").strip()
        self.targets = targets
        self.enabled = bool(enabled)

    @staticmethod
    def _truncate(text: str, url: str = "") -> str:
        if len(text) <= 3900:
            return text
        suffix = f"\n…\n{url}" if url else "\n…"
        return text[: max(0, 3900 - len(suffix))] + suffix

    def format_incoming(self, profile: str, reply) -> str:
        text = (
            "🔔 Facebook 留言收到新回覆／提及\n\n"
            f"環境：{profile}\n"
            f"回覆者：{reply.reply_user}\n"
            "回覆內容：\n"
            f"{reply.reply_text}\n\n"
            "原留言：\n"
            f"{reply.original_comment}\n\n"
            f"貼文作者：{reply.post_author}\n"
            f"通知時間：{reply.notification_time}\n\n"
            "Facebook 連結：\n"
            f"{reply.facebook_url}"
        )
        return self._truncate(text, reply.facebook_url)

    def format_replied(self, profile: str, reply, reply_text: str) -> str:
        text = (
            "✅ Facebook 已成功回覆客戶\n\n"
            f"環境：{profile}\n"
            f"客戶：{reply.reply_user}\n"
            "客戶訊息：\n"
            f"{reply.reply_text}\n\n"
            "我們的回覆：\n"
            f"{reply_text}\n\n"
            "Facebook 連結：\n"
            f"{reply.facebook_url}"
        )
        return self._truncate(text, reply.facebook_url)

    def _send_text(self, chat_id: str, text: str, retries: int = 3) -> bool:
        if not self.enabled:
            return True
        if not self.token or not chat_id:
            raise RuntimeError("Telegram Token 或目標 Chat ID 未設定")
        last_error = None
        with self._lock:
            for attempt in range(max(1, retries)):
                try:
                    response = requests.post(
                        f"https://api.telegram.org/bot{self.token}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": text,
                            "disable_web_page_preview": True,
                        },
                        timeout=20,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("ok"):
                        return True
                    last_error = RuntimeError(str(payload))
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                if attempt + 1 < retries:
                    time.sleep(1 + attempt * 2)
        safe_error = str(last_error)
        if self.token:
            safe_error = safe_error.replace(self.token, "***")
        safe_error = re.sub(
            r"(?i)(https://api\.telegram\.org/bot)[^/\s]+",
            r"\1***",
            safe_error,
        )
        raise RuntimeError(f"Telegram 傳送失敗：{safe_error}")

    def send_incoming(self, profile: str, reply, retries: int = 3) -> bool:
        return self._send_text(
            self.targets.incoming_chat_id,
            self.format_incoming(profile, reply),
            retries,
        )

    def send_replied(self, profile: str, reply, reply_text: str, retries: int = 3) -> bool:
        return self._send_text(
            self.targets.replied_chat_id,
            self.format_replied(profile, reply, reply_text),
            retries,
        )

    def test_incoming(self) -> bool:
        class R:
            reply_user = "SYSTEM"
            reply_text = "收到客戶留言群測試成功"
            original_comment = "—"
            post_author = "—"
            notification_time = "—"
            facebook_url = "https://www.facebook.com/notifications"

        return self.send_incoming("測試", R())

    def test_replied(self) -> bool:
        class R:
            reply_user = "SYSTEM"
            reply_text = "測試客戶訊息"
            original_comment = "—"
            post_author = "—"
            notification_time = "—"
            facebook_url = "https://www.facebook.com/notifications"

        return self.send_replied("測試", R(), "thanks")
