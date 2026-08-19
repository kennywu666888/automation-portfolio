"""獨立「回覆聊天室」任務：只處理 SQLite 已鎖定的 pending 工作。"""

from __future__ import annotations

import logging
import time
import uuid

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import ElementClickInterceptedException

from 聊天室資料庫 import ChatRepository, make_message_hash
from 訊息選擇器 import (
    LANGUAGE_WORDS, chat_id_from_url, chat_muted_profile_name,
    click_chat_item, has_chat_identity_restriction, restriction_scope,
    suppress_messenger_restore_prompts, wait_for_conversation,
    wait_for_sent_message,
)
from 任務結果 import TaskResult
from Telegram回報 import TelegramReporter
from 文字資料庫 import TextLibrary

_log = logging.getLogger("回覆聊天室")

REPLY_CONTACT_INFO = """臉書聯繫帳號
https://www.facebook.com/profile.php?id=61560690278002

WHAT APP 頻道鏈結
https://chat.whatsapp.com/Bb3N9dCB9j9KCIm405cayO

what app帳號
@phplotto"""


class ChatReplyTask:
    def __init__(self, driver, repository: ChatRepository, *, profile_id: str,
                 profile_name: str, text_file: str, reply_mode: str = "txt",
                 max_replies: int = 3, openai_reply=None, stop_event=None,
                 diagnostic_callback=None,
                 telegram_reporter: TelegramReporter | None = None,
                 rename_callback=None):
        self.driver = driver
        self.repository = repository
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.text_file = text_file
        self.reply_mode = reply_mode
        self.max_replies = max(1, max_replies)
        self.openai_reply = openai_reply
        self.stop_event = stop_event
        self.diagnostic_callback = diagnostic_callback
        self.telegram_reporter = telegram_reporter
        self.rename_callback = rename_callback
        self._chat_identity_renamed = False

    def _rename_chat_identity_restricted(self, page_text: str) -> bool:
        if not has_chat_identity_restriction(page_text):
            return False
        if self._chat_identity_renamed:
            return True
        new_name = chat_muted_profile_name(self.profile_name, self.profile_id)
        if new_name == self.profile_name:
            self._chat_identity_renamed = True
            return True
        if not self.rename_callback:
            _log.warning(
                "[%s] [回覆聊天室] 偵測到 Confirm your identity，"
                "但未提供 AdsPower 更名功能。",
                self.profile_name,
            )
            return False
        try:
            if self.rename_callback(new_name):
                _log.warning(
                    "[%s] [回覆聊天室] 偵測到 Confirm your identity，"
                    "環境已更名為「%s」。",
                    self.profile_name, new_name,
                )
                self.profile_name = new_name
                self._chat_identity_renamed = True
                return True
        except Exception as exc:
            _log.warning(
                "[%s] [回覆聊天室] 聊天室禁言環境更名失敗：%s",
                self.profile_name, exc,
            )
        return False

    def _reply_text(self, customer_text: str) -> str:
        if self.reply_mode == "openai":
            if not self.openai_reply:
                raise RuntimeError("OpenAI 回覆模式未設定 API")
            return str(self.openai_reply(customer_text)).strip()
        base_text = TextLibrary(
            self.text_file, "Messenger 回覆文案（文一.txt）"
        ).random_text().strip()
        return f"{base_text}\n\n{REPLY_CONTACT_INFO}"

    def _type_multiline_reply(self, input_box, reply: str) -> None:
        """以 Shift+Enter 保留換行，最後由呼叫端按一次 Enter 送出。"""
        lines = reply.splitlines()
        for index, line in enumerate(lines):
            if line:
                input_box.send_keys(line)
            if index < len(lines) - 1:
                ActionChains(self.driver).key_down(Keys.SHIFT).send_keys(
                    Keys.ENTER
                ).key_up(Keys.SHIFT).perform()

    def _dismiss_blocking_dialog(self, input_box) -> bool:
        """Close a PIN/notification dialog that covers the Messenger composer."""
        close_words = [word.casefold() for word in LANGUAGE_WORDS["close"]]
        script = """
            const input = arguments[0];
            const closeWords = arguments[1];
            const visible = el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 &&
                    s.visibility !== 'hidden' && s.display !== 'none';
            };
            const rect = input.getBoundingClientRect();
            const x = rect.left + Math.max(1, Math.min(rect.width - 1, rect.width / 2));
            const y = rect.top + Math.max(1, Math.min(rect.height - 1, rect.height / 2));
            const top = document.elementFromPoint(x, y);
            let blocker = top && top.closest('[role="dialog"],[aria-modal="true"]');
            if (blocker && blocker.contains(input)) blocker = null;
            if (!blocker) {
                const dialogs = [...document.querySelectorAll(
                    '[role="dialog"],[aria-modal="true"]'
                )].filter(visible).filter(el => !el.contains(input));
                blocker = dialogs.at(-1) || null;
            }
            if (!blocker) return false;
            const controls = [...blocker.querySelectorAll(
                'button,[role="button"],[aria-label],[title]'
            )].filter(visible);
            for (const control of controls) {
                const label = (control.getAttribute('aria-label') ||
                    control.getAttribute('title') || control.innerText || '')
                    .trim().toLocaleLowerCase();
                if (closeWords.some(word => label === word || label.startsWith(word))) {
                    control.click();
                    return true;
                }
            }
            return false;
        """
        dismissed = bool(
            self.driver.execute_script(script, input_box, close_words)
        )
        if dismissed:
            _log.info(
                "[%s] [回覆聊天室] 已關閉遮住輸入框的 Messenger 對話視窗。",
                self.profile_name,
            )
            time.sleep(0.8)
        return dismissed

    def run(self) -> TaskResult:
        started = time.monotonic()
        result = TaskResult(task_name="回覆聊天室")
        self.repository.recover_stale()
        jobs = self.repository.claim(self.profile_id, self.max_replies, uuid.uuid4().hex)
        if not jobs:
            result.status = "skipped"
            result.skipped_count = 1
            result.detail = "沒有待回覆資料"
            result.elapsed_seconds = time.monotonic() - started
            return result

        for job_index, job in enumerate(jobs):
            if self.stop_event and self.stop_event.is_set():
                remaining = jobs[job_index:]
                for pending_job in remaining:
                    self.repository.release(pending_job, "使用者停止執行，工作已安全退回佇列")
                result.skipped_count += len(remaining)
                result.detail = "使用者停止執行，未送出的回覆已退回佇列"
                break
            diagnostic = ""
            try:
                _log.info(
                    "[%s] [回覆聊天室] 正在處理：job=%s｜名稱=%s｜網址=%s｜客戶訊息=%s",
                    self.profile_name, job.id, job.chat_name, job.chat_url,
                    " ".join(job.message_text.split()),
                )
                # 已在 Messenger 時優先點左側聊天室，避免每一筆都整頁重新導航。
                # 若是由其他頁面開始，或目標不在目前左側清單，才使用網址進入。
                current_url = (self.driver.current_url or "").lower()
                opened_by_click = False
                if "/messages" in current_url or "/messaging/" in current_url:
                    opened_by_click = click_chat_item(
                        self.driver, job.chat_url, timeout=4.0
                    )
                if not opened_by_click:
                    self.driver.get(job.chat_url)
                restore_state = suppress_messenger_restore_prompts(self.driver)
                if (
                    restore_state["hidden_dialogs"]
                    or restore_state["hidden_veils"]
                ):
                    _log.info(
                        "[%s] [回覆聊天室] 已隱藏 Messenger Restore/PIN "
                        "彈窗與白色遮罩；未點擊任何還原選項。",
                        self.profile_name,
                    )
                expected_id = chat_id_from_url(job.chat_url, fallback=job.chat_id)
                (current_text, direction, _), input_box = wait_for_conversation(
                    self.driver, expected_id, timeout=20.0
                )
                body = self.driver.find_element(By.TAG_NAME, "body").text
                scope = restriction_scope(body)
                if scope:
                    identity_restricted = has_chat_identity_restriction(body)
                    if identity_restricted:
                        reason = "Confirm your identity to send messages"
                        if not self._rename_chat_identity_restricted(body):
                            reason += "；AdsPower 更名失敗"
                        result.detail = reason
                    else:
                        reason = "帳號層級限制" if scope == "account" else "單一聊天室限制"
                    _log.warning(
                        "[%s] [回覆聊天室] 無法回覆：job=%s｜名稱=%s｜原因=%s",
                        self.profile_name, job.id, job.chat_name, reason,
                    )
                    self.repository.mark_restricted(job, reason)
                    result.restricted_count += 1
                    if identity_restricted:
                        remaining = jobs[job_index + 1:]
                        for pending_job in remaining:
                            self.repository.release(
                                pending_job,
                                "環境已標記聊天室禁言，未執行回覆",
                            )
                        result.skipped_count += len(remaining)
                        break
                    continue
                if input_box is None:
                    raise RuntimeError("聊天室載入逾時，找不到中央 Messenger 輸入框")
                if self._dismiss_blocking_dialog(input_box):
                    (current_text, direction, _), input_box = wait_for_conversation(
                        self.driver, expected_id, timeout=8.0
                    )
                    if input_box is None:
                        raise RuntimeError("關閉 Messenger 遮罩後找不到輸入框")
                current_hash = make_message_hash(self.profile_id, job.chat_id, current_text)
                if direction != "incoming":
                    reason = (
                        "目前最後一則訊息是自己發送"
                        if direction == "outgoing"
                        else "目前訊息方向無法確認，安全略過"
                    )
                    _log.info(
                        "[%s] [回覆聊天室] 跳過：job=%s｜名稱=%s｜方向=%s｜原因=%s",
                        self.profile_name, job.id, job.chat_name, direction, reason,
                    )
                    self.repository.mark_skipped(job, reason)
                    result.skipped_count += 1
                    continue
                if current_hash != job.message_hash:
                    _log.info(
                        "[%s] [回覆聊天室] 跳過：job=%s｜名稱=%s｜原因=客戶訊息已變更｜目前訊息=%s",
                        self.profile_name, job.id, job.chat_name,
                        " ".join(current_text.split()),
                    )
                    self.repository.mark_changed(job, "客戶最後訊息已變更，必須重新查詢建單")
                    result.skipped_count += 1
                    continue

                reply = self._reply_text(current_text)
                if not reply:
                    raise RuntimeError("回覆文案為空")
                try:
                    input_box.click()
                except ElementClickInterceptedException:
                    if not self._dismiss_blocking_dialog(input_box):
                        raise
                    (retry_text, retry_direction, _), input_box = (
                        wait_for_conversation(self.driver, expected_id, timeout=8.0)
                    )
                    if input_box is None:
                        raise RuntimeError("關閉 Messenger 遮罩後找不到輸入框")
                    retry_hash = make_message_hash(
                        self.profile_id, job.chat_id, retry_text
                    )
                    if retry_direction != "incoming" or retry_hash != job.message_hash:
                        raise RuntimeError(
                            "關閉 Messenger 遮罩後最後訊息狀態改變，未送出回覆"
                        )
                    self.driver.execute_script("arguments[0].focus();", input_box)
                self._type_multiline_reply(input_box, reply)
                input_box.send_keys(Keys.ENTER)
                if not wait_for_sent_message(
                    self.driver, reply, expected_id, timeout=20.0
                ):
                    raise RuntimeError("按下 Enter 後未確認訊息成功送出")
                if not self.repository.mark_replied(job, reply):
                    raise RuntimeError("已送出但資料庫成功狀態更新失敗，請立即人工核對")
                _log.info(
                    "[%s] [回覆聊天室] 回覆成功：job=%s｜名稱=%s｜回覆訊息=%s",
                    self.profile_name, job.id, job.chat_name,
                    " ".join(reply.split()),
                )
                if self.telegram_reporter is not None:
                    try:
                        sent = self.telegram_reporter.send_reply_message_once(
                            profile_name=self.profile_name,
                            chat_id=job.chat_id,
                            chat_name=job.chat_name,
                            customer_message=current_text,
                            reply_text=reply,
                        )
                        _log.info(
                            "[%s] [回覆聊天室] Telegram 回報%s：名稱=%s",
                            self.profile_name,
                            "完成" if sent else "未送出（請檢查設定）",
                            job.chat_name,
                        )
                    except Exception as telegram_exc:
                        _log.warning(
                            "[%s] [回覆聊天室] Telegram 回報失敗，不影響後續任務："
                            "名稱=%s｜原因=%s",
                            self.profile_name, job.chat_name, telegram_exc,
                        )
                result.success_count += 1
            except Exception as exc:
                _log.exception("[%s] [回覆聊天室] job=%s 失敗：%s", self.profile_name, job.id, exc)
                if self.diagnostic_callback:
                    try:
                        diagnostic = str(self.diagnostic_callback(
                            task_name="回覆聊天室", stage="reply", reason=str(exc), job_id=job.id
                        ) or "")
                    except Exception:
                        diagnostic = ""
                self.repository.mark_failed(job, str(exc), diagnostic)
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
