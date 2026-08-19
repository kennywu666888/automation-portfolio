"""
browser.py
==========
Facebook Auto Warm-up Lite — 瀏覽器操作模組
負責建立 Selenium WebDriver 連線、頁面導航、元素操作等底層封裝。
所有 Driver 操作集中於此，上層模組不直接使用 selenium。
"""

import random
import os
import time
import uuid
from typing import Optional

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chromium.remote_connection import ChromiumRemoteConnection
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.remote.client_config import ClientConfig
from selenium.webdriver.remote.file_detector import UselessFileDetector
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from 環境管理介面 import BrowserSession
from 設定 import CONFIG, BrowserConfig
from 日誌 import get_logger
from 工具 import jitter_point, random_sleep

_log = get_logger(__name__)


FACEBOOK_NOTIFICATION_ORIGINS = (
    "https://www.facebook.com",
    "https://facebook.com",
    "https://m.facebook.com",
    "https://web.facebook.com",
)


def grant_facebook_notification_permission(driver) -> bool:
    """Grant Facebook notifications immediately after Selenium attaches.

    The Chrome Allow/Block bubble is browser UI, not Facebook DOM.  Setting
    the permission through CDP before health checks and task navigation keeps
    it from blocking the first page interaction.
    """
    granted = 0
    for origin in FACEBOOK_NOTIFICATION_ORIGINS:
        try:
            driver.execute_cdp_cmd(
                "Browser.setPermission",
                {
                    "permission": {"name": "notifications"},
                    "setting": "granted",
                    "origin": origin,
                },
            )
            granted += 1
        except Exception:
            continue
    return granted > 0


def dismiss_facebook_notification_overlay(driver) -> bool:
    """Close only Facebook's in-page push-notification request overlay."""
    try:
        result = driver.execute_script(
            r"""
            const dialogTerms=[
              'push notifications request','turn on notifications',
              'enable notifications','show notifications',
              '開啟通知','开启通知','顯示通知','显示通知','通知權限','通知权限',
              'activer les notifications','afficher les notifications',
              'i-on ang mga notification','ipakita ang mga notification',
              'เปิดการแจ้งเตือน','แสดงการแจ้งเตือน',
              'تشغيل الإشعارات','عرض الإشعارات','طلب الإشعارات'
            ];
            const closeTerms=[
              'close','not now','dismiss','cancel','later',
              '關閉','关闭','暫時不要','暂时不要','稍後','以后','取消',
              'fermer','pas maintenant','plus tard','annuler',
              'isara','hindi ngayon','mamaya','kanselahin',
              'ปิด','ไม่ใช่ตอนนี้','ไว้ภายหลัง','ยกเลิก',
              'إغلاق','ليس الآن','لاحقًا','إلغاء'
            ];
            const visible=el=>{
              if(!el||!el.isConnected)return false;
              const s=getComputedStyle(el),r=el.getBoundingClientRect();
              return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0;
            };
            const textOf=el=>((el.innerText||el.textContent||'')+' '+
              (el.getAttribute('aria-label')||'')).replace(/\s+/g,' ').trim().toLowerCase();
            const dialogs=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')]
              .filter(visible);
            for(const dialog of dialogs){
              const dialogText=textOf(dialog);
              if(!dialogTerms.some(term=>dialogText.includes(term)))continue;
              const controls=[...dialog.querySelectorAll(
                'button,[role="button"],[aria-label],[tabindex="0"]'
              )].filter(visible);
              for(const control of controls){
                const label=textOf(control);
                if(closeTerms.some(term=>label===term||label.includes(term))){
                  control.click();
                  return {matched:true,clicked:true};
                }
              }
              return {matched:true,clicked:false};
            }
            return {matched:false,clicked:false};
            """
        ) or {}
        if result.get("clicked"):
            return True
        if result.get("matched"):
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            return True
    except Exception:
        pass
    return False


