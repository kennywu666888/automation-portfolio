"""Google FLOW 圖片影片自動生成器 V1.6 Three Prompt Modes Stable

需求：Python 3.12+、Windows 10/11、pip install selenium
本程式不會繞過 Google 登入、CAPTCHA、點數或服務限制。
FLOW 是動態網站；若官方介面改版，可在 FlowController.LOCATORS 集中調整定位器。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

try:
    import requests
except ImportError:
    requests = None

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ImportError:
    raise SystemExit("此 Python 缺少 tkinter，請重新安裝含 tkinter 的 Python 3.12+。")

try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        ElementClickInterceptedException, JavascriptException,
        NoSuchElementException, StaleElementReferenceException,
        TimeoutException, WebDriverException,
    )
    from selenium.webdriver import ActionChains
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False


APP_NAME = "Google FLOW 圖片影片自動生成器 V1.6 Three Prompt Modes Stable"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "flow_gui_config.json"
PROGRESS_FILE = BASE_DIR / "flow_task_progress.json"
SCHEDULE_FILE = BASE_DIR / "flow_schedules.json"
PROFILE_DIR = BASE_DIR / "FLOW_Chrome_Profile"
MANUAL_PROFILE_DIR = BASE_DIR / "FLOW_Chrome_Profile_Manual"
LOG_DIR = BASE_DIR / "logs"
ERROR_DIR = BASE_DIR / "error_reports"
DOM_DIAG_DIR = BASE_DIR / "flow_dom_diagnostics"
DEFAULT_URL = "https://labs.google/fx/zh/tools/flow"
PROMPT_MODE_1 = "模式1：隨機選取 TXT 文案"
PROMPT_MODE_2 = "模式2：隨機菲律賓背景與人物穿著"
PROMPT_MODE_3 = "模式3：全手動文案"
PROMPT_MODES = [PROMPT_MODE_1, PROMPT_MODE_2, PROMPT_MODE_3]


def natural_key(value: str) -> list[Any]:
    return [int(x) if x.isdigit() else x.casefold() for x in re.split(r"(\d+)", value)]


def xpath_literal(value: str) -> str:
    """產生保留中日韓文字的 XPath 字串常值。"""
    return json.dumps(str(value), ensure_ascii=False)


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class Status(str, Enum):
    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    ADDING_TO_PROMPT = "ADDING_TO_PROMPT"
    READY = "READY"
    SUBMITTED = "SUBMITTED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    DOWNLOADING = "DOWNLOADING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


@dataclass
class FlowTask:
    number: int
    material_path: str = ""
    material_name: str = ""
    prompt: str = ""
    status: str = Status.PENDING.value
    uploaded: bool = False
    added_to_prompt: bool = False
    submitted: bool = False
    generated: bool = False
    expected_count: int = 1
    actual_count: int = 0
    downloaded: list[str] = field(default_factory=list)
    downloaded_indices: list[int] = field(default_factory=list)
    inspected_indices: list[int] = field(default_factory=list)
    retry_count: int = 0
    failure_reason: str = ""
    submit_time: str = ""
    last_update: str = ""
    baseline_cards: int = 0
    baseline_media: list[str] = field(default_factory=list)
    # 送出前已存在的 FLOW tile，以及本次送出後立即新增的 tile。
    # 作品完成順序可能與任務順序不同，因此不能用網格位置配對任務；
    # 必須沿著本次送出所建立的 tile 追蹤到最後的 media UUID。
    baseline_tile_ids: list[str] = field(default_factory=list)
    submission_tile_ids: list[str] = field(default_factory=list)
    generated_media: list[str] = field(default_factory=list)
    pre_upload_media: list[str] = field(default_factory=list)
    material_media: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FlowTask":
        valid = cls.__dataclass_fields__.keys()
        task = cls(**{k: v for k, v in value.items() if k in valid})
        if task.downloaded and not task.downloaded_indices:
            task.downloaded_indices = list(range(1, len(task.downloaded) + 1))
        return task


class ConfigManager:
    DEFAULTS = {
        "chrome_path": "", "profile_path": str(MANUAL_PROFILE_DIR), "flow_url": DEFAULT_URL,
        "debug_port": 9222,
        "generation_type": "圖片模式", "source_mode": "自動上傳資料夾素材",
        "prompt_mode": PROMPT_MODE_1, "material_folder": "",
        "material_list": "", "same_prompt": "", "manual_suffix": "", "prompt_file": "",
        "random_prompt_file": "", "auto_zoom_flow": True, "zoom_percent": 25,
        "make_count": 1, "download_folder": str(BASE_DIR / "downloads"),
        "upload_settle_seconds": 8,
        "image_ratio": "9:16", "image_count": "1x", "image_model": "Nano Banana 2",
        "image_quality": "1K", "image_format": "轉換為 PNG",
        "video_source": "素材", "video_ratio": "9:16", "video_count": "1x",
        "video_model": "Omni Flash", "video_seconds": "8 秒", "video_quality": "720P",
        "image_timeout": 900, "video_timeout": 1800, "check_interval": 5,
        "max_retries": 3, "duplicate_policy": "自動覆蓋", "close_chrome": False,
        "test_submit": False, "prompt_shortage": "停止並修正 TXT",
    }

    @classmethod
    def load(cls) -> dict[str, Any]:
        data = dict(cls.DEFAULTS)
        if CONFIG_FILE.exists():
            try:
                loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict): data.update(loaded)
            except Exception: pass
        # V1.1：舊版 Profile 可能已載入擴充功能或恢復大量舊分頁，改用乾淨專用目錄。
        if Path(str(data.get("profile_path", ""))) == PROFILE_DIR:
            data["profile_path"] = str(MANUAL_PROFILE_DIR)
        # V1.6 三模式相容轉換：移除舊「順序 TXT」模式，原模式2/3/4依序遞補。
        old_mode = str(data.get("prompt_mode", ""))
        mode_map = {
            "模式1：手動選擇文案 TXT 檔": PROMPT_MODE_1,
            "模式2：隨機選取 TXT 文案": PROMPT_MODE_1,
            "模式3：隨機菲律賓背景與人物穿著": PROMPT_MODE_2,
            "模式3：全手動文案": PROMPT_MODE_3,
            "模式4：全手動文案": PROMPT_MODE_3,
        }
        if old_mode in mode_map:
            data["prompt_mode"] = mode_map[old_mode]
        if not str(data.get("random_prompt_file", "")).strip() and str(data.get("prompt_file", "")).strip():
            data["random_prompt_file"] = data["prompt_file"]
        if data.get("prompt_mode") not in PROMPT_MODES:
            data["prompt_mode"] = PROMPT_MODE_1
        data["duplicate_policy"] = "自動覆蓋"
        if not data.get("image_ratio_default_migrated_v19", False):
            if str(data.get("image_ratio", "16:9")) == "16:9":
                data["image_ratio"] = "9:16"
            data["image_ratio_default_migrated_v19"] = True
        return data

    @staticmethod
    def save(data: dict[str, Any]) -> None:
        atomic_json(CONFIG_FILE, data)


class GuiLogHandler(logging.Handler):
    def __init__(self, callback: Callable[[str], None]):
        super().__init__(); self.callback = callback
    def emit(self, record: logging.LogRecord) -> None:
        try: self.callback(self.format(record))
        except Exception: pass


class ChromeManager:
    def __init__(self, log: logging.Logger):
        self.log, self.driver, self.manual_process = log, None, None

    @staticmethod
    def detect() -> str:
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for p in candidates:
            if p.is_file(): return str(p)
        found = shutil.which("chrome") or shutil.which("chrome.exe")
        return found or ""

    @staticmethod
    def port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=.5):
                return True
        except OSError:
            return False

    def launch_manual(self, chrome_path: str, profile: str, url: str, debug_port: int) -> None:
        """不啟動 WebDriver，以普通 Chrome 程序讓使用者手動登入。"""
        if not chrome_path or not Path(chrome_path).is_file():
            raise FileNotFoundError("Chrome 執行檔不存在")
        if self.port_open(debug_port):
            self.log.info("手動登入 Chrome 已在執行，不再重複開啟視窗")
            return
        Path(profile).mkdir(parents=True, exist_ok=True)
        args = [
            chrome_path,
            f"--remote-debugging-port={int(debug_port)}",
            f"--user-data-dir={Path(profile).resolve()}",
            "--start-maximized",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "https://www.google.com/",
        ]
        self.log.info("正在開啟手動專用 Chrome；請自行登入 Google 並進入 FLOW 專案製作頁，此階段尚未啟動 Selenium")
        self.manual_process = subprocess.Popen(args, close_fds=True)

    def attach(self, chrome_path: str, debug_port: int, download_dir: str) -> Any:
        """登入完成後才將 Selenium 連接到既有 Chrome。"""
        if not SELENIUM_OK: raise RuntimeError("缺少 selenium，請執行：pip install selenium")
        if self.driver:
            try: _ = self.driver.current_url; return self.driver
            except Exception: self.driver = None
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        if not self.port_open(debug_port):
            raise RuntimeError("找不到手動登入 Chrome。請先按「開啟手動登入 Chrome」，不要使用平常的 Chrome 視窗。")
        options = Options()
        if chrome_path: options.binary_location = chrome_path
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{int(debug_port)}")
        self.log.info("登入已由使用者完成，正在連接目前 Chrome（連接埠 %s）", debug_port)
        try:
            self.driver = webdriver.Chrome(options=options)
        except WebDriverException as exc:
            raise RuntimeError(
                "無法連接手動登入 Chrome。請先按「開啟手動登入 Chrome」，完成登入後不要關閉該視窗，再按「連接目前 Chrome」。"
            ) from exc
        self.driver.set_page_load_timeout(90)
        try:
            self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow", "downloadPath": str(Path(download_dir).resolve())
            })
        except Exception as exc:
            self.log.warning("無法預先指定下載資料夾，Chrome 可能使用原本下載位置：%s", exc)
        return self.driver

    def start(self, chrome_path: str, profile: str, download_dir: str) -> Any:
        """相容舊呼叫；新版工作流程固定連接手動登入 Chrome。"""
        return self.attach(chrome_path, 9222, download_dir)

    def close(self) -> None:
        if self.driver:
            try: self.driver.quit()
            except Exception: pass
            self.driver = None


class DownloadManager:
    def __init__(self, folder: str, log: logging.Logger):
        self.folder, self.log = Path(folder), log
        self.folder.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[Path, tuple[int, int]]:
        result = {}
        for p in self.folder.iterdir():
            if p.is_file():
                try:
                    s = p.stat(); result[p] = (s.st_size, s.st_mtime_ns)
                except OSError: pass
        return result

    def wait_new(self, before: dict[Path, tuple[int, int]], timeout: int = 180) -> Path:
        end, stable = time.time() + timeout, {}
        while time.time() < end:
            partial = list(self.folder.glob("*.crdownload"))
            candidates = []
            for p in self.folder.iterdir():
                if p.is_file() and not p.name.endswith(".crdownload"):
                    try:
                        st = p.stat()
                        if p not in before or (st.st_size, st.st_mtime_ns) != before[p]:
                            if st.st_size > 0: candidates.append(p)
                    except OSError: pass
            for p in sorted(candidates, key=lambda x: x.stat().st_mtime_ns, reverse=True):
                size = p.stat().st_size
                if stable.get(p) == size and not partial: return p
                stable[p] = size
            time.sleep(.25)
        raise TimeoutError("等待下載檔案逾時")

    def target(self, requested: Path, policy: str) -> Optional[Path]:
        # V1.8 固定同名直接覆蓋，不再產生 _2、_3。
        if requested.exists():
            requested.unlink()
        return requested

    def rename(self, source: Path, task_no: int, item_no: int, policy: str,
               convert_png: bool = False) -> Optional[Path]:
        suffix = source.suffix.lower() or ".bin"
        if convert_png and suffix in {".jpg", ".jpeg", ".webp"}:
            try:
                from PIL import Image
                requested = self.folder / f"{task_no}_{item_no}.png"
                target = self.target(requested, policy)
                if target is None: return None
                with Image.open(source) as img: img.convert("RGBA").save(target, "PNG")
                source.unlink(); return target
            except ImportError:
                self.log.warning("未安裝 Pillow，無法真正轉換 PNG，將保留原始格式；可執行 pip install pillow")
        requested = self.folder / f"{task_no}_{item_no}{suffix}"
        target = self.target(requested, policy)
        if target is None: return None
        if source.resolve() != target.resolve(): source.replace(target)
        return target

    def rename_sequence(self, source: Path, sequence: int, policy: str) -> Optional[Path]:
        """Rename a confirmed generated work to 1, 2, 3... without leading zeros."""
        requested = self.folder / f"{sequence}{source.suffix.lower() or '.bin'}"
        target = self.target(requested, policy)
        if target is None:
            return None
        if source.resolve() != target.resolve():
            source.replace(target)
        return target


class FlowController:
    """FLOW DOM 操作集中處。定位器皆使用多候選，不使用固定座標。"""
    def __init__(self, driver: Any, log: logging.Logger, stop: threading.Event,
                 pause: threading.Event, cfg: dict[str, Any]):
        self.d, self.log, self.stop, self.pause, self.cfg = driver, log, stop, pause, cfg
        self.wait = WebDriverWait(driver, 25, ignored_exceptions=(StaleElementReferenceException,))

    def checkpoint(self) -> None:
        if self.stop.is_set(): raise InterruptedError("使用者停止")
        while not self.pause.is_set():
            if self.stop.wait(.2): raise InterruptedError("使用者停止")

    def dismiss_toasts(self) -> None:
        """關閉畫面上殘留的提示訊息（例如「已完成高清重塑，圖片已下載！」）。
        這類 Toast 會疊在畫面固定位置，如果剛好蓋住某張作品卡片的「更多選項」
        按鈕，Selenium 的點擊會被攔截或打不中，造成該張卡片的下載選單一直開不起來。
        """
        try:
            closers = self.d.find_elements(
                By.XPATH,
                "//button[normalize-space(.)='關閉' or normalize-space(.)='关闭' "
                "or @aria-label='關閉' or @aria-label='关闭']",
            )
            for btn in closers:
                try:
                    if btn.is_displayed():
                        btn.click()
                except Exception:
                    pass
        except Exception:
            pass

    def find(self, locators: Iterable[tuple[str, str]], timeout: int = 20,
             clickable: bool = False, visible: bool = True) -> Any:
        end, last = time.time() + timeout, None
        while time.time() < end:
            self.checkpoint()
            for by, value in locators:
                try:
                    elements = self.d.find_elements(by, value)
                    for e in elements:
                        if (not visible or e.is_displayed()) and (not clickable or e.is_enabled()): return e
                except (StaleElementReferenceException, WebDriverException) as exc: last = exc
            time.sleep(.35)
        raise TimeoutException(f"找不到網頁元素：{list(locators)}；{last or ''}")

    def safe_click(self, e: Any) -> None:
        self.d.execute_script("arguments[0].scrollIntoView({block:'center'});", e)
        try: e.click()
        except (ElementClickInterceptedException, WebDriverException):
            try: ActionChains(self.d).move_to_element(e).pause(.2).click().perform()
            except Exception: self.d.execute_script("arguments[0].click();", e)

    def text_button(self, texts: list[str], timeout: int = 20) -> Any:
        wanted = " or ".join([f"contains(normalize-space(.), {xpath_literal(t)})" for t in texts])
        aria = " or ".join([f"contains(@aria-label, {xpath_literal(t)})" for t in texts])
        return self.find([
            (By.XPATH, f"//button[{wanted} or {aria}]"),
            (By.XPATH, f"//*[@role='button'][{wanted} or {aria}]"),
        ], timeout, clickable=True)

    def open(self, url: str) -> None:
        try: self.d.get(url)
        except TimeoutException: self.log.warning("FLOW 頁面載入逾時，繼續檢查已載入內容")
        self.find([(By.TAG_NAME, "body")], 30)

    def is_logged_in(self) -> bool:
        url = self.d.current_url.lower()
        if "accounts.google.com" in url: return False
        body = self.d.find_element(By.TAG_NAME, "body").text
        return not any(x in body for x in ["登入 Google", "登录 Google", "Sign in with Google"])

    def prompt_box(self) -> Any:
        return self.find([
            (By.CSS_SELECTOR, "div[data-slate-editor='true'][role='textbox'][contenteditable='true']"),
            (By.CSS_SELECTOR, "textarea[placeholder]"),
            (By.CSS_SELECTOR, "[contenteditable='true'][role='textbox']"),
            (By.CSS_SELECTOR, "textarea"), (By.CSS_SELECTOR, "[contenteditable='true']"),
        ], 30, clickable=True)

    def prompt_value(self, element: Optional[Any] = None) -> str:
        """讀取 Slate 真正內容，排除畫面 placeholder 文字。"""
        e = element or self.prompt_box()
        if e.get_attribute("data-slate-editor") == "true":
            return str(self.d.execute_script("""
                return Array.from(arguments[0].querySelectorAll('[data-slate-string="true"]'))
                    .map(x => x.textContent || '').join(String.fromCharCode(10));
            """, e) or "")
        return str(e.get_attribute("value") or e.get_attribute("textContent") or e.text or "")

    def set_prompt(self, prompt: str) -> None:
        prompt = prompt.rstrip("\r\n")
        if not prompt.strip(): raise ValueError("提示詞不可為空")
        e = self.prompt_box(); self.safe_click(e)
        # Slate 是 React 控制的 contenteditable；不可直接改 textContent，否則畫面有字但內部狀態仍為空。
        ActionChains(self.d).click(e).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
        if e.get_attribute("data-slate-editor") == "true":
            # 使用真實鍵盤輸入事件，分段輸入可避免長文案被 ChromeDriver 截斷。
            for i in range(0, len(prompt), 200):
                e.send_keys(prompt[i:i + 200])
        else:
            e.send_keys(prompt)

        end = time.time() + 12
        while time.time() < end:
            value = self.prompt_value(e)
            arrows = self.d.find_elements(By.XPATH, "//button[.//i[normalize-space(.)='arrow_forward']]")
            enabled = any(
                x.is_displayed() and x.is_enabled() and x.get_attribute("aria-disabled") != "true"
                for x in arrows
            )
            if prompt.strip() in value.strip() and enabled:
                self.log.info("提示詞已由 Slate 編輯器接受，送出按鈕已啟用")
                return
            time.sleep(.5)
        raise RuntimeError("提示詞雖顯示在畫面，但 FLOW Slate 編輯器未接受，送出按鈕仍為停用狀態")

    def plus(self) -> None:
        e = self.find([
            (By.XPATH, "//button[.//i[normalize-space(.)='add_2']]"),
            (By.CSS_SELECTOR, "button[aria-label*='新增']"), (By.CSS_SELECTOR, "button[aria-label*='添加']"),
            (By.CSS_SELECTOR, "button[aria-label*='媒體']"), (By.CSS_SELECTOR, "button[aria-label*='素材']"),
            (By.XPATH, "//button[normalize-space(.)='+' or .//*[normalize-space(.)='+']]"),
        ], 25, clickable=True); self.safe_click(e)

    def ensure_material_dialog(self) -> Any:
        dialogs = self.d.find_elements(By.CSS_SELECTOR, "div[role='dialog'][data-state='open']")
        visible = [x for x in dialogs if x.is_displayed()]
        if visible: return visible[-1]
        self.plus()
        return self.find([(By.CSS_SELECTOR, "div[role='dialog'][data-state='open']")], 15)

    def upload(self, path: str) -> None:
        dialog = self.ensure_material_dialog()
        # 不點「上傳媒體」按鈕，否則 Windows 會顯示原生檔案選擇視窗。
        # Selenium 直接將完整路徑送入隱藏 input[type=file]。
        inp = self.find([
            (By.CSS_SELECTOR, "input[type='file'][accept*='image']"),
            (By.CSS_SELECTOR, "input[type='file']"),
        ], 15, visible=False)
        inp.send_keys(str(Path(path).resolve()))
        # 只看到檔名不代表 FLOW 已完成縮圖與素材索引；必須等候該素材選項可用。
        name = Path(path).name
        def upload_ready(_d: Any) -> bool:
            try:
                current_dialog = self.ensure_material_dialog()
                options = current_dialog.find_elements(By.CSS_SELECTOR, "div[role='option']")
                matches = [x for x in options if x.is_displayed() and name in x.text]
                for option in matches:
                    if option.get_attribute("aria-busy") == "true":
                        continue
                    if option.find_elements(By.CSS_SELECTOR, "[role='progressbar'], [aria-busy='true']"):
                        continue
                    images = option.find_elements(By.CSS_SELECTOR, "img")
                    if images and not self.d.execute_script(
                        "return arguments[0].complete && arguments[0].naturalWidth > 0;", images[0]
                    ):
                        continue
                    return True
            except (StaleElementReferenceException, WebDriverException):
                return False
            return False
        WebDriverWait(self.d, 180).until(upload_ready)
        self.log.info("素材已完成上傳與索引，可加入提示：%s", name)
        settle = max(0, int(self.cfg.get("upload_settle_seconds", 8)))
        if settle:
            self.log.info("素材上傳完成後再穩定等待 %s 秒：%s", settle, name)
            end = time.time() + settle
            while time.time() < end:
                self.checkpoint(); time.sleep(min(.5, max(0, end - time.time())))

    def prompt_has_material(self) -> bool:
        try:
            composer = self.prompt_box()
            roots = composer.find_elements(By.XPATH, "./ancestor::*[.//button[.//i[normalize-space(.)='add_2']]][1]")
            composer_root = roots[0] if roots else composer.find_element(By.XPATH, "..")
            return any(x.is_displayed() for x in composer_root.find_elements(By.CSS_SELECTOR, "img"))
        except (StaleElementReferenceException, WebDriverException, TimeoutException):
            return False

    def prompt_material_key(self) -> str:
        """Read the UUID of the material thumbnail already attached to the composer."""
        try:
            composer = self.prompt_box()
            roots = composer.find_elements(By.XPATH, "./ancestor::*[.//button[.//i[normalize-space(.)='add_2']]][1]")
            composer_root = roots[0] if roots else composer.find_element(By.XPATH, "..")
            for media in composer_root.find_elements(By.CSS_SELECTOR, "img, video"):
                value = media.get_attribute("src") or media.get_attribute("poster") or ""
                key = self.normalize_media_key(value)
                if key and key != value:
                    return key
        except (StaleElementReferenceException, WebDriverException, TimeoutException):
            pass
        return ""

    def page_media_keys(self) -> list[str]:
        """Collect persistent FLOW media UUIDs, including upload cards not classed as generated output."""
        result: list[str] = []
        seen: set[str] = set()
        for media in self.d.find_elements(By.CSS_SELECTOR, "img, video"):
            try:
                value = media.get_attribute("src") or media.get_attribute("poster") or ""
                key = self.normalize_media_key(value)
                if key and key != value and key not in seen:
                    result.append(key); seen.add(key)
            except (StaleElementReferenceException, WebDriverException):
                continue
        return result

    def search_add_material(self, name: str) -> Optional[str]:
        selected_material_key = ""
        for attempt in range(1, int(self.cfg["max_retries"]) + 1):
            try:
                # 每個任務只會加入一個素材。若前一次其實已加入、但 DOM 重繪使確認失敗，
                # 重試時直接視為成功，避免同一素材被加入兩三次。
                composer = self.prompt_box()
                roots = composer.find_elements(By.XPATH, "./ancestor::*[.//button[.//i[normalize-space(.)='add_2']]][1]")
                composer_root = roots[0] if roots else composer.find_element(By.XPATH, "..")
                if self.prompt_has_material():
                    try: ActionChains(self.d).send_keys(Keys.ESCAPE).perform()
                    except Exception: pass
                    self.log.info("提示框已存在素材縮圖，判定前次加入成功，不再重複加入：%s", name)
                    return selected_material_key or self.prompt_material_key() or None
                dialog = self.ensure_material_dialog()
                inputs = dialog.find_elements(By.CSS_SELECTOR, "#add-menu-input")
                if not inputs: raise TimeoutException("素材視窗找不到搜尋框 #add-menu-input")
                search = inputs[0]
                search.send_keys(Keys.CONTROL, "a"); search.send_keys(Keys.BACKSPACE); search.send_keys(name)
                WebDriverWait(self.d, 20).until(lambda _d: any(
                    x.is_displayed() and name in x.text
                    for x in dialog.find_elements(By.CSS_SELECTOR, "div[role='option']")
                ))
                matches = [x for x in dialog.find_elements(By.CSS_SELECTOR, "div[role='option']")
                           if x.is_displayed() and name in x.text]
                exact = [x for x in matches if x.text.splitlines()[0].strip() == name]
                matches = exact or matches
                if not matches: raise TimeoutException(f"搜尋不到素材：{name}")
                if len(matches) > 1:
                    self.log.warning("素材庫存在 %s 個同名素材「%s」，依目前「最近」排序選擇第一個", len(matches), name)
                chosen = matches[0]
                images = chosen.find_elements(By.CSS_SELECTOR, "img")
                chosen_src = images[0].get_attribute("src") if images else ""
                selected_material_key = self.normalize_media_key(chosen_src)
                before_images = len(composer_root.find_elements(By.CSS_SELECTOR, "img"))
                self.safe_click(chosen)

                # 點選結果後 React 會重建 dialog，必須重新取得元素，不能沿用 stale 參照。
                dialog = self.find([(By.CSS_SELECTOR, "div[role='dialog'][data-state='open']")], 10)
                add_buttons = dialog.find_elements(By.XPATH,
                    ".//button[contains(normalize-space(.), '添加到提示') or contains(normalize-space(.), '加入提示') or contains(normalize-space(.), 'Add to prompt')]")
                visible_add = [x for x in add_buttons if x.is_displayed() and x.is_enabled()]
                if not visible_add: raise TimeoutException("選取素材後找不到「添加到提示」按鈕")
                self.safe_click(visible_add[-1])

                def material_added(_d: Any) -> bool:
                    open_dialog = any(x.is_displayed() for x in self.d.find_elements(By.CSS_SELECTOR, "div[role='dialog'][data-state='open']"))
                    fresh_composer = self.prompt_box()
                    fresh_roots = fresh_composer.find_elements(By.XPATH, "./ancestor::*[.//button[.//i[normalize-space(.)='add_2']]][1]")
                    fresh_root = fresh_roots[0] if fresh_roots else fresh_composer.find_element(By.XPATH, "..")
                    current = fresh_root.find_elements(By.CSS_SELECTOR, "img")
                    count_added = len(current) > before_images
                    src_added = bool(chosen_src) and any(x.get_attribute("src") == chosen_src for x in current)
                    return not open_dialog and (count_added or src_added)

                WebDriverWait(self.d, 25).until(material_added)
                self.log.info("已確認正確素材加入提示：%s", name)
                return selected_material_key or self.prompt_material_key() or None
            except Exception:
                # 點擊可能已成功，只是 React 重繪讓等待中的舊元素失效；先查實際提示框狀態。
                if self.prompt_has_material():
                    try: ActionChains(self.d).send_keys(Keys.ESCAPE).perform()
                    except Exception: pass
                    self.log.info("已在提示框確認素材縮圖，不列為加入失敗：%s", name)
                    return selected_material_key or self.prompt_material_key() or None
                if attempt >= int(self.cfg["max_retries"]): raise
                self.log.warning("搜尋並加入素材失敗，第 %s 次重試：%s", attempt, name)
                try: ActionChains(self.d).send_keys(Keys.ESCAPE).perform()
                except Exception: pass
                time.sleep(self.cfg["check_interval"])

    def choose_option(self, label_texts: list[str], value: str) -> None:
        # 先檢查已選取文字；否則點設定入口，再點所需選項。
        page = self.d.find_element(By.TAG_NAME, "body").text
        if value in page:
            candidates = self.d.find_elements(By.XPATH, f"//*[@aria-pressed='true' or @data-state='checked'][contains(., {xpath_literal(value)})]")
            if candidates: return
        opener = self.text_button(label_texts, 12); self.safe_click(opener)
        option = self.find([
            (By.XPATH, f"//*[@role='option' and contains(normalize-space(.), {xpath_literal(value)})]"),
            (By.XPATH, f"//*[@role='menuitem' and contains(normalize-space(.), {xpath_literal(value)})]"),
            (By.XPATH, f"//button[contains(normalize-space(.), {xpath_literal(value)})]"),
            (By.XPATH, f"//*[contains(@data-state,'') and normalize-space(.)={xpath_literal(value)}]"),
        ], 15, clickable=True); self.safe_click(option)

    def image_settings_button(self) -> Any:
        """新版 FLOW 的圖片模式與設定合併在提示框右下方模型按鈕。
        這顆按鈕其實是圖片／影片共用的同一顆設定觸發鈕，按鈕上顯示的文字
        會依照 FLOW 目前記住的「上一次生成類型」而不同：可能顯示圖片模型
        名稱（Nano Banana／Imagen），也可能顯示影片摘要（例如「視頻・8s」）。
        原本只比對模型名稱，若專案停留在影片模式就完全找不到這顆鈕；這裡
        加上跟 video_settings_button() 對稱的備援條件，兩種狀態都認得。
        """
        model_names = [self.cfg.get("image_model", ""), "Nano Banana", "Imagen"]
        model_names = [x for x in model_names if x]
        wanted = " or ".join(
            f"contains(normalize-space(.), {xpath_literal(x)})" for x in model_names
        )
        return self.find([
            (By.XPATH, "//button[@aria-haspopup='menu' and contains(normalize-space(.), 'Nano Banana')]"),
            (By.XPATH, f"//button[{wanted}]"),
            (By.XPATH, f"//*[@role='button'][{wanted}]"),
            (By.XPATH, "//button[@aria-haspopup='menu' and (contains(normalize-space(.), '影片') or contains(normalize-space(.), '视频') or contains(normalize-space(.), 'Video'))]"),
            (By.XPATH, "//button[@aria-haspopup='menu' and (.//i[contains(normalize-space(.), 'crop_')] or .//i[normalize-space(.)='play_circle'])]"),
        ], 20, clickable=True)

    def ensure_agent_off(self) -> None:
        """Manual image/video generation requires Agent mode to be disabled."""
        active_xpath = (
            "//button[@aria-pressed='true'][contains(normalize-space(.), '智能體') "
            "or contains(normalize-space(.), '智能体') or normalize-space(.)='Agent']"
        )

        def active_button() -> Optional[Any]:
            for item in self.d.find_elements(By.XPATH, active_xpath):
                try:
                    if item.is_displayed() and item.is_enabled():
                        return item
                except (StaleElementReferenceException, WebDriverException):
                    continue
            return None

        def wait_off(seconds: float = 3.0) -> bool:
            end = time.time() + seconds
            while time.time() < end:
                self.checkpoint()
                if active_button() is None:
                    return True
                time.sleep(.2)
            return active_button() is None

        if active_button() is None:
            self.log.info("智能體目前為關閉狀態")
            return

        methods = ("原生點擊", "ActionChains 點擊", "完整 Pointer／Mouse 事件")
        for method in methods:
            button = active_button()
            if button is None:
                self.log.info("已關閉智能體，切回手動圖片／影片製作模式")
                return
            try:
                self.d.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
                if method == "原生點擊":
                    button.click()
                elif method == "ActionChains 點擊":
                    ActionChains(self.d).move_to_element(button).pause(.35).click().pause(.2).perform()
                else:
                    self.d.execute_script("""
                        const el = arguments[0];
                        const r = el.getBoundingClientRect();
                        const init = {bubbles:true, cancelable:true, composed:true,
                                      clientX:r.left+r.width/2, clientY:r.top+r.height/2,
                                      button:0, buttons:1, pointerId:1, pointerType:'mouse', isPrimary:true};
                        el.dispatchEvent(new PointerEvent('pointerover', init));
                        el.dispatchEvent(new MouseEvent('mouseover', init));
                        el.dispatchEvent(new PointerEvent('pointerdown', init));
                        el.dispatchEvent(new MouseEvent('mousedown', init));
                        el.dispatchEvent(new PointerEvent('pointerup', {...init, buttons:0}));
                        el.dispatchEvent(new MouseEvent('mouseup', {...init, buttons:0}));
                        el.dispatchEvent(new MouseEvent('click', {...init, buttons:0}));
                    """, button)
            except (StaleElementReferenceException, WebDriverException):
                pass
            if wait_off():
                self.log.info("已使用%s關閉智能體，切回手動圖片／影片製作模式", method)
                return
            self.log.warning("%s未能關閉智能體，重新定位後改用下一種事件", method)
        raise RuntimeError("無法關閉智能體；為避免進入錯誤模式，本次任務已停止")

    def video_settings_button(self) -> Any:
        """Locate the prompt footer's combined image/video settings trigger."""
        return self.find([
            (By.XPATH, "//button[@aria-haspopup='menu' and (contains(normalize-space(.), '影片') or contains(normalize-space(.), '视频') or contains(normalize-space(.), 'Video'))]"),
            (By.XPATH, "//button[@aria-haspopup='menu' and (contains(normalize-space(.), 'Nano Banana') or contains(normalize-space(.), 'Imagen') or contains(normalize-space(.), 'Omni'))]"),
            (By.XPATH, "//button[@aria-haspopup='menu' and (.//i[contains(normalize-space(.), 'crop_')] or .//i[normalize-space(.)='play_circle'])]"),
        ], 20, clickable=True)

    def configure_video_from_real_menu(self) -> None:
        """Configure Flow video mode using the real Radix tab ids captured from the live UI."""
        self.ensure_agent_off()
        trigger = self.video_settings_button()
        if trigger.get_attribute("aria-expanded") != "true":
            self.safe_click(trigger)

        def open_menu() -> Any:
            return self.find([(By.CSS_SELECTOR, "div[role='menu'][data-state='open']")], 12)

        def select_tab(suffix: str, description: str) -> None:
            menu = open_menu()
            options = menu.find_elements(By.CSS_SELECTOR, f"button[role='tab'][id$='-trigger-{suffix}']")
            if not options:
                raise RuntimeError(f"影片設定選單找不到「{description}」")
            option = options[0]
            if option.get_attribute("aria-selected") != "true" and option.get_attribute("data-state") != "active":
                self.safe_click(option)
                WebDriverWait(self.d, 8).until(lambda _d: any(
                    x.get_attribute("aria-selected") == "true" or x.get_attribute("data-state") == "active"
                    for x in open_menu().find_elements(By.CSS_SELECTOR, f"button[role='tab'][id$='-trigger-{suffix}']")
                ))
            self.log.info("已確認影片設定：%s", description)

        select_tab("VIDEO", "影片模式")
        source = str(self.cfg.get("video_source", "素材"))
        source_suffix = "VIDEO_FRAMES" if source in {"幀", "帧", "Frames", "Frame"} else "VIDEO_REFERENCES"
        select_tab(source_suffix, source)
        ratio_suffix = {"9:16": "PORTRAIT", "16:9": "LANDSCAPE"}.get(str(self.cfg.get("video_ratio")))
        if not ratio_suffix:
            raise RuntimeError(f"不支援的影片比例：{self.cfg.get('video_ratio')}")
        select_tab(ratio_suffix, str(self.cfg["video_ratio"]))
        count = re.sub(r"\D", "", str(self.cfg.get("video_count", "1x"))) or "1"
        select_tab(count, str(self.cfg["video_count"]))
        selected_model = str(self.cfg.get("video_model", "")).strip()
        menu = open_menu()
        if selected_model and selected_model.casefold() not in menu.text.casefold():
            model_buttons = menu.find_elements(By.CSS_SELECTOR, "button[aria-haspopup='menu']")
            model_buttons = [x for x in model_buttons if x.is_displayed()]
            if not model_buttons:
                raise RuntimeError(f"影片設定選單找不到模型選擇器：{selected_model}")
            self.safe_click(model_buttons[-1])
            model_option = self.find([
                (By.XPATH, f"//*[@role='menuitem' or @role='option'][contains(normalize-space(.), {xpath_literal(selected_model)})]"),
                (By.XPATH, f"//button[contains(normalize-space(.), {xpath_literal(selected_model)})]"),
            ], 12, clickable=True)
            self.safe_click(model_option)
            time.sleep(.6)
        self.log.info("已確認影片設定：模型 %s", selected_model or "沿用 FLOW 目前模型")

        seconds = re.sub(r"\D", "", str(self.cfg.get("video_seconds", "8 秒"))) or "8"
        desired_seconds = str(self.cfg.get("video_seconds", "8 秒"))

        def select_seconds() -> None:
            deadline = time.time() + 12
            while time.time() < deadline:
                menu = open_menu()
                candidates = []
                selectors = [
                    f"button[role='tab'][id$='-trigger-{seconds}']",
                    f"[data-value='{seconds}']",
                    f"[value='{seconds}']",
                    f"button[role='radio']",
                    f"[role='option']",
                    f"button",
                ]
                for selector in selectors:
                    try:
                        for item in menu.find_elements(By.CSS_SELECTOR, selector):
                            try:
                                if not item.is_displayed() or not item.is_enabled():
                                    continue
                                text = (item.text or item.get_attribute('aria-label') or item.get_attribute('title') or '').strip()
                                ident = str(item.get_attribute('id') or '')
                                data_value = str(item.get_attribute('data-value') or item.get_attribute('value') or '')
                                normalized = text.casefold().replace(' ', '')
                                if (ident.endswith(f'-trigger-{seconds}') or data_value == seconds or
                                    normalized in {f'{seconds}秒', f'{seconds}s', f'{seconds}sec', f'{seconds}seconds'} or
                                    normalized.startswith(f'{seconds}秒') or normalized.startswith(f'{seconds}s')):
                                    candidates.append(item)
                            except Exception:
                                continue
                    except Exception:
                        continue
                if candidates:
                    option = candidates[0]
                    if option.get_attribute('aria-selected') != 'true' and option.get_attribute('data-state') != 'active' and option.get_attribute('aria-checked') != 'true':
                        self.safe_click(option)
                        time.sleep(.4)
                    self.log.info("已確認影片設定：%s", desired_seconds)
                    return
                time.sleep(.4)

            menu = open_menu()
            available = []
            for item in menu.find_elements(By.CSS_SELECTOR, "button,[role='tab'],[role='radio'],[role='option']"):
                try:
                    if not item.is_displayed():
                        continue
                    text = (item.text or item.get_attribute('aria-label') or item.get_attribute('title') or '').strip()
                    if text:
                        available.append(text.replace('\n', ' / '))
                except Exception:
                    continue
            unique = []
            for text in available:
                if text not in unique:
                    unique.append(text)
            preview = '；'.join(unique[:40]) or '（無可讀文字）'
            self.log.error(
                "找不到影片秒數 %s｜模型=%s｜素材模式=%s｜比例=%s｜數量=%s｜目前選單可見項目：%s",
                desired_seconds, selected_model or '沿用目前模型', source, self.cfg.get('video_ratio'), self.cfg.get('video_count'), preview
            )
            raise RuntimeError(
                f"影片設定選單找不到「{desired_seconds}」；目前模型={selected_model or '沿用目前模型'}、"
                f"素材模式={source}、比例={self.cfg.get('video_ratio')}、數量={self.cfg.get('video_count')}。"
                f"可見選項：{preview}"
            )

        select_seconds()
        ActionChains(self.d).send_keys(Keys.ESCAPE).perform()

    def choose_image_setting(self, value: str) -> None:
        # FLOW 實際摘要使用 1x／2x；比例摘要則以 crop_16_9 等圖示文字呈現。
        display_value = value
        ratio_icon = {
            "16:9": "crop_16_9", "9:16": "crop_9_16", "1:1": "crop_square",
            "4:3": "crop_4_3", "3:4": "crop_3_4",
        }.get(value, "")
        # 目前摘要已顯示設定時，不必重複開啟選單。
        summary = self.image_settings_button().text
        if (display_value.casefold() in summary.casefold() or
                (ratio_icon and ratio_icon.casefold() in summary.casefold())):
            return
        self.safe_click(self.image_settings_button())
        option = self.find([
            (By.XPATH, f"//*[@role='option' and contains(normalize-space(.), {xpath_literal(display_value)})]"),
            (By.XPATH, f"//*[@role='menuitem' and contains(normalize-space(.), {xpath_literal(display_value)})]"),
            (By.XPATH, f"//button[contains(normalize-space(.), {xpath_literal(display_value)})]"),
            (By.XPATH, f"//*[@role='radio' and contains(normalize-space(.), {xpath_literal(display_value)})]"),
            (By.XPATH, f"//*[@role='menuitemradio' and contains(normalize-space(.), {xpath_literal(display_value)})]"),
        ], 15, clickable=True)
        self.safe_click(option)

    def configure_image_from_real_menu(self) -> None:
        """依 FLOW Radix 選單的真實 DOM 設定圖片模式、比例及張數。"""
        trigger = self.image_settings_button()
        if trigger.get_attribute("aria-expanded") != "true":
            self.safe_click(trigger)
        menu = self.find([(By.CSS_SELECTOR, "div[role='menu'][data-state='open']")], 15)

        ratio_suffix = {
            "16:9": "LANDSCAPE", "4:3": "LANDSCAPE_4_3", "1:1": "SQUARE",
            "3:4": "PORTRAIT_3_4", "9:16": "PORTRAIT",
        }[self.cfg["image_ratio"]]
        count = re.sub(r"\D", "", self.cfg["image_count"]) or "1"
        selectors = [
            "button[role='tab'][id$='-trigger-IMAGE']",
            f"button[role='tab'][id$='-trigger-{ratio_suffix}']",
            f"button[role='tab'][id$='-trigger-{count}']",
        ]
        descriptions = ["圖片模式", self.cfg["image_ratio"], self.cfg["image_count"]]
        for selector, description in zip(selectors, descriptions):
            options = menu.find_elements(By.CSS_SELECTOR, selector)
            if not options:
                raise RuntimeError(f"設定選單找不到「{description}」")
            option = options[0]
            if option.get_attribute("aria-selected") != "true" and option.get_attribute("data-state") != "active":
                self.safe_click(option)
                WebDriverWait(self.d, 8).until(
                    lambda _d, el=option: el.get_attribute("aria-selected") == "true" or el.get_attribute("data-state") == "active"
                )
            self.log.info("已確認圖片設定：%s", description)

        selected_model = self.cfg["image_model"].strip()
        if selected_model and selected_model not in menu.text:
            self.log.warning("目前設定選單未顯示指定模型「%s」，暫時保留 FLOW 現有模型", selected_model)
        ActionChains(self.d).send_keys(Keys.ESCAPE).perform()

    def configure(self) -> None:
        typ = self.cfg["generation_type"]
        if typ == "圖片模式":
            self.ensure_agent_off()
            self.configure_image_from_real_menu()
            return
        if typ == "影片模式":
            self.configure_video_from_real_menu()
            return
        raise RuntimeError(f"未知生成類型：{typ}")

    def set_browser_zoom(self, percent: int = 25) -> None:
        """可靠縮放 FLOW 頁面內容；接管後立即執行，並可在 SPA 重繪後再次套用。"""
        target = max(25, min(100, int(percent)))
        factor = target / 100.0

        # Selenium 將 Ctrl+- 傳給網頁時，FLOW/Chrome 版本不同可能不會改變瀏覽器縮放。
        # 改用 CSS zoom 直接作用在 FLOW 根頁面，並調整寬度，確保 25% 時真的能看到更多 Tile。
        result = self.d.execute_script("""
            const percent = arguments[0];
            const factor = percent / 100;
            const root = document.documentElement;
            root.style.setProperty('zoom', String(factor), 'important');
            root.style.setProperty('width', String(100 / factor) + '%', 'important');
            root.dataset.flowAutomationZoom = String(percent);

            // FLOW 是 SPA；若 body 被 React 重建，根節點的 zoom 仍會保留。
            return {
                requested: percent,
                inlineZoom: root.style.zoom,
                computedZoom: getComputedStyle(root).zoom,
                marker: root.dataset.flowAutomationZoom
            };
        """, target)
        time.sleep(.5)

        verified = self.d.execute_script("""
            const root = document.documentElement;
            return {
                zoom: root.style.zoom || getComputedStyle(root).zoom,
                marker: root.dataset.flowAutomationZoom || ''
            };
        """)
        if str(verified.get('marker', '')) != str(target):
            raise RuntimeError(f"FLOW 縮放套用失敗：要求 {target}%／檢查結果 {verified}")
        self.log.info("已立即套用 FLOW 頁面縮放：%s%%（驗證 zoom=%s）", target, verified.get('zoom'))

    def card_count(self) -> int:
        return len(self.output_media())

    def tile_ids(self) -> list[str]:
        """Return visible FLOW tile ids in current DOM order, without duplicates."""
        result: list[str] = []
        seen: set[str] = set()
        for item in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
            try:
                value = str(item.get_attribute("data-tile-id") or "").strip()
                if value and value not in seen and item.is_displayed():
                    result.append(value)
                    seen.add(value)
            except (StaleElementReferenceException, WebDriverException):
                continue
        return result

    def wait_new_submission_tiles(self, before_ids: Iterable[str], expected: int,
                                  timeout: Optional[int] = None) -> list[str]:
        """Bind a submit action to the tile(s) it creates, before jobs finish out of order.

        重要修正：
        1. 舊版每一輪輪詢都用「這一輪偵測到的新 tile」直接覆蓋 best，若某個
           tile 因為捲動／虛擬化而暫時從 DOM 消失（FLOW 常見行為），下一輪
           偵測到的數量就會比前一輪少，導致已經找到的 tile id 被覆蓋遺失。
           一旦批次數量（例如 4x）越多，中間某張 tile 短暫消失的機率就越高，
           因此常見「第 4 張抓不到 tile」。現在改為只增不減的累加收集，
           一旦看過某個 tile id 就永久保留，不會再被之後的輪詢覆蓋掉。
        2. 舊版固定只等 15 秒，對於一次生成 3～4 張的批次，FLOW 建立每一張
           tile 需要的時間可能超過 15 秒，導致真正建立中的 tile 還沒等到就
           放棄。現在改為依預期張數自動放大等待時間，且此步驟在點擊送出後
           以同步方式執行、下一筆任務要等它結束才會開始，所以延長等待不會
           讓不同任務的 tile 互相搶錯，是安全的。
        """
        old = {str(value) for value in before_ids if str(value).strip()}
        # FLOW 目前可能要等作品成功或失敗後才建立 data-tile-id。
        # 單任務模式可安全等待完整生成時間，不會阻塞其他已送出的任務或造成配錯。
        generation_timeout = int(
            self.cfg["image_timeout"]
            if self.cfg["generation_type"] == "圖片模式"
            else self.cfg["video_timeout"]
        )
        wait_seconds = timeout if timeout is not None else generation_timeout
        end = time.time() + wait_seconds
        collected: list[str] = []
        seen: set[str] = set()
        while time.time() < end:
            self.checkpoint()
            current = self.tile_ids()
            for value in current:
                if value not in old and value not in seen:
                    collected.append(value)
                    seen.add(value)
            if len(collected) >= max(1, expected):
                selected = collected[:max(1, expected)]
                self.log.info("已將本次送出綁定至 FLOW tile：%s", ", ".join(selected))
                return selected
            time.sleep(.25)
        if collected:
            self.log.warning("只偵測到 %s 個新 tile（預期 %s），先保存已找到的識別碼", len(collected), expected)
            return collected[:max(1, expected)]
        raise RuntimeError(
            "送出後找不到本次任務建立的 FLOW tile；為避免亂序作品配錯任務，已停止快速排程"
        )

    def media_in_tiles(self, tile_ids: Iterable[str]) -> list[Any]:
        """Resolve completed output media only from the exact tiles owned by one task."""
        wanted = {str(value) for value in tile_ids if str(value).strip()}
        found: list[Any] = []
        seen: set[str] = set()
        for tile in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
            try:
                if str(tile.get_attribute("data-tile-id") or "") not in wanted:
                    continue
                for media in tile.find_elements(By.CSS_SELECTOR, "img, video"):
                    if not media.is_displayed() or not self.matches_output_ratio(media):
                        continue
                    key = self.media_key(media)
                    if key and key not in seen:
                        found.append(media)
                        seen.add(key)
            except (StaleElementReferenceException, WebDriverException):
                continue
        return found

    def tiles_present(self, tile_ids: Iterable[str]) -> bool:
        """Check whether any wanted data-tile-id node currently exists in DOM, regardless of visibility.

        用來區分兩種完全不同的等待情境：
        1. tile 節點根本不存在（可能已被 FLOW 移除，或同時送出過多任務時
           從未真正建立）－這種情況繼續等待到完整的生成逾時毫無意義。
        2. tile 節點存在，只是還在生成中或暫時被虛擬化捲動移出畫面－這種
           情況才需要用完整的生成逾時耐心等待。
        """
        wanted = {str(value) for value in tile_ids if str(value).strip()}
        if not wanted:
            return False
        try:
            for tile in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
                try:
                    if str(tile.get_attribute("data-tile-id") or "") in wanted:
                        return True
                except (StaleElementReferenceException, WebDriverException):
                    continue
        except WebDriverException:
            return False
        return False

    FAILURE_TILE_MARKERS = (
        "糟糕，出了点问题", "糟糕，出了點問題", "出了点问题", "出了點問題",
        "Something went wrong", "生成失败", "生成失敗",
    )

    FAILURE_RETRY_LABELS = (
        "重試", "重试", "重新生成", "再次生成", "Retry", "Regenerate",
    )
    FAILURE_DELETE_LABELS = (
        "刪除", "删除", "Delete",
    )

    def tile_failure_texts(self, tile_ids: Iterable[str]) -> Optional[str]:
        """Detect a FLOW failure tile, preferring stable DOM controls over language text.

        V1.5 判定順序：
        1. 指定 tile 內沒有已完成的 img/video，並同時出現「重試/重新生成」與
           「刪除」動作（含 refresh/delete 圖示或多語 aria-label/title/文字），
           立即視為失敗卡。
        2. 若按鈕結構改版，再用既有的繁中、簡中、英文失敗文字作備援。

        這樣不必等到完整生成逾時，也較不受 FLOW 顯示語言影響。
        """
        wanted = {str(value) for value in tile_ids if str(value).strip()}
        if not wanted:
            return None

        def visible(elements: Iterable[Any]) -> list[Any]:
            result: list[Any] = []
            for element in elements:
                try:
                    if element.is_displayed():
                        result.append(element)
                except (StaleElementReferenceException, WebDriverException):
                    continue
            return result

        try:
            for tile in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
                try:
                    tid = str(tile.get_attribute("data-tile-id") or "")
                    if tid not in wanted:
                        continue

                    # 已有符合輸出比例的完成媒體時，不能因卡片一般選單也有刪除鍵而誤判。
                    completed_media = []
                    for media in tile.find_elements(By.CSS_SELECTOR, "img, video"):
                        try:
                            if media.is_displayed() and self.matches_output_ratio(media):
                                completed_media.append(media)
                        except (StaleElementReferenceException, WebDriverException):
                            continue

                    retry_xpath = (
                        ".//button[.//i[normalize-space(.)='refresh' or "
                        "normalize-space(.)='replay' or normalize-space(.)='restart_alt'] "
                        "or contains(@aria-label,'重試') or contains(@aria-label,'重试') "
                        "or contains(@aria-label,'重新生成') or contains(@aria-label,'Retry') "
                        "or contains(@aria-label,'Regenerate') or contains(@title,'重試') "
                        "or contains(@title,'重试') or contains(@title,'Retry') "
                        "or contains(normalize-space(.),'重試') or contains(normalize-space(.),'重试') "
                        "or contains(normalize-space(.),'重新生成') or contains(normalize-space(.),'Retry') "
                        "or contains(normalize-space(.),'Regenerate')]"
                    )
                    delete_xpath = (
                        ".//button[.//i[normalize-space(.)='delete' or normalize-space(.)='delete_forever'] "
                        "or contains(@aria-label,'刪除') or contains(@aria-label,'删除') "
                        "or contains(@aria-label,'Delete') or contains(@title,'刪除') "
                        "or contains(@title,'删除') or contains(@title,'Delete') "
                        "or contains(normalize-space(.),'刪除') or contains(normalize-space(.),'删除') "
                        "or contains(normalize-space(.),'Delete')]"
                    )
                    retry_buttons = visible(tile.find_elements(By.XPATH, retry_xpath))
                    delete_buttons = visible(tile.find_elements(By.XPATH, delete_xpath))

                    if not completed_media and retry_buttons and delete_buttons:
                        return (
                            f"DOM 失敗卡：tile={tid}，偵測到重試按鈕 "
                            f"{len(retry_buttons)} 個及刪除按鈕 {len(delete_buttons)} 個"
                        )

                    text = str(
                        self.d.execute_script("return arguments[0].textContent || '';", tile) or ""
                    )
                except (StaleElementReferenceException, WebDriverException):
                    continue

                if any(marker.casefold() in text.casefold() for marker in self.FAILURE_TILE_MARKERS):
                    return f"文字失敗卡：{text.strip()[:150]}"
        except WebDriverException:
            return None
        return None

    def click_tile_retry(self, tile_ids: Iterable[str]) -> bool:
        """Click FLOW's own retry button on a failed tile, so it regenerates under the same tile id.

        FLOW 的失敗卡片本身就附有「重試」按鈕（icon 是 refresh），直接幫使用者
        點下去，比整個放棄這筆任務、依賴我們自己的重試流程重新上傳素材、重新
        輸入提示詞再送出一次要快得多，也更貼近使用者手動操作時會做的事。
        """
        wanted = {str(value) for value in tile_ids if str(value).strip()}
        if not wanted:
            return False
        try:
            for tile in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
                try:
                    tid = str(tile.get_attribute("data-tile-id") or "")
                    if tid not in wanted:
                        continue
                    buttons = tile.find_elements(
                        By.XPATH, ".//button[.//i[normalize-space(.)='refresh']]"
                    )
                    visible = [b for b in buttons if b.is_displayed() and b.is_enabled()]
                    if visible:
                        self.safe_click(visible[0])
                        return True
                except (StaleElementReferenceException, WebDriverException):
                    continue
        except WebDriverException:
            return False
        return False

    def scrollable_media_containers(self) -> list[Any]:
        """Return FLOW's real scroll viewports, ordered ahead of the document scroller."""
        try:
            return list(self.d.execute_script("""
                const candidates = [document.scrollingElement, ...document.querySelectorAll('*')];
                const unique = [...new Set(candidates.filter(Boolean))];
                return unique.filter(el => {
                    if (el === document.scrollingElement) {
                        return el.scrollHeight > el.clientHeight + 80;
                    }
                    const style = getComputedStyle(el);
                    const overflow = style.overflowY;
                    const rect = el.getBoundingClientRect();
                    return (overflow === 'auto' || overflow === 'scroll' || overflow === 'overlay') &&
                           el.scrollHeight > el.clientHeight + 80 &&
                           rect.width > 300 && rect.height > 250;
                }).sort((a, b) => {
                    const priority = el =>
                        (el.matches && el.matches('[data-radix-scroll-area-viewport], [data-scroll-container]') ? 1e15 : 0) +
                        (el === document.scrollingElement ? 0 : 1e12) +
                        Math.min(1e9, (el.scrollHeight - el.clientHeight) * Math.max(1, el.clientWidth));
                    return priority(b) - priority(a);
                }).slice(0, 8);
            """) or [])
        except WebDriverException:
            return []

    def sweep_media_containers(self, inspector: Callable[[], Any]) -> Any:
        """Scroll actual nested FLOW viewports from newest toward older cards until inspector succeeds."""
        immediate = inspector()
        if immediate:
            return immediate
        containers = self.scrollable_media_containers()
        for container_index in range(len(containers)):
            try:
                # Reacquire because scrolling may cause React to replace a viewport element.
                current = self.scrollable_media_containers()
                if container_index >= len(current):
                    break
                container = current[container_index]
                metrics = self.d.execute_script(
                    "return [arguments[0].scrollHeight, arguments[0].clientHeight, arguments[0].scrollTop];",
                    container,
                )
                total, viewport, original = (int(metrics[0]), int(metrics[1]), int(metrics[2]))
                maximum = max(0, total - viewport)
                step = max(300, int(viewport * .72))
                # 最新作品位於前方；本批次任務只需掃描前 40 個視窗，避免舊專案數百張作品拖慢。
                forward = list(range(0, min(maximum, step * 40) + 1, step))
                positions = [original, 0] + forward
                seen_positions: set[int] = set()
                for position in positions:
                    position = max(0, min(maximum, int(position)))
                    if position in seen_positions:
                        continue
                    seen_positions.add(position)
                    self.checkpoint()
                    self.d.execute_script("""
                        arguments[0].scrollTop = arguments[1];
                        arguments[0].dispatchEvent(new Event('scroll', {bubbles:true}));
                    """, container, position)
                    time.sleep(.35)
                    result = inspector()
                    if result:
                        self.log.info("已在 FLOW 作品捲動容器中定位指定任務 tile")
                        return result
            except (StaleElementReferenceException, WebDriverException, TypeError, ValueError):
                continue
        return None

    def wake_media_in_tiles(self, tile_ids: Iterable[str]) -> list[Any]:
        """Mount a virtualized FLOW tile by scrolling, then return its completed media."""
        wanted = {str(value) for value in tile_ids if str(value).strip()}
        if not wanted:
            return []

        def scroll_owned_tile() -> list[Any]:
            for tile in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
                try:
                    if str(tile.get_attribute("data-tile-id") or "") in wanted:
                        self.d.execute_script("arguments[0].scrollIntoView({block:'center'});", tile)
                        time.sleep(.6)
                        return self.media_in_tiles(wanted)
                except (StaleElementReferenceException, WebDriverException):
                    continue
            return []

        ready = self.sweep_media_containers(scroll_owned_tile)
        if ready:
            self.log.info("監控階段已捲動喚醒指定任務 tile")
            return list(ready)
        return []

    def generated_media(self) -> list[Any]:
        """Return completed FLOW media, excluding avatars and prompt ingredients."""
        found: list[Any] = []
        # FLOW 會依介面語系使用「生成的圖片／生成的图片／生成的图像」等字樣。
        # 以 alt 含「生成」涵蓋繁簡體版本；英文介面另外匹配 Generated。
        selectors = "img[alt*='生成'], img[alt*='Generated'], img[alt*='generated'], video"
        for media in self.d.find_elements(By.CSS_SELECTOR, selectors):
            try:
                if not media.is_displayed():
                    continue
                rect = media.rect
                if rect["width"] < 120 or rect["height"] < 120:
                    continue
                # 已加入提示框的縮圖不是新生成作品。
                if media.find_elements(By.XPATH, "./ancestor::*[@role='dialog' or @data-slate-editor='true']"):
                    continue
                found.append(media)
            except (StaleElementReferenceException, WebDriverException):
                continue
        return found

    def matches_output_ratio(self, media: Any) -> bool:
        if self.cfg["generation_type"] != "圖片模式" or media.tag_name.lower() == "video":
            return True
        expected = {
            "16:9": 16 / 9, "4:3": 4 / 3, "1:1": 1.0,
            "3:4": 3 / 4, "9:16": 9 / 16,
        }.get(str(self.cfg.get("image_ratio", "")))
        if not expected:
            return True
        try:
            size = self.d.execute_script(
                "return [arguments[0].naturalWidth || arguments[0].videoWidth || 0, arguments[0].naturalHeight || arguments[0].videoHeight || 0];",
                media,
            )
            if not size or not size[0] or not size[1]:
                return False
            actual = float(size[0]) / float(size[1])
            return abs(actual / expected - 1.0) <= .08
        except (StaleElementReferenceException, WebDriverException, TypeError, ValueError):
            return False

    def output_media(self) -> list[Any]:
        """Only media whose real pixel ratio matches this batch's requested output."""
        return [item for item in self.generated_media() if self.matches_output_ratio(item)]

    def media_card_text(self, media: Any) -> str:
        """Hover a FLOW tile and read its full card text, including lazy overlays."""
        try:
            cards = media.find_elements(
                By.XPATH,
                "./ancestor::*[@role='button' and (@aria-roledescription='draggable' or @draggable='true')][1]",
            )
            if not cards:
                cards = media.find_elements(By.XPATH, "./ancestor::*[@data-tile-id][1]/ancestor::*[self::div or self::span][1]")
            card = cards[0] if cards else media
            text = str(self.d.execute_script("return arguments[0].textContent || '';", card) or "").strip()
            if text:
                return text
            ActionChains(self.d).move_to_element(media).pause(.25).perform()
            return str(self.d.execute_script("return arguments[0].textContent || '';", card) or "").strip()
        except (StaleElementReferenceException, WebDriverException):
            return ""

    def is_named_material(self, media: Any, material_names: Iterable[str]) -> bool:
        text = self.media_card_text(media).casefold()
        if not text:
            return False
        return any(Path(name).name.casefold() in text for name in material_names if str(name).strip())

    def downloadable_output_media(self, material_names: Iterable[str]) -> list[Any]:
        names = list(material_names)
        accepted: list[Any] = []
        for media in self.output_media():
            if self.is_named_material(media, names):
                self.log.info("依卡片檔名排除上傳素材：%s", self.media_card_text(media).replace("\n", " ")[:120])
                continue
            accepted.append(media)
        return accepted

    @staticmethod
    def normalize_media_key(value: str) -> str:
        # FLOW 的持久作品識別碼位於 media.getMediaUrlRedirect?name=<UUID>。
        # 只保存 UUID，瀏覽器重開或簽名網址改變後仍可恢復辨識。
        match = re.search(r"media\.getMediaUrlRedirect\?name=([^&\"']+)", value or "")
        return match.group(1) if match else (value or "")

    @classmethod
    def media_key(cls, media: Any) -> str:
        value = media.get_attribute("src") or media.get_attribute("poster") or ""
        return cls.normalize_media_key(value)

    def media_by_key(self, key: str) -> Optional[Any]:
        key = self.normalize_media_key(key)
        for media in self.generated_media():
            try:
                if self.media_key(media) == key:
                    return media
            except (StaleElementReferenceException, WebDriverException):
                continue
        return None

    def media_by_key_from_task_tiles(self, key: str, tile_ids: Iterable[str]) -> Optional[Any]:
        """Wake a virtualized/off-screen task tile, then resolve its exact media UUID."""
        key = self.normalize_media_key(key)
        direct = self.media_by_key(key)
        if direct is not None:
            return direct
        wanted = {str(value) for value in tile_ids if str(value).strip()}

        def inspect_loaded_tiles() -> Optional[Any]:
            for tile in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
                try:
                    if str(tile.get_attribute("data-tile-id") or "") not in wanted:
                        continue
                    self.d.execute_script("arguments[0].scrollIntoView({block:'center'});", tile)
                    time.sleep(.35)
                    # React may replace the tile after scroll; reacquire it before reading descendants.
                    for fresh_tile in self.d.find_elements(By.CSS_SELECTOR, "[data-tile-id]"):
                        if str(fresh_tile.get_attribute("data-tile-id") or "") not in wanted:
                            continue
                        for media in fresh_tile.find_elements(By.CSS_SELECTOR, "img, video"):
                            if self.media_key(media) == key:
                                return media
                except (StaleElementReferenceException, WebDriverException):
                    continue
            return None

        found = self.sweep_media_containers(inspect_loaded_tiles)
        if found is not None:
            self.log.info("已捲動真正的 FLOW 作品容器並重新找到下載作品")
            return found
        return None

    def submit(self, on_clicked: Optional[Callable[[], None]] = None) -> bool:
        buttons = [
            (By.XPATH, "//button[.//i[normalize-space(.)='arrow_forward'] and not(@aria-disabled='true')]"),
            (By.CSS_SELECTOR, "button[type='submit']"), (By.CSS_SELECTOR, "button[aria-label*='生成']"),
            (By.CSS_SELECTOR, "button[aria-label*='Generate']"), (By.CSS_SELECTOR, "button[aria-label*='建立']"),
        ]
        try:
            e = self.find(buttons, 12, clickable=True)
        except TimeoutException:
            # 新版 FLOW 的送出鍵只有向右箭頭圖示，沒有文字及 aria-label。
            # 以提示框為基準，選擇同一輸入區域內最右側的可用按鈕。
            prompt = self.prompt_box()
            pr = prompt.rect
            candidates = []
            for button in self.d.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
                try:
                    if not button.is_displayed() or not button.is_enabled(): continue
                    r = button.rect
                    center_y = r["y"] + r["height"] / 2
                    prompt_center_y = pr["y"] + pr["height"] / 2
                    near_prompt = abs(center_y - prompt_center_y) <= max(100, pr["height"] + 30)
                    right_side = r["x"] >= pr["x"] + pr["width"] * .55
                    if near_prompt and right_side and r["width"] <= 90 and r["height"] <= 90:
                        candidates.append(button)
                except (StaleElementReferenceException, WebDriverException):
                    continue
            if not candidates:
                raise TimeoutException("找不到提示框右側的箭頭送出按鈕")
            e = max(candidates, key=lambda x: x.rect["x"] + x.rect["width"])
            self.log.info("已依提示框相對位置找到右側箭頭送出按鈕")
        before = self.card_count()
        self.log.info(
            "準備點擊送出按鈕：tag=%s aria-label=%r title=%r disabled=%r 位置=%s",
            e.tag_name, e.get_attribute("aria-label"), e.get_attribute("title"),
            e.get_attribute("disabled"), e.rect,
        )

        def submitted(wait_seconds: int) -> bool:
            end = time.time() + wait_seconds
            while time.time() < end:
                self.checkpoint()
                body = self.d.find_element(By.TAG_NAME, "body").text
                try:
                    box = self.prompt_box()
                    prompt_empty = not self.prompt_value(box).strip()
                except Exception:
                    prompt_empty = False
                if prompt_empty or self.card_count() > before or any(
                    x in body for x in ["生成中", "處理中", "排隊", "Generating", "Processing"]
                ):
                    return True
                time.sleep(1)
            return False

        # 第一次：原生 WebElement 點擊。
        self.safe_click(e)
        if on_clicked: on_clicked()
        if submitted(30):
            self.log.info("已確認任務提交成功；此任務送出鍵只點擊一次")
            return True
        self.log.warning("送出後暫時無法確認頁面狀態；已禁止補點與重送，改為只監控生成結果")
        return False

    def wait_generation(self, baseline: int, expected: int,
                        baseline_media: Optional[list[str]] = None,
                        excluded_media: Optional[list[str]] = None,
                        material_names: Optional[list[str]] = None) -> list[Any]:
        timeout = int(self.cfg["image_timeout"] if self.cfg["generation_type"] == "圖片模式" else self.cfg["video_timeout"])
        end = time.time() + timeout
        while time.time() < end:
            self.checkpoint(); body = self.d.find_element(By.TAG_NAME, "body").text
            if any(x in body for x in ["點數不足", "点数不足", "credits", "not enough credit"]):
                self.pause.clear(); raise RuntimeError("點數不足，已暫停全部任務")
            if any(x in body for x in ["內容被拒絕", "内容被拒绝", "無法生成", "Couldn't generate"]):
                raise RuntimeError("FLOW 回報內容被拒絕或生成失敗")
            media = self.downloadable_output_media(material_names or [])
            excluded = {self.normalize_media_key(item) for item in (excluded_media or [])}

            def safe_key(item: Any) -> Optional[str]:
                try:
                    return self.media_key(item)
                except (StaleElementReferenceException, WebDriverException):
                    # 卡片剛好在這一輪輪詢中被 React 重繪，略過即可，下一輪再重新掃描，
                    # 不應讓整個任務直接算失敗（這會浪費重試次數，且可能誤判任務失敗）。
                    return None

            if baseline_media:
                old = {self.normalize_media_key(item) for item in baseline_media}
                ready = []
                for item in media:
                    key = safe_key(item)
                    if key is None or key in old or key in excluded:
                        continue
                    ready.append(item)
            else:
                candidates = media[baseline:] if len(media) >= baseline else []
                ready = []
                for item in candidates:
                    key = safe_key(item)
                    if key is None or key in excluded:
                        continue
                    ready.append(item)
            if len(ready) >= expected: return ready[:expected]
            time.sleep(int(self.cfg["check_interval"]))
        raise TimeoutError("等待作品生成完成逾時")

    def wait_generation_for_tiles(self, tile_ids: list[str], expected: int,
                                  material_names: Optional[list[str]] = None) -> list[Any]:
        """Wait for one task by ownership, independent of completion/visual ordering."""
        if not tile_ids:
            raise RuntimeError("任務沒有保存 submission tile，不能安全判斷作品歸屬")
        timeout = int(self.cfg["image_timeout"] if self.cfg["generation_type"] == "圖片模式" else self.cfg["video_timeout"])
        # 「tile 節點完全找不到」跟「tile 存在但作品還在生成中」是兩種不同狀況。
        # 前者常發生在同一批快速送出太多任務、超出帳號同時生成上限時，FLOW 可能
        # 從未真正建立該筆的 tile，或建立後又整個移除；這種情況繼續等滿整個
        # 生成逾時（圖片 900 秒／影片 1800 秒，再乘上重試次數）只會讓整批任務
        # 卡住幾十分鐘。因此另外設定一個較短的「tile 存在性」偵測逾時，一旦
        # 從頭到尾都找不到這個 tile 節點本身，就快速判定失敗、盡快處理下一筆。
        tile_missing_timeout = min(timeout, max(60, int(self.cfg.get("check_interval", 5)) * 24))
        start = time.time()
        end = start + timeout
        tile_first_seen: Optional[float] = None
        last_wake = 0.0
        while time.time() < end:
            self.checkpoint()
            body = self.d.find_element(By.TAG_NAME, "body").text
            if any(x in body for x in ["點數不足", "点数不足", "credits", "not enough credit"]):
                self.pause.clear(); raise RuntimeError("點數不足，已暫停全部任務")
            if any(x in body for x in ["內容被拒絕", "内容被拒绝", "無法生成", "Couldn't generate"]):
                raise RuntimeError("FLOW 回報內容被拒絕或生成失敗")
            failure_text = self.tile_failure_texts(tile_ids)
            if failure_text:
                # 單任務模式不使用 Tile 內建重試。交由上層完整重建同一素材任務，
                # 包含重新設定、重新上傳、重新加入提示及重新送出。
                raise RuntimeError(f"FLOW 回報此任務生成失敗（tile 顯示失敗卡片）：{failure_text}")
            media_items = self.media_in_tiles(tile_ids)
            if media_items or self.tiles_present(tile_ids):
                tile_first_seen = tile_first_seen or time.time()
            # 大批次快速送出後，較早任務的卡片可能被 FLOW 從 DOM 虛擬化移除。
            # 每隔一小段時間依 tile id 掃描並捲動一次，不能只被動等待目前畫面。
            if not media_items and time.time() - last_wake >= 8:
                last_wake = time.time()
                self.log.info("指定任務 tile 尚未載入，正在自動捲動尋找並喚醒")
                media_items = self.wake_media_in_tiles(tile_ids)
                if media_items or self.tiles_present(tile_ids):
                    tile_first_seen = tile_first_seen or time.time()
            ready = [
                media for media in media_items
                if not self.is_named_material(media, material_names or [])
            ]
            if len(ready) >= expected:
                return ready[:expected]
            if tile_first_seen is None and time.time() - start >= tile_missing_timeout:
                raise RuntimeError(
                    "找不到本次任務建立的 FLOW tile 節點，可能已被 FLOW 移除或從未真正建立"
                    "（常見於同時快速送出過多任務、超出帳號同時生成上限）；已快速判定為失敗，"
                    "避免拖住後續任務"
                )
            time.sleep(int(self.cfg["check_interval"]))
        raise TimeoutError("等待指定 FLOW tile 的作品生成完成逾時")

    def download_card(self, card: Any, quality: str, manager: DownloadManager,
                      task: FlowTask, index: int,
                      material_names: Optional[list[str]] = None,
                      prompt_texts: Optional[list[str]] = None) -> tuple[Optional[Path], str]:
        # 用媒體 UUID 直接比對，比事後比對下載檔名更可靠：這張卡片本來就是任務自己
        # 上傳的素材，不需要下載，也不應該浪費一次網路請求。
        material_keys = {self.normalize_media_key(key) for key in (task.material_media or [])}
        if material_keys and self.media_key(card) in material_keys:
            self.log.info("[任務%03d] 第 %d 張依媒體 UUID 判定為自己的素材，直接排除", task.number, index)
            return None, "material"

        before = manager.snapshot()
        tag = card.tag_name.lower()
        if requests is not None:
            if tag == "video":
                direct = self.direct_download_video(card, manager, task)
                if direct:
                    return direct, "work"
            elif tag == "img":
                # 直接抓圖片網址下載：每張圖對應自己的網址，不會跟其他任務搶到
                # 瀏覽器下載資料夾裡同名的檔案，徹底解決批次下載時的錯位問題。
                # 缺點：抓到的是目前畫面渲染的解析度，若尚未點過高畫質重塑選單，
                # 可能不是 2K/4K 版本；此時會自動退回選單下載那條路徑。
                direct = self.direct_download_image(card, manager, task)
                if direct:
                    return direct, "work"
        download_xpath = "//*[@role='menuitem'][.//i[normalize-space(.)='download'] or contains(normalize-space(.),'下載') or contains(normalize-space(.),'下载') or contains(normalize-space(.),'Download')]"
        download_item = None
        media_key = task.generated_media[index - 1]
        # 快速模式：每輪只嘗試一次開選單，失敗交給整批下一輪補抓，避免卡住單張。
        for open_attempt in range(1, 2):
            try: ActionChains(self.d).send_keys(Keys.ESCAPE).perform()
            except Exception: pass
            self.dismiss_toasts()
            card = self.media_by_key(media_key)
            if card is None:
                raise RuntimeError("FLOW 重繪後找不到原作品卡片")
            ActionChains(self.d).move_to_element(card).pause(.25).perform()
            ancestors = card.find_elements(
                By.XPATH, "./ancestor::*[.//button[.//i[normalize-space(.)='more_vert']]][1]"
            )
            buttons = ancestors[0].find_elements(By.XPATH, ".//button[.//i[normalize-space(.)='more_vert']]") if ancestors else []
            visible_buttons = [button for button in buttons if button.is_displayed() and button.is_enabled()]
            if not visible_buttons:
                continue
            trigger = visible_buttons[-1]
            try:
                ActionChains(self.d).move_to_element(trigger).pause(.1).click().pause(.25).perform()
            except Exception:
                self.safe_click(trigger)
            visible_items = []
            menu_deadline = time.time() + 1.2
            while time.time() < menu_deadline and not visible_items:
                items = self.d.find_elements(By.XPATH, download_xpath)
                visible_items = [item for item in items if item.is_displayed() and item.is_enabled()]
                if not visible_items: time.sleep(.1)
            if visible_items:
                download_item = visible_items[-1]
                break
            self.log.warning("[任務%03d] 第 %d 張作品的更多選單第 %d 次未開啟，重新定位卡片", task.number, index, open_attempt)
        if download_item is None:
            raise RuntimeError("作品更多選單無法開啟或找不到下載項目")
        # 「下載」是子選單，必須維持 hover 才會顯示畫質選項。
        ActionChains(self.d).move_to_element(download_item).pause(.3).perform()
        quality_order = {"4K": ["4K", "2K", "1K"], "2K": ["2K", "1K"], "1K": ["1K"]}.get(quality, [quality, "1K"])
        quality_button = None
        for candidate in quality_order:
            matches = self.d.find_elements(
                By.XPATH,
                f"//*[@role='menuitem'][starts-with(normalize-space(.),'{candidate}') and not(@aria-disabled='true')]",
            )
            visible = [item for item in matches if item.is_displayed() and item.is_enabled()]
            if visible:
                quality_button = visible[-1]
                if candidate != quality:
                    self.log.warning("指定畫質 %s 不可用，改下載 %s", quality, candidate)
                break
        if quality_button is None:
            # 某些 Radix 版本需先點擊父項才開啟子選單。
            self.safe_click(download_item)
            for candidate in quality_order:
                matches = self.d.find_elements(
                    By.XPATH,
                    f"//*[@role='menuitem'][starts-with(normalize-space(.),'{candidate}') and not(@aria-disabled='true')]",
                )
                visible = [item for item in matches if item.is_displayed() and item.is_enabled()]
                if visible:
                    quality_button = visible[-1]
                    break
        if quality_button is None:
            raise RuntimeError(f"下載子選單找不到可用畫質（要求 {quality}）")
        self.safe_click(quality_button)
        source = manager.wait_new(before)
        # 先保留 FLOW 原始檔名分類，不再立即改成 001、002。
        raw_name = source.name.casefold()
        for material_name in material_names or []:
            base = Path(material_name).name.casefold()
            if base and raw_name.startswith(base):
                self.log.warning(
                    "[任務%03d] 原始下載檔名以素材名開頭，判定為素材並移除：%s",
                    task.number, source.name,
                )
                try: source.unlink()
                except OSError as exc: self.log.warning("無法移除誤下載素材 %s：%s", source, exc)
                return None, "material"

        # FLOW 會把提示詞中的空格與標點轉成底線；比較時統一移除非文字數字字元。
        # 注意：FLOW 有時依「風格／模型設定」命名下載檔（例如 Realistic_style_...），
        # 並非一定包含提示詞文字，因此提示詞比對僅作為輔助紀錄，不作為判定條件。
        # 前面已排除素材檔名，走到這裡代表不是素材，一律視為生成作品，避免真正下載
        # 成功卻被誤判為 unknown、導致任務不斷觸發補抓重試而卡關。
        normalized_raw = re.sub(r"[\W_]+", "", raw_name, flags=re.UNICODE)
        prompt_matched = False
        for prompt_text in prompt_texts or [task.prompt]:
            normalized_prompt = re.sub(r"[\W_]+", "", prompt_text.casefold(), flags=re.UNICODE)
            prompt_prefix = normalized_prompt[:24]
            if prompt_prefix and normalized_raw.startswith(prompt_prefix):
                prompt_matched = True
                break

        if prompt_matched:
            self.log.info(
                "[任務%03d] 原始檔名以提示詞開頭，確認為生成作品：%s",
                task.number, source.name,
            )
        else:
            self.log.info(
                "[任務%03d] 檔名未以提示詞開頭（FLOW 可能依風格命名），已排除素材名後判定為生成作品：%s",
                task.number, source.name,
            )
        return source, "work"

    def direct_download_video(self, video: Any, manager: DownloadManager,
                              task: FlowTask) -> Optional[Path]:
        """Download a loaded FLOW video URL directly with the attached Chrome cookies."""
        if requests is None:
            return None
        self.d.execute_script("arguments[0].scrollIntoView({block:'center'});", video)
        redirect_url = str(self.d.execute_script(
            "return arguments[0].currentSrc || arguments[0].src || '';", video
        ) or "")
        if "media.getMediaUrlRedirect" not in redirect_url:
            return None
        if redirect_url.startswith("/"):
            redirect_url = "https://labs.google" + redirect_url
        session = requests.Session()
        session.headers.update({
            "User-Agent": self.d.execute_script("return navigator.userAgent;"),
            "Referer": "https://labs.google/",
        })
        for cookie in self.d.get_cookies():
            try:
                session.cookies.set(cookie["name"], cookie["value"],
                                    domain=cookie.get("domain"), path=cookie.get("path", "/"))
            except Exception:
                session.cookies.set(cookie["name"], cookie["value"])
        first = session.get(redirect_url, allow_redirects=False, timeout=30)
        real_url = first.headers.get("Location")
        if not real_url:
            self.log.warning("[任務%03d] 影片網址沒有回傳重新導向，改用選單下載", task.number)
            return None
        target = manager.folder / f".flow_video_{self.media_key(video)}.mp4"
        with session.get(real_url, stream=True, timeout=(30, 300)) as response:
            if response.status_code not in (200, 206):
                self.log.warning("[任務%03d] 影片網址下載狀態 %s，改用選單下載", task.number, response.status_code)
                return None
            with target.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if not target.exists() or not target.stat().st_size:
            return None
        self.log.info("[任務%03d] 已透過媒體網址快速下載影片：%s", task.number, target.name)
        return target

    def _delete_visible_card_for_media(self, media: Any, description: str = "") -> bool:
        """刪除指定媒體所在 FLOW 卡片，只處理指定媒體自己的卡片。"""
        try:
            self.dismiss_toasts()
            self.d.execute_script("arguments[0].scrollIntoView({block:'center'});", media)
            time.sleep(.25)
            cards = media.find_elements(
                By.XPATH,
                "./ancestor::*[@data-tile-id or .//button[.//i[normalize-space(.)='more_vert']]][1]",
            )
            card = cards[0] if cards else media
            try:
                ActionChains(self.d).move_to_element(card).pause(.2).perform()
            except Exception:
                pass

            direct_delete = card.find_elements(
                By.XPATH,
                ".//button[.//i[normalize-space(.)='delete' or normalize-space(.)='delete_forever'] "
                "or contains(@aria-label,'刪除') or contains(@aria-label,'删除') or contains(@aria-label,'Delete')]",
            )
            direct_delete = [x for x in direct_delete if x.is_displayed() and x.is_enabled()]
            if direct_delete:
                self.safe_click(direct_delete[-1])
            else:
                more = card.find_elements(By.XPATH, ".//button[.//i[normalize-space(.)='more_vert']]")
                more = [x for x in more if x.is_displayed() and x.is_enabled()]
                if not more:
                    return False
                self.safe_click(more[-1])
                items = self.d.find_elements(
                    By.XPATH,
                    "//*[@role='menuitem' or self::button]["
                    ".//i[normalize-space(.)='delete' or normalize-space(.)='delete_forever'] "
                    "or contains(normalize-space(.),'刪除') or contains(normalize-space(.),'删除') "
                    "or contains(normalize-space(.),'Delete')]",
                )
                items = [x for x in items if x.is_displayed() and x.is_enabled()]
                if not items:
                    try: ActionChains(self.d).send_keys(Keys.ESCAPE).perform()
                    except Exception: pass
                    return False
                self.safe_click(items[-1])

            end = time.time() + 2.5
            while time.time() < end:
                confirms = self.d.find_elements(
                    By.XPATH,
                    "//*[@role='dialog']//button["
                    "contains(normalize-space(.),'刪除') or contains(normalize-space(.),'删除') "
                    "or normalize-space(.)='Delete' or normalize-space(.)='Confirm']",
                )
                confirms = [x for x in confirms if x.is_displayed() and x.is_enabled()]
                if confirms:
                    self.safe_click(confirms[-1])
                    break
                time.sleep(.15)

            self.log.info("已清除 FLOW %s", description or "媒體卡片")
            time.sleep(.35)
            return True
        except Exception as exc:
            self.log.warning("清除 FLOW %s 失敗：%s", description or "媒體卡片", exc)
            try: ActionChains(self.d).send_keys(Keys.ESCAPE).perform()
            except Exception: pass
            return False

    def any_media_by_key(self, key: str) -> Optional[Any]:
        """尋找整個 FLOW 專案頁的指定媒體 UUID，包含素材與作品。"""
        wanted = self.normalize_media_key(key)

        def inspect() -> Optional[Any]:
            for media in self.d.find_elements(By.CSS_SELECTOR, "img, video"):
                try:
                    value = media.get_attribute("src") or media.get_attribute("poster") or ""
                    if self.normalize_media_key(value) == wanted:
                        return media
                except (StaleElementReferenceException, WebDriverException):
                    continue
            return None

        found = inspect()
        if found is not None:
            return found
        return self.sweep_media_containers(inspect)

    def cleanup_schedule_batch(self, tasks: list["FlowTask"]) -> tuple[int, int, int]:
        """清除本排程素材與已成功下載作品；未下載成功作品保留。"""
        deleted_works = 0
        deleted_materials = 0
        kept_works = 0

        for task in tasks:
            downloaded = set(task.downloaded_indices)
            for index, key in enumerate(task.generated_media, 1):
                if index not in downloaded:
                    kept_works += 1
                    self.log.warning(
                        "[任務%03d] 第 %d 個作品尚未確認下載成功，保留在 FLOW",
                        task.number, index,
                    )
                    continue
                try:
                    media = self.media_by_key_from_task_tiles(key, task.submission_tile_ids)
                    if media is None:
                        media = self.any_media_by_key(key)
                    if media is not None and self._delete_visible_card_for_media(
                        media, f"已下載作品：任務{task.number} 第{index}個"
                    ):
                        deleted_works += 1
                except Exception as exc:
                    self.log.warning("[任務%03d] 第 %d 個作品清理失敗：%s", task.number, index, exc)

        material_keys = []
        seen = set()
        for task in tasks:
            for key in task.material_media:
                normalized = self.normalize_media_key(key)
                if normalized and normalized not in seen:
                    material_keys.append(normalized)
                    seen.add(normalized)

        for idx, key in enumerate(material_keys, 1):
            try:
                media = self.any_media_by_key(key)
                if media is None:
                    self.log.warning("找不到本排程素材 UUID，無法自動清除：%s", key)
                    continue
                if self._delete_visible_card_for_media(media, f"本排程上傳素材 {idx}"):
                    deleted_materials += 1
            except Exception as exc:
                self.log.warning("清除本排程素材失敗：%s", exc)

        self.log.info(
            "FLOW 本排程清理完成｜已刪作品：%s｜已刪素材：%s｜保留未下載作品：%s",
            deleted_works, deleted_materials, kept_works,
        )
        return deleted_works, deleted_materials, kept_works

    def direct_download_image(self, image: Any, manager: DownloadManager,
                              task: FlowTask) -> Optional[Path]:
        """直接用 FLOW 媒體網址下載圖片，完全繞開瀏覽器下載資料夾。
        因為 FLOW 匯出的檔名全部相同（都是風格名稱＋時間戳記），多個任務連續下載時
        很容易靠「資料夾裡新出現的檔案」誤判成搶到別的任務的檔案。直接對每張圖片自己
        的媒體網址發 request，一一對應，不會有這種混淆。
        注意：這裡抓到的是目前畫面渲染的解析度（通常等同 1K 預覽圖）；若該卡片先前
        沒有點過「高畫質重塑」，可能不是 2K/4K 的重塑版本。若確定要 2K/4K，仍需保留
        選單點擊那條路徑。
        """
        if requests is None:
            return None
        self.d.execute_script("arguments[0].scrollIntoView({block:'center'});", image)
        redirect_url = str(self.d.execute_script(
            "return arguments[0].currentSrc || arguments[0].src || '';", image
        ) or "")
        if "media.getMediaUrlRedirect" not in redirect_url:
            return None
        if redirect_url.startswith("/"):
            redirect_url = "https://labs.google" + redirect_url
        session = requests.Session()
        session.headers.update({
            "User-Agent": self.d.execute_script("return navigator.userAgent;"),
            "Referer": "https://labs.google/",
        })
        for cookie in self.d.get_cookies():
            try:
                session.cookies.set(cookie["name"], cookie["value"],
                                    domain=cookie.get("domain"), path=cookie.get("path", "/"))
            except Exception:
                session.cookies.set(cookie["name"], cookie["value"])
        first = session.get(redirect_url, allow_redirects=False, timeout=30)
        real_url = first.headers.get("Location")
        if not real_url:
            self.log.warning("[任務%03d] 圖片網址沒有回傳重新導向，改用選單下載", task.number)
            return None
        with session.get(real_url, stream=True, timeout=(30, 120)) as response:
            if response.status_code not in (200, 206):
                self.log.warning("[任務%03d] 圖片網址下載狀態 %s，改用選單下載", task.number, response.status_code)
                return None
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}.get(content_type, ".jpg")
            target = manager.folder / f".flow_image_{self.media_key(image)}{ext}"
            with target.open("wb") as handle:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        if not target.exists() or not target.stat().st_size:
            return None
        self.log.info("[任務%03d] 已透過媒體網址快速下載圖片：%s", task.number, target.name)
        return target


