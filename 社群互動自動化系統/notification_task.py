import time
from types import SimpleNamespace

from 留言回覆讀取 import ReplyData, read_reply
from notification_parser import click_candidate, collect_all_candidates, select_candidates
from reply_to_customer import reply_to_customer
from 任務結果 import TaskResult
from text_sources import build_customer_reply


def _reply_from_state(state):
    return ReplyData(
        reply_user=state.get("reply_user", ""),
        reply_text=state.get("reply_text", ""),
        original_comment=state.get("original_comment", ""),
        post_author=state.get("post_author", ""),
        notification_time=state.get("notification_time", ""),
        facebook_url=state.get("facebook_url") or state.get("notification_url", ""),
        reader_detail="notification_history",
    )


def _candidate_from_state(state):
    return SimpleNamespace(
        key=state["notification_key"],
        text=state.get("notification_text", ""),
        url=state.get("notification_url", ""),
    )


def _open_unread_tab(driver, logger, enabled: bool) -> bool:
    """Open and verify the Unread tab; fail closed when strict mode is enabled."""
    if not enabled:
        return True

    def unread_control_state():
        return driver.execute_script(
            r"""
            const norm = value => (value || '').replace(/\s+/g,' ').trim().toLowerCase();
            const visible = node => {
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' && style.visibility !== 'hidden';
            };
            const unreadTerms = [
                'unread', '未讀', '未读', 'hindi pa nababasa',
                'belum dibaca', 'ยังไม่ได้อ่าน', 'غير مقروءة'
            ];
            const toolbar = [...document.querySelectorAll('[role="toolbar"]')].find(node =>
                /filter|篩選|筛选/i.test(node.getAttribute('aria-label') || '')
            );
            const scope = toolbar || document;
            const controls = [...scope.querySelectorAll(
                '[role="button"], [role="tab"], button, a'
            )].filter(visible);
            for (const control of controls) {
                const text = norm(control.innerText || control.textContent);
                const matches = unreadTerms.some(term =>
                    text === term || (text.startsWith(term + ' ') && text.length < 80)
                );
                if (!matches) continue;
                const pressed = control.getAttribute('aria-pressed');
                const selected = pressed === 'true' ||
                    control.getAttribute('aria-selected') === 'true' ||
                    control.getAttribute('aria-current') === 'page';
                return {
                    found: true,
                    selected,
                    pressed,
                    element: control
                };
            }
            return {found:false, selected:false, pressed:null, element:null};
            """
        ) or {}

    try:
        result = unread_control_state()
        if not result.get('found'):
            logger.warning('已勾選只處理未讀通知，但找不到 Unread 分頁；本次通知任務停止')
            return False
        if result.get('selected'):
            logger.info('已確認 Notifications 位於 Unread 分頁')
            return True

        control = result.get('element')
        if control is None:
            logger.warning('找到 Unread 分頁文字，但找不到可操作按鈕；本次通知任務停止')
            return False
        # Use the WebDriver click path instead of HTMLElement.click().  The
        # latter can leave Facebook's filter focused without actually changing
        # aria-pressed, which visually looks selected but remains on All.
        control.click()
        logger.info('已點擊 Notifications 的 Unread 分頁，等待 aria-pressed 確認')
        time.sleep(0.8)

        deadline = time.time() + 5
        while time.time() < deadline:
            verified = unread_control_state()
            if verified.get('found') and verified.get('selected'):
                logger.info('已確認 Notifications 位於 Unread 分頁')
                return True
            time.sleep(0.3)
        logger.warning('Unread 分頁點擊後 aria-pressed 仍不是 true；本次通知任務停止')
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning('點擊或確認 Unread 分頁失敗：%s', exc)
        return False


