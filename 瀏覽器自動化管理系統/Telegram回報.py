"""Telegram Bot API 回報；僅 HTTP 成功後才寫永久去重紀錄。"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone

import requests


class TelegramReporter:
    def __init__(self, repository, bot_token: str = "", chat_id: str = ""):
        self.repository = repository
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()

    def send_once(self, telegram_account: str, text: str) -> bool:
        account = telegram_account.strip().lower()
        if not account or account == "@phplottopromotercenter5859_bot":
            return False
        key = hashlib.sha256(f"{account}\n{text}".encode("utf-8")).hexdigest()
        with self.repository._connect() as conn:
            if conn.execute("SELECT 1 FROM telegram_reports WHERE report_key=?", (key,)).fetchone():
                return True
        if not self.bot_token or not self.chat_id:
            return False
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text}, timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            return False
        with self.repository.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO telegram_reports(report_key,telegram_account,sent_at) VALUES(?,?,?)",
                (key, account, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
        return True

    def send_incoming_message_once(
        self,
        *,
        profile_name: str,
        chat_number: int,
        chat_id: str,
        chat_name: str,
        message_text: str,
    ) -> bool:
        """回報 Messenger 讀取結果；同環境、聊天室與訊息只成功傳送一次。"""
        profile = profile_name.strip() or "未命名環境"
        name = chat_name.strip() or f"聊天室 {chat_id}"
        message = message_text.strip()
        if not message or not self.bot_token or not self.chat_id:
            return False

        key = hashlib.sha256(
            f"messenger\n{profile}\n{chat_id}\n{message}".encode("utf-8")
        ).hexdigest()
        with self.repository._connect() as conn:
            if conn.execute(
                "SELECT 1 FROM telegram_reports WHERE report_key=?", (key,)
            ).fetchone():
                return True

        report_text = (
            "【讀取聊天室訊息】\n"
            f"環境名稱：{profile}\n"
            f"第幾個聊天室：第 {chat_number} 個聊天室\n"
            f"聊天室名稱：{name}\n"
            f"訊息內容：{message}"
        )
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": report_text},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            return False

        with self.repository.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO telegram_reports"
                "(report_key,telegram_account,sent_at) VALUES(?,?,?)",
                (
                    key,
                    f"messenger:{profile}:{chat_id}",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        return True

    def send_reply_message_once(
        self,
        *,
        profile_name: str,
        chat_id: str,
        chat_name: str,
        customer_message: str,
        reply_text: str,
    ) -> bool:
        """回報 Messenger 回覆結果；同環境、聊天室及回覆內容只傳送一次。"""
        profile = profile_name.strip() or "未命名環境"
        name = chat_name.strip() or f"聊天室 {chat_id}"
        customer = customer_message.strip()
        reply = reply_text.strip()
        if not reply or not self.bot_token or not self.chat_id:
            return False

        key = hashlib.sha256(
            f"messenger-reply\n{profile}\n{chat_id}\n{customer}\n{reply}".encode("utf-8")
        ).hexdigest()
        with self.repository._connect() as conn:
            if conn.execute(
                "SELECT 1 FROM telegram_reports WHERE report_key=?", (key,)
            ).fetchone():
                return True

        report_text = (
            "【回覆聊天室成功】\n"
            f"環境名稱：{profile}\n"
            f"聊天室名稱：{name}\n"
            f"客戶訊息：{customer or '（無內容）'}\n"
            f"回覆內容：{reply}"
        )
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": report_text},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            return False

        with self.repository.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO telegram_reports"
                "(report_key,telegram_account,sent_at) VALUES(?,?,?)",
                (
                    key,
                    f"messenger-reply:{profile}:{chat_id}",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        return True
