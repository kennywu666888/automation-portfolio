"""逐一檢查 AdsPower Facebook 環境是否遭 Messenger 帳號層級禁言。

用途：
1. 一次只開啟一個 AdsPower 環境。
2. 切到 Facebook 分頁並進入 Messenger。
3. 偵測「Confirm your identity to send messages」等帳號層級限制。
4. 確認禁言時將環境更名為「聊天室禁言＋原名稱」。
5. 不論結果如何，盡力回到本人個人主頁後解除接管並關閉環境。

本工具不讀取或回覆訊息、不輸入文字，也不會送出任何 Facebook 訊息。
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By


# 原始碼執行時使用程式所在資料夾；PyInstaller 可攜版則使用 EXE
# 所在資料夾，讓設定、Timeline 快取及 LOG 留在可搬移資料夾外層。
BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
load_dotenv(BASE_DIR / ".env")

from 環境管理介面 import AdsPowerClient, ProfileInfo
from 瀏覽器 import BrowserController
from 設定 import CONFIG
from 主程式 import (
    cache_personal_timeline_url,
    configure_chrome_cookie_access,
    detect_account_removal_status,
    prepare_ip_expired_profile,
    prepare_profile_removal,
    return_to_personal_profile,
)
from 臉書操作 import HealthChecker, HealthStatus
from 訊息選擇器 import (
    chat_id_from_url,
    chat_muted_profile_name,
    click_chat_item,
    find_message_input,
    has_chat_identity_restriction,
    restriction_scope,
    suppress_messenger_restore_prompts,
    wait_for_chat_items,
)
from 任務診斷 import save_task_diagnostic
from 個人資料工具 import profile_matches_search, sort_profiles_by_number


MESSENGER_URL = "https://www.facebook.com/messages"
FACEBOOK_HOME_URL = "https://www.facebook.com/"
PROFILE_CACHE_FILE = BASE_DIR / "facebook_profile_urls.json"
GUI_SETTINGS_FILE = BASE_DIR / "gui_settings.json"
LOG_DIR = BASE_DIR / "logs"
PROFILE_URL_RE = re.compile(
    r"^https?://(?:www\.)?facebook\.com/profile\.php\?id=(\d+)",
    flags=re.IGNORECASE,
)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("臨時聊天室禁言檢查")
    if logger.handlers:
        return logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    path = LOG_DIR / (
        "臨時聊天室禁言檢查_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
    )
    file_handler = RotatingFileHandler(
        path, maxBytes=8 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.log_path = path  # type: ignore[attr-defined]
    return logger


LOG = _build_logger()


class RemovalDetected(RuntimeError):
    """用於中止目前環境後續流程；真正關閉／刪除統一由 finally 處理。"""


def _safe_error(exc: BaseException) -> str:
    """只保留例外第一行，避免 Selenium 原生 stacktrace 淹沒 GUI。"""
    first = (str(exc).splitlines() or [type(exc).__name__])[0].strip()
    return first or type(exc).__name__


def _load_saved_api_key() -> str:
    """讀取主程式已儲存的 AdsPower API Key；絕不寫入 LOG。"""
    key = str(os.environ.get("ADSPOWER_API_KEY", "") or "").strip()
    if key:
        return key
    try:
        data = json.loads(GUI_SETTINGS_FILE.read_text(encoding="utf-8"))
        return str(data.get("adspower_api_key", "") or "").strip()
    except Exception:
        return ""


def _save_api_key(api_key: str) -> None:
    """沿用主程式設定檔儲存 API Key；不在畫面或 LOG 顯示內容。"""
    data = {}
    try:
        loaded = json.loads(GUI_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except Exception:
        pass
    data["adspower_api_key"] = str(api_key or "").strip()
    temp = GUI_SETTINGS_FILE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(GUI_SETTINGS_FILE)


def _load_profile_cache() -> dict[str, str]:
    try:
        data = json.loads(PROFILE_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                str(key): str(value)
                for key, value in data.items()
                if str(key).strip() and PROFILE_URL_RE.match(str(value).strip())
            }
    except Exception:
        pass
    return {}


def _save_profile_cache(cache: dict[str, str]) -> None:
    temp = PROFILE_CACHE_FILE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(PROFILE_CACHE_FILE)


def _profile_url_from_current_page(driver) -> str:
    try:
        current = str(driver.current_url or "").strip()
    except Exception:
        return ""
    match = PROFILE_URL_RE.match(current)
    if not match:
        return ""
    return "https://www.facebook.com/profile.php?id=" + match.group(1)


def _body_text(driver) -> str:
    try:
        return str(driver.execute_script(
            "return document.body ? (document.body.innerText || '') : '';"
        ) or "")
    except Exception:
        try:
            return driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            return ""


def _navigate(driver, controller: BrowserController, url: str) -> None:
    try:
        driver.get(url)
    except (TimeoutException, WebDriverException):
        try:
            controller.stop_loading()
        except Exception:
            pass


def _main_program_is_running() -> bool:
    """主 GUI 即使閒置也要求先關閉，避免使用者途中啟動批次造成重疊。"""
    if os.name != "nt":
        return False
    script = (
        "$p=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Name -match '^python(w)?(\\.exe)?$' -and "
        "$_.CommandLine -match '[\\\\/]main\\.py([\"\' ]|$)' }; "
        "if($p){'RUNNING'}"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return "RUNNING" in completed.stdout
    except Exception:
        return False


class MutedCheckEngine:
    def __init__(self, api: AdsPowerClient, stop_event: threading.Event, emit):
        self.api = api
        self.stop_event = stop_event
        self.emit = emit
        self.cache = _load_profile_cache()

    def _log(self, profile_name: str, message: str, level: int = logging.INFO) -> None:
        text = f"[{profile_name}] {message}"
        LOG.log(level, text)
        self.emit(text)

    def _discover_personal_url(
        self,
        ctrl: BrowserController,
        profile: ProfileInfo,
    ) -> str:
        driver = ctrl.driver
        if driver is None:
            return ""
        cached = str(self.cache.get(profile.profile_id, "") or "").strip()
        if PROFILE_URL_RE.match(cached):
            driver._facebook_personal_profile_url = cached
            self._log(profile.name, "已載入本人個人主頁網址快取。")
            return cached

        current = _profile_url_from_current_page(driver)
        if current:
            driver._facebook_personal_profile_url = current
            self.cache[profile.profile_id] = current
            _save_profile_cache(self.cache)
            self._log(profile.name, "已從 AdsPower 啟動頁記住本人個人主頁。")
            return current
        return ""

    def _ensure_personal_url_after_check(
        self,
        ctrl: BrowserController,
        profile: ProfileInfo,
    ) -> str:
        driver = ctrl.driver
        if driver is None:
            return ""
        known = str(getattr(driver, "_facebook_personal_profile_url", "") or "")
        if PROFILE_URL_RE.match(known):
            return known
        cached = str(self.cache.get(profile.profile_id, "") or "")
        if PROFILE_URL_RE.match(cached):
            driver._facebook_personal_profile_url = cached
            return cached

        self._log(
            profile.name,
            "尚無本人主頁網址，檢查完成後從 Facebook 首頁讀取一次 Timeline。",
        )
        _navigate(driver, ctrl, FACEBOOK_HOME_URL)
        time.sleep(1.0)
        discovered = cache_personal_timeline_url(
            ctrl,
            profile.name,
            # 清理階段即使使用者已按停止，也要完成目前環境的回首頁動作。
            None,
        )
        self.cache[profile.profile_id] = discovered
        _save_profile_cache(self.cache)
        return discovered

    def _check_messenger(self, ctrl: BrowserController, profile: ProfileInfo) -> tuple[str, str]:
        """回傳 (normal/restricted/unknown, 說明)。"""
        driver = ctrl.driver
        if driver is None:
            return "unknown", "Driver 不存在"

        _navigate(driver, ctrl, MESSENGER_URL)
        restriction = ""
        page_text = ""
        deadline = time.monotonic() + 7.0
        while time.monotonic() < deadline and not self.stop_event.is_set():
            suppress_messenger_restore_prompts(driver)
            page_text = _body_text(driver)
            restriction = restriction_scope(page_text)
            if restriction == "account" or has_chat_identity_restriction(page_text):
                return "restricted", (
                    "Confirm your identity to send messages"
                    if has_chat_identity_restriction(page_text)
                    else "帳號層級 Messenger 傳送限制"
                )
            if wait_for_chat_items(driver, timeout=0.5):
                break
            time.sleep(0.2)

        items = wait_for_chat_items(driver, timeout=5.0)
        if not items:
            if restriction == "chat":
                return "unknown", "目前頁面只有單一聊天室／收件人限制，不能判定整個帳號禁言"
            return "unknown", "Messenger 沒有可開啟的聊天室，無法確認傳送權限"

        href = items[0].get_attribute("href") or ""
        expected_id = chat_id_from_url(href)
        if not href or not click_chat_item(driver, href, timeout=5.0):
            return "unknown", "第一個聊天室無法開啟"

        deadline = time.monotonic() + 8.0
        saw_input = False
        restriction = ""
        while time.monotonic() < deadline and not self.stop_event.is_set():
            suppress_messenger_restore_prompts(driver)
            page_text = _body_text(driver)
            restriction = restriction_scope(page_text)
            if restriction == "account" or has_chat_identity_restriction(page_text):
                return "restricted", (
                    "Confirm your identity to send messages"
                    if has_chat_identity_restriction(page_text)
                    else "帳號層級 Messenger 傳送限制"
                )
            try:
                current_id = chat_id_from_url(driver.current_url)
            except Exception:
                current_id = ""
            if not expected_id or current_id == expected_id:
                saw_input = find_message_input(driver) is not None
                if saw_input:
                    return "normal", "已看到可用的訊息輸入欄，未偵測到帳號層級禁言"
            time.sleep(0.25)

        if restriction == "chat":
            return "unknown", "只有單一聊天室／收件人限制，未判定為帳號禁言"
        if saw_input:
            return "normal", "已看到訊息輸入欄"
        return "unknown", "未出現帳號限制文字，但也沒有可確認的訊息輸入欄"

    def _removal_decision(
        self,
        ctrl: BrowserController,
        profile: ProfileInfo,
    ) -> tuple[str, str]:
        """沿用主程式狀態判定，不擴大刪除範圍。

        代理驗證／Tunnel／重試後仍逾時，均屬於只更名、關閉、不刪除
        的 IP 到期安全分支；實際錯誤碼由主程式共用判定器辨識。
        """
        kind, new_name = detect_account_removal_status(ctrl, profile)
        if kind:
            return kind, new_name

        # 主程式另將明確的 Facebook 登入頁視為失效環境並永久刪除。
        try:
            health, _detail = HealthChecker(ctrl).check()
        except Exception:
            return "", ""
        if health == HealthStatus.LOGIN_PAGE:
            original = (profile.name or "").strip() or profile.profile_id
            return (
                "login_page",
                original if original.startswith("登入") else f"登入{original}",
            )
        return "", ""

    def process(self, profile: ProfileInfo) -> dict[str, str]:
        ctrl: BrowserController | None = None
        result = "error"
        detail = "尚未開始"
        renamed = ""
        delete_after_detection = False
        removal_kind = ""
        removal_name = ""
        deleted = False
        try:
            self._log(profile.name, "正在開啟 AdsPower 環境。")
            session = self.api.get_or_open_browser(profile.profile_id)
            ctrl = BrowserController(session)
            ctrl.connect()
            if not ctrl.switch_to_facebook_tab():
                ctrl.navigate(FACEBOOK_HOME_URL)
            ctrl.bring_window_to_front()
            configure_chrome_cookie_access(ctrl, profile.name)

            # 與主程式一致：代理／IP失效只更名為 IP到期並關閉、不刪除；
            # 其他既有刪除狀態只有更名成功才允許稍後永久刪除。
            removal_kind, removal_name = self._removal_decision(ctrl, profile)
            if removal_kind:
                if removal_kind == "tunnel_connection_failed":
                    rename_ok = prepare_ip_expired_profile(
                        self.api, profile, removal_name
                    )
                    result = "ip_expired"
                    detail = (
                        f"代理／IP連線失效；已更名為「{removal_name}」"
                        if rename_ok
                        else f"代理／IP連線失效；更名為「{removal_name}」失敗"
                    ) + "；環境將關閉但不刪除"
                    self._log(
                        profile.name,
                        detail,
                        logging.WARNING,
                    )
                else:
                    delete_after_detection = prepare_profile_removal(
                        self.api,
                        profile,
                        removal_kind,
                        removal_name,
                    )
                    result = "removal_detected"
                    detail = f"偵測到主程式刪除狀態：{removal_kind}"
                    if delete_after_detection:
                        self._log(
                            profile.name,
                            f"{detail}；已更名為「{removal_name}」，準備關閉並永久刪除。",
                            logging.WARNING,
                        )
                    else:
                        detail += "；更名失敗，基於安全原則不刪除"
                        self._log(profile.name, detail, logging.ERROR)
                raise RemovalDetected(detail)

            self._discover_personal_url(ctrl, profile)

            self._log(profile.name, "正在檢查 Messenger 是否有帳號層級禁言。")
            result, detail = self._check_messenger(ctrl, profile)
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")

            # Messenger 導頁後異常頁才可能完成渲染，因此再沿用相同刪除判定。
            removal_kind, removal_name = self._removal_decision(ctrl, profile)
            if removal_kind:
                if removal_kind == "tunnel_connection_failed":
                    rename_ok = prepare_ip_expired_profile(
                        self.api, profile, removal_name
                    )
                    result = "ip_expired"
                    detail = (
                        f"代理／IP連線失效；已更名為「{removal_name}」"
                        if rename_ok
                        else f"代理／IP連線失效；更名為「{removal_name}」失敗"
                    ) + "；環境將關閉但不刪除"
                    self._log(
                        profile.name,
                        detail,
                        logging.WARNING,
                    )
                else:
                    delete_after_detection = prepare_profile_removal(
                        self.api,
                        profile,
                        removal_kind,
                        removal_name,
                    )
                    result = "removal_detected"
                    detail = f"偵測到主程式刪除狀態：{removal_kind}"
                    if delete_after_detection:
                        self._log(
                            profile.name,
                            f"{detail}；已更名為「{removal_name}」，準備關閉並永久刪除。",
                            logging.WARNING,
                        )
                    else:
                        detail += "；更名失敗，基於安全原則不刪除"
                        self._log(profile.name, detail, logging.ERROR)
                raise RemovalDetected(detail)

            if result == "restricted":
                renamed = chat_muted_profile_name(profile.name, profile.profile_id)
                if renamed != profile.name:
                    if not self.api.rename_profile(profile.profile_id, renamed):
                        detail += "；AdsPower 更名失敗"
                        renamed = ""
                    else:
                        self._log(profile.name, f"確認禁言，已更名為「{renamed}」。", logging.WARNING)
                else:
                    renamed = profile.name
                    self._log(profile.name, "確認禁言；環境名稱已含聊天室禁言前綴。", logging.WARNING)
            elif result == "normal":
                self._log(profile.name, f"檢查正常：{detail}")
            else:
                self._log(profile.name, f"無法完整判定：{detail}", logging.WARNING)
                try:
                    save_task_diagnostic(
                        ctrl.driver,
                        profile.name,
                        "臨時聊天室禁言檢查",
                        detail,
                    )
                except Exception:
                    pass
        except RemovalDetected:
            # 已完整記錄；跳到 finally 執行解除接管、關閉與條件式刪除。
            # Tunnel 分支的 delete_after_detection 固定為 False，因此不會刪除。
            pass
        except InterruptedError:
            result, detail = "stopped", "使用者停止"
            self._log(profile.name, "收到停止要求；仍會先回個人主頁並關閉環境。", logging.WARNING)
        except Exception as exc:
            result, detail = "error", _safe_error(exc)
            self._log(profile.name, f"檢查失敗：{detail}", logging.ERROR)
            if ctrl is not None:
                try:
                    save_task_diagnostic(
                        ctrl.driver,
                        profile.name,
                        "臨時聊天室禁言檢查",
                        detail,
                    )
                except Exception:
                    pass
        finally:
            if (
                not removal_kind
                and ctrl is not None
                and ctrl.driver is not None
            ):
                try:
                    self._ensure_personal_url_after_check(ctrl, profile)
                    if return_to_personal_profile(
                        ctrl,
                        renamed or profile.name,
                        "臨時禁言檢查完成後",
                        None,
                    ):
                        self._log(profile.name, "已回到本人個人主頁。")
                    else:
                        self._log(profile.name, "回到本人個人主頁失敗。", logging.ERROR)
                except Exception as exc:
                    self._log(
                        profile.name,
                        f"回個人主頁失敗：{_safe_error(exc)}",
                        logging.ERROR,
                    )
                try:
                    ctrl.detach_keep_browser()
                except Exception:
                    pass
            elif ctrl is not None and ctrl.driver is not None:
                # 異常狀態不需返回個人主頁；先安全解除 Selenium 接管。
                try:
                    ctrl.detach_keep_browser()
                except Exception:
                    pass
            try:
                self.api.close_browser(profile.profile_id)
                self._log(profile.name, "AdsPower 環境已關閉。")
            except Exception as exc:
                self._log(profile.name, f"關閉環境失敗：{_safe_error(exc)}", logging.ERROR)
            if delete_after_detection:
                inactive_deadline = time.monotonic() + 12.0
                while (
                    time.monotonic() < inactive_deadline
                    and self.api.check_status(profile.profile_id)
                ):
                    time.sleep(0.4)
                try:
                    deleted = bool(self.api.delete_profile(profile.profile_id))
                except Exception as exc:
                    deleted = False
                    self._log(
                        profile.name,
                        f"永久刪除失敗：{_safe_error(exc)}",
                        logging.ERROR,
                    )
                if deleted:
                    result = "deleted"
                    detail = f"{removal_kind}；環境已永久刪除"
                    self.cache.pop(profile.profile_id, None)
                    try:
                        _save_profile_cache(self.cache)
                    except Exception:
                        pass
                    self._log(
                        profile.name,
                        f"{removal_kind} 環境已永久刪除（更名後：{removal_name}）。",
                        logging.WARNING,
                    )
                else:
                    result = "delete_failed"
                    detail = f"{removal_kind}；環境刪除失敗，請手動確認"
                    self._log(profile.name, detail, logging.ERROR)
        return {
            "profile_id": profile.profile_id,
            "profile_name": profile.name,
            "result": result,
            "detail": detail,
            "renamed": renamed,
            "deleted": "yes" if deleted else "no",
        }


class TemporaryCheckerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("臨時工具｜聊天室禁言檢查＋返回個人主頁")
        self.root.geometry("900x680")
        self.root.minsize(760, 560)
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.profiles: list[ProfileInfo] = []
        self.visible_profiles: list[ProfileInfo] = []
        self.groups: list[dict] = []

        self.api = AdsPowerClient()
        saved_key = _load_saved_api_key()
        self.api_key_var = tk.StringVar(value=saved_key)
        self.api_key_status_var = tk.StringVar(
            value="已讀取主程式設定" if saved_key else "尚未輸入"
        )
        if saved_key:
            self.api.set_api_key(saved_key)
            CONFIG.adspower.api_key = saved_key

        self.group_var = tk.StringVar(value="讀取中……")
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="準備中")
        self._build_ui()
        self.search_var.trace_add("write", lambda *_: self._render_profiles())
        self.root.after(100, self._poll_events)
        self._load_groups()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        notice = (
            "只檢查 Messenger 傳送限制，不讀取、不輸入、不發送訊息。"
            "每個環境完成後會回到本人個人主頁並關閉 AdsPower。"
        )
        ttk.Label(outer, text=notice, wraplength=850).pack(anchor="w", pady=(0, 10))

        api_row = ttk.Frame(outer)
        api_row.pack(fill="x", pady=(0, 10))
        ttk.Label(api_row, text="AdsPower API Key：").pack(side="left")
        self.api_key_entry = ttk.Entry(
            api_row,
            textvariable=self.api_key_var,
            show="•",
            width=48,
        )
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self.api_test_button = ttk.Button(
            api_row,
            text="測試連線",
            command=self._test_api_key,
        )
        self.api_test_button.pack(side="left")
        ttk.Label(api_row, textvariable=self.api_key_status_var).pack(
            side="left", padx=(8, 0)
        )

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="AdsPower 群組：").pack(side="left")
        self.group_combo = ttk.Combobox(
            toolbar, textvariable=self.group_var, state="readonly", width=32
        )
        self.group_combo.pack(side="left", padx=(4, 8))
        self.load_button = ttk.Button(toolbar, text="讀取環境", command=self._load_profiles)
        self.load_button.pack(side="left")
        ttk.Button(toolbar, text="全選", command=lambda: self.listbox.select_set(0, "end")).pack(
            side="left", padx=(12, 4)
        )
        ttk.Button(toolbar, text="清除選取", command=lambda: self.listbox.selection_clear(0, "end")).pack(
            side="left"
        )

        search_row = ttk.Frame(outer)
        search_row.pack(fill="x", pady=(8, 0))
        ttk.Label(search_row, text="搜尋（名稱／群組／ID／序號／IP）：").pack(side="left")
        ttk.Entry(search_row, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )

        list_frame = ttk.LabelFrame(outer, text="環境（可多選）", padding=8)
        list_frame.pack(fill="both", expand=True, pady=10)
        self.listbox = tk.Listbox(list_frame, selectmode="extended", exportselection=False)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        action = ttk.Frame(outer)
        action.pack(fill="x")
        self.start_button = ttk.Button(action, text="開始檢查所選環境", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(action, text="停止", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(action, textvariable=self.status_var).pack(side="left", padx=10)

        log_frame = ttk.LabelFrame(outer, text="執行紀錄", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def _append(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _background(self, func) -> None:
        threading.Thread(target=func, daemon=True).start()

    def _load_groups(self) -> None:
        def worker():
            try:
                groups = self.api.list_groups()
                self.events.put(("groups", groups))
            except Exception as exc:
                self.events.put(("error", "讀取 AdsPower 群組失敗：" + _safe_error(exc)))
        self._background(worker)

    def _test_api_key(self) -> None:
        if self.running:
            return
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning(
                "尚未輸入",
                "請先輸入 AdsPower API Key。永久刪除環境必須使用 API Key。",
                parent=self.root,
            )
            return
        self.api.set_api_key(api_key)
        CONFIG.adspower.api_key = api_key
        self.api_key_status_var.set("測試中……")
        self.api_test_button.configure(state="disabled")

        def worker():
            try:
                self.api.test_connection()
                _save_api_key(api_key)
                self.events.put(("api_test", True, "連線成功，可執行刪除"))
            except Exception as exc:
                self.events.put(("api_test", False, _safe_error(exc)))
        self._background(worker)

    def _load_profiles(self) -> None:
        if self.running:
            return
        label = self.group_var.get()
        group_id = "0"
        for item in self.groups:
            if item["group_name"] == label:
                group_id = item["group_id"]
                break
        self.status_var.set("正在讀取環境……")
        self.load_button.configure(state="disabled")

        def worker():
            try:
                profiles = self.api.list_profiles_by_group(group_id)
                self.events.put(("profiles", profiles))
            except Exception as exc:
                self.events.put(("error", "讀取 AdsPower 環境失敗：" + _safe_error(exc)))
        self._background(worker)

    def _render_profiles(self) -> None:
        keyword = self.search_var.get().strip()
        self.visible_profiles = [
            profile
            for profile in self.profiles
            if profile_matches_search(profile, keyword)
        ]
        self.listbox.delete(0, "end")
        for profile in self.visible_profiles:
            self.listbox.insert("end", f"{profile.name}  (id={profile.profile_id})")
        if self.visible_profiles:
            # This temporary batch tool historically defaults to all profiles.
            # Search results follow the same rule so execution is predictable.
            self.listbox.select_set(0, "end")
        self.status_var.set(
            f"顯示 {len(self.visible_profiles)}／{len(self.profiles)} 個環境，預設全選"
        )

    def _start(self) -> None:
        if self.running:
            return
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showerror(
                "缺少 AdsPower API Key",
                "請先輸入並測試 AdsPower API Key；否則無法安全執行永久刪除。",
                parent=self.root,
            )
            return
        indexes = list(self.listbox.curselection())
        if not indexes:
            messagebox.showwarning("尚未選取", "請先選取至少一個環境。", parent=self.root)
            return
        if _main_program_is_running():
            messagebox.showerror(
                "主程式仍在執行",
                "請先完整關閉 12 功能版主程式，再啟動此臨時工具，避免同時接管 AdsPower。",
                parent=self.root,
            )
            return
        selected = sort_profiles_by_number(
            self.visible_profiles[index] for index in indexes
        )
        if not messagebox.askyesno(
            "確認開始",
            f"將依序檢查 {len(selected)} 個環境。\n\n"
            "偵測到帳號層級聊天室禁言時會更名；一般環境最後會返回個人主頁並關閉。\n"
            "代理驗證／Tunnel／重試後仍逾時時，只更名為「IP到期＋原名稱」並關閉，不會刪除。\n"
            "若命中其他主程式既有刪除規則，才會先更名、關閉並永久刪除環境。\n"
            "請確認沒有其他程式正在控制 AdsPower。",
            parent=self.root,
        ):
            return

        self.running = True
        self.stop_event.clear()
        self.api.set_api_key(api_key)
        CONFIG.adspower.api_key = api_key
        self.start_button.configure(state="disabled")
        self.load_button.configure(state="disabled")
        self.api_test_button.configure(state="disabled")
        self.api_key_entry.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("正在驗證 AdsPower API Key……")

        def worker():
            try:
                # 刪除屬於不可逆操作；每次正式批次前都重新驗證 API Key。
                self.api.test_connection()
                _save_api_key(api_key)
                self.events.put(("api_status", "API Key 驗證成功，開始處理環境。"))
            except Exception as exc:
                self.events.put(("run_error", "AdsPower API Key 驗證失敗：" + _safe_error(exc)))
                return
            engine = MutedCheckEngine(self.api, self.stop_event, lambda msg: self.events.put(("log", msg)))
            counts = {
                "normal": 0,
                "restricted": 0,
                "ip_expired": 0,
                "unknown": 0,
                "error": 0,
                "stopped": 0,
                "deleted": 0,
                "delete_failed": 0,
                "removal_detected": 0,
            }
            completed = 0
            for profile in selected:
                if self.stop_event.is_set():
                    break
                outcome = engine.process(profile)
                counts[outcome["result"]] = counts.get(outcome["result"], 0) + 1
                completed += 1
                self.events.put(("progress", completed, len(selected)))
                if not self.stop_event.is_set():
                    self.stop_event.wait(1.5)
            self.events.put(("done", completed, len(selected), counts))
        self._background(worker)

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.status_var.set("正在安全停止；目前環境仍會先返回個人主頁並關閉……")
        self._append("已要求停止，不會再開啟下一個環境。")

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "groups":
                    self.groups = [{"group_id": "0", "group_name": "全部群組"}] + list(event[1])
                    names = [item["group_name"] for item in self.groups]
                    self.group_combo["values"] = names
                    default_name = "全部群組"
                    for item in self.groups:
                        if item["group_id"] == str(CONFIG.adspower.target_group_id):
                            default_name = item["group_name"]
                            break
                    self.group_var.set(default_name)
                    self._load_profiles()
                elif kind == "profiles":
                    self.profiles = sort_profiles_by_number(event[1])
                    self._render_profiles()
                    self.load_button.configure(state="normal")
                elif kind == "log":
                    self._append(str(event[1]))
                elif kind == "api_test":
                    success, message = bool(event[1]), str(event[2])
                    self.api_test_button.configure(state="normal")
                    self.api_key_status_var.set("連線成功" if success else "連線失敗")
                    if success:
                        self._append("AdsPower API Key 連線測試成功。")
                        if not self.groups:
                            self._load_groups()
                    else:
                        messagebox.showerror(
                            "API Key 測試失敗",
                            message,
                            parent=self.root,
                        )
                elif kind == "api_status":
                    self.api_key_status_var.set("連線成功")
                    self._append(str(event[1]))
                elif kind == "progress":
                    self.status_var.set(f"{event[1]}/{event[2]}")
                elif kind == "done":
                    completed, total, counts = event[1], event[2], event[3]
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.load_button.configure(state="normal")
                    self.api_test_button.configure(state="normal")
                    self.api_key_entry.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set(f"完成 {completed}/{total}")
                    summary = (
                        f"完成 {completed}/{total}\n"
                        f"正常：{counts.get('normal', 0)}\n"
                        f"確認禁言：{counts.get('restricted', 0)}\n"
                        f"IP到期（保留未刪除）：{counts.get('ip_expired', 0)}\n"
                        f"永久刪除：{counts.get('deleted', 0)}\n"
                        f"刪除失敗：{counts.get('delete_failed', 0)}\n"
                        f"命中刪除規則但未刪除：{counts.get('removal_detected', 0)}\n"
                        f"無法判定：{counts.get('unknown', 0)}\n"
                        f"錯誤：{counts.get('error', 0)}"
                    )
                    self._append(summary.replace("\n", "；"))
                    messagebox.showinfo("檢查完成", summary, parent=self.root)
                elif kind == "error":
                    self.load_button.configure(state="normal")
                    self.status_var.set("發生錯誤")
                    self._append(str(event[1]))
                    messagebox.showerror("錯誤", str(event[1]), parent=self.root)
                elif kind == "run_error":
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.load_button.configure(state="normal")
                    self.api_test_button.configure(state="normal")
                    self.api_key_entry.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status_var.set("API Key 驗證失敗，未開啟任何環境")
                    self.api_key_status_var.set("連線失敗")
                    self._append(str(event[1]))
                    messagebox.showerror("無法開始", str(event[1]), parent=self.root)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_events)

    def close(self) -> None:
        if self.running:
            if not messagebox.askyesno(
                "仍在執行",
                "要停止嗎？目前環境仍會先返回個人主頁並關閉，完成後才能關閉視窗。",
                parent=self.root,
            ):
                return
            self._stop()
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = TemporaryCheckerApp(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