class NotificationTask:
    def __init__(self, driver, profile, settings, repo, reporter, logger, stop):
        self.driver = driver
        self.profile = profile
        self.s = settings
        self.repo = repo
        self.reporter = reporter
        self.log = logger
        self.stop = stop
        self.candidates = []
        self.all_candidates = []

    def _retry_pending_reports(self, result):
        attempted = set()
        for state in self.repo.pending_replied(self.profile.profile_id):
            candidate = _candidate_from_state(state)
            attempted.add(candidate.key)
            reply = _reply_from_state(state)
            reply_text = state.get("sent_reply_text", "")
            try:
                if not reply_text:
                    raise RuntimeError("歷史紀錄缺少已送出的 Facebook 回覆文字")
                self.reporter.send_replied(self.profile.name, reply, reply_text)
                self.repo.mark(
                    self.profile.profile_id,
                    self.profile.name,
                    candidate,
                    "reported",
                    reply,
                    reply_sent=True,
                    sent_reply_text=reply_text,
                    incoming_telegram_sent=bool(state.get("incoming_telegram_sent")),
                    replied_telegram_sent=True,
                )
                result.reported += 1
                self.log.info(
                    "已補送 Facebook 回覆成功資訊至 TELEGRAM_CHAT_ID2；未重送 Facebook 回覆"
                )
            except Exception as exc:  # noqa: BLE001
                self.repo.mark(
                    self.profile.profile_id,
                    self.profile.name,
                    candidate,
                    "failed",
                    reply,
                    error=str(exc),
                    reply_sent=True,
                    sent_reply_text=reply_text,
                    incoming_telegram_sent=bool(state.get("incoming_telegram_sent")),
                )
                result.failed += 1
                result.issues.append(str(exc))
                self.log.exception("補送 Telegram 回覆結果失敗；Facebook 回覆不會重送")
        return attempted

    def run(self):
        result = TaskResult("SKIPPED")
        retried_keys = self._retry_pending_reports(result)
        self.driver.get("https://www.facebook.com/notifications")
        time.sleep(3)
        only_unread = bool(self.s.get("only_unread"))
        if not _open_unread_tab(self.driver, self.log, only_unread):
            result.status = "PARTIAL" if result.reported else "SKIPPED"
            result.issues.append("無法確認已切換至 Unread 分頁，為避免處理已讀通知而停止")
            return result
        previous = -1
        stable = 0
        for _ in range(max(1, int(self.s["max_scrolls"]))):
            if self.stop.is_set():
                return TaskResult("STOPPED")
            self.all_candidates = collect_all_candidates(self.driver, self.profile.profile_id)
            self.candidates = select_candidates(
                self.all_candidates,
                process_replies=bool(self.s.get("process_replies", True)),
                process_mentions=bool(self.s.get("process_mentions", True)),
                only_unread=bool(self.s.get("only_unread")),
                new_section_only=bool(self.s.get("new_section_only", True)),
            )
            stable = stable + 1 if len(self.all_candidates) == previous else 0
            if len(self.candidates) >= int(self.s["max_replies"]) or stable >= 2:
                break
            previous = len(self.all_candidates)
            self.driver.execute_script("window.scrollBy(0, Math.max(400, innerHeight*0.7));")
            time.sleep(1.5)

        type_counts = {}
        for candidate in self.all_candidates:
            type_counts[candidate.kind] = type_counts.get(candidate.kind, 0) + 1
        self.log.info("通知分類統計：%s", type_counts)
        for index, candidate in enumerate(self.all_candidates, 1):
            self.log.info(
                "通知候選[%d] 區段=%s 類型=%s 語言=%s 未讀=%s 接受=%s 原因=%s 文字=%s",
                index,
                candidate.section,
                candidate.kind,
                candidate.language,
                candidate.unread,
                candidate.accepted,
                candidate.skip_reason or "-",
                candidate.text[:180],
            )

        unique = []
        seen = set()
        for candidate in self.candidates:
            if (
                candidate.key not in seen
                and candidate.key not in retried_keys
                and not self.repo.is_reported(candidate.key)
            ):
                unique.append(candidate)
                seen.add(candidate.key)
        if self.s.get("sort_order") == "oldest":
            unique.reverse()
        unique = unique[: int(self.s["max_replies"])]
        result.found = len(unique)
        self.log.info("符合留言回覆／留言提及數量：%d", len(unique))
        if not unique:
            if result.failed:
                result.status = "PARTIAL" if result.reported else "FAILED"
            elif result.reported:
                result.status = "SUCCESS"
            return result

        unread_blocked = False
        for index, candidate in enumerate(unique, 1):
            if self.stop.is_set():
                result.status = "STOPPED"
                break
            reply = None
            reply_text = ""
            state = self.repo.get_state(candidate.key) or {}
            reply_sent = bool(state.get("facebook_reply_sent"))
            incoming_sent = bool(state.get("incoming_telegram_sent"))
            replied_sent = bool(state.get("replied_telegram_sent"))
            try:
                self.log.info(
                    "處理第 %d/%d 則｜類型=%s｜語言=%s",
                    index,
                    len(unique),
                    candidate.kind,
                    candidate.language,
                )
                if reply_sent:
                    reply = _reply_from_state(state)
                    reply_text = state.get("sent_reply_text", "")
                    if not reply_text:
                        raise RuntimeError("歷史紀錄缺少已送出的 Facebook 回覆文字")
                    if not replied_sent:
                        self.reporter.send_replied(self.profile.name, reply, reply_text)
                        replied_sent = True
                    self.repo.mark(
                        self.profile.profile_id,
                        self.profile.name,
                        candidate,
                        "reported",
                        reply,
                        reply_sent=True,
                        sent_reply_text=reply_text,
                        incoming_telegram_sent=incoming_sent,
                        replied_telegram_sent=replied_sent,
                    )
                    result.reported += 1
                    self.log.info("歷史紀錄顯示 Facebook 已回覆；本次只完成 Telegram 回報")
                    continue

                self.driver.get("https://www.facebook.com/notifications")
                time.sleep(2)
                if not _open_unread_tab(self.driver, self.log, only_unread):
                    unread_blocked = True
                    result.skipped += len(unique) - index + 1
                    result.issues.append("無法確認 Unread 分頁，剩餘通知已安全跳過")
                    self.log.warning("無法確認 Unread 分頁；不點擊本則與其餘通知")
                    break
                before_url = self.driver.current_url
                clicked, click_reason = click_candidate(
                    self.driver,
                    candidate,
                    require_unread=only_unread,
                )
                if not clicked:
                    if click_reason == "not_unread_at_click":
                        result.skipped += 1
                        self.log.info("通知在實際點擊前已不是未讀，已安全跳過")
                        continue
                    raise RuntimeError(f"找不到對應通知容器，未執行點擊：{click_reason}")
                self.log.info("已實際點擊通知容器：區段=%s、序號=%s", candidate.section, candidate.occurrence)
                deadline = time.time() + 10
                while time.time() < deadline:
                    if self.stop.is_set():
                        return TaskResult("STOPPED")
                    if self.driver.current_url != before_url:
                        break
                    time.sleep(0.4)
                if self.driver.current_url == before_url and candidate.url and candidate.url != before_url:
                    self.log.warning("通知點擊後網址未變更，使用通知目標網址備援")
                    self.driver.get(candidate.url)
                time.sleep(3)
                reply = read_reply(self.driver, candidate.text)
                self.log.info(
                    "已讀取客戶訊息：回覆者=%s｜內容=%s｜定位=%s",
                    reply.reply_user,
                    (reply.reply_text or "無法讀取").replace("\n", " | ")[:500],
                    reply.reader_detail,
                )
                if not reply.reply_text or reply.reply_text == "無法讀取":
                    raise RuntimeError(
                        "未能可靠讀取客戶留言，已停止回覆；"
                        f"定位={reply.reader_detail}"
                    )

                # Group 1: incoming customer comment/reply.
                if not incoming_sent:
                    self.reporter.send_incoming(self.profile.name, reply)
                    incoming_sent = True
                    self.repo.mark(
                        self.profile.profile_id,
                        self.profile.name,
                        candidate,
                        "incoming_reported",
                        reply,
                        incoming_telegram_sent=True,
                    )
                    self.log.info("客戶留言已傳送至 TELEGRAM_CHAT_ID1")

                if bool(self.s.get("auto_reply_enabled", True)):
                    reply_text = build_customer_reply(
                        str(self.s.get("customer_reply_text_file", "")).strip(),
                        str(self.s.get("telegram_account", "")).strip(),
                    )
                    self.log.info(
                        "準備輸入客戶回覆：%s",
                        reply_text.replace("\n", " | "),
                    )
                    send_result = reply_to_customer(self.driver, reply_text)
                    if not send_result.success:
                        raise RuntimeError(
                            "Facebook 自動回覆失敗："
                            f"方式={send_result.method or '-'}；{send_result.detail}"
                        )
                    reply_sent = True
                    self.log.info("已成功回覆客戶：%s（方式=%s）", reply_text, send_result.method)

                    # Persist before any external reporting so a Telegram
                    # failure can never cause the Facebook reply to be sent again.
                    self.repo.mark(
                        self.profile.profile_id,
                        self.profile.name,
                        candidate,
                        "facebook_replied",
                        reply,
                        reply_sent=True,
                        sent_reply_text=reply_text,
                        incoming_telegram_sent=incoming_sent,
                    )

                    # Group 2: our successfully sent reply.
                    self.reporter.send_replied(self.profile.name, reply, reply_text)
                    replied_sent = True
                    self.log.info("回覆成功資訊已傳送至 TELEGRAM_CHAT_ID2")

                self.repo.mark(
                    self.profile.profile_id,
                    self.profile.name,
                    candidate,
                    "reported",
                    reply,
                    reply_sent=reply_sent,
                    sent_reply_text=reply_text if reply_sent else "",
                    incoming_telegram_sent=incoming_sent,
                    replied_telegram_sent=replied_sent,
                )
                result.reported += 1
            except Exception as exc:  # noqa: BLE001
                self.repo.mark(
                    self.profile.profile_id,
                    self.profile.name,
                    candidate,
                    "failed",
                    reply=reply,
                    error=str(exc),
                    reply_sent=reply_sent,
                    sent_reply_text=reply_text if reply_sent else "",
                    incoming_telegram_sent=incoming_sent,
                    replied_telegram_sent=replied_sent,
                )
                result.failed += 1
                result.issues.append(str(exc))
                self.log.exception("單一通知失敗")
            finally:
                self.driver.get("https://www.facebook.com/notifications")
                time.sleep(float(self.s.get("notification_wait_seconds", 2)))
                _open_unread_tab(
                    self.driver,
                    self.log,
                    bool(self.s.get("only_unread")),
                )

        if result.failed:
            result.status = "PARTIAL" if result.reported else "FAILED"
        elif result.reported:
            result.status = "PARTIAL" if unread_blocked else "SUCCESS"
        else:
            result.status = "SKIPPED"
        return result