class BrowserController:
    """
    封裝 Selenium WebDriver 的所有底層操作。
    透過 AdsPower 回傳的 BrowserSession 建立連線。

    使用方式（建議搭配 context manager）：
        session = adspower.open_browser(profile_id)
        with BrowserController(session) as ctrl:
            ctrl.navigate("https://www.facebook.com")
            ...
    """
    def switch_to_facebook_tab(driver):
        """切換到 Facebook 分頁"""

        for handle in driver.window_handles:
            driver.switch_to.window(handle)

            current_url = driver.current_url.lower()

            if "facebook.com" in current_url:
                return True

        return False
    
    def __init__(
        self,
        session: BrowserSession,
        cfg: Optional[BrowserConfig] = None,
    ) -> None:
        self._session = session
        self._cfg = cfg or CONFIG.browser
        self.driver: Optional[webdriver.Remote] = None

    # ── Context Manager ──────────────────────────

    def __enter__(self) -> "BrowserController":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # 不吞掉例外，讓上層自行處理
        return False

    # ── 連線管理 ────────────────────────────────

    def connect(self) -> None:
        """
        連線至 AdsPower 已開啟的瀏覽器。
        使用 selenium_address 作為 chromedriver remote 位址。
        """
        options = Options()
        options.add_experimental_option(
            "debuggerAddress", self._session.selenium_address
        )

        service = Service(executable_path=self._session.webdriver_path)
        service.start()
        connect_timeout = max(5, int(getattr(self._cfg, "connect_timeout", 20)))
        client_config = ClientConfig(
            remote_server_addr=service.service_url,
            keep_alive=True,
            timeout=connect_timeout,
        )
        executor = ChromiumRemoteConnection(
            remote_server_addr=service.service_url,
            vendor_prefix="goog",
            browser_name="chrome",
            keep_alive=True,
            ignore_proxy=options._ignore_local_proxy,
            client_config=client_config,
        )
        try:
            self.driver = webdriver.Remote(
                command_executor=executor,
                options=options,
            )
            # AdsPower ChromeDriver 與程式都在同一台電腦。RemoteWebDriver
            # 預設 LocalFileDetector 會先呼叫遠端 /se/file，但本機
            # ChromeDriver 不支援該端點，造成 Reels 上傳 unknown command。
            # 改為直接把 Windows 本機路徑交給 input[type=file]。
            self.driver.file_detector = UselessFileDetector()
            # Remote 不會自動保留本地 Service；補上後 detach_keep_browser()
            # 才能只關閉本次 chromedriver，不關閉 AdsPower 瀏覽器。
            self.driver.service = service
        except Exception:
            try:
                executor.close()
            except Exception:
                pass
            service.stop()
            raise

        # 設定全域 Timeout
        self.driver.implicitly_wait(self._cfg.implicit_wait)
        self.driver.set_page_load_timeout(self._cfg.page_load_timeout)

        # Must run immediately after attach, before switching tabs, health
        # checks, login handling, or whichever task happens to be first.
        if grant_facebook_notification_permission(self.driver):
            _log.info(
                "已在 Selenium 接管後立即預設允許 Facebook 通知（Profile=%s）。",
                self._session.profile_id,
            )
        else:
            _log.warning(
                "無法在 Selenium 接管後立即設定 Facebook 通知權限，後續仍會重試"
                "（Profile=%s）。",
                self._session.profile_id,
            )

        _log.info(
            "Selenium 已連線（Profile=%s，Address=%s）。",
            self._session.profile_id,
            self._session.selenium_address,
        )

    def bring_window_to_front(self) -> bool:
        """還原並置前目前接管的 AdsPower Chrome，且驗證前景視窗。"""
        self._ensure_driver()
        if os.name != "nt":
            _log.info("目前不是 Windows，略過 AdsPower 視窗置前。")
            return False

        marker = f"ADSPOWER_ACTIVE_{uuid.uuid4().hex}"
        original_title = ""
        try:
            # Selenium 目前控制的分頁不一定是 Chrome 視窗中真正的作用中分頁。
            # 先明確切回目前 handle，讓稍後寫入的 marker 能反映到 Windows
            # 頂層視窗標題；AdsPower 剛啟動時標題更新也可能需要數秒。
            current_handle = self.driver.current_window_handle  # type: ignore[union-attr]
            self.driver.switch_to.window(current_handle)  # type: ignore[union-attr]
            original_title = str(self.driver.execute_script(  # type: ignore[union-attr]
                "const old=document.title; document.title=arguments[0]; return old;", marker
            ) or "")

            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowThreadProcessId.argtypes = (
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            )
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            found_hwnd = 0
            deadline = time.monotonic() + 4.0
            while not found_hwnd and time.monotonic() < deadline:
                @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                def enum_callback(hwnd, _lparam):
                    nonlocal found_hwnd
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length <= 0:
                        return True
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buffer, length + 1)
                    if marker in buffer.value and user32.IsWindowVisible(hwnd):
                        found_hwnd = int(hwnd)
                        return False
                    return True

                user32.EnumWindows(enum_callback, 0)
                if not found_hwnd:
                    # 重新要求 Chrome 啟用該分頁並補寫 marker，處理剛啟動、
                    # 分頁切換中及視窗標題更新延遲。
                    try:
                        self.driver.switch_to.window(current_handle)  # type: ignore[union-attr]
                        self.driver.execute_script(  # type: ignore[union-attr]
                            "window.focus(); document.title=arguments[0];", marker
                        )
                    except Exception:
                        pass
                    time.sleep(0.25)
            if not found_hwnd:
                _log.warning(
                    "無法從 Windows 視窗標題辨識目前 AdsPower 瀏覽器，"
                    "略過置前（瀏覽器仍可繼續執行任務）。"
                )
                return False

            SW_RESTORE = 9
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
            target_hwnd = wintypes.HWND(found_hwnd)
            kernel32 = ctypes.windll.kernel32

            # SetForegroundWindow 可能因 Windows 的 foreground lock 只回傳
            # 失敗而不拋例外。每次實際檢查 GetForegroundWindow；若第一次
            # 沒成功，再暫時連接目前／目標視窗的輸入執行緒後重試。
            for attempt in range(1, 5):
                attached_threads: list[int] = []
                try:
                    foreground_hwnd = user32.GetForegroundWindow()
                    current_thread = int(kernel32.GetCurrentThreadId())
                    foreground_thread = int(
                        user32.GetWindowThreadProcessId(foreground_hwnd, None)
                    ) if foreground_hwnd else 0
                    target_thread = int(
                        user32.GetWindowThreadProcessId(target_hwnd, None)
                    )

                    if attempt > 1:
                        for thread_id in {foreground_thread, target_thread}:
                            if (
                                thread_id
                                and thread_id != current_thread
                                and user32.AttachThreadInput(
                                    current_thread, thread_id, True
                                )
                            ):
                                attached_threads.append(thread_id)

                    user32.ShowWindowAsync(target_hwnd, SW_RESTORE)
                    user32.BringWindowToTop(target_hwnd)
                    user32.SetWindowPos(
                        target_hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags
                    )
                    user32.SetWindowPos(
                        target_hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags
                    )
                    user32.SetForegroundWindow(target_hwnd)
                    if attached_threads:
                        user32.SetFocus(target_hwnd)
                finally:
                    for thread_id in reversed(attached_threads):
                        user32.AttachThreadInput(
                            int(kernel32.GetCurrentThreadId()), thread_id, False
                        )

                time.sleep(0.2)
                active_hwnd = int(user32.GetForegroundWindow() or 0)
                if active_hwnd == found_hwnd:
                    _log.info(
                        "已確認目前處理中的 AdsPower 瀏覽器位於最前面"
                        "（第 %d 次嘗試）。",
                        attempt,
                    )
                    return True

            _log.warning(
                "已執行 AdsPower 視窗還原與置前，但 Windows 未將該視窗"
                "回報為目前前景視窗；任務仍可繼續。"
            )
            return False
        except Exception as exc:
            # Selenium 例外字串會附上數十行原生 stacktrace，容易被誤認成
            # 程式崩潰；此處本來就是可恢復警告，只保留第一行原因。
            summary = (str(exc).splitlines() or [type(exc).__name__])[0].strip()
            _log.warning(
                "AdsPower 瀏覽器置前失敗，不影響任務繼續：%s",
                summary or type(exc).__name__,
            )
            return False
        finally:
            try:
                self.driver.execute_script(  # type: ignore[union-attr]
                    "if(document.title===arguments[0]) document.title=arguments[1];",
                    marker, original_title,
                )
            except Exception:
                pass

    def dismiss_floating_chats(self, profile_name: str = "") -> int:
        """關閉 Facebook 右下角已展開的 Messenger 浮動對話視窗。

        每次點擊後 Facebook 都可能重建 DOM，因此逐輪重新定位，避免
        stale element。僅接受聊天視窗專用的 Close chat／Close
        conversation 語意，不會點一般頁面或 Dialog 的 Close。
        """
        self._ensure_driver()
        labels = (
            "close chat", "close conversation", "close conversation with",
            "關閉聊天", "关闭聊天", "關閉對話", "关闭对话",
            "isara ang chat", "isara ang pag-uusap",
            "fermer la discussion", "fermer la conversation",
            "cerrar chat", "cerrar conversación",
            "fechar conversa", "chat schließen",
            "đóng đoạn chat", "tutup obrolan", "tutup perbualan",
            "ปิดแชท", "إغلاق الدردشة", "チャットを閉じる", "채팅 닫기",
        )
        closed = 0
        for _ in range(12):
            try:
                target = self.driver.execute_script(  # type: ignore[union-attr]
                    """
                    const words=arguments[0].map(x=>String(x).toLowerCase());
                    const visible=el=>{
                      const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                      return r.width>0&&r.height>0&&s.display!=='none'&&
                             s.visibility!=='hidden'&&r.bottom>0&&r.top<innerHeight;
                    };
                    const norm=v=>String(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                    for(const el of document.querySelectorAll(
                      '[role="button"][aria-label],button[aria-label],div[aria-label][tabindex]'
                    )){
                      if(!visible(el)) continue;
                      const label=norm(el.getAttribute('aria-label'));
                      if(!words.some(w=>label===w||label.startsWith(w+' '))) continue;
                      const r=el.getBoundingClientRect();
                      const popup=el.closest('[role="dialog"]') ||
                                  el.closest('[data-pagelet*="ChatTab"]');
                      if(popup && r.left>innerWidth*0.35 && r.top>innerHeight*0.25)
                        return el;
                    }
                    return null;
                    """,
                    list(labels),
                )
            except Exception:
                target = None
            if target is None:
                break
            try:
                self.driver.execute_script("arguments[0].click();", target)  # type: ignore[union-attr]
                closed += 1
                time.sleep(0.4)
            except StaleElementReferenceException:
                continue
            except Exception:
                break

        if closed:
            prefix = f"[{profile_name}] " if profile_name else ""
            _log.info(
                "%s任務開始前已關閉 %d 個右下角 Messenger 浮動對話視窗。",
                prefix,
                closed,
            )
        return closed

    def wake_rendering_surface(self, profile_name: str = "") -> bool:
        """安全喚醒 Chrome/AdsPower 顯示層，不重新整理或重新導航頁面。

        Selenium 仍能讀到 DOM、截圖也正常，但 Windows 視窗偶爾維持白畫面。
        依序要求目前分頁置前、重新取得焦點、微調視窗尺寸再還原，並做一次
        不改變閱讀位置的捲動，促使 Chrome compositor 重新繪製。
        """
        self._ensure_driver()
        prefix = f"[{profile_name}] " if profile_name else ""
        try:
            try:
                self.driver.execute_cdp_cmd("Page.bringToFront", {})  # type: ignore[union-attr]
            except Exception:
                pass

            original_rect = self.driver.get_window_rect()  # type: ignore[union-attr]
            original_scroll = self.driver.execute_script(  # type: ignore[union-attr]
                "return {x: window.scrollX || 0, y: window.scrollY || 0};"
            ) or {"x": 0, "y": 0}
            self.driver.execute_script(  # type: ignore[union-attr]
                "window.focus();"
                "window.scrollBy(0, 1);"
                "window.scrollTo(arguments[0], arguments[1]);",
                int(original_scroll.get("x", 0)),
                int(original_scroll.get("y", 0)),
            )

            width = int(original_rect.get("width", 0))
            height = int(original_rect.get("height", 0))
            if width > 300 and height > 300:
                self.driver.set_window_rect(  # type: ignore[union-attr]
                    x=int(original_rect.get("x", 0)),
                    y=int(original_rect.get("y", 0)),
                    width=width - 1,
                    height=height,
                )
                time.sleep(0.12)
                self.driver.set_window_rect(**original_rect)  # type: ignore[union-attr]

            _log.info("%s已安全喚醒 AdsPower 畫面顯示層。", prefix)
            return True
        except Exception as exc:
            _log.debug("%s畫面喚醒略過，不影響任務執行：%s", prefix, exc)
            return False

    def quit(self) -> None:
        """
        結束 WebDriver 連線（不關閉 AdsPower 瀏覽器，由 AdsPowerClient 負責）。
        """
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
            finally:
                self.driver = None
            _log.info("Selenium 連線已結束（Profile=%s）。", self._session.profile_id)

    def detach_keep_browser(self) -> None:
        """
        解除本程式對 Selenium 的接管，但保留 AdsPower Chrome 視窗。

        不呼叫 driver.quit()，避免 WebDriver 送出關閉瀏覽器指令；只關閉
        Python 端 HTTP 連線與本次 chromedriver service，AdsPower 環境會
        繼續保持開啟，可供人工操作或之後再次接管。
        """
        driver = self.driver
        if driver is None:
            return

        try:
            command_executor = getattr(driver, "command_executor", None)
            close_connection = getattr(command_executor, "close", None)
            if callable(close_connection):
                close_connection()
        except Exception:
            pass

        try:
            service = getattr(driver, "service", None)
            stop_service = getattr(service, "stop", None)
            if callable(stop_service):
                stop_service()
        except Exception:
            pass

        self.driver = None
        _log.info(
            "Selenium 已解除接管，AdsPower Browser 保持開啟（Profile=%s）。",
            self._session.profile_id,
        )

    # ── 頁面導航 ────────────────────────────────

    def navigate(self, url: str) -> None:
        """
        導航至指定 URL。

        Args:
            url: 目標網址。

        Raises:
            RuntimeError: 頁面載入逾時。
        """
        self._ensure_driver()
        try:
            _log.debug("導航至：%s", url)
            self.driver.get(url)  # type: ignore[union-attr]
        except TimeoutException as exc:
            try:
                self.stop_loading()
            except Exception:
                pass
            raise RuntimeError(f"頁面載入逾時：{url}") from exc

    def stop_loading(self) -> None:
        """停止目前頁面載入，避免網路不穩時卡住。"""
        self._ensure_driver()
        try:
            self.driver.execute_script("window.stop();")  # type: ignore[union-attr]
        except WebDriverException:
            pass

    def current_url(self) -> str:
        """回傳目前頁面的 URL。"""
        self._ensure_driver()
        return self.driver.current_url  # type: ignore[union-attr]

    def page_source(self) -> str:
        """回傳目前頁面的 HTML 原始碼。"""
        self._ensure_driver()
        return self.driver.page_source  # type: ignore[union-attr]

    def title(self) -> str:
        """回傳目前頁面標題。"""
        self._ensure_driver()
        return self.driver.title  # type: ignore[union-attr]

    # ── 元素等待與取得 ────────────────────────────

    def wait_for(
        self,
        by: str,
        value: str,
        timeout: Optional[int] = None,
    ) -> WebElement:
        """
        等待元素出現並回傳。

        Args:
            by:      定位方式（By.CSS_SELECTOR / By.XPATH 等）。
            value:   選擇器值。
            timeout: 等待秒數，省略則使用設定預設值。

        Returns:
            目標 WebElement。

        Raises:
            TimeoutException: 超時未找到元素。
        """
        self._ensure_driver()
        wait_sec = timeout or self._cfg.explicit_wait
        return WebDriverWait(self.driver, wait_sec).until(  # type: ignore[arg-type]
            EC.presence_of_element_located((by, value))
        )

    def wait_clickable(
        self,
        by: str,
        value: str,
        timeout: Optional[int] = None,
    ) -> WebElement:
        """等待元素可點擊並回傳。"""
        self._ensure_driver()
        wait_sec = timeout or self._cfg.explicit_wait
        return WebDriverWait(self.driver, wait_sec).until(  # type: ignore[arg-type]
            EC.element_to_be_clickable((by, value))
        )

    def find(self, by: str, value: str) -> Optional[WebElement]:
        """
        嘗試找到元素，找不到時回傳 None（不拋出例外）。

        Args:
            by:    定位方式。
            value: 選擇器值。

        Returns:
            WebElement 或 None。
        """
        self._ensure_driver()
        try:
            return self.driver.find_element(by, value)  # type: ignore[union-attr]
        except NoSuchElementException:
            return None

    def find_all(self, by: str, value: str) -> list[WebElement]:
        """找到所有符合的元素，沒有時回傳空列表。"""
        self._ensure_driver()
        try:
            return self.driver.find_elements(by, value)  # type: ignore[union-attr]
        except NoSuchElementException:
            return []

    # ── 點擊操作 ────────────────────────────────

    def click(self, element: WebElement) -> bool:
        """
        點擊元素，自動加入座標隨機偏移模擬真人點擊。
        若被遮擋則嘗試 JavaScript 點擊。

        Args:
            element: 目標 WebElement。

        Returns:
            True 表示點擊成功。
        """
        self._ensure_driver()
        try:
            # 滾動至元素可見範圍
            self.driver.execute_script(  # type: ignore[union-attr]
                "arguments[0].scrollIntoView({block: 'center'});", element
            )
            random_sleep(0.3, 0.8)

            # 使用 ActionChains 模擬真人點擊偏移
            loc = element.location
            size = element.size
            center_x = int(loc["x"] + size["width"] / 2)
            center_y = int(loc["y"] + size["height"] / 2)
            jx, jy = jitter_point(center_x, center_y, jitter=6)

            actions = ActionChains(self.driver)  # type: ignore[arg-type]
            actions.move_to_element_with_offset(
                element, jx - center_x, jy - center_y
            ).click().perform()
            return True

        except ElementClickInterceptedException:
            # 被遮擋時退而使用 JS 點擊
            try:
                self.driver.execute_script("arguments[0].click();", element)  # type: ignore[union-attr]
                return True
            except WebDriverException:
                return False
        except (StaleElementReferenceException, WebDriverException):
            return False

    # ── 鍵盤輸入 ────────────────────────────────

    def type_text(self, element: WebElement, text: str) -> None:
        """
        逐字輸入文字，模擬真人打字速度。

        Args:
            element: 輸入框 WebElement。
            text:    要輸入的文字。
        """
        self._ensure_driver()
        element.click()
        random_sleep(0.3, 0.7)
        for char in text:
            element.send_keys(char)
            # 每個字元之間加入隨機延遲（30 ~ 120ms）
            time.sleep(random.uniform(0.03, 0.12))

    # ── 滾動操作 ────────────────────────────────

    def scroll_down(self, pixels: int) -> None:
        """
        向下滾動指定像素。
        使用 JavaScript + WheelEvent，若頁面不動會再補一次 PageDown。

        Args:
            pixels: 滾動像素數。
        """
        self._ensure_driver()
        try:
            before = self.get_scroll_position()
        except WebDriverException:
            before = -1

        try:
            self.driver.execute_script(  # type: ignore[union-attr]
                "window.scrollBy(0, arguments[0]);", pixels
            )
            time.sleep(0.15)

            after = self.get_scroll_position()
            if before == after:
                self.driver.execute_script(  # type: ignore[union-attr]
                    "document.dispatchEvent(new WheelEvent('wheel', {deltaY: arguments[0], bubbles: true}));",
                    pixels,
                )
                time.sleep(0.15)

            after2 = self.get_scroll_position()
            if before == after2:
                body = self.driver.find_element(By.TAG_NAME, "body")  # type: ignore[union-attr]
                from selenium.webdriver.common.keys import Keys
                body.send_keys(Keys.PAGE_DOWN if pixels > 0 else Keys.PAGE_UP)
        except WebDriverException:
            pass

    def scroll_up(self, pixels: int) -> None:
        """向上滾動指定像素。"""
        self.scroll_down(-pixels)

    def scroll_to_top(self) -> None:
        """回到頁面頂端。"""
        self._ensure_driver()
        self.driver.execute_script("window.scrollTo(0, 0);")  # type: ignore[union-attr]

    def get_scroll_position(self) -> int:
        """取得目前滾動位置（Y 軸像素）。"""
        self._ensure_driver()
        return self.driver.execute_script("return window.scrollY;")  # type: ignore[union-attr]

    def get_page_height(self) -> int:
        """取得頁面總高度（像素）。"""
        self._ensure_driver()
        return self.driver.execute_script(  # type: ignore[union-attr]
            "return document.documentElement.scrollHeight;"
        )

    # ── JavaScript 工具 ─────────────────────────

    def run_js(self, script: str, *args) -> object:
        """
        執行任意 JavaScript 並回傳結果。

        Args:
            script: JS 程式碼字串。
            *args:  傳入 JS 的 arguments。

        Returns:
            JS 執行回傳值。
        """
        self._ensure_driver()
        return self.driver.execute_script(script, *args)  # type: ignore[union-attr]

    # ── 分頁管理 ────────────────────────────────

    def switch_to_facebook_tab(self) -> bool:
        """
        在所有已開啟的分頁中，切換至 Facebook 相關分頁。

        Returns:
            True 表示成功切換，False 表示找不到 Facebook 分頁。
        """
        self._ensure_driver()
        handles = self.driver.window_handles  # type: ignore[union-attr]

        # AdsPower 第一分頁是環境資訊，Facebook 通常在第二分頁。
        # 逐頁讀取 current_url 會等待各分頁 renderer，慢環境可能
        # 額外卡住數秒。先由 CDP Target 清單找 Facebook targetId
        # 並直接切換；取不到時再完整保留原本逐頁路徑。
        try:
            target_infos = self.driver.execute_cdp_cmd(  # type: ignore[union-attr]
                "Target.getTargets", {}
            ).get("targetInfos", [])
            facebook_target_ids = {
                str(info.get("targetId", ""))
                for info in target_infos
                if info.get("type") == "page"
                and "facebook.com" in str(info.get("url", "")).lower()
            }
            for handle in reversed(handles):
                normalized = str(handle).removeprefix("CDwindow-")
                if normalized in facebook_target_ids:
                    self.driver.switch_to.window(handle)  # type: ignore[union-attr]
                    dismiss_facebook_notification_overlay(self.driver)
                    _log.debug(
                        "已由 CDP Target 切換至 Facebook 分頁（handle=%s）。",
                        handle,
                    )
                    return True
        except (TimeoutException, WebDriverException, AttributeError, TypeError):
            pass

        for handle in handles:
            try:
                self.driver.switch_to.window(handle)
                url = self.driver.current_url.lower()
            except (TimeoutException, WebDriverException) as exc:
                # AdsPower 剛啟動時 renderer 偶爾尚未回應。單一分頁讀取
                # 逾時不應讓整個環境失敗，略過後繼續檢查其他分頁。
                summary = (str(exc).splitlines() or [type(exc).__name__])[0].strip()
                _log.warning(
                    "讀取分頁網址暫時逾時，改查下一個分頁（handle=%s）：%s",
                    handle,
                    summary or type(exc).__name__,
                )
                try:
                    self.stop_loading()
                except Exception:
                    pass
                time.sleep(0.2)
                continue
            if (
                "facebook.com" in url
                or "m.facebook.com" in url
                or "web.facebook.com" in url
            ):
                _log.debug("已切換至 Facebook 分頁（handle=%s）。", handle)
                dismiss_facebook_notification_overlay(self.driver)
                return True
        _log.warning("找不到 Facebook 分頁，嘗試導航至首頁。")
        return False

    def open_new_tab(self, url: str = "") -> str:
        """
        開啟新分頁並切換過去。

        Args:
            url: 新分頁要載入的 URL（省略則開空白頁）。

        Returns:
            新分頁的 window handle。
        """
        self._ensure_driver()
        self.driver.execute_script(f"window.open('{url}', '_blank');")  # type: ignore[union-attr]
        new_handle = self.driver.window_handles[-1]  # type: ignore[union-attr]
        self.driver.switch_to.window(new_handle)
        return new_handle

    def close_current_tab(self) -> None:
        """關閉目前分頁並切換回第一個分頁。"""
        self._ensure_driver()
        self.driver.close()
        if self.driver.window_handles:
            self.driver.switch_to.window(self.driver.window_handles[0])

    # ── 內部工具 ────────────────────────────────

    def _ensure_driver(self) -> None:
        """確認 driver 已連線，否則拋出例外。"""
        if self.driver is None:
            raise RuntimeError("WebDriver 尚未連線，請先呼叫 connect()。")

    def move_mouse_randomly(self) -> None:
        """
        隨機移動滑鼠至頁面某處，增加真人感。
        若失敗則靜默忽略。
        """
        self._ensure_driver()
        try:
            width = self.driver.execute_script("return window.innerWidth;")  # type: ignore[union-attr]
            height = self.driver.execute_script("return window.innerHeight;")  # type: ignore[union-attr]
            x = random.randint(100, max(200, int(width) - 100))
            y = random.randint(100, max(200, int(height) - 100))
            actions = ActionChains(self.driver)  # type: ignore[arg-type]
            actions.move_by_offset(x, y).perform()
        except WebDriverException:
            pass