class TaskManager:
    def __init__(self, app: "FlowAutomationApp"):
        self.app, self.tasks = app, []
        self.stop_event, self.run_event = threading.Event(), threading.Event()
        self.run_event.set(); self.thread = None

    def save(self) -> None:
        atomic_json(PROGRESS_FILE, {"saved_at": datetime.now().isoformat(), "tasks": [asdict(t) for t in self.tasks]})

    def load(self) -> bool:
        if not PROGRESS_FILE.exists(): return False
        try:
            data = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
            self.tasks = [FlowTask.from_dict(x) for x in data.get("tasks", [])]
            return bool(self.tasks)
        except Exception: return False

    @staticmethod
    def read_lines(path: str) -> list[str]:
        return [x.strip() for x in Path(path).read_text(encoding="utf-8-sig").splitlines() if x.strip()]

    def build_prompts(self, cfg: dict[str, Any], count: int) -> list[str]:
        mode = cfg["prompt_mode"]
        if mode == PROMPT_MODE_1:
            lines = self.read_lines(str(cfg["random_prompt_file"]))
            if len(lines) < count:
                raise ValueError(
                    f"模式1隨機文案 TXT 只有 {len(lines)} 筆非空白文案，但本批次需要 {count} 筆；"
                    "請補足文案或降低製作數量"
                )
            selected = random.sample(lines, count)
            self.app.log.info("模式1已從 TXT 的 %s 筆文案中隨機抽取 %s 筆，不重複使用", len(lines), count)
            return selected
        if mode == PROMPT_MODE_2:
            return [
                "隨機選擇一個背景 海灘 街景 農村 咖啡聽 廣播室 皆以菲律賓 "
                "隨機選擇一個 並符合 背景的的人物穿著"
            ] * count
        if mode == PROMPT_MODE_3:
            return [str(cfg.get("same_prompt", "")).strip()] * count
        raise ValueError("提示詞模式設定不正確")

    def build(self, cfg: dict[str, Any]) -> list[FlowTask]:
        source = cfg["source_mode"]
        materials: list[tuple[str, str]] = []
        if source == "自動上傳資料夾素材":
            allowed = {".jpg", ".jpeg", ".png", ".webp"}
            if cfg["generation_type"] == "影片模式": allowed |= {".mp4", ".mov", ".webm"}
            for p in Path(cfg["material_folder"]).iterdir():
                if p.is_file() and not p.name.startswith((".", "~")) and p.suffix.lower() in allowed and p.stat().st_size:
                    materials.append((str(p.resolve()), p.name))
            materials.sort(key=lambda x: natural_key(x[1]))
        elif source == "使用 FLOW 內已手動上傳的素材":
            materials = [("", x) for x in self.read_lines(cfg["material_list"])]
        if source == "不使用素材，只輸入提示詞":
            materials = [("", "") for _ in range(max(1, int(cfg["make_count"])))]
        elif not materials:
            if source == "自動上傳資料夾素材":
                raise ValueError("素材資料夾內沒有讀取到可用素材（JPG、JPEG、PNG、WEBP；影片另支援 MP4、MOV、WEBM）")
            raise ValueError("已上傳素材檔名 TXT 內沒有讀取到任何素材檔名")
        requested = max(1, int(cfg["make_count"]))
        if source != "不使用素材，只輸入提示詞" and len(materials) > requested:
            self.app.log.info(
                "素材共有 %s 筆，依「製作數量」%s 僅取自然排序前 %s 筆",
                len(materials), requested, requested,
            )
            materials = materials[:requested]
        prompts = self.build_prompts(cfg, len(materials))
        expected = int(re.sub(r"\D", "", cfg["image_count"] if cfg["generation_type"] == "圖片模式" else cfg["video_count"]) or 1)
        return [FlowTask(i + 1, path, name, prompts[i], expected_count=expected)
                for i, (path, name) in enumerate(materials)]

    def start(self, test: bool = False, config_override: Optional[dict[str, Any]] = None,
              fresh_batch: bool = False, schedule_id: Optional[str] = None) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError("目前已有任務執行中")
        self.stop_event.clear(); self.run_event.set()
        self.thread = threading.Thread(
            target=self.worker,
            args=(test, dict(config_override) if config_override else None, fresh_batch, schedule_id),
            daemon=True,
        )
        self.thread.start()

    def worker(self, test: bool, config_override: Optional[dict[str, Any]] = None,
               fresh_batch: bool = False, schedule_id: Optional[str] = None) -> None:
        cfg = dict(config_override) if config_override else self.app.collect_config()
        cfg["duplicate_policy"] = "自動覆蓋"
        app = self.app
        try:
            if fresh_batch:
                self.tasks = self.build(cfg)
            elif not self.tasks or all(t.status in (Status.SUCCESS.value, Status.FAILED.value) for t in self.tasks):
                self.tasks = self.build(cfg)
            if test: self.tasks = self.tasks[:1]
            self.save(); app.set_totals(len(self.tasks))
            driver = app.chrome.attach(cfg["chrome_path"], int(cfg["debug_port"]), cfg["download_folder"])
            ctrl = FlowController(driver, app.log, self.stop_event, self.run_event, cfg)
            ctrl.open(cfg["flow_url"])
            if not ctrl.is_logged_in(): raise RuntimeError("Google 登入已失效，請先完成登入")
            if bool(cfg.get("auto_zoom_flow", True)):
                ctrl.set_browser_zoom(int(cfg.get("zoom_percent", 25)))
            dm = DownloadManager(cfg["download_folder"], app.log)
            # 單任務穩定模式：一次只處理一筆。
            # 每筆必須完成「上傳素材 → 加入提示 → 送出 → 找到本次 Tile → 判斷成功/失敗」
            # 才會進入下一筆，避免多任務同時生成造成 Tile 與作品配錯。
            app.log.info("已啟用單任務穩定模式：每次只處理一筆，成功後才進入下一筆")
            for task in self.tasks:
                if task.status in (Status.COMPLETED.value, Status.SUCCESS.value, Status.FAILED.value):
                    continue
                if self.stop_event.is_set():
                    break
                app.current(task)
                self.process(ctrl, dm, task, cfg, test, submit_only=False)
                self.save()
                app.update_progress(self.tasks)
            app.log.info("任務執行結束")
        except InterruptedError: app.log.warning("已安全停止任務")
        except Exception as exc:
            app.log.exception("執行中止：%s", exc); app.alert("執行中止", str(exc), error=True)
        finally:
            self.save()
            app.running(False)
            app.root.after(0, lambda sid=schedule_id: app._task_manager_finished(sid))

    def download_task_immediately(self, ctrl: FlowController, dm: DownloadManager,
                                  task: FlowTask, cfg: dict[str, Any]) -> bool:
        """找到任務 Tile 並確認作品完成後，立即下載該任務全部作品。"""
        quality = cfg["image_quality"] if cfg["generation_type"] == "圖片模式" else cfg["video_quality"]
        material_names = [item.material_name for item in self.tasks]
        for download_round in range(1, 6):
            if download_round > 1:
                wait_seconds = 20 if (cfg["generation_type"] == "圖片模式" and str(quality).upper() in {"2K", "4K"}) else 3
                self.app.log.info("[任務%03d] 立即下載第 %d 輪補抓，等待 %s 秒", task.number, download_round, wait_seconds)
                time.sleep(wait_seconds)
                ctrl.dismiss_toasts()
            for index, key in enumerate(task.generated_media, 1):
                if index in task.downloaded_indices or index in task.inspected_indices:
                    continue
                try:
                    media = ctrl.media_by_key_from_task_tiles(key, task.submission_tile_ids)
                    if media is None:
                        raise RuntimeError("找不到任務 Tile 內的作品卡片")
                    task.status = Status.DOWNLOADING.value
                    self.save()
                    result, kind = ctrl.download_card(
                        media, quality, dm, task, index, material_names, [task.prompt]
                    )
                    if kind == "work" and result:
                        sequence = sum(previous.expected_count for previous in self.tasks if previous.number < task.number) + index
                        renamed = dm.rename_sequence(result, sequence, cfg["duplicate_policy"])
                        if renamed is None:
                            raise RuntimeError(f"作品 {sequence} 因重名規則被跳過")
                        task.downloaded.append(str(renamed))
                        task.downloaded_indices.append(index)
                        self.app.log.info("[任務%03d] Tile 完成後已立即下載：%s", task.number, renamed.name)
                    else:
                        task.inspected_indices.append(index)
                    self.save()
                except Exception as exc:
                    log_method = self.app.log.error if download_round >= 5 else self.app.log.warning
                    log_method("[任務%03d] 第 %d 張立即下載第 %d 輪失敗：%s", task.number, index, download_round, exc)
            if len(set(task.downloaded_indices)) >= task.expected_count:
                task.status = Status.SUCCESS.value
                self.save()
                return True
        task.status = Status.COMPLETED.value
        self.save()
        return False

    def process(self, ctrl: FlowController, dm: DownloadManager, task: FlowTask,
                cfg: dict[str, Any], test: bool, submit_only: bool = False) -> None:
        for attempt in range(task.retry_count, int(cfg["max_retries"])):
            try:
                task.retry_count = attempt; task.last_update = datetime.now().isoformat(); self.save()
                ctrl.checkpoint()
                if not task.submitted:
                    ctrl.configure()
                    if cfg["source_mode"] != "不使用素材，只輸入提示詞" and not task.added_to_prompt:
                        if cfg["source_mode"] == "自動上傳資料夾素材" and not task.uploaded:
                            task.pre_upload_media = ctrl.page_media_keys()
                            task.status = Status.UPLOADING.value; self.save(); ctrl.upload(task.material_path)
                            task.uploaded = True; task.status = Status.UPLOADED.value; self.save()
                        task.status = Status.ADDING_TO_PROMPT.value; self.save()
                        selected_material_key = ctrl.search_add_material(task.material_name)
                        if not selected_material_key:
                            selected_material_key = ctrl.prompt_material_key()
                        if not selected_material_key and task.pre_upload_media:
                            before_keys = {ctrl.normalize_media_key(key) for key in task.pre_upload_media}
                            new_keys = [key for key in ctrl.page_media_keys() if key not in before_keys]
                            if len(new_keys) == 1:
                                selected_material_key = new_keys[0]
                                self.app.log.info(
                                    "[任務%03d] 已依上傳前後 UUID 差集補回素材識別碼", task.number
                                )
                        if selected_material_key:
                            task.material_media = [selected_material_key]
                            self.app.log.info("[任務%03d] 已記錄上傳素材 UUID，下載時永久排除", task.number)
                        else:
                            self.app.log.warning(
                                "[任務%03d] 提示框已有素材，但無法取得素材 UUID；作品仍依專屬 tile 判斷",
                                task.number,
                            )
                        task.added_to_prompt = True; self.save()
                    ctrl.set_prompt(task.prompt); task.status = Status.READY.value; self.save()
                    if test and not cfg["test_submit"]:
                        task.status = Status.SUCCESS.value; self.app.log.info("[任務%03d] 測試成功，依設定不送出", task.number); return
                    task.baseline_cards = ctrl.card_count()
                    task.baseline_media = [ctrl.media_key(item) for item in ctrl.generated_media()]
                    task.baseline_tile_ids = ctrl.tile_ids()
                    def mark_submitted() -> None:
                        task.submitted = True
                        task.submit_time = datetime.now().isoformat()
                        task.status = Status.GENERATING.value
                        self.save()
                        # 必須在點擊送出後立刻取得新 tile；若等全部任務送完才掃描，
                        # 完成順序不同時就無法知道作品屬於哪一筆任務。
                        task.submission_tile_ids = ctrl.wait_new_submission_tiles(
                            task.baseline_tile_ids, task.expected_count
                        )
                        self.save()
                    confirmed = ctrl.submit(mark_submitted)
                    if not confirmed:
                        self.app.log.warning("[任務%03d] 提交狀態未確認，但已記錄為已送出；後續只監控、不重送", task.number)
                    if submit_only:
                        self.app.log.info("[任務%03d] 已送入 FLOW 排程，立即處理下一筆", task.number)
                        return
                else:
                    self.app.log.info("[任務%03d] 已有送出紀錄，本次只監控結果，不再設定提示或點擊送出", task.number)
                    if submit_only:
                        return
                if task.submission_tile_ids:
                    cards = ctrl.wait_generation_for_tiles(
                        task.submission_tile_ids, task.expected_count,
                        [item.material_name for item in self.tasks],
                    )
                else:
                    # 僅相容舊進度檔。新任務一律應有 submission_tile_ids。
                    self.app.log.warning(
                        "[任務%03d] 為舊版進度，沒有送出 tile 識別碼；此次只能使用基準差異救援，可能需人工核對",
                        task.number,
                    )
                    excluded_materials = [key for item in self.tasks for key in item.material_media]
                    excluded_materials += [key for item in self.tasks if item is not task for key in item.generated_media]
                    cards = ctrl.wait_generation(
                        task.baseline_cards, task.expected_count, task.baseline_media,
                        excluded_materials, [item.material_name for item in self.tasks]
                    )
                task.generated = True
                task.actual_count = len(cards)
                task.generated_media = [ctrl.media_key(card) for card in cards]
                task.status = Status.COMPLETED.value
                self.save()
                self.app.log.info("[任務%03d] 已找到 Tile 並確認作品完成，立即下載此任務", task.number)
                completed = self.download_task_immediately(ctrl, dm, task, cfg)
                if completed:
                    self.app.log.info("[任務%03d] 生成與下載均完成，開始下一筆任務", task.number)
                else:
                    self.app.log.warning(
                        "[任務%03d] 作品已生成，但仍有檔案未下載；可稍後按「挑選並下載本次作品」補抓",
                        task.number,
                    )
                return
            except InterruptedError: raise
            except Exception as exc:
                task.failure_reason = str(exc); self.app.capture_error(task, exc)

                # FLOW 已明確顯示此 Tile 失敗：重做同一任務，但沿用素材庫中的舊素材。
                # 保留 uploaded=True 與 material_media，下一輪跳過 upload()，只重新搜尋素材、
                # 加入提示框、輸入原文案，再按一次做圖。
                if "tile 顯示失敗卡片" in str(exc):
                    if attempt + 1 >= int(cfg["max_retries"]):
                        task.status = Status.FAILED.value
                        self.save()
                        self.app.log.error(
                            "[任務%03d] 同一素材重做已達上限 %s 次，標記失敗：%s",
                            task.number, cfg["max_retries"], exc,
                        )
                        return
                    self.app.log.warning(
                        "[任務%03d] Tile 顯示失敗，將沿用已上傳素材重做（第 %d/%s 次）：%s",
                        task.number, attempt + 2, cfg["max_retries"], task.material_name or "無素材",
                    )
                    task.status = Status.PENDING.value
                    # 素材已存在 FLOW 素材庫，不重新上傳。
                    # 自動上傳模式保留 uploaded=True；手動素材模式原值也照舊。
                    task.added_to_prompt = False
                    task.submitted = False
                    task.generated = False
                    task.actual_count = 0
                    task.baseline_cards = 0
                    task.baseline_media = []
                    task.baseline_tile_ids = []
                    task.submission_tile_ids = []
                    task.generated_media = []
                    # 保留 pre_upload_media/material_media，確保舊素材 UUID 仍可用於排除素材。
                    task.submit_time = ""
                    task.failure_reason = ""
                    self.save()
                    try:
                        ActionChains(ctrl.d).send_keys(Keys.ESCAPE).perform()
                    except Exception:
                        pass
                    time.sleep(int(cfg["check_interval"]))
                    continue

                if "找不到本次任務建立的 FLOW tile" in str(exc):
                    task.status = Status.FAILED.value
                    self.save()
                    self.app.log.error(
                        "[任務%03d] 無法取得送出 tile，已跳過此任務並繼續處理下一筆：%s",
                        task.number, exc,
                    )
                    # 不整批停止，但加一段緩衝等待：讓可能延遲出現的 tile
                    # 有時間完全掛載到畫面上。因為下一筆任務會即時重新掃描
                    # baseline_tile_ids，只要這顆遲來的 tile 在下一筆任務
                    # 「送出前」就已出現在畫面上，就會被視為舊有 tile，
                    # 不會被誤判成下一筆任務自己新產生的 tile，避免作品錯配。
                    try:
                        time.sleep(max(5, int(cfg.get("check_interval", 5))))
                        stale_ids = ctrl.tile_ids()
                        self.app.log.info(
                            "[任務%03d] 緩衝等待後重新掃描，目前畫面 tile 數：%d",
                            task.number, len(stale_ids),
                        )
                    except Exception:
                        pass
                    return
                if task.submitted and not task.generated and "無法確認任務是否成功提交" in str(exc):
                    task.status = Status.FAILED.value; self.save(); return
                if attempt + 1 >= int(cfg["max_retries"]):
                    task.status = Status.FAILED.value; self.save(); self.app.log.error("[任務%03d] 已達重試上限：%s", task.number, exc); return
                self.app.log.warning("[任務%03d] 第 %d 次失敗，將從安全階段重試：%s", task.number, attempt + 1, exc)
                time.sleep(int(cfg["check_interval"]))


