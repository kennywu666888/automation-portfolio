"""獨立粉專私訊任務；使用 kolurl.txt 與文二.txt。"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from 訊息選擇器 import (
    LANGUAGE_WORDS,
    contains_any,
    find_active_messenger_container,
    find_message_input,
    restriction_scope,
    visible,
)
from 任務結果 import TaskResult
from 文字資料庫 import TextLibrary

_log = logging.getLogger("粉專私訊")

FANPAGE_CONTACT_INFO = """Kung interesado ka, maaari mo akong kontakin sa aking Telegram account

@phplotto777"""


def append_fanpage_contact_info(message: str) -> str:
    """在粉專私訊正文後空一行，再附加固定 Telegram 聯絡資訊。"""
    base = str(message or "").rstrip()
    return f"{base}\n\n{FANPAGE_CONTACT_INFO}" if base else FANPAGE_CONTACT_INFO


class FanpageMessageTask:
    def __init__(self, driver, *, profile_name: str, url_file: str, text_file: str,
                 reply_mode: str = "txt", max_urls: int = 1, openai_reply=None,
                 stop_event=None, diagnostic_callback=None, rename_callback=None):
        self.driver = driver
        self.profile_name = profile_name
        self.url_file = Path(url_file)
        self.text_file = text_file
        self.reply_mode = reply_mode
        self.max_urls = max(1, max_urls)
        self.openai_reply = openai_reply
        self.stop_event = stop_event
        self.diagnostic_callback = diagnostic_callback
        self.rename_callback = rename_callback
        self._target_chat_name = ""

    @staticmethod
    def _original_profile_name(profile_name: str) -> str:
        return re.sub(r"^(?:禁言|觀察3|觀察2|觀察)+", "", (profile_name or "").strip())

    @classmethod
    def _restricted_profile_name(cls, profile_name: str) -> str:
        current = (profile_name or "").strip()
        original = cls._original_profile_name(current)
        if current.startswith(("禁言", "觀察3")):
            return f"禁言{original}"
        if current.startswith("觀察2"):
            return f"觀察3{original}"
        if current.startswith("觀察"):
            return f"觀察2{original}"
        return f"觀察{original}"

    def _rename(self, new_name: str, reason: str) -> None:
        if not self.rename_callback or not new_name or new_name == self.profile_name:
            return
        try:
            if self.rename_callback(new_name):
                _log.info("[%s] [粉專私訊] %s，環境已更名為「%s」。",
                          self.profile_name, reason, new_name)
                self.profile_name = new_name
            else:
                _log.warning("[%s] [粉專私訊] %s，但 AdsPower 更名失敗。",
                             self.profile_name, reason)
        except Exception as exc:
            _log.warning("[%s] [粉專私訊] %s，但 AdsPower 更名異常：%s",
                         self.profile_name, reason, exc)

    def _page_restriction_scope(self) -> str:
        try:
            return restriction_scope(self.driver.find_element(By.TAG_NAME, "body").text)
        except StaleElementReferenceException:
            time.sleep(0.3)
            return restriction_scope(self.driver.find_element(By.TAG_NAME, "body").text)

    def _dismiss_blocking_overlay(self, input_box=None) -> bool:
        """關閉擋住聊天室的上層 Dialog；不關閉 Messenger 聊天視窗本身。"""
        close_words = LANGUAGE_WORDS["close"]
        try:
            result = self.driver.execute_script(
                r"""
                const target = arguments[0];
                const closeWords = arguments[1].map(x => String(x).toLowerCase());
                const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'))
                  .filter(el => {
                    const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                    return s.display!=='none' && s.visibility!=='hidden' &&
                           Number(s.opacity)!==0 && r.width>180 && r.height>100;
                  });
                let blocker = null;
                if (target) {
                  const r=target.getBoundingClientRect();
                  const top=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
                  blocker=top && top.closest('[role="dialog"]');
                  if (blocker && blocker.contains(target)) blocker=null;
                }
                if (!blocker && dialogs.length > 1) {
                  dialogs.sort((a,b) => {
                    const za=Number(getComputedStyle(a).zIndex)||0;
                    const zb=Number(getComputedStyle(b).zIndex)||0;
                    return zb-za;
                  });
                  blocker=dialogs.find(d => !d.querySelector(
                    'div[contenteditable="true"][role="textbox"],' +
                    'div[contenteditable="true"][data-lexical-editor="true"]'
                  )) || null;
                }
                if (!blocker) return null;
                const controls=Array.from(blocker.querySelectorAll(
                  'button,[role="button"],[aria-label],[title]'
                ));
                const close=controls.find(el => {
                  const t=[el.innerText,el.textContent,el.getAttribute('aria-label'),
                    el.getAttribute('title')].filter(Boolean).join(' ')
                    .replace(/\s+/g,' ').trim().toLowerCase();
                  return closeWords.some(w => t===w || t.startsWith(w+' '));
                });
                return close || null;
                """,
                input_box,
                list(close_words),
            )
            if result:
                self.driver.execute_script("arguments[0].click()", result)
                _log.info("[%s] [粉專私訊] 已關閉擋住訊息欄的彈窗。", self.profile_name)
                time.sleep(0.8)
                return True
        except Exception as exc:
            _log.debug("[%s] [粉專私訊] 遮罩關閉檢查略過：%s", self.profile_name, exc)
        return False

    def _current_target_name(self) -> str:
        """由個人頁標題取得本次對象名稱，供多聊天室精準綁定。"""
        try:
            candidates = visible(self.driver.find_elements(
                By.CSS_SELECTOR, "div[role='main'] h1, div[role='main'] h2"
            ))
            for element in candidates:
                text = self._normalized_text(element.text)
                if text and len(text) <= 120:
                    return text
        except Exception:
            pass
        return ""

    def _wake_rendering_surface(self) -> None:
        """粉專導航後觸發 Chrome 顯示層重繪，但不重新整理頁面。"""
        try:
            try:
                self.driver.execute_cdp_cmd("Page.bringToFront", {})
            except Exception:
                pass
            rect = self.driver.get_window_rect()
            scroll = self.driver.execute_script(
                "return {x:window.scrollX||0,y:window.scrollY||0};"
            ) or {"x": 0, "y": 0}
            self.driver.execute_script(
                "window.focus();window.scrollBy(0,1);"
                "window.scrollTo(arguments[0],arguments[1]);",
                int(scroll.get("x", 0)), int(scroll.get("y", 0)),
            )
            width, height = int(rect.get("width", 0)), int(rect.get("height", 0))
            if width > 300 and height > 300:
                self.driver.set_window_rect(
                    x=int(rect.get("x", 0)), y=int(rect.get("y", 0)),
                    width=width - 1, height=height,
                )
                time.sleep(0.12)
                self.driver.set_window_rect(**rect)
            _log.info("[%s] [粉專私訊] 已喚醒 AdsPower 畫面顯示層。",
                      self.profile_name)
        except Exception as exc:
            _log.debug("[%s] [粉專私訊] 畫面喚醒略過：%s",
                       self.profile_name, exc)

    def _target_container(self):
        return find_active_messenger_container(
            self.driver, self._target_chat_name
        )

    def _type_multiline_message(self, input_box, message: str) -> None:
        """用 Shift+Enter 寫入換行，避免 Facebook 把多行文案拆成多則訊息。"""
        lines = str(message or "").splitlines()
        for index, line in enumerate(lines):
            if line:
                input_box.send_keys(line)
            if index < len(lines) - 1:
                ActionChains(self.driver).key_down(Keys.SHIFT).send_keys(
                    Keys.ENTER
                ).key_up(Keys.SHIFT).perform()

    def _send_message_with_stale_retry(self, message: str) -> None:
        typed = False
        last_error = None
        for _ in range(3):
            container = self._target_container()
            input_box = find_message_input(self.driver, container)
            if input_box is None:
                time.sleep(0.4)
                continue
            try:
                input_box.click()
                existing = (
                    input_box.get_attribute("textContent")
                    or input_box.get_attribute("value")
                    or ""
                )
                if not typed:
                    if message in existing:
                        pass
                    elif existing and message.startswith(existing):
                        self._type_multiline_message(
                            input_box, message[len(existing):]
                        )
                    else:
                        self._type_multiline_message(input_box, message)
                typed = True
                break
            except ElementClickInterceptedException as exc:
                last_error = exc
                self._dismiss_blocking_overlay(input_box)
                time.sleep(0.5)
            except StaleElementReferenceException as exc:
                last_error = exc
                time.sleep(0.4)
        else:
            raise last_error or RuntimeError("找不到可用的粉專私訊輸入框")

        # 輸入後 Facebook 可能重建 contenteditable，Enter 前重新定位。
        for _ in range(3):
            container = self._target_container()
            input_box = find_message_input(self.driver, container)
            if input_box is None:
                time.sleep(0.4)
                continue
            try:
                input_box.send_keys(Keys.ENTER)
                return
            except ElementClickInterceptedException as exc:
                last_error = exc
                self._dismiss_blocking_overlay(input_box)
                time.sleep(0.5)
            except StaleElementReferenceException as exc:
                last_error = exc
                time.sleep(0.4)
        raise last_error or RuntimeError("粉專私訊輸入框在送出前持續失效")

    @staticmethod
    def _normalized_text(value: str) -> str:
        return " ".join((value or "").split()).strip()

    def _wait_message_sent(self, message: str, timeout: float = 20.0) -> bool:
        """等待 Facebook 完成送出與畫面重繪，避免慢網路被過早判定失敗。"""
        expected = self._normalized_text(message)
        # 使用足以辨識該筆文案的前段；Facebook 可能將換行或空白重新排版。
        marker = expected[:120]
        deadline = time.monotonic() + max(1.0, timeout)
        last_scope = ""
        empty_since = None
        while time.monotonic() < deadline:
            if self.stop_event and self.stop_event.is_set():
                return False
            try:
                last_scope = self._page_restriction_scope()
                if last_scope:
                    return False

                container = self._target_container()
                if container is not None:
                    conversation = self._normalized_text(container.text)
                    if marker and marker in conversation:
                        return True
                    input_box = find_message_input(self.driver, container)
                    if input_box is not None:
                        current = self._normalized_text(
                            input_box.get_attribute("textContent")
                            or input_box.get_attribute("value")
                            or ""
                        )
                        if not current:
                            if empty_since is None:
                                empty_since = time.monotonic()
                            # 輸入欄持續清空且未出現限制，代表 Facebook 已接受
                            # Enter；慢網路下訊息泡泡可能稍後才顯示。
                            elif time.monotonic() - empty_since >= 2.0:
                                return True
                        else:
                            empty_since = None
            except StaleElementReferenceException:
                pass
            except Exception as exc:
                _log.debug(
                    "[%s] [粉專私訊] 等待送出確認時暫時無法讀取畫面：%s",
                    self.profile_name,
                    exc,
                )
            time.sleep(0.4)
        return False

    def _take_url(self) -> str:
        if not self.url_file.is_file():
            raise FileNotFoundError(f"找不到粉專網址檔案：{self.url_file}")
        lock_path = self.url_file.with_suffix(self.url_file.suffix + ".lock")
        deadline = time.monotonic() + 30
        fd = None
        while fd is None:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"粉專網址檔案鎖定逾時：{lock_path}")
                time.sleep(0.2)
        try:
            lines = [line.strip() for line in self.url_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            if not lines:
                return ""
            chosen = lines[0]
            temp = self.url_file.with_suffix(self.url_file.suffix + ".tmp")
            temp.write_text("\n".join(lines[1:]) + ("\n" if len(lines) > 1 else ""), encoding="utf-8")
            temp.replace(self.url_file)
            return chosen
        finally:
            os.close(fd)
            try:
                lock_path.unlink()
            except OSError:
                pass

    def _return_url(self, url: str) -> None:
        """未成功送出時把網址安全放回佇列尾端，避免素材永久遺失。"""
        clean_url = (url or "").strip()
        if not clean_url:
            return
        lock_path = self.url_file.with_suffix(self.url_file.suffix + ".lock")
        deadline = time.monotonic() + 30
        fd = None
        while fd is None:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    _log.warning(
                        "[%s] [粉專私訊] 網址回存鎖定逾時，請人工保留：%s",
                        self.profile_name,
                        clean_url,
                    )
                    return
                time.sleep(0.2)
        try:
            lines = [
                line.strip()
                for line in self.url_file.read_text(
                    encoding="utf-8-sig"
                ).splitlines()
                if line.strip()
            ]
            if clean_url not in lines:
                lines.append(clean_url)
                temp = self.url_file.with_suffix(self.url_file.suffix + ".return.tmp")
                temp.write_text(
                    "\n".join(lines) + "\n",
                    encoding="utf-8",
                )
                temp.replace(self.url_file)
            _log.info(
                "[%s] [粉專私訊] 本次未成功，網址已放回佇列：%s",
                self.profile_name,
                clean_url,
            )
        except Exception as exc:
            _log.warning(
                "[%s] [粉專私訊] 網址回存失敗，請人工保留 %s：%s",
                self.profile_name,
                clean_url,
                exc,
            )
        finally:
            os.close(fd)
            try:
                lock_path.unlink()
            except OSError:
                pass

    def _save_restriction_diagnostic(
        self, result: TaskResult, url: str, stage: str
    ) -> None:
        if not self.diagnostic_callback:
            return
        try:
            result.diagnostic_zip = str(self.diagnostic_callback(
                task_name="粉專私訊",
                stage=stage,
                reason=result.detail or "粉專私訊受限",
                job_id=url,
            ) or "")
        except Exception:
            pass

    def _find_message_button(self):
        """只尋找個人頁主內容區的「Message」動作按鈕。

        Facebook 頂部導覽列也有 Messages／Messenger 元素；誤點它會開啟
        Messenger（部分帳號接著顯示 Create PIN）。因此這裡不再使用
        messages/messenger href，並明確排除 banner、navigation 與頁首區域。
        """
        candidates = []
        selectors = [
            "div[role='main'] [role='button'][aria-label]",
            "div[role='main'] a[role='button'][aria-label]",
            "div[role='main'] div[role='button']",
            "div[role='main'] span[role='button']",
        ]
        for selector in selectors:
            candidates.extend(visible(self.driver.find_elements(By.CSS_SELECTOR, selector)))

        exact_labels = {
            "訊息", "發送訊息", "傳送訊息", "消息", "发送消息", "发送信息",
            "message", "send message",
            "i-message", "magpadala ng mensahe", "mensahe",
            "envoyer un message", "enviar mensaje", "enviar mensagem",
            "nhắn tin", "kirim pesan", "hantar mesej",
            "nachricht senden", "invia messaggio",
            "メッセージを送信", "메시지 보내기",
            "отправить сообщение", "संदेश भेजें", "mesaj gönder",
            "ข้อความ", "ส่งข้อความ",
            "رسالة", "إرسال رسالة",
        }
        scored = []
        for element in candidates:
            try:
                text = " ".join((element.text or "").split()).strip().lower()
                aria = " ".join((element.get_attribute("aria-label") or "").split()).strip().lower()
                labels = {value for value in (text, aria) if value}
                if not labels.intersection(exact_labels):
                    continue

                # 明確排除頂部 Messages／Messenger 導覽、圖示及其連結。
                href = (element.get_attribute("href") or "").lower()
                if "/messages" in href or "messenger" in href:
                    continue
                if self.driver.execute_script(
                    """
                    const el = arguments[0];
                    return Boolean(
                        el.closest('[role="banner"]') ||
                        el.closest('[role="navigation"]') ||
                        el.closest('header')
                    );
                    """,
                    element,
                ):
                    continue

                rect = element.rect
                # Facebook 頂部導覽列通常位於最上方 0～100px。
                if rect.get("y", 0) < 100:
                    continue
                if rect.get("width", 0) < 45 or rect.get("height", 0) < 20:
                    continue

                # 優先選取畫面上方、個人頁名稱／封面下方的動作按鈕。
                scored.append((rect.get("y", 99999), element))
            except Exception:
                continue
        return min(scored, key=lambda item: item[0])[1] if scored else None

    def _message(self) -> str:
        if self.reply_mode == "openai":
            if not self.openai_reply:
                raise RuntimeError("OpenAI 模式未設定 API")
            base_message = str(
                self.openai_reply("請產生粉專首次開發訊息")
            ).strip()
        else:
            base_message = TextLibrary(
                self.text_file, "粉專私訊文案（文二.txt）"
            ).random_text()
        return append_fanpage_contact_info(base_message)

    def run(self) -> TaskResult:
        started = time.monotonic()
        result = TaskResult(task_name="粉專私訊")
        for _ in range(self.max_urls):
            if self.stop_event and self.stop_event.is_set():
                break
            url = ""
            try:
                url = self._take_url()
                if not url:
                    result.skipped_count += 1
                    result.detail = "kolurl.txt 沒有可用網址"
                    break
                self.driver.get(url)
                time.sleep(1.8)
                self._wake_rendering_surface()
                # Message 動作按鈕位於個人頁標頭；每筆網址都先回頁面最上方。
                self.driver.execute_script(
                    "window.scrollTo({top: 0, left: 0, behavior: 'instant'});"
                )
                time.sleep(0.8)
                scope = self._page_restriction_scope()
                if scope == "account":
                    result.restricted_count += 1
                    result.status = "restricted"
                    result.detail = "偵測到帳號層級 Messenger 限制"
                    self._save_restriction_diagnostic(
                        result, url, "account_restricted"
                    )
                    self._rename(self._restricted_profile_name(self.profile_name), "偵測到帳號受限")
                    self._return_url(url)
                    break
                button = self._find_message_button()
                if button is None:
                    result.skipped_count += 1
                    result.detail = "目標粉專找不到 Message 按鈕，網址已放回佇列"
                    self._return_url(url)
                    continue
                self._target_chat_name = self._current_target_name()
                self.driver.execute_script("arguments[0].click()", button)
                # 等待 Messenger Popup 或限制提示完成渲染。
                deadline = time.monotonic() + 7
                scope = ""
                container = None
                while time.monotonic() < deadline:
                    scope = self._page_restriction_scope()
                    if scope:
                        break
                    container = self._target_container()
                    if container is not None:
                        break
                    time.sleep(0.25)
                if scope:
                    result.restricted_count += 1
                    result.status = "restricted"
                    result.detail = "Message Popup 顯示無法存取或傳送訊息"
                    self._save_restriction_diagnostic(
                        result, url, "popup_restricted"
                    )
                    self._rename(self._restricted_profile_name(self.profile_name), "粉專私訊受限")
                    self._return_url(url)
                    break
                if container is None:
                    raise RuntimeError("點擊 Message 後未找到 Messenger 對話視窗")
                # 聊天視窗出現後再檢查一次，避免限制文字延遲載入。
                time.sleep(0.8)
                scope = self._page_restriction_scope()
                if scope:
                    result.restricted_count += 1
                    result.status = "restricted"
                    result.detail = "Message Popup 顯示訊息請求上限或聊天室受限"
                    self._save_restriction_diagnostic(
                        result, url, "popup_delayed_restricted"
                    )
                    self._rename(self._restricted_profile_name(self.profile_name), "粉專私訊受限")
                    self._return_url(url)
                    break
                message = self._message()
                self._send_message_with_stale_retry(message)
                if not self._wait_message_sent(message, timeout=20.0):
                    scope = self._page_restriction_scope()
                    if scope:
                        result.restricted_count += 1
                        result.status = "restricted"
                        result.detail = "送出等待期間出現訊息限制"
                        self._save_restriction_diagnostic(
                            result, url, "send_wait_restricted"
                        )
                        self._rename(
                            self._restricted_profile_name(self.profile_name),
                            "粉專私訊受限",
                        )
                        self._return_url(url)
                        break
                    raise RuntimeError("Enter 後 20 秒內未確認目標粉專私訊成功送出")
                result.success_count += 1
                self._rename(self._original_profile_name(self.profile_name), "訊息成功送出")
                break
            except Exception as exc:
                result.failed_count += 1
                _log.exception("[%s] [粉專私訊] url=%s 失敗：%s", self.profile_name, url, exc)
                self._return_url(url)
                if self.diagnostic_callback:
                    try:
                        result.diagnostic_zip = str(self.diagnostic_callback(
                            task_name="粉專私訊", stage="send", reason=str(exc), job_id=url
                        ) or "")
                    except Exception:
                        pass
        if result.status == "restricted":
            pass
        elif result.failed_count and not result.success_count:
            result.status = "failed"
        elif not result.success_count:
            result.status = "skipped"
        result.elapsed_seconds = time.monotonic() - started
        return result
