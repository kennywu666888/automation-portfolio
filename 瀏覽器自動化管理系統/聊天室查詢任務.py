"""獨立「查詢聊天室」任務：只讀取並入庫，絕不輸入或送出訊息。"""

from __future__ import annotations

import logging
import re
import time
from selenium.webdriver.common.by import By

from 聊天室資料庫 import ChatRepository
from 訊息選擇器 import (
    chat_id_from_url, chat_item_name, chat_muted_profile_name,
    click_chat_item, has_chat_identity_restriction, restriction_scope,
    suppress_messenger_restore_prompts, wait_for_chat_items,
    wait_for_conversation,
)
from 任務結果 import TaskResult
from Telegram回報 import TelegramReporter

_log = logging.getLogger("查詢聊天室")
TELEGRAM_RE = re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{4,31}")
IGNORED_TELEGRAM = {"@phplottopromotercenter5859_bot"}


class ChatQueryTask:
    MESSENGER_URL = "https://www.facebook.com/messages"

    def __init__(self, driver, repository: ChatRepository, *, profile_id: str,
                 profile_name: str, max_chats: int = 5, unread_only: bool = False,
                 max_retries: int = 3, stop_event=None,
                 telegram_reporter: TelegramReporter | None = None,
                 diagnostic_callback=None, rename_callback=None):
        self.driver = driver
        self.repository = repository
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.max_chats = max(1, max_chats)
        self.unread_only = unread_only
        self.max_retries = max(1, max_retries)
        self.stop_event = stop_event
        self.telegram_reporter = telegram_reporter
        self.diagnostic_callback = diagnostic_callback
        self.rename_callback = rename_callback

    def _rename_chat_identity_restricted(self, page_text: str) -> bool:
        if not has_chat_identity_restriction(page_text):
            return False
        new_name = chat_muted_profile_name(self.profile_name, self.profile_id)
        if new_name == self.profile_name:
            return True
        if not self.rename_callback:
            _log.warning(
                "[%s] [查詢聊天室] 偵測到 Confirm your identity，"
                "但未提供 AdsPower 更名功能。",
                self.profile_name,
            )
            return False
        try:
            if self.rename_callback(new_name):
                _log.warning(
                    "[%s] [查詢聊天室] 偵測到 Confirm your identity，"
                    "環境已更名為「%s」。",
                    self.profile_name, new_name,
                )
                self.profile_name = new_name
                return True
        except Exception as exc:
            _log.warning(
                "[%s] [查詢聊天室] 聊天室禁言環境更名失敗：%s",
                self.profile_name, exc,
            )
        return False

    def run(self) -> TaskResult:
        started = time.monotonic()
        result = TaskResult(task_name="查詢聊天室")
        _log.info("[%s] [查詢聊天室] 開始純查詢；此任務不會發送訊息。", self.profile_name)
        self.driver.get(self.MESSENGER_URL)
        time.sleep(2)
        # Messenger 的身分限制可能比左側聊天室清單晚數秒渲染。
        # 在任何聊天室讀取前先給限制提示一個短暫動態等待時間，
        # 避免固定 2 秒判斷過早而漏掉 Confirm your identity。
        page_text = ""
        scope = ""
        restriction_deadline = time.monotonic() + 6
        restore_hidden_logged = False
        while time.monotonic() < restriction_deadline:
            if self.stop_event and self.stop_event.is_set():
                break
            restore_state = suppress_messenger_restore_prompts(self.driver)
            if (
                not restore_hidden_logged
                and (
                    restore_state["hidden_dialogs"]
                    or restore_state["hidden_veils"]
                )
            ):
                _log.info(
                    "[%s] [查詢聊天室] 已隱藏 Messenger Restore/PIN "
                    "彈窗與白色遮罩；未點擊任何還原選項。",
                    self.profile_name,
                )
                restore_hidden_logged = True
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            scope = restriction_scope(page_text)
            if scope:
                break
            time.sleep(0.25)
        if scope == "account":
            result.status = "restricted"
            result.restricted_count = 1
            if has_chat_identity_restriction(page_text):
                result.detail = "Confirm your identity to send messages"
                if not self._rename_chat_identity_restricted(page_text):
                    result.detail += "；AdsPower 更名失敗"
            else:
                result.detail = "帳號層級 Messenger 限制"
            if self.diagnostic_callback:
                try:
                    result.diagnostic_zip = str(self.diagnostic_callback(
                        task_name="查詢聊天室",
                        stage="restricted",
                        reason=result.detail,
                        job_id=self.profile_id,
                    ) or "")
                except Exception:
                    pass
            result.elapsed_seconds = time.monotonic() - started
            return result

        # 左側清單若存在通常在數秒內完成；空清單不再固定等待 20 秒。
        items = wait_for_chat_items(self.driver, timeout=10.0)[: self.max_chats]
        if not items:
            result.status = "skipped"
            result.skipped_count = 1
            result.detail = "目前沒有可查詢聊天室"
            result.elapsed_seconds = time.monotonic() - started
            return result

        targets = []
        for item in items:
            href = item.get_attribute("href") or ""
            if not href:
                continue
            targets.append((href, chat_item_name(item, href)))
        _log.info(
            "[%s] [查詢聊天室] 左側清單共抓到 %d 個聊天室，本次準備讀取 %d 個。",
            self.profile_name, len(items), len(targets),
        )
        for chat_number, (href, name) in enumerate(targets, start=1):
            if self.stop_event and self.stop_event.is_set():
                break
            try:
                expected_id = chat_id_from_url(href, fallback=name)
                _log.info(
                    "[%s] [查詢聊天室] 正在讀取：名稱=%s｜ID=%s｜網址=%s",
                    self.profile_name, name, expected_id, href,
                )
                if not click_chat_item(self.driver, href, timeout=8.0):
                    raise RuntimeError("左側聊天室項目已消失或無法點選")
                _log.info(
                    "[%s] [查詢聊天室] 已點選，等待中央聊天室完成切換：ID=%s",
                    self.profile_name, expected_id,
                )
                (text, direction, unread), input_box = wait_for_conversation(
                    self.driver, expected_id, timeout=20.0
                )
                if input_box is None and not text:
                    raise RuntimeError("聊天室載入逾時，找不到中央對話內容或輸入欄")
                if not text or direction != "incoming":
                    if not text:
                        reason = "尚無有效對話（可能只有加密或其他系統提示）"
                    elif direction == "outgoing":
                        reason = "最後一則是自己送出的訊息"
                    else:
                        reason = "訊息方向證據不足，安全略過"
                    _log.info(
                        "[%s] [查詢聊天室] 跳過：名稱=%s｜方向=%s｜訊息=%s｜原因=%s",
                        self.profile_name, name, direction,
                        " ".join(text.split()) if text else "無", reason,
                    )
                    result.skipped_count += 1
                    continue
                if self.unread_only and not unread:
                    _log.info(
                        "[%s] [查詢聊天室] 跳過：名稱=%s｜原因=不是未讀聊天室",
                        self.profile_name, name,
                    )
                    result.skipped_count += 1
                    continue
                chat_url = self.driver.current_url
                chat_id = chat_id_from_url(chat_url, fallback=expected_id)
                display_text = " ".join(text.split())
                _log.info(
                    "[%s] [查詢聊天室] 已讀取：名稱=%s｜方向=%s｜未讀=%s｜訊息=%s",
                    self.profile_name, name,
                    "對方傳入" if direction == "incoming" else direction,
                    "是" if unread else "否", display_text,
                )
                if self.telegram_reporter is not None:
                    try:
                        sent = self.telegram_reporter.send_incoming_message_once(
                            profile_name=self.profile_name,
                            chat_number=chat_number,
                            chat_id=chat_id,
                            chat_name=name,
                            message_text=text,
                        )
                        _log.info(
                            "[%s] [查詢聊天室] Telegram 回報%s：第 %d 個聊天室｜名稱=%s",
                            self.profile_name,
                            "完成" if sent else "未送出（請檢查設定）",
                            chat_number,
                            name,
                        )
                    except Exception as telegram_exc:
                        _log.warning(
                            "[%s] [查詢聊天室] Telegram 回報失敗，不影響後續任務："
                            "第 %d 個聊天室｜名稱=%s｜原因=%s",
                            self.profile_name, chat_number, name, telegram_exc,
                        )
                matches = [m.lower() for m in TELEGRAM_RE.findall(text)]
                telegram = next((m for m in matches if m not in IGNORED_TELEGRAM), "")
                job_id, created = self.repository.enqueue(
                    profile_id=self.profile_id, profile_name=self.profile_name,
                    chat_id=chat_id, chat_name=name, chat_url=chat_url,
                    message_text=text, is_unread=unread,
                    telegram_account=telegram, is_lead=bool(telegram),
                    max_retries=self.max_retries,
                )
                if created:
                    _log.info(
                        "[%s] [查詢聊天室] 已加入待回覆：job=%s｜名稱=%s",
                        self.profile_name, job_id, name,
                    )
                    result.success_count += 1
                else:
                    _log.info(
                        "[%s] [查詢聊天室] 已存在，略過重複訊息：job=%s｜名稱=%s",
                        self.profile_name, job_id, name,
                    )
                    result.skipped_count += 1
            except Exception as exc:
                _log.exception(
                    "[%s] [查詢聊天室] 單一聊天室讀取失敗：名稱=%s｜網址=%s｜原因=%s",
                    self.profile_name, name, href, exc,
                )
                if self.diagnostic_callback:
                    try:
                        diagnostic = str(self.diagnostic_callback(
                            task_name="查詢聊天室",
                            stage="query",
                            reason=str(exc),
                            job_id=href,
                        ) or "")
                        if diagnostic:
                            result.diagnostic_zip = diagnostic
                    except Exception:
                        pass
                result.failed_count += 1
        if result.failed_count and not result.success_count:
            result.status = "failed"
        elif result.restricted_count and not result.success_count:
            result.status = "restricted"
        elif result.success_count:
            result.status = "success"
        else:
            result.status = "skipped"
        result.elapsed_seconds = time.monotonic() - started
        return result