class FlowAutomationApp:
    def __init__(self, root: tk.Tk):
        self.root = root; root.title(APP_NAME); root.geometry("1180x820"); root.minsize(980, 700)
        self.cfg = ConfigManager.load(); self.vars: dict[str, tk.Variable] = {}; self.log_queue = queue.Queue()
        LOG_DIR.mkdir(exist_ok=True)
        self.log = logging.getLogger("FLOW"); self.log.setLevel(logging.INFO); self.log.handlers.clear()
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(LOG_DIR / f"flow_{datetime.now():%Y-%m-%d}.log", encoding="utf-8"); fh.setFormatter(fmt); self.log.addHandler(fh)
        gh = GuiLogHandler(self.log_queue.put); gh.setFormatter(fmt); self.log.addHandler(gh)
        self.chrome = ChromeManager(self.log); self.manager = TaskManager(self)
        self.schedules: list[dict[str, Any]] = []
        self.schedule_queue: list[str] = []
        self.active_schedule_id: Optional[str] = None
        self.schedule_editing_id: Optional[str] = None
        self._build()
        self._load_schedules()
        self._refresh_schedule_tree()
        self.root.after(100, self._flush_log)
        self.root.after(1000, self._schedule_tick)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if not SELENIUM_OK: self.root.after(500, lambda: messagebox.showwarning("缺少套件", "請先開啟 CMD 執行：\n\npip install selenium"))
        self.root.after(800, self._resume_offer)

    def sv(self, key: str) -> tk.StringVar:
        v = tk.StringVar(value=str(self.cfg.get(key, ""))); self.vars[key] = v; return v
    def bv(self, key: str) -> tk.BooleanVar:
        v = tk.BooleanVar(value=bool(self.cfg.get(key, False))); self.vars[key] = v; return v
    def spin(self, parent: Any, key: str, start: int, end: int, width: int = 8) -> ttk.Spinbox:
        return ttk.Spinbox(parent, from_=start, to=end, textvariable=self.sv(key), width=width)
    def combo(self, parent: Any, key: str, values: list[str], width: int = 18) -> ttk.Combobox:
        return ttk.Combobox(parent, textvariable=self.sv(key), values=values, width=width)

    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=8); top.pack(fill="both", expand=True)
        nb = ttk.Notebook(top); nb.pack(fill="both", expand=True)
        basic, options, run = ttk.Frame(nb, padding=10), ttk.Frame(nb, padding=10), ttk.Frame(nb, padding=10)
        nb.add(basic, text="Chrome／任務設定"); nb.add(options, text="生成／進階設定"); nb.add(run, text="執行狀態與 LOG")
        for c in range(3): basic.columnconfigure(c, weight=1 if c == 1 else 0)
        row = 0
        def pathrow(label: str, key: str, folder: bool = False):
            nonlocal row
            ttk.Label(basic, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(basic, textvariable=self.sv(key)).grid(row=row, column=1, sticky="ew", padx=5)
            ttk.Button(basic, text="選擇", command=lambda: self.pick(key, folder)).grid(row=row, column=2); row += 1
        pathrow("Chrome 執行檔", "chrome_path")
        ttk.Button(basic, text="自動偵測 Chrome", command=self.detect_chrome).grid(row=row, column=1, sticky="w"); row += 1
        pathrow("Chrome Profile", "profile_path", True)
        ttk.Label(basic, text="FLOW 網址").grid(row=row, column=0, sticky="w"); ttk.Entry(basic, textvariable=self.sv("flow_url")).grid(row=row, column=1, sticky="ew", padx=5)
        bf = ttk.Frame(basic); bf.grid(row=row, column=2); ttk.Button(bf, text="開啟手動 Chrome", command=self.open_flow).pack(side="left"); row += 1
        ttk.Label(basic, text="連接埠").grid(row=row, column=0, sticky="w"); self.spin(basic, "debug_port", 1024, 65535).grid(row=row, column=1, sticky="w"); row += 1
        self.login_label = ttk.Label(basic, text="接管狀態：尚未接管"); self.login_label.grid(row=row, column=1, sticky="w"); ttk.Button(basic, text="接管目前 FLOW 頁面", command=self.test_login).grid(row=row, column=2); row += 1
        ttk.Checkbutton(basic, text="啟動任務後自動縮放 FLOW", variable=self.bv("auto_zoom_flow")).grid(row=row, column=0, sticky="w")
        ttk.Label(basic, text="縮放比例").grid(row=row, column=1, sticky="e")
        self.combo(basic, "zoom_percent", ["25", "33", "40", "50", "67", "75", "80", "90", "100"], 8).grid(row=row, column=2, sticky="w"); row += 1
        ttk.Label(basic, text="網頁診斷").grid(row=row, column=0, sticky="w"); ttk.Button(basic, text="收集 FLOW DOM 診斷", command=self.collect_dom_diagnostic).grid(row=row, column=1, sticky="w"); row += 1
        ttk.Separator(basic).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8); row += 1
        for label, key, vals in [
            ("生成類型", "generation_type", ["圖片模式", "影片模式"]),
            ("素材來源模式", "source_mode", ["自動上傳資料夾素材", "使用 FLOW 內已手動上傳的素材", "不使用素材，只輸入提示詞"]),
        ]:
            ttk.Label(basic, text=label).grid(row=row, column=0, sticky="w", pady=3); self.combo(basic, key, vals, 32).grid(row=row, column=1, sticky="w"); row += 1
        ttk.Label(basic, text="提示詞模式").grid(row=row, column=0, sticky="nw", pady=3)
        prompt_mode_frame = ttk.LabelFrame(basic, text="直接點選一種模式", padding=6)
        prompt_mode_frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        prompt_mode_var = self.sv("prompt_mode")
        for index, mode in enumerate(PROMPT_MODES):
            ttk.Radiobutton(
                prompt_mode_frame, text=mode, value=mode, variable=prompt_mode_var,
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 18), pady=2)
        row += 1
        pathrow("素材資料夾", "material_folder", True); pathrow("已上傳素材檔名 TXT", "material_list")
        pathrow("模式1隨機文案 TXT", "random_prompt_file")
        ttk.Label(basic, text="模式3全手動文案").grid(row=row, column=0, sticky="nw")
        self.prompt_text = scrolledtext.ScrolledText(basic, height=5, wrap="word")
        self.prompt_text.grid(row=row, column=1, columnspan=2, sticky="nsew", pady=3)
        self.prompt_text.insert("1.0", self.cfg.get("same_prompt", ""))
        basic.rowconfigure(row, weight=1)
        row += 1
        ttk.Label(basic, text="文案使用方式").grid(row=row, column=0, sticky="nw")
        ttk.Label(
            basic,
            text="模式1：依本批次任務數，從指定 TXT 隨機抽取同數量文案（不重複）\n"
                 "模式2：隨機選擇菲律賓背景，並搭配符合背景的人物穿著\n"
                 "模式3：每個任務都使用上方輸入的同一段完整手動文案",
            foreground="#305080",
        ).grid(row=row, column=1, columnspan=2, sticky="w", pady=3); row += 1
        ttk.Label(basic, text="本批次製作數量上限").grid(row=row, column=0, sticky="w"); self.spin(basic, "make_count", 1, 9999).grid(row=row, column=1, sticky="w"); row += 1
        pathrow("下載資料夾", "download_folder", True)

        options.columnconfigure(1, weight=1); r = 0
        def opt(label: str, key: str, vals: list[str]):
            nonlocal r
            ttk.Label(options, text=label).grid(row=r, column=0, sticky="w", pady=4); self.combo(options, key, vals, 25).grid(row=r, column=1, sticky="w"); r += 1
        ttk.Label(options, text="圖片設定", font=("TkDefaultFont", 11, "bold")).grid(row=r, column=0, pady=6); r += 1
        opt("圖片比例", "image_ratio", ["16:9", "4:3", "1:1", "3:4", "9:16"]); opt("每次張數", "image_count", ["1x", "2x", "3x", "4x"]); opt("圖片模型（可輸入）", "image_model", ["Nano Banana 2"]); opt("圖片畫質", "image_quality", ["1K", "2K", "4K"]); opt("圖片格式", "image_format", ["轉換為 PNG", "保留原始格式"])
        ttk.Label(options, text="影片設定", font=("TkDefaultFont", 11, "bold")).grid(row=r, column=0, pady=6); r += 1
        opt("影片素材模式", "video_source", ["幀", "素材"]); opt("影片比例", "video_ratio", ["9:16", "16:9"]); opt("每次數量", "video_count", ["1x", "2x", "3x", "4x"]); opt("影片模型（可輸入）", "video_model", ["Omni Flash"]); opt("影片秒數", "video_seconds", ["4 秒", "6 秒", "8 秒", "10 秒"]); opt("影片畫質", "video_quality", ["420P", "720P"])
        ttk.Label(options, text="執行設定", font=("TkDefaultFont", 11, "bold")).grid(row=r, column=0, pady=6); r += 1
        for label, key, lo, hi in [("圖片最大等待秒數", "image_timeout", 30, 7200), ("影片最大等待秒數", "video_timeout", 30, 14400), ("檢查間隔秒數", "check_interval", 1, 60), ("最大重試次數", "max_retries", 1, 10)]:
            ttk.Label(options, text=label).grid(row=r, column=0, sticky="w"); self.spin(options, key, lo, hi).grid(row=r, column=1, sticky="w"); r += 1
        ttk.Label(options, text="素材上傳後穩定等待秒數").grid(row=r, column=0, sticky="w"); self.spin(options, "upload_settle_seconds", 0, 60).grid(row=r, column=1, sticky="w"); r += 1
        opt("重名處理", "duplicate_policy", ["自動覆蓋"])
        ttk.Checkbutton(options, text="測試目前任務時實際送出並下載（預設關閉）", variable=self.bv("test_submit")).grid(row=r, column=0, columnspan=2, sticky="w"); r += 1
        ttk.Checkbutton(options, text="程式關閉時關閉 Chrome", variable=self.bv("close_chrome")).grid(row=r, column=0, columnspan=2, sticky="w")

        controls = ttk.Frame(run); controls.pack(fill="x")
        for text, cmd in [("開始執行", self.start), ("暫停", self.pause), ("繼續", self.resume), ("停止", self.stop), ("測試目前任務", self.test), ("挑選並下載本次作品", self.choose_download), ("收集 FLOW DOM", self.collect_dom_diagnostic), ("清除 LOG", lambda: self.log_box.delete("1.0", "end")), ("開啟下載資料夾", self.open_download), ("儲存設定", self.save_config), ("載入設定", self.reload_config)]:
            ttk.Button(controls, text=text, command=cmd).pack(side="left", padx=2, pady=3)

        schedule_editor = ttk.LabelFrame(run, text="排程新增／編輯", padding=6)
        schedule_editor.pack(fill="x", pady=(6, 4))
        for c in (1, 3, 5): schedule_editor.columnconfigure(c, weight=1)
        self.schedule_time_var = tk.StringVar(value="13:00")
        self.schedule_type_var = tk.StringVar(value="影片模式")
        self.schedule_count_var = tk.StringVar(value="1")
        self.schedule_input_var = tk.StringVar()
        self.schedule_output_var = tk.StringVar()
        self.schedule_prompt_mode_var = tk.StringVar(value=PROMPT_MODE_1)
        self.schedule_prompt_file_var = tk.StringVar()
        self.schedule_manual_prompt_var = tk.StringVar()
        self.schedule_cleanup_var = tk.BooleanVar(value=True)
        self.schedule_retry_var = tk.StringVar(value=str(self.cfg.get("max_retries", 3)))
        ttk.Label(schedule_editor, text="時間 HH:MM").grid(row=0,column=0,sticky="w")
        ttk.Entry(schedule_editor,textvariable=self.schedule_time_var,width=9,justify="center").grid(row=0,column=1,sticky="w",padx=4)
        ttk.Label(schedule_editor,text="生成類型").grid(row=0,column=2,sticky="w")
        ttk.Combobox(schedule_editor,textvariable=self.schedule_type_var,values=["圖片模式","影片模式"],width=12,state="readonly").grid(row=0,column=3,sticky="w",padx=4)
        ttk.Label(schedule_editor,text="製作數量").grid(row=0,column=4,sticky="w")
        ttk.Spinbox(schedule_editor,from_=1,to=9999,textvariable=self.schedule_count_var,width=8).grid(row=0,column=5,sticky="w",padx=4)
        ttk.Label(schedule_editor,text="匯入素材資料夾").grid(row=1,column=0,sticky="w",pady=3)
        ttk.Entry(schedule_editor,textvariable=self.schedule_input_var).grid(row=1,column=1,columnspan=4,sticky="ew",padx=4)
        ttk.Button(schedule_editor,text="選擇",command=lambda:self._pick_schedule_path(self.schedule_input_var,True)).grid(row=1,column=5,sticky="w")
        ttk.Label(schedule_editor,text="匯出／下載資料夾").grid(row=2,column=0,sticky="w",pady=3)
        ttk.Entry(schedule_editor,textvariable=self.schedule_output_var).grid(row=2,column=1,columnspan=4,sticky="ew",padx=4)
        ttk.Button(schedule_editor,text="選擇",command=lambda:self._pick_schedule_path(self.schedule_output_var,True)).grid(row=2,column=5,sticky="w")
        ttk.Label(schedule_editor,text="提示詞模式").grid(row=3,column=0,sticky="w",pady=3)
        ttk.Combobox(schedule_editor,textvariable=self.schedule_prompt_mode_var,values=PROMPT_MODES,width=34,state="readonly").grid(row=3,column=1,sticky="w",padx=4)
        ttk.Label(schedule_editor,text="模式1 TXT").grid(row=3,column=2,sticky="w")
        ttk.Entry(schedule_editor,textvariable=self.schedule_prompt_file_var).grid(row=3,column=3,columnspan=2,sticky="ew",padx=4)
        ttk.Button(schedule_editor,text="選擇",command=lambda:self._pick_schedule_path(self.schedule_prompt_file_var,False)).grid(row=3,column=5,sticky="w")
        ttk.Label(schedule_editor,text="模式3手動提示詞").grid(row=4,column=0,sticky="w",pady=3)
        ttk.Entry(schedule_editor,textvariable=self.schedule_manual_prompt_var).grid(row=4,column=1,columnspan=3,sticky="ew",padx=4)
        ttk.Checkbutton(schedule_editor,text="排程完成後清除本次 FLOW 素材及已下載作品",variable=self.schedule_cleanup_var).grid(row=4,column=4,columnspan=2,sticky="w",padx=4)
        ttk.Label(schedule_editor,text="失敗重做次數").grid(row=5,column=0,sticky="w",pady=3)
        ttk.Spinbox(schedule_editor,from_=1,to=10,textvariable=self.schedule_retry_var,width=8).grid(row=5,column=1,sticky="w",padx=4)
        ttk.Label(schedule_editor,text="每筆任務失敗時最多重做次數（1～10）").grid(row=5,column=2,columnspan=4,sticky="w")
        sb=ttk.Frame(schedule_editor); sb.grid(row=6,column=0,columnspan=6,sticky="w",pady=(4,0))
        for label,cmd in [("加入排程",self._add_schedule),("修改排程",self._update_schedule),("刪除排程",self._delete_schedule),("啟用排程",lambda:self._set_schedule_enabled(True)),("停用排程",lambda:self._set_schedule_enabled(False)),("立即執行選取排程",self._run_selected_schedule_now),("清除全部排程",self._clear_schedules),("載入選取到編輯欄",self._load_selected_schedule_to_editor)]:
            ttk.Button(sb,text=label,command=cmd).pack(side="left",padx=2)

        pane=ttk.Panedwindow(run,orient="vertical"); pane.pack(fill="both",expand=True,pady=(4,0))
        schedule_pane=ttk.Frame(pane); log_pane=ttk.Frame(pane); pane.add(schedule_pane,weight=2); pane.add(log_pane,weight=3)
        cols=("enabled","time","type","count","retry","input","output","prompt","cleanup","status","last","next")
        self.schedule_tree=ttk.Treeview(schedule_pane,columns=cols,show="headings",height=8)
        headers={"enabled":"啟用","time":"時間","type":"類型","count":"數量","retry":"重做次數","input":"匯入資料夾","output":"匯出資料夾","prompt":"提示詞模式","cleanup":"完成後清理","status":"執行狀態","last":"最後執行","next":"下次執行"}
        widths={"enabled":55,"time":65,"type":80,"count":60,"retry":75,"input":220,"output":220,"prompt":150,"cleanup":90,"status":90,"last":130,"next":120}
        for col in cols:
            self.schedule_tree.heading(col,text=headers[col]); self.schedule_tree.column(col,width=widths[col],minwidth=50,stretch=col in {"input","output","prompt"})
        sy=ttk.Scrollbar(schedule_pane,orient="vertical",command=self.schedule_tree.yview); sx=ttk.Scrollbar(schedule_pane,orient="horizontal",command=self.schedule_tree.xview)
        self.schedule_tree.configure(yscrollcommand=sy.set,xscrollcommand=sx.set); self.schedule_tree.grid(row=0,column=0,sticky="nsew"); sy.grid(row=0,column=1,sticky="ns"); sx.grid(row=1,column=0,sticky="ew")
        schedule_pane.rowconfigure(0,weight=1); schedule_pane.columnconfigure(0,weight=1); self.schedule_tree.bind("<Double-1>",lambda _e:self._load_selected_schedule_to_editor())
        self.progress=ttk.Progressbar(log_pane,mode="determinate"); self.progress.pack(fill="x",pady=(2,6))
        self.status_var=tk.StringVar(value="總任務數：0｜已完成：0｜成功：0｜失敗：0｜剩餘：0"); ttk.Label(log_pane,textvariable=self.status_var).pack(anchor="w")
        self.current_var=tk.StringVar(value="目前任務：無"); ttk.Label(log_pane,textvariable=self.current_var,wraplength=1100).pack(anchor="w",pady=2)
        self.current_schedule_var=tk.StringVar(value="目前執行排程：無"); self.next_schedule_var=tk.StringVar(value="下一個排程：無"); self.schedule_system_var=tk.StringVar(value="排程狀態：系統運作中｜目前無等待任務")
        ttk.Label(log_pane,textvariable=self.current_schedule_var,wraplength=1100).pack(anchor="w"); ttk.Label(log_pane,textvariable=self.next_schedule_var,wraplength=1100).pack(anchor="w"); ttk.Label(log_pane,textvariable=self.schedule_system_var).pack(anchor="w",pady=(0,3))
        self.log_box=scrolledtext.ScrolledText(log_pane,height=18,state="normal",wrap="word"); self.log_box.pack(fill="both",expand=True)

    # ---------------- V1.8 多排程管理 ----------------
    def _pick_schedule_path(self, variable: tk.StringVar, folder: bool) -> None:
        value = filedialog.askdirectory() if folder else filedialog.askopenfilename()
        if value: variable.set(value)

    def _editor_schedule_config(self) -> dict[str, Any]:
        raw=self.schedule_time_var.get().strip()
        try: normalized=datetime.strptime(raw, "%H:%M").strftime("%H:%M")
        except ValueError as exc: raise ValueError("排程時間格式必須是 HH:MM，例如 13:00") from exc
        typ=self.schedule_type_var.get().strip()
        if typ not in {"圖片模式","影片模式"}: raise ValueError("生成類型不正確")
        try: count=max(1,int(self.schedule_count_var.get()))
        except ValueError as exc: raise ValueError("製作數量必須是整數") from exc
        cfg=self.collect_config(); cfg["generation_type"]=typ; cfg["source_mode"]="自動上傳資料夾素材"
        cfg["material_folder"]=self.schedule_input_var.get().strip(); cfg["download_folder"]=self.schedule_output_var.get().strip(); cfg["make_count"]=count
        cfg["prompt_mode"]=self.schedule_prompt_mode_var.get().strip(); cfg["random_prompt_file"]=self.schedule_prompt_file_var.get().strip(); cfg["same_prompt"]=self.schedule_manual_prompt_var.get().strip(); cfg["cleanup_after_run"]=bool(self.schedule_cleanup_var.get()); cfg["max_retries"]=max(1,min(10,int(self.schedule_retry_var.get()))); cfg["duplicate_policy"]="自動覆蓋"
        if not cfg["material_folder"] or not Path(cfg["material_folder"]).is_dir(): raise ValueError("請選擇有效的排程匯入素材資料夾")
        if not cfg["download_folder"]: raise ValueError("請選擇排程匯出／下載資料夾")
        Path(cfg["download_folder"]).mkdir(parents=True,exist_ok=True)
        if cfg["prompt_mode"]==PROMPT_MODE_1 and (not cfg["random_prompt_file"] or not Path(cfg["random_prompt_file"]).is_file()): raise ValueError("模式1排程必須指定有效的 TXT 文案檔")
        if cfg["prompt_mode"]==PROMPT_MODE_3 and not cfg["same_prompt"]: raise ValueError("模式3排程的手動提示詞不可為空")
        return {"time":normalized,"config":cfg}

    def _selected_schedule(self) -> Optional[dict[str, Any]]:
        selected=self.schedule_tree.selection() if hasattr(self,"schedule_tree") else ()
        if not selected: return None
        sid=selected[0]; return next((s for s in self.schedules if s.get("id")==sid),None)

    def _add_schedule(self) -> None:
        try:
            built=self._editor_schedule_config(); item={"id":uuid.uuid4().hex,"enabled":True,"time":built["time"],"config":built["config"],"status":"等待中","last_run_date":"","last_run_time":"","queued_at":""}
            self.schedules.append(item); self._save_schedules(); self._refresh_schedule_tree(); self.log.info("已加入排程：%s｜%s｜%s → %s",item["time"],item["config"]["generation_type"],item["config"]["material_folder"],item["config"]["download_folder"])
        except Exception as exc: messagebox.showerror("加入排程失敗",str(exc))

    def _load_selected_schedule_to_editor(self) -> None:
        item=self._selected_schedule()
        if not item: messagebox.showwarning("尚未選擇","請先選擇一筆排程"); return
        cfg=item.get("config",{}); self.schedule_editing_id=item["id"]; self.schedule_time_var.set(item.get("time","")); self.schedule_type_var.set(cfg.get("generation_type","影片模式")); self.schedule_count_var.set(str(cfg.get("make_count",1))); self.schedule_input_var.set(cfg.get("material_folder","")); self.schedule_output_var.set(cfg.get("download_folder","")); self.schedule_prompt_mode_var.set(cfg.get("prompt_mode",PROMPT_MODE_1)); self.schedule_prompt_file_var.set(cfg.get("random_prompt_file","")); self.schedule_manual_prompt_var.set(cfg.get("same_prompt","")); self.schedule_cleanup_var.set(bool(cfg.get("cleanup_after_run",True))); self.schedule_retry_var.set(str(cfg.get("max_retries",self.cfg.get("max_retries",3)))); self.log.info("已載入排程到編輯欄：%s",item.get("time"))

    def _update_schedule(self) -> None:
        item=self._selected_schedule() or next((s for s in self.schedules if s.get("id")==self.schedule_editing_id),None)
        if not item: messagebox.showwarning("尚未選擇","請先選擇要修改的排程"); return
        try:
            built=self._editor_schedule_config(); item["time"]=built["time"]; item["config"]=built["config"]; item["status"]="等待中" if item.get("enabled",True) else "已停用"; self._save_schedules(); self._refresh_schedule_tree(); self.log.info("已修改排程：%s",item["time"])
        except Exception as exc: messagebox.showerror("修改排程失敗",str(exc))

    def _delete_schedule(self) -> None:
        item=self._selected_schedule()
        if not item: messagebox.showwarning("尚未選擇","請先選擇要刪除的排程"); return
        if item.get("id")==self.active_schedule_id: messagebox.showwarning("無法刪除","這筆排程目前正在執行"); return
        sid=item["id"]; self.schedules=[s for s in self.schedules if s.get("id")!=sid]; self.schedule_queue=[x for x in self.schedule_queue if x!=sid]; self._save_schedules(); self._refresh_schedule_tree()

    def _set_schedule_enabled(self, enabled: bool) -> None:
        item=self._selected_schedule()
        if not item: messagebox.showwarning("尚未選擇","請先選擇排程"); return
        item["enabled"]=enabled; item["status"]="等待中" if enabled else "已停用"
        if not enabled: self.schedule_queue=[x for x in self.schedule_queue if x!=item["id"]]
        self._save_schedules(); self._refresh_schedule_tree()

    def _clear_schedules(self) -> None:
        if self.active_schedule_id: messagebox.showwarning("目前有排程執行中","請等待目前排程完成後再清除全部排程"); return
        if self.schedules and messagebox.askyesno("清除全部排程","確定刪除全部排程？"):
            self.schedules=[]; self.schedule_queue=[]; self._save_schedules(); self._refresh_schedule_tree()

    def _run_selected_schedule_now(self) -> None:
        item=self._selected_schedule()
        if not item: messagebox.showwarning("尚未選擇","請先選擇要執行的排程"); return
        self._queue_schedule(item,manual=True)

    def _save_schedules(self) -> None:
        atomic_json(SCHEDULE_FILE,{"saved_at":datetime.now().isoformat(),"schedules":self.schedules})

    def _load_schedules(self) -> None:
        self.schedules=[]
        if not SCHEDULE_FILE.exists(): return
        try:
            data=json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
            for raw in data.get("schedules",[]):
                if not isinstance(raw,dict): continue
                raw.setdefault("id",uuid.uuid4().hex); raw.setdefault("enabled",True); raw.setdefault("status","等待中" if raw["enabled"] else "已停用"); raw.setdefault("last_run_date",""); raw.setdefault("last_run_time",""); raw.setdefault("queued_at","")
                cfg=dict(raw.get("config") or {}); cfg["duplicate_policy"]="自動覆蓋"; cfg.setdefault("cleanup_after_run",True); cfg.setdefault("max_retries",int(self.cfg.get("max_retries",3))); raw["config"]=cfg
                if raw.get("status") in {"執行中","等待執行"}: raw["status"]="等待中" if raw["enabled"] else "已停用"
                self.schedules.append(raw)
        except Exception as exc: self.log.warning("排程檔讀取失敗：%s",exc)

    def _next_run_text(self,item:dict[str,Any])->str:
        if not item.get("enabled",True): return "--"
        from datetime import timedelta
        now=datetime.now(); target=datetime.combine(now.date(),datetime.strptime(item.get("time","00:00"),"%H:%M").time())
        if target<now or item.get("last_run_date")==now.date().isoformat(): target+=timedelta(days=1)
        return f"{'今天' if target.date()==now.date() else '明天'} {target:%H:%M}"

    def _refresh_schedule_tree(self) -> None:
        if not hasattr(self,"schedule_tree"): return
        selected=self.schedule_tree.selection(); keep=selected[0] if selected else None
        for iid in self.schedule_tree.get_children(): self.schedule_tree.delete(iid)
        for item in sorted(self.schedules,key=lambda s:(s.get("time",""),s.get("id",""))):
            cfg=item.get("config",{}); self.schedule_tree.insert("","end",iid=item["id"],values=("✓" if item.get("enabled",True) else "✗",item.get("time",""),cfg.get("generation_type",""),cfg.get("make_count",""),cfg.get("max_retries",3),cfg.get("material_folder",""),cfg.get("download_folder",""),cfg.get("prompt_mode",""),"是" if cfg.get("cleanup_after_run",True) else "否",item.get("status","等待中"),item.get("last_run_time","") or "--",self._next_run_text(item)))
        if keep and self.schedule_tree.exists(keep): self.schedule_tree.selection_set(keep)
        self._update_schedule_status_labels()

    def _update_schedule_status_labels(self) -> None:
        if not hasattr(self,"current_schedule_var"): return
        active=next((s for s in self.schedules if s.get("id")==self.active_schedule_id),None)
        if active:
            cfg=active["config"]; self.current_schedule_var.set(f"目前執行排程：{active['time']}｜{cfg.get('generation_type','')}｜{cfg.get('material_folder','')} → {cfg.get('download_folder','')}")
        else: self.current_schedule_var.set("目前執行排程：無")
        queued=[next((s for s in self.schedules if s.get("id")==sid),None) for sid in self.schedule_queue]; queued=[s for s in queued if s]
        if queued:
            q=queued[0]; self.next_schedule_var.set(f"下一個排程：{q['time']}｜{q['config'].get('generation_type','')}"); self.schedule_system_var.set(f"排程狀態：系統運作中｜等待執行 {len(queued)} 筆")
        else: self.next_schedule_var.set("下一個排程：無"); self.schedule_system_var.set("排程狀態：系統運作中｜目前無等待任務")

    def _schedule_tick(self) -> None:
        try:
            now=datetime.now(); today=now.date().isoformat(); hm=now.strftime("%H:%M"); changed=False
            for item in self.schedules:
                if not item.get("enabled",True) or item.get("time")!=hm or item.get("last_run_date")==today: continue
                item["last_run_date"]=today; item["last_run_time"]=now.strftime("%Y-%m-%d %H:%M:%S"); changed=True; self._queue_schedule(item,manual=False)
            if changed: self._save_schedules(); self._refresh_schedule_tree()
        finally: self.root.after(1000,self._schedule_tick)

    def _queue_schedule(self,item:dict[str,Any],manual:bool=False)->None:
        sid=item["id"]
        if sid==self.active_schedule_id or sid in self.schedule_queue: return
        if self.manager.thread and self.manager.thread.is_alive():
            self.schedule_queue.append(sid); item["status"]="等待執行"; item["queued_at"]=datetime.now().isoformat(); self.log.info("排程 %s 到點，但目前主任務仍在執行，已加入等待佇列",item["time"]); self._save_schedules(); self._refresh_schedule_tree(); return
        self._start_schedule_item(item,manual=manual)

    def _start_schedule_item(self,item:dict[str,Any],manual:bool=False)->None:
        try:
            cfg=dict(item["config"]); cfg["duplicate_policy"]="自動覆蓋"; base=self.collect_config()
            for key in ("chrome_path","profile_path","flow_url","debug_port","auto_zoom_flow","zoom_percent","close_chrome"): cfg[key]=base.get(key,cfg.get(key))
            if not self.chrome.driver or "/tools/flow/project/" not in self.chrome.driver.current_url: raise RuntimeError("排程已到點，但尚未接管 FLOW 專案製作頁。請先完成手動登入並按「接管目前 FLOW 頁面」。")
            Path(cfg["download_folder"]).mkdir(parents=True,exist_ok=True); item["status"]="執行中"; self.active_schedule_id=item["id"]; self.manager.tasks=[]; self._save_schedules(); self._refresh_schedule_tree()
            self.log.info("="*58); self.log.info("排程%s觸發","立即" if manual else "自動"); self.log.info("排程 ID：%s",item["id"]); self.log.info("時間：%s",item["time"]); self.log.info("類型：%s",cfg["generation_type"]); self.log.info("素材資料夾：%s",cfg["material_folder"]); self.log.info("輸出資料夾：%s",cfg["download_folder"]); self.log.info("製作數量：%s",cfg["make_count"]); self.log.info("失敗重做次數：%s",cfg.get("max_retries",3)); self.log.info("完成後清理 FLOW：%s","是" if cfg.get("cleanup_after_run",True) else "否"); self.log.info("="*58)
            self.manager.start(config_override=cfg,fresh_batch=True,schedule_id=item["id"]); self.running(True)
        except Exception as exc:
            item["status"]="失敗"; self.active_schedule_id=None; self._save_schedules(); self._refresh_schedule_tree(); self.log.exception("排程啟動失敗：%s",exc); self.alert("排程啟動失敗",str(exc),error=True); self.root.after(100,self._start_next_queued_schedule)

    def _task_manager_finished(self,schedule_id:Optional[str])->None:
        if not schedule_id:
            self.root.after(100,self._start_next_queued_schedule)
            return
        item=next((s for s in self.schedules if s.get("id")==schedule_id),None)
        if not item:
            self.active_schedule_id=None
            self.root.after(100,self._start_next_queued_schedule)
            return

        success=sum(t.status in (Status.COMPLETED.value,Status.SUCCESS.value) for t in self.manager.tasks)
        failed=sum(t.status==Status.FAILED.value for t in self.manager.tasks)
        final_status="已完成" if failed==0 else ("部分失敗" if success else "失敗")
        self.log.info("排程 %s 製作階段完成｜成功：%s｜失敗：%s",item["time"],success,failed)

        if bool(item.get("config",{}).get("cleanup_after_run",True)):
            item["status"]="清理 FLOW 中"
            self._save_schedules()
            self._refresh_schedule_tree()
            tasks_snapshot=list(self.manager.tasks)
            threading.Thread(
                target=self._cleanup_schedule_worker,
                args=(schedule_id,tasks_snapshot,final_status),
                daemon=True,
            ).start()
            return

        item["status"]=final_status
        self.active_schedule_id=None
        self._save_schedules()
        self._refresh_schedule_tree()
        self.root.after(100,self._start_next_queued_schedule)

    def _cleanup_schedule_worker(self,schedule_id:str,tasks:list[FlowTask],final_status:str)->None:
        kept=0
        try:
            item=next((s for s in self.schedules if s.get("id")==schedule_id),None)
            if not item:
                return
            cfg=dict(item.get("config") or {})
            base=self.collect_config()
            for key in ("chrome_path","flow_url","debug_port","auto_zoom_flow","zoom_percent"):
                cfg[key]=base.get(key,cfg.get(key))
            driver=self.chrome.attach(cfg["chrome_path"],int(cfg["debug_port"]),cfg["download_folder"])
            pause=threading.Event(); pause.set()
            ctrl=FlowController(driver,self.log,threading.Event(),pause,cfg)
            deleted_works,deleted_materials,kept=ctrl.cleanup_schedule_batch(tasks)
            self.log.info(
                "排程 %s 清理完成｜刪除作品：%s｜刪除素材：%s｜保留未下載作品：%s",
                item["time"],deleted_works,deleted_materials,kept,
            )
            if kept and final_status=="已完成":
                final_status="部分失敗"
        except Exception as exc:
            self.log.exception("排程完成後清理 FLOW 失敗：%s",exc)
        finally:
            def done()->None:
                item=next((s for s in self.schedules if s.get("id")==schedule_id),None)
                if item:
                    item["status"]=final_status
                self.active_schedule_id=None
                self._save_schedules()
                self._refresh_schedule_tree()
                self.root.after(100,self._start_next_queued_schedule)
            self.root.after(0,done)

    def _start_next_queued_schedule(self)->None:
        if self.manager.thread and self.manager.thread.is_alive(): return
        while self.schedule_queue:
            sid=self.schedule_queue.pop(0); item=next((s for s in self.schedules if s.get("id")==sid),None)
            if not item or not item.get("enabled",True): continue
            self._start_schedule_item(item,manual=False); break
        self._save_schedules(); self._refresh_schedule_tree()

    def pick(self, key: str, folder: bool) -> None:
        value = filedialog.askdirectory() if folder else filedialog.askopenfilename()
        if value: self.vars[key].set(value)
    def detect_chrome(self) -> None:
        p = ChromeManager.detect(); self.vars["chrome_path"].set(p)
        messagebox.showinfo("偵測結果", f"已找到：\n{p}" if p else "找不到 Chrome，請手動選擇 chrome.exe")
    def collect_config(self) -> dict[str, Any]:
        data = dict(self.cfg)
        for k, v in self.vars.items(): data[k] = v.get()
        data["same_prompt"] = self.prompt_text.get("1.0", "end-1c")
        for k in ["make_count", "image_timeout", "video_timeout", "check_interval", "max_retries", "debug_port", "upload_settle_seconds", "zoom_percent"]: data[k] = int(data[k])
        data["duplicate_policy"] = "自動覆蓋"
        return data
    def save_config(self) -> None:
        self.cfg = self.collect_config(); ConfigManager.save(self.cfg); self.log.info("設定已保存")
    def reload_config(self) -> None:
        self.cfg = ConfigManager.load()
        for k, v in self.vars.items(): v.set(self.cfg.get(k, ""))
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", self.cfg.get("same_prompt", ""))
        self.log.info("設定已載入")
    def validate(self) -> dict[str, Any]:
        cfg = self.collect_config()
        if not SELENIUM_OK: raise ValueError("缺少 selenium，請執行：pip install selenium")
        if not cfg["chrome_path"] or not Path(cfg["chrome_path"]).is_file(): raise ValueError("Chrome 執行檔不存在")
        if not cfg["flow_url"].startswith("https://labs.google/fx/"): raise ValueError("FLOW 網址格式不正確")
        source = cfg["source_mode"]
        if source == "自動上傳資料夾素材":
            if not str(cfg["material_folder"]).strip(): raise ValueError("你選擇了自動上傳素材，請先選擇素材資料夾")
            if not Path(cfg["material_folder"]).is_dir(): raise ValueError("素材資料夾不存在")
        if source == "使用 FLOW 內已手動上傳的素材":
            if not str(cfg["material_list"]).strip(): raise ValueError("你選擇了 FLOW 已上傳素材，請先選擇素材檔名 TXT")
            if not Path(cfg["material_list"]).is_file(): raise ValueError("素材檔名 TXT 不存在")
        if cfg["prompt_mode"] == PROMPT_MODE_1:
            if not str(cfg["random_prompt_file"]).strip(): raise ValueError("模式1請先選擇隨機文案 TXT 檔")
            if not Path(cfg["random_prompt_file"]).is_file(): raise ValueError("模式1隨機文案 TXT 不存在")
            if not TaskManager.read_lines(str(cfg["random_prompt_file"])): raise ValueError("模式1隨機文案 TXT 沒有可用的非空白文案")
        if cfg["prompt_mode"] == PROMPT_MODE_3 and not cfg["same_prompt"].strip():
            raise ValueError("模式3的全手動文案不可為空")
        folder = Path(cfg["download_folder"]); folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".flow_write_test"; probe.write_text("ok"); probe.unlink()
        return cfg
    def open_flow(self) -> None:
        try:
            cfg = self.collect_config()
            self.chrome.launch_manual(cfg["chrome_path"], cfg["profile_path"], cfg["flow_url"], int(cfg["debug_port"]))
            self.login_label.config(text="接管狀態：等待你登入並進入專案")
            self.log.info("請自行登入 Google，進入網址含 /flow/project/ 的真正製作頁；停留在該頁後按「接管目前 FLOW 頁面」")
        except Exception as exc: messagebox.showerror("開啟失敗", str(exc))
    def test_login(self) -> None:
        try:
            cfg = self.collect_config()
            driver = self.chrome.attach(cfg["chrome_path"], int(cfg["debug_port"]), cfg["download_folder"])
            pause = threading.Event(); pause.set()
            ctrl = FlowController(driver, self.log, threading.Event(), pause, cfg)
            state = ctrl.is_logged_in()
            current_url = driver.current_url
            if not state:
                self.login_label.config(text="接管狀態：尚未完成 Google 登入")
                raise RuntimeError("目前頁面尚未完成 Google 登入，請先登入後再接管")
            if "labs.google/fx/" not in current_url or "/tools/flow/project/" not in current_url:
                self.login_label.config(text="接管狀態：目前不是 FLOW 專案製作頁")
                raise RuntimeError(
                    "目前分頁不是 FLOW 真正製作頁。\n\n"
                    "請先進入類似：\n"
                    "https://labs.google/fx/zh/tools/flow/project/專案ID\n\n"
                    "停留在該分頁後再按接管。"
                )
            self.login_label.config(text="接管狀態：已接管 FLOW 專案製作頁")
            if bool(cfg.get("auto_zoom_flow", True)):
                ctrl.set_browser_zoom(int(cfg.get("zoom_percent", 25)))
            self.vars["flow_url"].set(current_url)
            self.save_config()
            self.log.info("已接管目前 FLOW 專案製作頁：%s", current_url)
        except Exception as exc: messagebox.showerror("測試失敗", str(exc))
    def start(self) -> None:
        try:
            if self.manager.thread and self.manager.thread.is_alive():
                raise RuntimeError("目前已有任務執行中；排程到點時會自動加入等待佇列")
            self.cfg = self.validate()
            if not self.chrome.driver or "/tools/flow/project/" not in self.chrome.driver.current_url:
                raise RuntimeError("請先手動進入 FLOW 專案製作頁，再按「接管目前 FLOW 頁面」")
            self.save_config()
            finished = (Status.COMPLETED.value, Status.SUCCESS.value, Status.FAILED.value)
            if not self.manager.tasks or all(t.status in finished for t in self.manager.tasks):
                self.manager.tasks = self.manager.build(self.cfg)
            else:
                self.log.info("偵測到尚未完成的批次，沿用原任務進度，不重新上傳素材")
            self.manager.start(); self.running(True)
        except Exception as exc: messagebox.showerror("啟動前檢查失敗", str(exc))
    def test(self) -> None:
        try:
            self.cfg = self.validate()
            if not self.chrome.driver or "/tools/flow/project/" not in self.chrome.driver.current_url:
                raise RuntimeError("請先手動進入 FLOW 專案製作頁，再按「接管目前 FLOW 頁面」")
            self.manager.tasks = self.manager.build(self.cfg)[:1]; self.manager.start(test=True); self.running(True)
        except Exception as exc: messagebox.showerror("測試失敗", str(exc))
    def pause(self) -> None: self.manager.run_event.clear(); self.log.warning("已要求暫停，將在安全步驟暫停")
    def resume(self) -> None: self.manager.run_event.set(); self.log.info("已繼續執行")
    def stop(self) -> None: self.manager.stop_event.set(); self.manager.run_event.set(); self.log.warning("已要求安全停止")
    def choose_download(self) -> None:
        if self.manager.thread and self.manager.thread.is_alive():
            messagebox.showwarning("尚在製作", "請等待本次所有任務製作結束後再下載。")
            return
        candidates = [t for t in self.manager.tasks if t.generated_media and len(set(t.downloaded_indices)) < t.expected_count]
        if not candidates:
            recoverable = [t for t in self.manager.tasks if t.submitted and not t.generated_media]
            if recoverable and messagebox.askyesno(
                "恢復本次作品",
                "偵測到程式曾中斷，但有已送出的任務尚未建立下載清單。\n\n是否掃描目前 FLOW 頁面，恢復本次新生成作品？",
            ):
                cfg = self.collect_config()
                threading.Thread(target=self._recover_generated_worker, args=(recoverable, cfg), daemon=True).start()
                return
            messagebox.showinfo("沒有可下載作品", "本次任務尚無未下載的生成作品。\n上傳素材不會列入此清單。")
            return
        dialog = tk.Toplevel(self.root); dialog.title("挑選本次生成作品"); dialog.geometry("760x440"); dialog.transient(self.root); dialog.grab_set()
        ttk.Label(dialog, text="只列出本次任務新生成的作品；不包含上傳素材。可複選任務：").pack(anchor="w", padx=12, pady=(12, 5))
        box = tk.Listbox(dialog, selectmode="extended", activestyle="dotbox")
        box.pack(fill="both", expand=True, padx=12, pady=5)
        for task in candidates:
            remain = max(0, task.expected_count - len(set(task.downloaded_indices)))
            box.insert("end", f"任務 {task.number:03d}｜待下載 {remain} 張｜素材：{task.material_name or '無'}｜{task.prompt[:80]}")
        box.select_set(0, "end")
        buttons = ttk.Frame(dialog); buttons.pack(fill="x", padx=12, pady=10)
        def begin() -> None:
            chosen = [candidates[i] for i in box.curselection()]
            if not chosen:
                messagebox.showwarning("尚未選擇", "請至少選擇一個任務。", parent=dialog); return
            cfg = self.collect_config()
            dialog.destroy()
            threading.Thread(target=self._download_selected_worker, args=(chosen, cfg), daemon=True).start()
        ttk.Button(buttons, text="下載勾選作品", command=begin).pack(side="right", padx=4)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=4)

    def _recover_generated_worker(self, tasks: list[FlowTask], cfg: dict[str, Any]) -> None:
        try:
            driver = self.chrome.attach(cfg["chrome_path"], int(cfg["debug_port"]), cfg["download_folder"])
            pause = threading.Event(); pause.set()
            ctrl = FlowController(driver, self.log, threading.Event(), pause, cfg)
            material_names = [item.material_name for item in self.manager.tasks]
            current = ctrl.downloadable_output_media(material_names)
            current_by_key = {ctrl.media_key(item): item for item in current}
            claimed = {
                ctrl.normalize_media_key(key)
                for task in self.manager.tasks for key in task.generated_media
            }
            excluded_materials = {
                ctrl.normalize_media_key(key)
                for task in self.manager.tasks for key in task.material_media
            }
            recovered = 0
            for task in sorted(tasks, key=lambda item: item.number):
                baseline = {ctrl.normalize_media_key(key) for key in task.baseline_media}
                fresh = [
                    key for key in current_by_key
                    if key not in baseline and key not in claimed and key not in excluded_materials
                ]
                selected = fresh[:task.expected_count]
                if selected:
                    task.generated_media = selected
                    task.actual_count = len(selected)
                    task.generated = len(selected) >= task.expected_count
                    task.status = Status.COMPLETED.value if task.generated else Status.GENERATING.value
                    claimed.update(selected); recovered += len(selected)
                    self.manager.save()
                    self.log.info("[任務%03d] 中斷恢復掃描找到 %s 個本次作品", task.number, len(selected))
            self.update_progress(self.manager.tasks)
            if not recovered:
                self.alert("尚未找到作品", "目前頁面尚未找到相對於送出前基準的新作品；請確認仍在同一 FLOW 專案頁。")
                return
            self.log.info("中斷恢復完成，共找回 %s 個本次生成作品", recovered)
            self.root.after(0, self.choose_download)
        except Exception as exc:
            self.log.exception("恢復本次作品失敗：%s", exc)
            self.alert("恢復失敗", str(exc), error=True)

    def _download_selected_worker(self, tasks: list[FlowTask], cfg: dict[str, Any]) -> None:
        try:
            driver = self.chrome.attach(cfg["chrome_path"], int(cfg["debug_port"]), cfg["download_folder"])
            pause = threading.Event(); pause.set()
            ctrl = FlowController(driver, self.log, threading.Event(), pause, cfg)
            dm = DownloadManager(cfg["download_folder"], self.log)
            quality = cfg["image_quality"] if cfg["generation_type"] == "圖片模式" else cfg["video_quality"]
            material_names = [item.material_name for item in self.manager.tasks]
            # FLOW 專案網格最新卡片在前。有素材時每個任務取「素材 + 作品」，無素材只取作品。
            # 只有在任務本身還沒有已確認的 generated_media（例如中斷後重新載入進度、
            # 或該任務監控階段失敗導致沒記錄到）時，才需要用「最新卡片」去猜測，
            # 且必須排除所有任務已知的卡片鍵值，否則猜測結果可能會把別的任務（即使
            # 該任務被標記失敗、但 FLOW 端其實已生成）的卡片誤配給目前這個任務。
            known_keys: set[str] = set()
            for other in self.manager.tasks:
                for key in other.generated_media:
                    known_keys.add(ctrl.normalize_media_key(key))
                for key in other.material_media:
                    known_keys.add(ctrl.normalize_media_key(key))

            tasks_needing_guess = [task for task in tasks if not task.generated_media]
            if tasks_needing_guess:
                # 優先從任務自己的 tile 恢復 UUID；作品亂序完成也不會配錯。
                unresolved: list[FlowTask] = []
                for task in tasks_needing_guess:
                    owned = ctrl.media_in_tiles(task.submission_tile_ids) if task.submission_tile_ids else []
                    owned_keys = [ctrl.media_key(item) for item in owned]
                    if len(owned_keys) >= task.expected_count:
                        task.generated_media = owned_keys[:task.expected_count]
                        task.actual_count = len(task.generated_media)
                        task.generated = True
                        task.status = Status.COMPLETED.value
                        known_keys.update(task.generated_media)
                        self.log.info("[任務%03d] 已依送出 tile 恢復 %s 個作品", task.number, len(task.generated_media))
                        self.manager.save()
                    else:
                        unresolved.append(task)
                tasks_needing_guess = unresolved
            if tasks_needing_guess:
                quotas = {
                    task.number: task.expected_count + (1 if task.material_name else 0)
                    for task in tasks_needing_guess
                }
                latest_limit = sum(quotas.values())
                candidate_keys = [
                    ctrl.media_key(item) for item in ctrl.generated_media()
                    if ctrl.media_key(item) not in known_keys
                ]
                latest_keys = candidate_keys[:latest_limit]
                self.log.warning(
                    "仍有舊版任務缺少 tile 識別碼，啟用最新卡片人工救援規則：作品目標 %s、素材 %s、共取最新 %s 張；請核對順序",
                    sum(t.expected_count for t in tasks_needing_guess),
                    sum(1 for t in tasks_needing_guess if t.material_name), latest_limit,
                )
                offset = 0
                # 最新項目對應最後送出的任務，因此由任務編號大到小分配。
                for task in sorted(tasks_needing_guess, key=lambda item: item.number, reverse=True):
                    count = quotas[task.number]
                    assigned = latest_keys[offset:offset + count]
                    offset += count
                    task.generated_media = assigned
                    task.downloaded = []
                    task.downloaded_indices = []
                    task.inspected_indices = []
                    task.status = Status.COMPLETED.value
                    self.manager.save()
            elif all(task.generated_media for task in tasks):
                self.log.info("所有選取任務皆已有記錄的作品卡片，直接依原記錄下載，不重新猜測")
            for download_round in range(1, 6):
                attempted = 0
                if download_round > 1:
                    # 高畫質（2K/4K）下載前 FLOW 可能需要在後端做「高清重塑」，
                    # 這是非同步處理，太短的等待會讓還沒處理完的卡片被永久判定失敗。
                    self.log.info("開始第 %s 輪補抓，只重試尚未成功開啟或下載的候選卡片", download_round)
                    high_quality_wait = cfg["generation_type"] == "圖片模式" and str(quality).upper() in {"2K", "4K"}
                    retry_wait = 20 if high_quality_wait else (3 if cfg["generation_type"] == "影片模式" else 2)
                    if high_quality_wait:
                        self.log.info("等待 20 秒讓 FLOW 完成高畫質處理後再補抓")
                    else:
                        self.log.info("等待 %s 秒並重新捲動載入任務 tile", retry_wait)
                    time.sleep(retry_wait)
                    ctrl.dismiss_toasts()
                for task in tasks:
                    for index, key in enumerate(task.generated_media, 1):
                        if index in task.downloaded_indices or index in task.inspected_indices:
                            continue
                        attempted += 1
                        try:
                            media = ctrl.media_by_key_from_task_tiles(key, task.submission_tile_ids)
                            if media is None:
                                raise RuntimeError("找不到最新候選卡片，可能已離開原專案頁")
                            if media.tag_name.lower() == "video" and ctrl.is_named_material(media, material_names):
                                task.inspected_indices.append(index)
                                self.manager.save()
                                self.log.info("[任務%03d] 依卡片檔名排除上傳影片素材", task.number)
                                continue
                            task.status = Status.DOWNLOADING.value; self.manager.save()
                            result, kind = ctrl.download_card(
                                media, quality, dm, task, index, material_names, [task.prompt]
                            )
                            if kind == "work" and result:
                                sequence = sum(
                                    previous.expected_count for previous in tasks
                                    if previous.number < task.number
                                ) + len(task.downloaded_indices) + 1
                                renamed = dm.rename_sequence(result, sequence, cfg["duplicate_policy"])
                                if renamed is None:
                                    raise RuntimeError(f"作品 {sequence} 因重名規則被跳過")
                                self.log.info(
                                    "[任務%03d] 已依任務順序命名作品：%s",
                                    task.number, renamed.name,
                                )
                                task.downloaded.append(str(renamed))
                                task.downloaded_indices.append(index)
                            else:
                                task.inspected_indices.append(index)
                            self.manager.save()
                        except Exception as item_exc:
                            log_method = self.log.error if download_round >= 5 else self.log.warning
                            log_method(
                                "[任務%03d] 第 %d 張第 %d 輪下載失敗，稍後補抓：%s",
                                task.number, index, download_round, item_exc,
                            )
                    if len(set(task.downloaded_indices)) >= task.expected_count:
                        task.status = Status.SUCCESS.value; self.manager.save()
                if not attempted or all(len(set(t.downloaded_indices)) >= t.expected_count for t in tasks):
                    break
            self.update_progress(self.manager.tasks)
            remaining = sum(max(0, t.expected_count - len(set(t.downloaded_indices))) for t in tasks)
            if remaining:
                self.alert("部分下載完成", f"仍有 {remaining} 張下載失敗或尚未下載；可再次按「挑選並下載本次作品」重試。")
            else:
                self.alert("下載完成", "已完成所選本次生成作品的下載；上傳素材未包含在內。")
        except Exception as exc:
            self.log.exception("手動下載失敗：%s", exc)
            self.alert("下載失敗", str(exc), error=True)

    def open_download(self) -> None:
        p = Path(self.collect_config()["download_folder"]); p.mkdir(parents=True, exist_ok=True)
        if os.name == "nt": os.startfile(p)  # type: ignore[attr-defined]
        else: self.log.info("下載資料夾：%s", p)
    def _flush_log(self) -> None:
        try:
            while True: self.log_box.insert("end", self.log_queue.get_nowait() + "\n"); self.log_box.see("end")
        except queue.Empty: pass
        self.root.after(100, self._flush_log)
    def set_totals(self, total: int) -> None: self.root.after(0, lambda: self.progress.configure(maximum=max(total, 1)))
    def current(self, t: FlowTask) -> None: self.root.after(0, lambda: self.current_var.set(f"目前任務：{t.number:03d}｜素材：{t.material_name or '無'}｜重試：{t.retry_count}｜提示詞：{t.prompt[:120]}"))
    def update_progress(self, tasks: list[FlowTask]) -> None:
        done = sum(t.status in (Status.COMPLETED.value, Status.SUCCESS.value, Status.FAILED.value) for t in tasks); success = sum(t.status in (Status.COMPLETED.value, Status.SUCCESS.value) for t in tasks); failed = sum(t.status == Status.FAILED.value for t in tasks)
        self.root.after(0, lambda: (self.progress.configure(value=done), self.status_var.set(f"總任務數：{len(tasks)}｜已完成：{done}｜成功：{success}｜失敗：{failed}｜剩餘：{len(tasks)-done}")))
    def running(self, value: bool) -> None: pass
    def alert(self, title: str, text: str, error: bool = False) -> None: self.root.after(0, lambda: (messagebox.showerror if error else messagebox.showinfo)(title, text))
    def capture_error(self, task: FlowTask, exc: Exception) -> None:
        folder = ERROR_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_任務{task.number:03d}"; folder.mkdir(parents=True, exist_ok=True)
        data = {"url": "", "title": "", "error": str(exc), "traceback": traceback.format_exc(), "task": asdict(task)}
        try:
            if self.chrome.driver:
                data["url"] = self.chrome.driver.current_url; data["title"] = self.chrome.driver.title
                self.chrome.driver.save_screenshot(str(folder / "screenshot.png"))
        except Exception: pass
        atomic_json(folder / "diagnostic.json", data)

    def collect_dom_diagnostic(self) -> None:
        """收集可見互動元素，不保存 Cookie、LocalStorage、密碼或完整頁面原始碼。"""
        if self.manager.thread and self.manager.thread.is_alive():
            messagebox.showwarning("目前無法收集", "請先停止自動任務，再收集 FLOW DOM。")
            return
        if not self.chrome.driver:
            messagebox.showwarning("尚未接管", "請先進入 FLOW 專案頁並按「接管目前 FLOW 頁面」。")
            return

        def worker() -> None:
            try:
                driver = self.chrome.driver
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                folder = DOM_DIAG_DIR / stamp
                folder.mkdir(parents=True, exist_ok=True)
                script = r"""
                    const visible = el => {
                      const r = el.getBoundingClientRect();
                      const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.display !== 'none' &&
                             s.visibility !== 'hidden' && r.bottom >= 0 && r.top <= innerHeight;
                    };
                    const attrs = el => {
                      const names = ['id','class','type','role','aria-label','aria-disabled',
                        'aria-expanded','aria-haspopup','aria-pressed','data-state','data-testid',
                        'placeholder','contenteditable','title','disabled','tabindex'];
                      const out = {};
                      for (const n of names) if (el.hasAttribute(n)) out[n] = el.getAttribute(n);
                      return out;
                    };
                    const cleanHTML = el => {
                      if (!el) return '';
                      const clone = el.cloneNode(true);
                      for (const x of clone.querySelectorAll('input,textarea')) {
                        x.removeAttribute('value');
                        if (x.type === 'password') x.remove();
                      }
                      let html = clone.outerHTML || '';
                      return html.slice(0, 30000);
                    };
                    const selector = 'button,[role="button"],[role="option"],[role="menuitem"],'+
                      '[role="radio"],[role="menuitemradio"],textarea,input,[contenteditable="true"]';
                    const elements = [...document.querySelectorAll(selector)].filter(visible).map((el,i) => {
                      const r = el.getBoundingClientRect();
                      return {index:i, tag:el.tagName.toLowerCase(), attrs:attrs(el),
                        text:(el.innerText || el.textContent || '').trim().slice(0,500),
                        rect:{x:r.x,y:r.y,width:r.width,height:r.height},
                        html:cleanHTML(el).slice(0,5000)};
                    });
                    const prompts = [...document.querySelectorAll('textarea,[contenteditable="true"],input[placeholder]')]
                      .filter(visible).map(el => {
                        let root=el;
                        for(let i=0;i<5 && root.parentElement;i++) root=root.parentElement;
                        return {tag:el.tagName.toLowerCase(),attrs:attrs(el),
                          text:(el.innerText || el.textContent || '').trim().slice(0,1000),
                          element_html:cleanHTML(el), ancestor_html:cleanHTML(root)};
                      });
                    return {url:location.href,title:document.title,viewport:{width:innerWidth,height:innerHeight},
                      collected_at:new Date().toISOString(),elements,prompts};
                """
                data = driver.execute_script(script)
                atomic_json(folder / "flow_dom_diagnostic.json", data)
                driver.save_screenshot(str(folder / "flow_dom_screenshot.png"))
                self.log.info("FLOW DOM 診斷已保存：%s", folder)
                self.alert("收集完成", f"已產生：\n{folder}\n\n請把 flow_dom_diagnostic.json 傳給我。")
            except Exception as exc:
                self.log.exception("收集 FLOW DOM 失敗：%s", exc)
                self.alert("收集失敗", str(exc), error=True)

        threading.Thread(target=worker, daemon=True).start()
    def _resume_offer(self) -> None:
        if self.manager.load() and any(t.status not in (Status.SUCCESS.value, Status.FAILED.value) for t in self.manager.tasks):
            if messagebox.askyesno("偵測到未完成進度", "是否保留未完成任務，稍後按「開始執行」繼續？"):
                self.log.info("已載入未完成進度")
            else: self.manager.tasks = []
    def close(self) -> None:
        try:
            self.save_config(); self._save_schedules(); self.manager.stop_event.set(); self.manager.run_event.set(); self.manager.save()
        except Exception: pass
        if self.collect_config().get("close_chrome"): self.chrome.close()
        self.root.destroy()


def main() -> None:
    if sys.version_info < (3, 12):
        messagebox.showwarning("Python 版本", "建議使用 Python 3.12 或更新版本。")
    root = tk.Tk(); FlowAutomationApp(root); root.mainloop()


if __name__ == "__main__":
    main()
