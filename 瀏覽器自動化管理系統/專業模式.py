from __future__ import annotations

import json
import logging
import queue
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests
import tkinter as tk
from tkinter import messagebox, ttk
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    JavascriptException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait


APP_NAME = "Facebook 專業模式 V1.3.0 Fast Stable"
APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
SCREENSHOT_DIR = APP_DIR / "screenshots"
LOG_DIR.mkdir(exist_ok=True)
SCREENSHOT_DIR.mkdir(exist_ok=True)


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("professional_mode")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    file_name = datetime.now().strftime("%Y%m%d_%H%M%S") + ".log"
    handler = logging.FileHandler(LOG_DIR / file_name, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


LOGGER = setup_logger()


@dataclass
class Profile:
    user_id: str
    name: str
    group_name: str = ""


@dataclass
class AdsPowerGroup:
    group_id: str
    group_name: str


class AdsPowerAPI:
    _rate_lock = threading.Lock()
    _last_request_at = 0.0

    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        min_request_interval: float = 1.2,
        max_retries: int = 8,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_request_interval = max(1.0, min_request_interval)
        self.max_retries = max(1, max_retries)
        self.session = requests.Session()

    @classmethod
    def _wait_for_request_slot(cls, interval: float) -> None:
        with cls._rate_lock:
            elapsed = time.monotonic() - cls._last_request_at
            if elapsed < interval:
                time.sleep(interval - elapsed)
            cls._last_request_at = time.monotonic()

    @staticmethod
    def _is_rate_limited(message: str) -> bool:
        text = message.lower()
        return (
            "too many request" in text
            or "too many requests" in text
            or "request per second" in text
            or "rate limit" in text
            or "頻率" in text
            or "限流" in text
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            self._wait_for_request_slot(self.min_request_interval)
            try:
                response = self.session.get(
                    f"{self.base_url}{path}",
                    params=params or {},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("code") == 0:
                    return data

                message = str(data.get("msg") or f"AdsPower API 錯誤：{data}")
                if not self._is_rate_limited(message):
                    raise RuntimeError(message)
                last_error = RuntimeError(message)
            except requests.RequestException as exc:
                last_error = exc
                status = getattr(exc.response, "status_code", None)
                if status != 429 and not self._is_rate_limited(str(exc)):
                    raise

            if attempt + 1 >= self.max_retries:
                break

            # AdsPower 被其他程式同時呼叫時，逐次延長等待時間並加入抖動，
            # 避免多個程式在同一秒一起重試。
            wait_seconds = min(30.0, 3.0 * (2 ** attempt))
            wait_seconds += random.uniform(0.3, 1.2)
            LOGGER.warning(
                "AdsPower API 觸發限流，%.1f 秒後進行第 %d/%d 次重試：%s",
                wait_seconds,
                attempt + 2,
                self.max_retries,
                path,
            )
            time.sleep(wait_seconds)

        raise RuntimeError(
            "AdsPower API 持續限流，自動重試仍未成功。"
            "請確認沒有其他程式正在密集呼叫 AdsPower API，稍後再試。"
            f" 最後錯誤：{last_error}"
        )

    def list_profiles(self, group_id: str = "") -> list[Profile]:
        profiles: list[Profile] = []
        page = 1
        while True:
            params = {"page": page, "page_size": 100}
            if group_id.strip():
                params["group_id"] = group_id.strip()
            data = self._get("/api/v1/user/list", params).get("data") or {}
            rows = data.get("list") or []
            for row in rows:
                profiles.append(
                    Profile(
                        user_id=str(row.get("user_id", "")),
                        name=str(row.get("name") or row.get("user_name") or ""),
                        group_name=str(row.get("group_name") or ""),
                    )
                )
            if len(rows) < 100:
                break
            page += 1
        return profiles

    def list_groups(self) -> list[AdsPowerGroup]:
        data = self._get(
            "/api/v1/group/list", {"page": 1, "page_size": 100}
        ).get("data") or {}
        rows = data.get("list") or []
        groups = [
            AdsPowerGroup(
                group_id=str(row.get("group_id") or ""),
                group_name=str(row.get("group_name") or row.get("name") or ""),
            )
            for row in rows
            if row.get("group_id") is not None
        ]
        return sorted(groups, key=lambda item: item.group_name.lower())

    def start_browser(self, user_id: str) -> dict:
        return self._get(
            "/api/v1/browser/start",
            {"user_id": user_id, "open_tabs": 1, "ip_tab": 0},
        ).get("data") or {}

    def stop_browser(self, user_id: str) -> None:
        self._get("/api/v1/browser/stop", {"user_id": user_id})


class ProfessionalModeWorker:
    def __init__(
        self,
        api: AdsPowerAPI,
        min_delay: float,
        max_delay: float,
        close_after: bool,
        emit: Callable[[str], None],
        stop_event: threading.Event,
    ):
        self.api = api
        self.min_delay = min_delay
        self.max_delay = max(max_delay, min_delay)
        self.close_after = close_after
        self.emit = emit
        self.stop_event = stop_event

    def log(self, profile: Profile, message: str, level: int = logging.INFO) -> None:
        text = f"[{profile.name}] {message}"
        LOGGER.log(level, text)
        self.emit(text)

    def pause(self, short: bool = False) -> None:
        if short:
            seconds = random.uniform(0.5, min(1.5, self.max_delay))
        else:
            seconds = random.uniform(self.min_delay, self.max_delay)
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")
            time.sleep(min(0.2, end - time.time()))

    @staticmethod
    def connect_driver(start_data: dict) -> webdriver.Chrome:
        ws = start_data.get("ws") or {}
        debugger = (
            ws.get("selenium")
            or start_data.get("debugger_address")
            or start_data.get("debug_port")
        )
        webdriver_path = (
            (start_data.get("webdriver") or "")
            or (start_data.get("webdriver_path") or "")
        )
        if not debugger:
            raise RuntimeError(f"AdsPower 未回傳瀏覽器連線位址：{start_data}")
        debugger = str(debugger).replace("http://", "").replace("https://", "")
        options = Options()
        options.add_experimental_option("debuggerAddress", debugger)
        if webdriver_path:
            service = Service(executable_path=webdriver_path)
            return webdriver.Chrome(service=service, options=options)
        return webdriver.Chrome(options=options)

    @staticmethod
    def switch_to_facebook(driver: webdriver.Chrome) -> None:
        for handle in reversed(driver.window_handles):
            driver.switch_to.window(handle)
            if "facebook.com" in (driver.current_url or "").lower():
                return
        driver.get("https://www.facebook.com/")

    @staticmethod
    def visible(element: WebElement) -> bool:
        try:
            return element.is_displayed() and element.is_enabled()
        except (StaleElementReferenceException, WebDriverException):
            return False

    def find_visible(self, driver: webdriver.Chrome, xpaths: list[str]) -> WebElement | None:
        for xpath in xpaths:
            try:
                candidates = driver.find_elements(By.XPATH, xpath)
                for element in reversed(candidates):
                    if self.visible(element):
                        return element
            except (WebDriverException, StaleElementReferenceException):
                continue
        return None

    def wait_visible(
        self, driver: webdriver.Chrome, xpaths: list[str], timeout: float = 25
    ) -> WebElement:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")
            element = self.find_visible(driver, xpaths)
            if element:
                return element
            self.stop_event.wait(min(0.35, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(f"等待元素逾時：{xpaths[0]}")

    @staticmethod
    def click(driver: webdriver.Chrome, element: WebElement) -> None:
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center',inline:'center'});", element
            )
            time.sleep(0.35)
            element.click()
        except (
            ElementClickInterceptedException,
            StaleElementReferenceException,
            WebDriverException,
            JavascriptException,
        ):
            driver.execute_script("arguments[0].click();", element)

    def click_named(
        self,
        driver: webdriver.Chrome,
        labels: list[str],
        timeout: float = 25,
        exact: bool = True,
        role: str = "button",
    ) -> None:
        deadline = time.monotonic() + timeout
        wanted = [str(label).strip().lower() for label in labels if str(label).strip()]
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")
            element = driver.execute_script(
                """
                const wanted=arguments[0], exact=arguments[1], role=arguments[2];
                const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                const visible=el=>{
                    if(!el)return false;
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>1&&r.height>1&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden'&&
                           parseFloat(s.opacity||'1')>0&&!el.disabled;
                };
                const selector=role==='menuitem'
                    ? '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"]'
                    : 'button,[role="button"],input[type="button"],input[type="submit"],a[role="button"]';
                return [...document.querySelectorAll(selector)].find(el=>{
                    if(!visible(el))return false;
                    const values=[
                        norm(el.getAttribute('aria-label')),norm(el.innerText),
                        norm(el.textContent),norm(el.value)
                    ].filter(Boolean);
                    return wanted.some(w=>values.some(v=>exact ? v===w : v.includes(w)));
                })||null;
                """,
                wanted,
                exact,
                role,
            )
            if element:
                self.click(driver, element)
                self.pause(short=True)
                return
            self.stop_event.wait(0.25)
        raise TimeoutError(f"等待可點擊項目逾時：{' / '.join(labels)}")

    def click_named_xpath_legacy(
        self,
        driver: webdriver.Chrome,
        labels: list[str],
        timeout: float = 5,
        exact: bool = True,
        role: str = "button",
    ) -> None:
        """舊版 XPath 備援；正常流程改用瀏覽器內一次掃描。"""
        conditions: list[str] = []
        for label in labels:
            safe = label.replace('"', '\\"')
            if exact:
                conditions.extend(
                    [
                        f'//*[@role="{role}" and normalize-space(@aria-label)="{safe}"]',
                        f'//*[@role="{role}" and normalize-space(.)="{safe}"]',
                        f'//button[normalize-space(.)="{safe}"]',
                    ]
                )
            else:
                conditions.extend(
                    [
                        f'//*[@role="{role}" and contains(translate(@aria-label,'
                        f'"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),'
                        f'"{safe.lower()}")]',
                        f'//*[@role="{role}" and contains(translate(normalize-space(.),'
                        f'"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),'
                        f'"{safe.lower()}")]',
                    ]
                )
        element = self.wait_visible(driver, conditions, timeout)
        self.click(driver, element)
        self.pause()

    def wait_page_marker(
        self, driver: webdriver.Chrome, markers: list[str], timeout: float = 30
    ) -> None:
        wanted = [str(marker).strip().lower() for marker in markers if str(marker).strip()]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")
            found = driver.execute_script(
                """
                const wanted=arguments[0];
                const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                const text=norm(document.body ? document.body.innerText : '');
                if(wanted.some(x=>text.includes(x)))return true;
                return [...document.querySelectorAll('[aria-label]')]
                    .some(el=>wanted.some(x=>norm(el.getAttribute('aria-label')).includes(x)));
                """,
                wanted,
            )
            if found:
                return
            self.stop_event.wait(0.25)
        raise TimeoutError(f"等待頁面標記逾時：{' / '.join(markers)}")

    def go_to_own_profile(self, driver: webdriver.Chrome) -> None:
        # 不可逐條執行大型 XPath：Facebook DOM 很大時，單一 XPath
        # 就可能阻塞 60～120 秒。改成瀏覽器內一次掃描所有語言標記。
        profile_words = [
            "edit profile",
            "編輯個人檔案",
            "แก้ไขโปรไฟล์",
            "i-edit ang profile",
            "تعديل الملف الشخصي",
        ]
        profile_aria_words = [
            "profile settings",
            "個人檔案設定",
            "การตั้งค่าโปรไฟล์",
            "mga setting ng profile",
            "إعدادات الملف الشخصي",
        ]

        def profile_ready() -> bool:
            try:
                return bool(driver.execute_script(
                    """
                    const textWords = arguments[0];
                    const ariaWords = arguments[1];
                    const norm = v => (v || '').replace(/\\s+/g, ' ')
                        .trim().toLowerCase();
                    const bodyText = norm(document.body
                        ? document.body.innerText : '');
                    if (textWords.some(word => bodyText.includes(word))) {
                        return true;
                    }
                    return [...document.querySelectorAll('[aria-label]')]
                        .some(el => {
                            const value = norm(el.getAttribute('aria-label'));
                            return ariaWords.some(word => value.includes(word));
                        });
                    """,
                    profile_words,
                    profile_aria_words,
                ))
            except WebDriverException:
                return False

        # 任務通常已在個人主頁完成。先做快速確認，避免重複載入。
        current_url = (driver.current_url or "").lower()
        is_dashboard = (
            "/professional_dashboard" in current_url
            or "/professional-dashboard" in current_url
        )
        if "facebook.com" in current_url and not is_dashboard:
            if profile_ready():
                return

        profile_url = getattr(driver, "_facebook_personal_profile_url", "")
        if not profile_url:
            driver.get("https://www.facebook.com/")
            discover_deadline = time.monotonic() + 20
            while time.monotonic() < discover_deadline:
                if self.stop_event.is_set():
                    raise InterruptedError("使用者停止")
                profile_url = driver.execute_script(
                    """
                    const links = [...document.querySelectorAll('a[href*="profile.php?id="]')];
                    const found = links.map(a => ({
                        href: (a.href || '').split('&__cft__')[0].split('&__tn__')[0],
                        label: ((a.getAttribute('aria-label') || a.innerText || '') + '')
                            .trim().toLowerCase()
                    })).filter(x =>
                        ['timeline', 'journal', 'ไทม์ไลน์', 'يوميات']
                            .some(word => x.label.includes(word))
                    );
                    return [...new Set(found.map(x => x.href))].length === 1
                        ? [...new Set(found.map(x => x.href))][0] : '';
                    """
                )
                if profile_url:
                    driver._facebook_personal_profile_url = profile_url
                    break
                self.stop_event.wait(0.4)
        if not profile_url:
            raise RuntimeError("Facebook 首頁找不到唯一的本人 Timeline 連結")

        # 直接使用首頁讀到的固定 Profile URL，不使用 facebook.com/me。
        try:
            driver.execute_cdp_cmd(
                "Page.navigate", {"url": profile_url}
            )
        except WebDriverException:
            driver.execute_script(
                "window.location.replace(arguments[0])", profile_url
            )
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")
            url = (driver.current_url or "").lower()
            if (
                "facebook.com" in url
                and "/professional_dashboard" not in url
                and "/professional-dashboard" not in url
            ):
                if url.startswith(profile_url.lower()) or profile_ready():
                    return
            self.stop_event.wait(0.2)
        raise TimeoutError(
            f"已開啟 {driver.current_url}，但 12 秒內尚未確認進入個人主頁"
        )

    def already_enabled(self, driver: webdriver.Chrome) -> bool:
        page_text = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
        return any(
            phrase in page_text
            for phrase in (
                "professional dashboard",
                "turn off professional mode",
                "關閉專業模式",
                "專業主控板",
                "professional dashboard",
                "i-off ang professional mode",
                "dashboard ng propesyonal",
                "لوحة المعلومات الاحترافية",
                "إيقاف تشغيل الوضع الاحترافي",
            )
        )

    def open_professional_mode_dialog(self, driver: webdriver.Chrome) -> bool:
        """打開三點選單；若出現「關閉專業模式」則回傳 True。"""
        more_xpaths = [
            '//*[@role="button" and @aria-label="Profile settings see more options"]',
            '//*[@role="button" and contains(translate(@aria-label,'
            '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"profile") '
            'and contains(translate(@aria-label,'
            '"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz"),"option")]',
            '//*[@role="button" and (@aria-label="個人檔案設定查看更多選項" '
            'or @aria-label="顯示更多選項" or @aria-label="更多")]',
            '//*[@role="button" and (contains(@aria-label,"ดูตัวเลือกเพิ่มเติม") '
            'or contains(@aria-label,"ตัวเลือกเพิ่มเติม") '
            'or contains(@aria-label,"การตั้งค่าโปรไฟล์"))]',
            '//*[@role="button" and (contains(@aria-label,"Tumingin ng higit pang opsyon") '
            'or contains(@aria-label,"Higit pang opsyon") '
            'or contains(@aria-label,"mga setting ng profile"))]',
            '//*[@role="button" and (contains(@aria-label,"عرض المزيد من الخيارات") '
            'or contains(@aria-label,"المزيد من الخيارات") '
            'or contains(@aria-label,"إعدادات الملف الشخصي"))]',
            '//*[@role="button" and @aria-haspopup="menu" '
            'and ancestor::*[.//*[contains(normalize-space(.),"แก้ไขโปรไฟล์") '
            'or contains(normalize-space(.),"Edit profile") '
            'or contains(normalize-space(.),"編輯個人檔案")]][1]]',
        ]
        # 優先以一次 JavaScript 掃描定位，避免多語 XPath 逐條遍歷大型 DOM。
        more = driver.execute_script(
            """
            const visible=el=>{
                if(!el)return false;
                const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                return r.width>1&&r.height>1&&r.bottom>0&&r.top<innerHeight&&
                       s.display!=='none'&&s.visibility!=='hidden';
            };
            const keys=[
                'profile settings see more options','顯示更多選項','查看更多選項',
                'ดูตัวเลือกเพิ่มเติม','ตัวเลือกเพิ่มเติม',
                'tumingin ng higit pang opsyon','higit pang opsyon',
                'عرض المزيد من الخيارات','المزيد من الخيارات'
            ];
            const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
            const named=[...document.querySelectorAll('[role="button"],button')]
                .filter(visible)
                .find(el=>keys.some(k=>norm(el.getAttribute('aria-label')).includes(k)));
            if(named)return named;
            const menus=[...document.querySelectorAll(
                '[role="button"][aria-haspopup="menu"],button[aria-haspopup="menu"]'
            )].filter(el=>{
                if(!visible(el))return false;
                const r=el.getBoundingClientRect();
                return r.width>20&&r.width<=100&&r.height>20&&r.top>=180&&r.top<=900;
            });
            return menus.length ? menus[menus.length-1] : null;
            """
        )
        # 找不到時不再進入 7 條大型 XPath 迴圈；下方只保留單次 CSS/JS 備援。
        if more is None:
            more = driver.execute_script(
                """
                const candidates = [...document.querySelectorAll(
                    '[role="button"][aria-haspopup="menu"], button[aria-haspopup="menu"]'
                )].filter(el => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 20 && r.width <= 90 && r.height > 20 &&
                           r.top >= 300 && r.top <= 850 &&
                           s.visibility !== 'hidden' && s.display !== 'none';
                });
                return candidates.length ? candidates[candidates.length - 1] : null;
                """
            )
        if more is None:
            raise TimeoutError("找不到個人頁面的三點／更多選項按鈕（已嘗試泰文與向下捲動）")
        self.click(driver, more)
        self.pause(short=True)

        turn_off_labels = [
            "Turn off professional mode", "關閉專業模式", "停用專業模式",
            "ปิดโหมดมืออาชีพ", "I-off ang professional mode",
            "I-off ang propesyonal na mode", "إيقاف تشغيل الوضع الاحترافي",
            "إيقاف الوضع الاحترافي",
        ]
        off_deadline = time.monotonic() + 3.0
        while time.monotonic() < off_deadline:
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")
            found_off = driver.execute_script(
                """
                const wanted=arguments[0];
                const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                const nodes=[...document.querySelectorAll(
                    '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"]'
                )];
                return nodes.some(el=>{
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    if(r.width<1||r.height<1||s.display==='none'||s.visibility==='hidden')return false;
                    const t=norm(el.innerText||el.textContent||el.getAttribute('aria-label'));
                    return wanted.some(x=>t===x||t.includes(x));
                });
                """,
                [x.lower() for x in turn_off_labels],
            )
            if found_off:
                return True
            self.stop_event.wait(0.2)

        self.click_named(
            driver,
            [
                "Turn on pro mode",
                "Turn on professional mode",
                "開啟專業模式",
                "啟用專業模式",
                "เปิดโหมดมืออาชีพ",
                "I-on ang professional mode",
                "I-on ang propesyonal na mode",
                "تشغيل الوضع الاحترافي",
            ],
            timeout=8,
            role="menuitem",
            exact=True,
        )
        self.wait_page_marker(
            driver,
            [
                "Turn on pro mode", "Turn on professional mode",
                "開啟專業模式", "professional mode",
                "เปิดโหมดมืออาชีพ", "โหมดมืออาชีพ",
                "I-on ang professional mode", "propesyonal na mode",
                "تشغيل الوضع الاحترافي", "الوضع الاحترافي",
                "Turn on",
            ],
            8,
        )
        return False

    def complete_wizard(self, driver: webdriver.Chrome, profile: Profile) -> None:
        self.log(profile, "步驟 1/10：確認開啟專業模式")
        self.click_named(
            driver,
            ["Turn on", "開啟", "啟用", "เปิด", "I-on", "تشغيل"],
        )

        self.log(profile, "步驟 2/10：Welcome to professional mode")
        self.wait_page_marker(
            driver,
            [
                "step 1 of 6", "Welcome", "歡迎", "ยินดีต้อนรับ", "ขั้นตอนที่ 1",
                "Hakbang 1", "Maligayang pagdating", "الخطوة 1", "مرحبًا",
            ],
            35,
        )
        self.click_named(driver, ["Next", "下一步", "ถัดไป", "Susunod", "التالي"])

        self.log(profile, "步驟 3/10：Define your audience")
        self.wait_page_marker(
            driver,
            [
                "step 2 of 6", "Define your audience", "定義你的受眾",
                "กำหนดกลุ่มเป้าหมาย", "ขั้นตอนที่ 2",
                "Hakbang 2", "Tukuyin ang iyong audience",
                "الخطوة 2", "تحديد جمهورك",
            ],
            30,
        )
        self.click_named(driver, ["Next", "下一步", "ถัดไป", "Susunod", "التالي"])

        self.log(profile, "步驟 4/10：預設受眾選擇 Public")
        self.wait_page_marker(
            driver,
            [
                "step 3 of 6", "Choose your default audience", "選擇預設受眾",
                "เลือกกลุ่มเป้าหมายเริ่มต้น", "ขั้นตอนที่ 3",
                "Hakbang 3", "Piliin ang iyong default na audience",
                "الخطوة 3", "اختيار جمهورك الافتراضي",
            ],
            30,
        )
        public_words = [
            "public", "公開", "สาธารณะ", "pampubliko", "publiko", "العامة",
        ]
        deadline = time.monotonic() + 8.0
        public = None
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                raise InterruptedError("使用者停止")
            public = driver.execute_script(
                """
                const wanted=arguments[0];
                const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                const visible=el=>{
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>1&&r.height>1&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden';
                };
                const nodes=[...document.querySelectorAll(
                    '[role="radio"],label,input[type="radio"],button,[role="button"]'
                )];
                for(const el of nodes){
                    if(!visible(el))continue;
                    const values=[
                        norm(el.getAttribute('aria-label')),
                        norm(el.innerText),norm(el.textContent),
                        norm(el.closest('label')?.innerText)
                    ].filter(Boolean);
                    if(wanted.some(w=>values.some(v=>v===w||v.includes(w)))){
                        return el.closest('[role="radio"],label')||el;
                    }
                }
                return null;
                """,
                public_words,
            )
            if public:
                break
            self.stop_event.wait(0.25)
        if public is None:
            raise TimeoutError("8 秒內找不到 Public／公開受眾選項")
        if public.get_attribute("aria-checked") != "true":
            self.click(driver, public)
            self.stop_event.wait(0.3)
        self.click_named(
            driver, ["Next", "下一步", "ถัดไป", "Susunod", "التالي"],
            timeout=6,
        )

        self.log(profile, "步驟 5/10：確認受眾選擇")
        self.wait_page_marker(
            driver,
            [
                "Review selection",
                "確認選擇",
                "檢查選擇",
                "ตรวจสอบการเลือก",
                "ตรวจดูตัวเลือก",
                "Suriin ang pinili",
                "Suriin ang pagpili",
                "مراجعة الاختيار",
            ],
            30,
        )
        self.click_named(
            driver,
            ["Confirm", "確認", "ยืนยัน", "Kumpirmahin", "تأكيد"],
            exact=True,
        )

        self.log(profile, "步驟 6/10：預設受眾更新完成")
        self.wait_page_marker(
            driver,
            [
                "Default audience updated", "預設受眾已更新", "audience updated",
                "อัพเดตกลุ่มเป้าหมายเริ่มต้นแล้ว",
                "Na-update ang default na audience",
                "تم تحديث الجمهور الافتراضي",
            ],
            30,
        )
        self.click_named(
            driver,
            [
                "Done, close dialog and return to settings", "Done", "完成", "เรียบร้อย",
                "Tapos na", "Tapos", "تم", "إغلاق",
            ],
            exact=True,
        )

        self.log(profile, "步驟 7/10：Monetization tools")
        self.wait_page_marker(
            driver,
            [
                "step 4 of 6", "Monetization tools", "營利工具",
                "เครื่องมือสร้างรายได้", "ขั้นตอนที่ 4",
                "Hakbang 4", "Mga tool sa monetization",
                "الخطوة 4", "أدوات تحقيق الأرباح",
            ],
            35,
        )
        self.click_named(driver, ["Next", "下一步", "ถัดไป", "Susunod", "التالي"])

        self.log(profile, "步驟 8/10：Professional tools")
        self.wait_page_marker(
            driver,
            [
                "step 5 of 6", "Professional tools", "專業工具",
                "เครื่องมือระดับมืออาชีพ", "ขั้นตอนที่ 5",
                "Hakbang 5", "Mga propesyonal na tool",
                "الخطوة 5", "الأدوات الاحترافية",
            ],
            30,
        )
        self.click_named(driver, ["Next", "下一步", "ถัดไป", "Susunod", "التالي"])

        self.log(profile, "步驟 9/10：略過 Profile frame")
        self.wait_page_marker(
            driver,
            [
                "step 6 of 6", "Profile frame", "個人檔案相框",
                "กรอบรูปโปรไฟล์", "ขั้นตอนที่ 6",
                "Hakbang 6", "Frame ng profile",
                "الخطوة 6", "إطار الملف الشخصي",
            ],
            30,
        )
        self.click_named(driver, ["Skip", "略過", "跳過", "ข้าม", "Laktawan", "تخطي"])

        self.log(profile, "步驟 10/10：前往專業主控板")
        self.wait_page_marker(
            driver,
            ["Finish", "完成", "เสร็จสิ้น", "Tapusin", "إنهاء"],
            30,
        )
        self.click_named(
            driver,
            [
                "Go to professional dashboard", "前往專業主控板",
                "前往專業模式主控板", "ไปที่แดชบอร์ดมืออาชีพ",
                "Pumunta sa professional dashboard",
                "Pumunta sa dashboard ng propesyonal",
                "الانتقال إلى لوحة المعلومات الاحترافية",
            ],
        )

    def save_failure(self, driver: webdriver.Chrome, profile: Profile) -> Path:
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", profile.name) or profile.user_id
        path = SCREENSHOT_DIR / f"{safe_name}_{datetime.now():%Y%m%d_%H%M%S}.png"
        try:
            driver.save_screenshot(str(path))
        except WebDriverException:
            pass
        return path

    def run_profile(self, profile: Profile) -> tuple[str, str]:
        driver: webdriver.Chrome | None = None
        browser_started = False
        try:
            if self.stop_event.is_set():
                return "stopped", "已停止"
            self.log(profile, "啟動 AdsPower 環境")
            start_data = self.api.start_browser(profile.user_id)
            browser_started = True
            driver = self.connect_driver(start_data)
            driver.set_page_load_timeout(60)
            self.switch_to_facebook(driver)
            self.log(profile, "正在返回 Facebook 個人主頁")
            self.go_to_own_profile(driver)
            self.log(profile, "已到達 Facebook 個人主頁")

            if self.already_enabled(driver):
                self.log(profile, "已經是專業模式，跳過")
                return "skipped", "已是專業模式"

            self.log(profile, "開啟個人頁面三點選單")
            self.open_professional_mode_dialog(driver)
            self.complete_wizard(driver, profile)
            self.log(profile, "最後步驟：正在回到 Facebook 個人主頁")
            self.go_to_own_profile(driver)
            self.log(profile, "最後步驟：已回到 Facebook 個人主頁")
            self.log(profile, "專業模式設定完成")
            return "success", "完成"
        except InterruptedError:
            self.log(profile, "已停止", logging.WARNING)
            return "stopped", "已停止"
        except Exception as exc:
            path = self.save_failure(driver, profile) if driver else None
            suffix = f"；截圖：{path.name}" if path else ""
            self.log(profile, f"失敗：{exc}{suffix}", logging.ERROR)
            return "failed", f"{exc}"
        finally:
            if browser_started and self.close_after:
                try:
                    self.api.stop_browser(profile.user_id)
                    self.log(profile, "已關閉 AdsPower 環境")
                except Exception as exc:
                    self.log(profile, f"關閉環境失敗：{exc}", logging.WARNING)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1020x720")
        self.minsize(900, 620)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.output_queue: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.profiles: list[Profile] = []
        self.groups: list[AdsPowerGroup] = []
        self.group_display_to_id: dict[str, str] = {}
        self.config = self.load_config()
        self.build_ui()
        self.after(100, self.poll_queue)
        self.after(500, self.load_groups)

    @staticmethod
    def load_config() -> dict:
        defaults = {
            "api_url": "http://local.adspower.net:50325",
            "group_id": "",
            "min_delay": 2.0,
            "max_delay": 4.0,
            "close_after": True,
        }
        if CONFIG_PATH.exists():
            try:
                defaults.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return defaults

    def save_config(self) -> None:
        data = {
            "api_url": self.api_var.get().strip(),
            "group_id": self.selected_group_id(),
            "min_delay": float(self.min_delay_var.get()),
            "max_delay": float(self.max_delay_var.get()),
            "close_after": bool(self.close_var.get()),
        }
        CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def build_ui(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        settings = ttk.LabelFrame(self, text="AdsPower 設定", padding=10)
        settings.pack(fill="x", padx=12, pady=(12, 6))

        self.api_var = tk.StringVar(value=self.config["api_url"])
        self.group_var = tk.StringVar(value="全部群組")
        self.min_delay_var = tk.StringVar(value=str(self.config["min_delay"]))
        self.max_delay_var = tk.StringVar(value=str(self.config["max_delay"]))
        self.close_var = tk.BooleanVar(value=bool(self.config["close_after"]))

        ttk.Label(settings, text="API 位址").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.api_var, width=35).grid(
            row=0, column=1, padx=(6, 16), sticky="ew"
        )
        ttk.Label(settings, text="選擇群組").grid(row=0, column=2, sticky="w")
        self.group_combo = ttk.Combobox(
            settings,
            textvariable=self.group_var,
            width=28,
            state="readonly",
            values=("全部群組",),
        )
        self.group_combo.grid(
            row=0, column=3, padx=(6, 16), sticky="ew"
        )
        self.group_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_profiles())
        ttk.Button(settings, text="讀取環境", command=self.load_profiles).grid(
            row=0, column=4, padx=4
        )

        ttk.Label(settings, text="操作間隔（秒）").grid(row=1, column=0, pady=(10, 0), sticky="w")
        delay_frame = ttk.Frame(settings)
        delay_frame.grid(row=1, column=1, pady=(10, 0), sticky="w")
        ttk.Entry(delay_frame, textvariable=self.min_delay_var, width=7).pack(side="left")
        ttk.Label(delay_frame, text=" ～ ").pack(side="left")
        ttk.Entry(delay_frame, textvariable=self.max_delay_var, width=7).pack(side="left")
        ttk.Checkbutton(
            settings, text="完成後關閉 AdsPower 環境", variable=self.close_var
        ).grid(row=1, column=2, columnspan=2, pady=(10, 0), sticky="w")
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)

        profile_frame = ttk.LabelFrame(self, text="環境列表", padding=8)
        profile_frame.pack(fill="both", expand=True, padx=12, pady=6)
        toolbar = ttk.Frame(profile_frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="全選", command=lambda: self.select_all(True)).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(toolbar, text="取消全選", command=lambda: self.select_all(False)).pack(
            side="left"
        )
        ttk.Label(toolbar, text="搜尋環境：").pack(side="left", padx=(20, 4))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        ttk.Entry(toolbar, textvariable=self.search_var, width=25).pack(side="left")
        self.count_label = ttk.Label(toolbar, text="尚未讀取")
        self.count_label.pack(side="right")

        columns = ("selected", "name", "user_id", "group")
        self.tree = ttk.Treeview(profile_frame, columns=columns, show="headings", height=12)
        self.tree.heading("selected", text="選取")
        self.tree.heading("name", text="環境名稱")
        self.tree.heading("user_id", text="環境 ID")
        self.tree.heading("group", text="群組")
        self.tree.column("selected", width=55, anchor="center", stretch=False)
        self.tree.column("name", width=260)
        self.tree.column("user_id", width=260)
        self.tree.column("group", width=180)
        scrollbar = ttk.Scrollbar(profile_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self.toggle_selection)

        bottom = ttk.Frame(self, padding=(12, 4, 12, 12))
        bottom.pack(fill="x")
        controls = ttk.Frame(bottom)
        controls.pack(fill="x")
        self.start_button = ttk.Button(
            controls, text="開始執行", command=self.start_run, width=16
        )
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            controls, text="停止", command=self.stop_run, width=12, state="disabled"
        )
        self.stop_button.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(controls, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        self.log_text = tk.Text(bottom, height=10, wrap="word", state="disabled")
        self.log_text.pack(fill="x", pady=(8, 0))

    def selected_group_id(self) -> str:
        return self.group_display_to_id.get(self.group_var.get(), "")

    def load_groups(self) -> None:
        self.append_log("正在自動讀取 AdsPower 群組……")

        def task() -> None:
            try:
                api = AdsPowerAPI(self.api_var.get().strip())
                groups = api.list_groups()
                self.output_queue.put(("groups", groups))
            except Exception as exc:
                self.output_queue.put(("group_error", f"讀取群組失敗：{exc}"))

        threading.Thread(target=task, daemon=True).start()

    def emit(self, text: str) -> None:
        self.output_queue.put(("log", text))

    def append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def load_profiles(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return
        try:
            self.save_config()
        except ValueError:
            messagebox.showerror("設定錯誤", "操作間隔必須是數字。")
            return
        self.start_button.configure(state="disabled")
        self.append_log("正在讀取 AdsPower 環境……")

        def task() -> None:
            try:
                api = AdsPowerAPI(self.api_var.get().strip())
                profiles = api.list_profiles(self.selected_group_id())
                self.output_queue.put(("profiles", profiles))
            except Exception as exc:
                self.output_queue.put(("error", f"讀取環境失敗：{exc}"))

        threading.Thread(target=task, daemon=True).start()

    def refresh_tree(self) -> None:
        selected_ids = {
            self.tree.item(item, "values")[2]
            for item in self.tree.get_children()
            if self.tree.item(item, "values")[0] == "✓"
        }
        self.tree.delete(*self.tree.get_children())
        keyword = self.search_var.get().strip().lower()
        shown = 0
        for profile in self.profiles:
            haystack = f"{profile.name} {profile.user_id} {profile.group_name}".lower()
            if keyword and keyword not in haystack:
                continue
            mark = "✓" if profile.user_id in selected_ids else ""
            self.tree.insert(
                "", "end", values=(mark, profile.name, profile.user_id, profile.group_name)
            )
            shown += 1
        self.count_label.configure(text=f"顯示 {shown}／共 {len(self.profiles)} 個環境")

    def toggle_selection(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        item = self.tree.identify_row(event.y)
        if item:
            values = list(self.tree.item(item, "values"))
            values[0] = "" if values[0] == "✓" else "✓"
            self.tree.item(item, values=values)

    def select_all(self, selected: bool) -> None:
        for item in self.tree.get_children():
            values = list(self.tree.item(item, "values"))
            values[0] = "✓" if selected else ""
            self.tree.item(item, values=values)

    def selected_profiles(self) -> list[Profile]:
        ids = {
            self.tree.item(item, "values")[2]
            for item in self.tree.get_children()
            if self.tree.item(item, "values")[0] == "✓"
        }
        return [profile for profile in self.profiles if profile.user_id in ids]

    def start_run(self) -> None:
        profiles = self.selected_profiles()
        if not profiles:
            messagebox.showwarning("尚未選取", "請先勾選至少一個 AdsPower 環境。")
            return
        try:
            self.save_config()
            min_delay = float(self.min_delay_var.get())
            max_delay = float(self.max_delay_var.get())
            if min_delay < 0 or max_delay < min_delay:
                raise ValueError
        except ValueError:
            messagebox.showerror("設定錯誤", "操作間隔設定不正確。")
            return
        if not messagebox.askyesno(
            "確認執行", f"將依序處理 {len(profiles)} 個環境，確定開始嗎？"
        ):
            return

        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(maximum=len(profiles), value=0)

        def task() -> None:
            api = AdsPowerAPI(self.api_var.get().strip())
            worker = ProfessionalModeWorker(
                api,
                min_delay,
                max_delay,
                self.close_var.get(),
                self.emit,
                self.stop_event,
            )
            counts = {"success": 0, "skipped": 0, "failed": 0, "stopped": 0}
            for index, profile in enumerate(profiles, 1):
                if self.stop_event.is_set():
                    break
                status, _ = worker.run_profile(profile)
                counts[status] = counts.get(status, 0) + 1
                self.output_queue.put(("progress", index))
            self.output_queue.put(("finished", counts))

        self.worker_thread = threading.Thread(target=task, daemon=True)
        self.worker_thread.start()

    def stop_run(self) -> None:
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.append_log("已送出停止要求，會在目前安全步驟結束後停止。")

    def poll_queue(self) -> None:
        try:
            while True:
                event = self.output_queue.get_nowait()
                kind = event[0]
                if kind == "log":
                    self.append_log(event[1])
                elif kind == "groups":
                    self.groups = event[1]
                    self.group_display_to_id = {"全部群組": ""}
                    values = ["全部群組"]
                    for group in self.groups:
                        display = f"{group.group_name}（{group.group_id}）"
                        values.append(display)
                        self.group_display_to_id[display] = group.group_id
                    self.group_combo.configure(values=values)
                    saved_id = str(self.config.get("group_id") or "")
                    selected = next(
                        (
                            display
                            for display, group_id in self.group_display_to_id.items()
                            if group_id == saved_id
                        ),
                        "全部群組",
                    )
                    self.group_var.set(selected)
                    self.append_log(f"已讀取 {len(self.groups)} 個群組。")
                    self.load_profiles()
                elif kind == "group_error":
                    self.append_log(event[1])
                    self.append_log("仍可按「讀取環境」載入全部環境。")
                elif kind == "profiles":
                    self.profiles = event[1]
                    self.refresh_tree()
                    self.start_button.configure(state="normal")
                    self.append_log(f"已讀取 {len(self.profiles)} 個環境。")
                elif kind == "progress":
                    self.progress.configure(value=event[1])
                elif kind == "error":
                    self.start_button.configure(state="normal")
                    self.append_log(event[1])
                    messagebox.showerror("執行錯誤", event[1])
                elif kind == "finished":
                    counts = event[1]
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    summary = (
                        f"執行完成：成功 {counts['success']}、已是專業模式 "
                        f"{counts['skipped']}、失敗 {counts['failed']}。"
                    )
                    self.append_log(summary)
                    messagebox.showinfo("執行完成", summary)
        except queue.Empty:
            pass
        self.after(100, self.poll_queue)

    def on_close(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            if not messagebox.askyesno("程式仍在執行", "確定停止並關閉程式嗎？"):
                return
            self.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
