# -*- coding: utf-8 -*-
r"""
AdsPower + Selenium：Facebook 自動更換頭像＋Messenger PIN V2.4.3 Stable
此檔亦作為「養號加頭像加 PIN」整合版的功能模組。
Python 3.12 / Windows 10、11

安裝套件：
    py -3.12 -m pip install requests selenium

圖片預設資料夾：
    C:\Users\USER\Desktop\頭像圖片

配對範例：
    環境名稱「私訊33」 -> 33.jpg / 33.jpeg / 33.png / 33.webp
    環境名稱「新001」  -> 001.jpg / 001.jpeg / 001.png / 001.webp
"""

from __future__ import annotations

import os
import re
import sys
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================
# 使用者設定
# ============================================================

ADSPOWER_BASE_URL = os.getenv("ADSPOWER_BASE_URL", "http://127.0.0.1:50325").rstrip("/")
ADSPOWER_API_KEY = os.getenv("ADSPOWER_API_KEY", "").strip()

# 留空表示讀取全部群組；也可填入群組 ID，例如 10085779
ADSPOWER_TARGET_GROUP_ID = os.getenv("ADSPOWER_TARGET_GROUP_ID", "").strip()

# 頭像圖片資料夾
IMAGE_FOLDER = Path.home() / "Desktop" / "頭像圖片"

# 支援的圖片副檔名，依優先順序尋找
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# AdsPower API 請求間隔，避免 Too many request per second
API_REQUEST_INTERVAL = 1.2

# Selenium 等待秒數
WAIT_SECONDS = 20

# 上傳後等待 Facebook 產生預覽
UPLOAD_PREVIEW_WAIT = 4

# 儲存後等待
SAVE_WAIT = 6

CHAT_PIN = "123789"
MESSENGER_WAIT = 5


# ============================================================
# LOG
# ============================================================

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "換頭像試跑.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("fb_avatar")


@dataclass
class Profile:
    user_id: str
    name: str
    group_id: str = ""
    group_name: str = ""


def api_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if ADSPOWER_API_KEY:
        headers["Authorization"] = f"Bearer {ADSPOWER_API_KEY}"
    return headers


def api_get(
    path: str,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
    apply_delay: bool = True,
) -> dict[str, Any]:
    """呼叫 AdsPower Local API。"""
    if apply_delay:
        time.sleep(API_REQUEST_INTERVAL)
    url = f"{ADSPOWER_BASE_URL}{path}"
    response = requests.get(
        url,
        params=params or {},
        headers=api_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("code") != 0:
        raise RuntimeError(f"AdsPower API 失敗：{data.get('msg', data)}")

    return data


def get_all_profiles() -> list[Profile]:
    """分頁取得 AdsPower Profile。"""
    profiles: list[Profile] = []
    page = 1
    page_size = 100

    logger.info("正在讀取 AdsPower Profile...")

    while True:
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
        }
        if ADSPOWER_TARGET_GROUP_ID:
            params["group_id"] = ADSPOWER_TARGET_GROUP_ID

        result = api_get("/api/v1/user/list", params=params)
        data = result.get("data") or {}
        items = data.get("list") or []

        for item in items:
            user_id = str(item.get("user_id") or "").strip()
            name = str(item.get("name") or item.get("user_name") or "").strip()
            if not user_id:
                continue

            profiles.append(
                Profile(
                    user_id=user_id,
                    name=name or user_id,
                    group_id=str(item.get("group_id") or ""),
                    group_name=str(item.get("group_name") or ""),
                )
            )

        if len(items) < page_size:
            break

        page += 1

    logger.info("共讀取 %s 個 Profile。", len(profiles))
    return profiles


def first_series_character(name: str) -> str:
    """只有名稱第一個字本身是中文字才歸入該系列，否則歸類為「其他」."""
    if name and "\u4e00" <= name[0] <= "\u9fff":
        return name[0]
    return "其他"


def extract_first_number(name: str) -> str | None:
    """
    擷取環境名稱中的第一組數字。

    規則：
    - 單一數字自動補成二位數：2 -> 02
    - 兩位數以上維持原樣：10 -> 10、100 -> 100
    - 原本已有前導零則保留：08 -> 08、001 -> 001
    """
    match = re.search(r"\d+", name)
    if not match:
        return None

    number_text = match.group(0)

    if len(number_text) == 1:
        return number_text.zfill(2)

    return number_text


def number_sort_key(profile: Profile) -> tuple[int, int, str]:
    value = extract_first_number(profile.name)
    if value is None:
        return (1, 0, profile.name)
    return (0, int(value), profile.name)


def choose_profiles(profiles: list[Profile]) -> list[Profile]:
    groups: dict[str, list[Profile]] = {}
    for profile in profiles:
        groups.setdefault(first_series_character(profile.name), []).append(profile)

    ordered_names = sorted(
        groups,
        key=lambda value: (value == "其他", value),
    )

    print("\n可用系列：\n")
    for index, series in enumerate(ordered_names, start=1):
        print(f"[{index}] {series}（{len(groups[series])} 個）")
    print("\n[0] 退出")
    print("可複選，例如：1 或 1+2+4")

    while True:
        raw = input("\n請選擇系列：").strip()
        if raw == "0":
            return []

        try:
            indexes = [int(value.strip()) for value in raw.split("+") if value.strip()]
            if not indexes or any(value < 1 or value > len(ordered_names) for value in indexes):
                raise ValueError
            selected_series = [ordered_names[value - 1] for value in dict.fromkeys(indexes)]
            break
        except ValueError:
            print("輸入錯誤，請輸入例如 1 或 1+2。")

    selected: list[Profile] = []

    if len(selected_series) == 1:
        series = selected_series[0]
        series_profiles = sorted(groups[series], key=number_sort_key)
        values = [
            int(number)
            for profile in series_profiles
            if (number := extract_first_number(profile.name)) is not None
        ]

        if values:
            print(f"\n可辨識號碼範圍：{min(values)} ～ {max(values)}")
        else:
            print("\n此系列沒有可辨識的數字，將不使用號碼篩選。")

        start_raw = input("請輸入從幾號開始跑（Enter＝不指定）：").strip()
        end_raw = input("請輸入跑到幾號（Enter＝不指定）：").strip()

        try:
            start_number = int(start_raw) if start_raw else None
            end_number = int(end_raw) if end_raw else None
        except ValueError:
            print("號碼格式錯誤，將不指定起訖號碼。")
            start_number = None
            end_number = None

        for profile in series_profiles:
            number_text = extract_first_number(profile.name)

            if start_number is None and end_number is None:
                selected.append(profile)
                continue

            if number_text is None:
                continue

            number = int(number_text)
            if start_number is not None and number < start_number:
                continue
            if end_number is not None and number > end_number:
                continue
            selected.append(profile)
    else:
        for series in selected_series:
            selected.extend(groups[series])
        selected.sort(key=lambda item: (first_series_character(item.name), number_sort_key(item)))

    print(f"\n已建立執行清單：{len(selected)} 個 Profile")
    for profile in selected:
        print(f"  - {profile.name} ({profile.user_id})")

    # 試跑版預設只跑 1 個，避免一次更換太多帳號
    raw_limit = input("\n試跑幾個 Profile？（Enter＝1，0＝全部）：").strip()
    try:
        limit = 1 if raw_limit == "" else int(raw_limit)
    except ValueError:
        limit = 1

    if limit > 0:
        selected = selected[:limit]

    return selected


def find_matching_image(profile_name: str) -> Path | None:
    number_text = extract_first_number(profile_name)
    if not number_text:
        logger.warning("[%s] 環境名稱沒有數字，無法配對圖片。", profile_name)
        return None

    logger.info("[%s] 已擷取環境號碼：%s", profile_name, number_text)

    if not IMAGE_FOLDER.exists():
        logger.error("頭像圖片資料夾不存在：%s", IMAGE_FOLDER)
        return None

    for extension in IMAGE_EXTENSIONS:
        image_path = IMAGE_FOLDER / f"{number_text}{extension}"
        if image_path.is_file():
            logger.info("[%s] 找到對應頭像：%s", profile_name, image_path)
            return image_path.resolve()

    # Windows 通常不分大小寫，但仍補充掃描大寫副檔名
    wanted_stem = number_text.casefold()
    extension_priority = {ext: index for index, ext in enumerate(IMAGE_EXTENSIONS)}
    candidates = [
        path
        for path in IMAGE_FOLDER.iterdir()
        if path.is_file()
        and path.stem.casefold() == wanted_stem
        and path.suffix.casefold() in extension_priority
    ]

    if candidates:
        candidates.sort(key=lambda path: extension_priority[path.suffix.casefold()])
        logger.info("[%s] 找到對應頭像：%s", profile_name, candidates[0])
        return candidates[0].resolve()

    logger.warning("[%s] 找不到對應圖片，已跳過換頭像。", profile_name)
    return None


def start_adspower_browser(profile: Profile) -> tuple[webdriver.Chrome, str]:
    logger.info("[%s] 正在啟動 AdsPower Browser...", profile.name)

    result = api_get(
        "/api/v1/browser/start",
        params={"user_id": profile.user_id},
        timeout=90,
    )
    data = result.get("data") or {}
    ws = data.get("ws") or {}

    debugger_address = str(ws.get("selenium") or "").strip()
    webdriver_path = str(data.get("webdriver") or "").strip()

    if not debugger_address:
        raise RuntimeError("AdsPower 回傳資料沒有 Selenium debugger address。")
    if not webdriver_path:
        raise RuntimeError("AdsPower 回傳資料沒有 webdriver 路徑。")

    options = webdriver.ChromeOptions()
    options.add_experimental_option("debuggerAddress", debugger_address)

    driver = webdriver.Chrome(
        service=Service(webdriver_path),
        options=options,
    )
    logger.info("[%s] Selenium 連接成功。", profile.name)

    # V2.4.2：連接瀏覽器後先允許 Facebook 通知，
    # 避免 Chrome 原生 Allow / Block 視窗遮住 Facebook。
    allow_facebook_notifications(driver, profile.name)

    return driver, webdriver_path


def stop_adspower_browser(profile: Profile) -> None:
    try:
        api_get(
            "/api/v1/browser/stop",
            params={"user_id": profile.user_id},
            timeout=15,
            apply_delay=False,
        )
        logger.info("[%s] AdsPower Browser 已關閉。", profile.name)
    except KeyboardInterrupt:
        logger.warning("[%s] 使用者中止程式，略過等待關閉回應。", profile.name)
    except Exception as exc:
        logger.warning("[%s] 關閉 AdsPower Browser 失敗：%s", profile.name, exc)



def allow_facebook_notifications(
    driver: webdriver.Chrome,
    profile_name: str,
) -> bool:
    """
    自動允許 Facebook 通知權限。

    Chrome 上方顯示的：
        www.facebook.com wants to
        Show notifications
        Allow / Block

    屬於瀏覽器原生權限視窗，不在網頁 DOM 中，不能用一般
    Selenium XPath 點擊。因此使用 Chrome DevTools Protocol
    直接把 facebook.com 的 notifications 權限設定為 granted。

    設定成功後，已出現的通知權限提示通常會立即關閉，
    Facebook 畫面也會恢復，不再霧化。
    """
    origins = (
        "https://www.facebook.com",
        "https://facebook.com",
    )

    success = False

    for origin in origins:
        try:
            driver.execute_cdp_cmd(
                "Browser.setPermission",
                {
                    "permission": {"name": "notifications"},
                    "setting": "granted",
                    "origin": origin,
                },
            )
            success = True
        except Exception as exc:
            logger.debug(
                "[%s] 設定通知權限失敗（%s）：%s",
                profile_name,
                origin,
                exc,
            )

    if success:
        logger.info("[%s] 已自動允許 Facebook 通知權限。", profile_name)
        time.sleep(1)
        return True

    logger.warning(
        "[%s] 無法透過 Chrome DevTools 設定 Facebook 通知權限，繼續執行。",
        profile_name,
    )
    return False


def switch_to_facebook_tab(driver: webdriver.Chrome, profile_name: str) -> bool:
    for handle in driver.window_handles:
        driver.switch_to.window(handle)
        current_url = driver.current_url.lower()
        if "facebook.com" in current_url:
            logger.info("[%s] 已切換至 Facebook 分頁。", profile_name)
            allow_facebook_notifications(driver, profile_name)
            return True

    driver.execute_script("window.open('https://www.facebook.com/', '_blank');")
    driver.switch_to.window(driver.window_handles[-1])

    try:
        WebDriverWait(driver, WAIT_SECONDS).until(
            lambda current_driver: "facebook.com" in current_driver.current_url.lower()
        )
        logger.info("[%s] 已開啟 Facebook 首頁。", profile_name)
        allow_facebook_notifications(driver, profile_name)
        return True
    except TimeoutException:
        logger.warning("[%s] 無法開啟 Facebook 首頁。", profile_name)
        return False


def first_clickable(
    driver: webdriver.Chrome,
    selectors: list[tuple[str, str]],
    timeout: int = WAIT_SECONDS,
):
    last_error: Exception | None = None

    for by, selector in selectors:
        try:
            return WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((by, selector))
            )
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise TimeoutException("找不到可點擊元素。")


def click_profile_entry(driver: webdriver.Chrome, profile_name: str) -> bool:
    """使用首頁 Timeline 的固定 URL 進入目前登入帳號的個人檔案。"""
    try:
        # 關閉可能已展開的帳號選單或彈窗
        from selenium.webdriver.common.keys import Keys
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except Exception:
        pass

    try:
        profile_url = getattr(driver, "_facebook_personal_profile_url", "")
        if not profile_url:
            driver.get("https://www.facebook.com/")
            deadline = time.monotonic() + WAIT_SECONDS
            while time.monotonic() < deadline and not profile_url:
                profile_url = driver.execute_script(
                    """
                    const links = [...document.querySelectorAll('a[href*="profile.php?id="]')];
                    const urls = [...new Set(links.filter(a =>
                        ['timeline', 'journal', 'ไทม์ไลน์', 'يوميات'].some(word =>
                            ((a.getAttribute('aria-label') || a.innerText || '') + '')
                                .toLowerCase().includes(word)
                        )
                    ).map(a => (a.href || '').split('&__cft__')[0].split('&__tn__')[0]))];
                    return urls.length === 1 ? urls[0] : '';
                    """
                )
                if not profile_url:
                    time.sleep(0.4)
            if not profile_url:
                raise RuntimeError("Facebook 首頁找不到唯一的本人 Timeline 連結")
            driver._facebook_personal_profile_url = profile_url
        driver.get(profile_url)

        WebDriverWait(driver, WAIT_SECONDS).until(
            lambda current_driver: (
                "facebook.com" in current_driver.current_url.lower()
                and "/live/producer" not in current_driver.current_url.lower()
                and "/login" not in current_driver.current_url.lower()
            )
        )

        # 確認不是建立直播或其他工具頁
        current_url = driver.current_url.lower()
        if "/live/producer" in current_url:
            logger.warning("[%s] 誤進入直播工具頁，停止換頭像。", profile_name)
            return False

        WebDriverWait(driver, WAIT_SECONDS).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(3)
        logger.info("[%s] 已使用首頁 Timeline 網址開啟個人檔案頁：%s", profile_name, driver.current_url)
        return True
    except Exception as exc:
        logger.warning("[%s] 無法進入個人檔案頁：%s", profile_name, exc)
        return False


def _click_closest_button(driver: webdriver.Chrome, element) -> None:
    """點擊文字、SVG 或圖片所在的最近可操作父層。"""
    clickable = driver.execute_script(
        """
        let el = arguments[0];
        while (el && el !== document.body) {
            const role = el.getAttribute && el.getAttribute('role');
            const tag = (el.tagName || '').toLowerCase();
            const tabindex = el.getAttribute && el.getAttribute('tabindex');

            if (
                role === 'button' ||
                role === 'menuitem' ||
                tag === 'button' ||
                tag === 'a' ||
                tabindex === '0'
            ) {
                return el;
            }
            el = el.parentElement;
        }
        return arguments[0];
        """,
        element,
    )

    driver.execute_script(
        "arguments[0].scrollIntoView({block:'center', inline:'center'});",
        clickable,
    )
    time.sleep(0.5)
    driver.execute_script("arguments[0].click();", clickable)


def _find_main_profile_image(driver: webdriver.Chrome):
    """
    找出個人檔案頁上方的大頭貼。

    Facebook 的頭像使用 SVG <image>，Element 類似：
        <image width="100%" height="100%" ... style="height:168px;width:168px">

    不使用 mount_0_0_xxx，因為該 ID 每次開啟都可能改變。
    """
    return driver.execute_script(
        """
        const images = Array.from(
            document.querySelectorAll('svg image, svg img, img')
        );

        const candidates = images.map((el) => {
            const rect = el.getBoundingClientRect();
            const href =
                el.getAttribute('href') ||
                el.getAttribute('xlink:href') ||
                el.getAttributeNS?.('http://www.w3.org/1999/xlink', 'href') ||
                el.src ||
                '';

            return {
                el,
                href,
                width: rect.width,
                height: rect.height,
                top: rect.top,
                left: rect.left,
                area: rect.width * rect.height
            };
        }).filter((item) => {
            const square = Math.abs(item.width - item.height) <= 35;
            const suitableSize =
                item.width >= 120 && item.width <= 260 &&
                item.height >= 120 && item.height <= 260;
            const suitablePosition =
                item.top >= 180 && item.top <= 750 &&
                item.left >= 20 && item.left <= 500;
            const facebookImage =
                item.href.includes('fbcdn') ||
                item.href.includes('facebook');

            return square && suitableSize && suitablePosition && facebookImage;
        });

        candidates.sort((a, b) => b.area - a.area);
        return candidates.length ? candidates[0].el : null;
        """
    )


def open_avatar_editor(driver: webdriver.Chrome, profile_name: str) -> bool:
    """
    V2.4.3 Stable：支援兩種換頭像入口。

    A. 已有頭像：
       點擊目前頭像
       → 點擊「Choose profile picture／選擇大頭貼照」

    B. 沒有頭像：
       個人檔案頁會直接顯示「Choose profile picture」
       → 不必先點灰色預設頭像
       → 直接點擊真正可操作的按鈕

    點擊後必須確認「Upload photo／上傳相片」視窗出現，
    才算成功進入上傳流程。
    """
    if "/live/producer" in driver.current_url.lower():
        logger.warning("[%s] 目前在直播工具頁，拒絕繼續操作。", profile_name)
        return False

    choose_texts = (
        "選擇大頭貼照",
        "Choose profile picture",
        "Pumili ng profile picture",
        "Choisir une photo de profil",
        "เลือกรูปโปรไฟล์",
        "اختيار صورة الملف الشخصي",
    )

    upload_texts = (
        "上傳相片",
        "Upload photo",
        "Mag-upload ng larawan",
        "Importer une photo",
        "อัพโหลดรูปภาพ",
        "تحميل صورة",
    )

    def find_visible_choose_button():
        """
        找到真正可點擊的 Choose profile picture。

        不使用 mount_0_0_xx，也不使用使用者提供的 role=none 外框。
        找到文字節點後，往上尋找最近的：
        role=button / role=menuitem / button / tabindex=0。
        """
        return driver.execute_script(
            """
            const texts = arguments[0];

            function visible(el) {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return (
                    r.width > 0 &&
                    r.height > 0 &&
                    s.display !== 'none' &&
                    s.visibility !== 'hidden' &&
                    s.opacity !== '0'
                );
            }

            const nodes = Array.from(
                document.querySelectorAll('span, div, button')
            ).filter(el => {
                if (!visible(el)) return false;
                const text = (el.innerText || el.textContent || '').trim();
                return texts.some(value => text === value || text.includes(value));
            });

            const candidates = [];

            for (const node of nodes) {
                let current = node;

                while (current && current !== document.body) {
                    const role = current.getAttribute && current.getAttribute('role');
                    const tag = (current.tagName || '').toLowerCase();
                    const tabindex = current.getAttribute && current.getAttribute('tabindex');

                    if (
                        visible(current) &&
                        (
                            role === 'button' ||
                            role === 'menuitem' ||
                            tag === 'button' ||
                            tag === 'a' ||
                            tabindex === '0'
                        )
                    ) {
                        const r = current.getBoundingClientRect();
                        candidates.push({
                            element: current,
                            area: r.width * r.height,
                            top: r.top,
                            left: r.left
                        });
                        break;
                    }

                    current = current.parentElement;
                }
            }

            // 優先面積較小、較接近文字本體的真正按鈕，
            // 避免選到整頁大型外層容器。
            candidates.sort((a, b) => {
                if (a.area !== b.area) return a.area - b.area;
                if (a.top !== b.top) return a.top - b.top;
                return a.left - b.left;
            });

            return candidates.length ? candidates[0].element : null;
            """,
            list(choose_texts),
        )

    def upload_dialog_visible() -> bool:
        page_text = ""
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            pass

        if any(text in page_text for text in upload_texts):
            return True

        # 備用：視窗中已出現圖片上傳 input
        try:
            inputs = driver.find_elements(
                By.CSS_SELECTOR,
                "input[type='file'][accept*='image']",
            )
            return len(inputs) > 0
        except Exception:
            return False

    def click_choose_and_confirm(stage_name: str) -> bool:
        try:
            choose_button = WebDriverWait(driver, 8).until(
                lambda d: find_visible_choose_button()
            )
        except Exception:
            return False

        try:
            button_text = (choose_button.text or "").strip().replace("\n", " ")
            role = choose_button.get_attribute("role") or ""
            logger.info(
                "[%s] 找到%s的 Choose profile picture：文字=%r，role=%r",
                profile_name,
                stage_name,
                button_text,
                role,
            )

            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});",
                choose_button,
            )
            time.sleep(0.5)

            # 直接點真正可操作按鈕，不再點 role=none 外框
            driver.execute_script("arguments[0].click();", choose_button)
            logger.info("[%s] 已點擊「Choose profile picture」。", profile_name)

        except Exception as exc:
            logger.warning(
                "[%s] 無法點擊 Choose profile picture：%s",
                profile_name,
                exc,
            )
            return False

        try:
            WebDriverWait(driver, 15).until(
                lambda d: upload_dialog_visible()
            )
            logger.info(
                "[%s] 已確認「Upload photo／上傳相片」視窗出現。",
                profile_name,
            )
            return True
        except Exception:
            logger.warning(
                "[%s] 點擊 Choose profile picture 後沒有出現 Upload photo 視窗。",
                profile_name,
            )
            return False

    # ------------------------------------------------------------
    # 情況 B：沒有頭像，頁面已直接顯示 Choose profile picture
    # ------------------------------------------------------------
    direct_button = find_visible_choose_button()

    if direct_button is not None:
        logger.info(
            "[%s] 個人檔案頁已直接顯示 Choose profile picture，"
            "略過點擊灰色預設頭像。",
            profile_name,
        )
        return click_choose_and_confirm("直接入口")

    # ------------------------------------------------------------
    # 情況 A：已有頭像，先點擊主頭像
    # ------------------------------------------------------------
    try:
        WebDriverWait(driver, WAIT_SECONDS).until(
            lambda d: _find_main_profile_image(d) is not None
        )
        profile_image = _find_main_profile_image(driver)

        if profile_image is None:
            logger.warning(
                "[%s] 找不到主頭像，也找不到直接顯示的 Choose profile picture。",
                profile_name,
            )
            return False

        _click_closest_button(driver, profile_image)
        logger.info("[%s] 已點擊個人檔案大頭貼。", profile_name)

    except Exception as exc:
        logger.warning("[%s] 無法點擊個人檔案大頭貼：%s", profile_name, exc)
        return False

    # 點頭像後等待選單動畫完成，再找真正按鈕
    time.sleep(1)
    return click_choose_and_confirm("頭像選單")


def upload_avatar_file(
    driver: webdriver.Chrome,
    image_path: Path,
    profile_name: str,
) -> bool:
    """
    修正版上傳流程：

    Facebook 的「選擇大頭貼照」視窗不一定把所有內容放在
    Selenium 抓到的第一個 role=dialog 裡，因此不再限制只搜尋 dialog。

    直接在整個頁面尋找 input[type=file]，並優先選擇：
    1. accept 含 image
    2. accept 含 jpg/jpeg/png/webp
    3. 最後出現的 file input
    """
    if not image_path.is_file():
        logger.warning("[%s] 圖片不存在：%s", profile_name, image_path)
        return False

    # 先確認畫面上確實有「上傳相片」文字，但不再限定必須位於 role=dialog。
    upload_text_xpath = (
        "//*[self::span or self::div or self::button]"
        "[normalize-space()='上傳相片' "
        "or normalize-space()='Upload photo' "
        "or normalize-space()='Mag-upload ng larawan' "
        "or normalize-space()='Importer une photo' "
        "or normalize-space()='อัพโหลดรูปภาพ' "
        "or normalize-space()='تحميل صورة']"
    )

    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.XPATH, upload_text_xpath))
        )
        logger.info("[%s] 已確認「上傳相片」功能出現在畫面。", profile_name)
    except Exception:
        # 即使文字定位不到，仍然繼續找 file input，避免 Facebook DOM 包裝差異。
        logger.warning(
            "[%s] 無法用文字定位「上傳相片」，改為直接搜尋圖片上傳欄位。",
            profile_name,
        )

    def collect_file_inputs():
        return driver.find_elements(By.CSS_SELECTOR, "input[type='file']")

    try:
        WebDriverWait(driver, 12).until(
            lambda d: len(d.find_elements(By.CSS_SELECTOR, "input[type='file']")) > 0
        )
    except Exception:
        logger.warning("[%s] 頁面沒有找到 input[type=file]。", profile_name)
        return False

    file_inputs = collect_file_inputs()
    logger.info("[%s] 頁面共找到 %s 個檔案上傳欄位。", profile_name, len(file_inputs))

    image_inputs = []
    possible_inputs = []
    other_inputs = []

    for index, item in enumerate(file_inputs, start=1):
        try:
            accept = (item.get_attribute("accept") or "").lower()
            multiple = item.get_attribute("multiple")
            logger.info(
                "[%s] 上傳欄位 %s：accept=%r，multiple=%r",
                profile_name,
                index,
                accept,
                multiple,
            )

            if "image" in accept:
                image_inputs.append(item)
            elif any(ext in accept for ext in (".jpg", ".jpeg", ".png", ".webp")):
                possible_inputs.append(item)
            else:
                other_inputs.append(item)
        except Exception:
            continue

    candidates = image_inputs or possible_inputs or other_inputs

    if not candidates:
        logger.warning("[%s] 找不到可使用的圖片上傳欄位。", profile_name)
        return False

    # Facebook 通常把目前剛開啟視窗使用的 input 放在 DOM 最後面。
    ordered_candidates = list(reversed(candidates))
    upload_errors = []

    for candidate_index, file_input in enumerate(ordered_candidates, start=1):
        try:
            file_input.send_keys(str(image_path.resolve()))
            logger.info(
                "[%s] 已透過候選欄位 %s 送出頭像圖片：%s",
                profile_name,
                candidate_index,
                image_path.name,
            )
        except Exception as first_exc:
            try:
                # 只有送檔失敗時才解除隱藏再試。
                driver.execute_script(
                    """
                    const input = arguments[0];
                    input.style.display = 'block';
                    input.style.visibility = 'visible';
                    input.style.opacity = '1';
                    input.removeAttribute('hidden');
                    """,
                    file_input,
                )
                file_input.send_keys(str(image_path.resolve()))
                logger.info(
                    "[%s] 已解除隱藏並透過候選欄位 %s 送出圖片：%s",
                    profile_name,
                    candidate_index,
                    image_path.name,
                )
            except Exception as second_exc:
                upload_errors.append(f"候選{candidate_index}: {second_exc}")
                continue

        # 每送入一個候選欄位，就檢查是否出現裁切預覽與儲存按鈕。
        save_text_xpath = (
            "//*[self::span or self::div or self::button]"
            "[normalize-space()='儲存' or normalize-space()='Save' "
            "or normalize-space()='I-save' or normalize-space()='Enregistrer' "
            "or normalize-space()='บันทึก' or normalize-space()='حفظ']"
        )

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, save_text_xpath))
            )
            time.sleep(2)
            logger.info("[%s] 頭像預覽及裁切畫面已出現。", profile_name)
            return True
        except Exception:
            logger.warning(
                "[%s] 候選欄位 %s 已送檔，但沒有出現頭像裁切畫面，改試下一個欄位。",
                profile_name,
                candidate_index,
            )

    logger.warning(
        "[%s] 所有圖片上傳欄位均嘗試失敗：%s",
        profile_name,
        " | ".join(upload_errors) if upload_errors else "送檔後未出現裁切畫面",
    )
    return False


def save_avatar(driver: webdriver.Chrome, profile_name: str) -> bool:
    # 儲存前再次確認通知權限，避免裁切畫面被 Chrome 權限提示遮住。
    allow_facebook_notifications(driver, profile_name)

    """
    只修正「儲存」按鈕點擊。

    依實際 Facebook Element：
        <span>儲存</span>

    流程：
    1. 找到目前可見裁切視窗內的「儲存 / Save」文字。
    2. 往上尋找真正可點擊的按鈕父層。
    3. 優先使用 ActionChains 點擊。
    4. 失敗時依序改用 JavaScript click 與完整滑鼠事件。
    5. 必須確認裁切視窗消失，才算成功。
    """
    from selenium.webdriver import ActionChains

    save_texts = (
        "儲存", "保存", "Save", "I-save", "Enregistrer",
        "บันทึก", "حفظ",
    )
    avatar_dialog_markers = (
        "choose profile picture", "crop photo", "make temporary",
        "pumili ng profile picture", "i-crop ang litrato", "gawing pansamantala",
        "choisir une photo de profil", "recadrer la photo",
        "เลือกรูปโปรไฟล์", "ครอบตัดรูปภาพ",
        "اختيار صورة الملف الشخصي", "قص الصورة",
        "選擇大頭貼照", "裁切相片", "选择头像", "裁剪照片",
    )

    def find_real_save_button():
        return driver.execute_script(
            """
            const texts = new Set(arguments[0].map(value => value.toLowerCase()));
            const dialogMarkers = arguments[1].map(value => value.toLowerCase());

            function isVisible(el) {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return (
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0'
                );
            }

            const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'))
                .filter(isVisible)
                .filter(dialog => {
                    const text=(dialog.innerText||'').replace(/\\s+/g,' ').trim().toLowerCase();
                    return dialogMarkers.some(marker => text.includes(marker));
                });
            if (!dialogs.length) return null;
            const dialog = dialogs[dialogs.length - 1];
            const nodes = Array.from(dialog.querySelectorAll('span, div, button'))
                .filter(el => {
                    const text=(el.innerText||el.textContent||el.getAttribute('aria-label')||'')
                        .replace(/\\s+/g,' ').trim().toLowerCase();
                    return isVisible(el) && texts.has(text);
                });

            const candidates = [];

            for (const node of nodes) {
                let el = node;

                while (el && el !== document.body) {
                    const role = el.getAttribute && el.getAttribute('role');
                    const tag = (el.tagName || '').toLowerCase();
                    const tabindex = el.getAttribute && el.getAttribute('tabindex');
                    const disabled =
                        (el.getAttribute && el.getAttribute('aria-disabled') === 'true') ||
                        el.hasAttribute?.('disabled');

                    if (
                        !disabled &&
                        isVisible(el) &&
                        (
                            role === 'button' ||
                            tag === 'button' ||
                            tabindex === '0'
                        )
                    ) {
                        const rect = el.getBoundingClientRect();
                        const top = document.elementFromPoint(
                            rect.left + rect.width / 2,
                            rect.top + rect.height / 2
                        );
                        // 僅接受目前頭像對話框最上層真正可點的按鈕；不再依賴
                        // 螢幕右下角座標，避免視窗尺寸／RTL 版面改變後找不到 Save。
                        if (top && (top === el || el.contains(top))) {
                            candidates.push({
                                element: el,
                                left: rect.left,
                                top: rect.top,
                                right: rect.right,
                                bottom: rect.bottom,
                                area: rect.width * rect.height
                            });
                        }
                        break;
                    }

                    el = el.parentElement;
                }
            }

            candidates.sort((a, b) => {
                if (b.bottom !== a.bottom) return b.bottom - a.bottom;
                if (b.right !== a.right) return b.right - a.right;
                return b.area - a.area;
            });

            return candidates.length ? candidates[0].element : null;
            """,
            list(save_texts),
            list(avatar_dialog_markers),
        )

    try:
        save_button = WebDriverWait(driver, 20).until(
            lambda d: find_real_save_button()
        )
    except Exception as exc:
        logger.warning("[%s] 找不到真正可點擊的儲存按鈕：%s", profile_name, exc)
        return False

    try:
        rect = driver.execute_script(
            """
            const r = arguments[0].getBoundingClientRect();
            return {
                left: Math.round(r.left),
                top: Math.round(r.top),
                width: Math.round(r.width),
                height: Math.round(r.height)
            };
            """,
            save_button,
        )

        logger.info(
            "[%s] 已定位右下角儲存按鈕：文字=%r，位置=%s",
            profile_name,
            (save_button.text or "").strip(),
            rect,
        )

        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});",
            save_button,
        )
        time.sleep(1)

        clicked = False

        # 方法 1：ActionChains 真實滑鼠點擊
        try:
            ActionChains(driver).move_to_element(save_button).pause(0.5).click().perform()
            clicked = True
            logger.info("[%s] 已使用 ActionChains 點擊儲存。", profile_name)
        except Exception as exc:
            logger.warning("[%s] ActionChains 點擊失敗：%s", profile_name, exc)

        # 方法 2：JavaScript click
        if not clicked:
            try:
                driver.execute_script("arguments[0].click();", save_button)
                clicked = True
                logger.info("[%s] 已使用 JavaScript click 點擊儲存。", profile_name)
            except Exception as exc:
                logger.warning("[%s] JavaScript click 失敗：%s", profile_name, exc)

        # 方法 3：完整 mouse event
        if not clicked:
            try:
                driver.execute_script(
                    """
                    const el = arguments[0];
                    const events = [
                        'mouseover',
                        'mouseenter',
                        'mousemove',
                        'mousedown',
                        'mouseup',
                        'click'
                    ];

                    for (const type of events) {
                        el.dispatchEvent(
                            new MouseEvent(type, {
                                view: window,
                                bubbles: true,
                                cancelable: true,
                                buttons: type === 'mousedown' ? 1 : 0
                            })
                        );
                    }
                    """,
                    save_button,
                )
                clicked = True
                logger.info("[%s] 已使用完整滑鼠事件點擊儲存。", profile_name)
            except Exception as exc:
                logger.warning("[%s] 完整滑鼠事件點擊失敗：%s", profile_name, exc)

        if not clicked:
            logger.warning("[%s] 所有儲存點擊方式都失敗。", profile_name)
            return False

    except Exception as exc:
        logger.warning("[%s] 儲存按鈕操作失敗：%s", profile_name, exc)
        return False

    # 點擊後必須確認裁切視窗消失，避免假成功
    def save_completed(d):
        try:
            current_button = find_real_save_button()
            if current_button is not None:
                return False

            body_text = d.find_element(By.TAG_NAME, "body").text or ""
            crop_markers = (
                "拖曳或使用方向鍵來調整圖像的位置",
                "裁切相片",
                "設為臨時大頭貼照",
                "Drag or use the arrow keys to reposition the image",
                "Crop photo",
                "Make temporary",
                "I-crop ang Litrato",
                "Gawing Pansamantala",
                "Recadrer la photo",
                "ครอบตัดรูปภาพ",
                "قص الصورة",
            )

            return not any(marker in body_text for marker in crop_markers)
        except Exception:
            return False

    try:
        WebDriverWait(driver, 35).until(save_completed)
        logger.info("[%s] 裁切視窗已關閉，確認儲存成功。", profile_name)
    except Exception:
        logger.warning(
            "[%s] 點擊儲存後裁切視窗仍存在，本次判定儲存失敗。",
            profile_name,
        )
        return False

    time.sleep(SAVE_WAIT)
    logger.info("[%s] Facebook 頭像更換流程完成。", profile_name)
    return True



def _visible_elements(driver: webdriver.Chrome, by: str, selector: str):
    result = []
    for element in driver.find_elements(by, selector):
        try:
            if element.is_displayed():
                result.append(element)
        except Exception:
            continue
    return result


def create_messenger_pin(
    driver: webdriver.Chrome,
    profile_name: str,
    pin: str = CHAT_PIN,
) -> bool:
    """
    V2.4.2 Stable：換完頭像後處理 Messenger PIN。

    固定判斷順序：
    1. 立即修正 → 備份聊天內容 → 輸入兩次 PIN。
    2. Create PIN → 輸入兩次 PIN。
    3. 已存在的 PIN／確認 PIN 六格畫面 → 只輸入一次 PIN。
    4. 三種畫面都沒有 → 正常跳過。

    重要修正：
    - 不再把一般 Messenger 清單誤判為 PIN 畫面。
    - 不再使用 active_element 亂送 123789。
    - Create PIN 一定先點擊，不能直接送數字。
    """
    from selenium.webdriver import ActionChains

    if not pin.isdigit() or len(pin) != 6:
        logger.error("[%s] CHAT_PIN 必須是 6 位數字。", profile_name)
        return False

    # ------------------------------------------------------------
    # 基礎工具
    # ------------------------------------------------------------

    def body_text() -> str:
        try:
            return driver.find_element(By.TAG_NAME, "body").text or ""
        except Exception:
            return ""

    def get_pin_input():
        selectors = [
            (By.CSS_SELECTOR, "#mw-numeric-code-input-prevent-composer-focus-steal"),
            (By.CSS_SELECTOR, 'input[aria-label="PIN"][maxlength="6"]'),
            (By.CSS_SELECTOR, 'input[autocomplete="one-time-code"][maxlength="6"]'),
            (By.CSS_SELECTOR, 'input[maxlength="6"][type="text"]'),
            (By.CSS_SELECTOR, 'input[inputmode="numeric"][maxlength="6"]'),
        ]

        for by, selector in selectors:
            items = _visible_elements(driver, by, selector)
            if items:
                return items[-1]
        return None

    def has_six_pin_boxes() -> bool:
        """
        僅偵測真正的六格 PIN 方框。
        不再因一般頁面出現 PIN 文字就判定。
        """
        try:
            return bool(driver.execute_script(
                """
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return (
                        r.width > 0 &&
                        r.height > 0 &&
                        s.display !== 'none' &&
                        s.visibility !== 'hidden'
                    );
                };

                const boxes = Array.from(document.querySelectorAll('div'))
                    .filter(el => {
                        if (!visible(el)) return false;

                        const r = el.getBoundingClientRect();
                        const txt = (el.innerText || '').trim();

                        return (
                            r.width >= 35 && r.width <= 85 &&
                            r.height >= 40 && r.height <= 85 &&
                            (txt === '-' || txt === '–' || txt === '')
                        );
                    });

                // 六格需集中在同一水平區域，避免一般頁面的小方塊誤判。
                for (let i = 0; i < boxes.length; i++) {
                    const first = boxes[i].getBoundingClientRect();
                    const sameRow = boxes.filter(el => {
                        const r = el.getBoundingClientRect();
                        return (
                            Math.abs(r.top - first.top) <= 20 &&
                            Math.abs(r.height - first.height) <= 15
                        );
                    });

                    if (sameRow.length >= 6) {
                        return true;
                    }
                }

                return false;
                """
            ))
        except Exception:
            return False

    def is_existing_pin_screen() -> bool:
        """
        已有 PIN／確認 PIN 畫面。
        Create PIN 按鈕頁不算輸入畫面。
        """
        text = body_text()

        markers = (
            "輸入 PIN 碼以還原聊天紀錄",
            "確認 PIN 碼以免遺失聊天紀錄",
            "建立 PIN 碼以免遺失聊天紀錄",
            "Enter your PIN",
            "Confirm your PIN",
            "Re-enter your PIN",
            "Enter PIN again",
            "restore your chat history",
            "avoid losing chat history",
            "Kumpirmahin ang iyong PIN",
            "Saisissez votre code PIN",
            "Confirmez votre code PIN",
            "Saisissez à nouveau votre code PIN",
            "ป้อน PIN ของคุณ",
            "ยืนยัน PIN ของคุณ",
            "ป้อน PIN อีกครั้ง",
            "أدخل رمز PIN",
            "تأكيد رمز PIN",
            "أعد إدخال رمز PIN",
            "استعادة سجل الدردشة",
            "تجنب فقدان سجل الدردشة",
        )

        return (
            get_pin_input() is not None
            or (
                has_six_pin_boxes()
                and any(marker in text for marker in markers)
            )
        )

    def send_pin_to_real_screen(stage_name: str) -> bool:
        """
        只有已確認目前是 PIN 輸入畫面後才會呼叫。

        不使用 active_element。
        優先送到真正 input；若沒有 input，直接使用全域鍵盤事件。
        """
        pin_input = get_pin_input()

        if pin_input is not None:
            try:
                pin_input.clear()
            except Exception:
                pass

            try:
                pin_input.send_keys(pin)
                logger.info(
                    "[%s] 已%s輸入 PIN：%s（實際 input）",
                    profile_name,
                    stage_name,
                    pin,
                )
                return True
            except Exception as exc:
                logger.warning(
                    "[%s] %s透過實際 input 輸入失敗：%s",
                    profile_name,
                    stage_name,
                    exc,
                )

        # 六格 PIN 畫面通常已自動聚焦，直接送全域鍵盤，不點任何格子。
        try:
            ActionChains(driver).send_keys(pin).perform()
            logger.info(
                "[%s] 已%s輸入 PIN：%s（全域鍵盤）",
                profile_name,
                stage_name,
                pin,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[%s] %s輸入 PIN 失敗：%s",
                profile_name,
                stage_name,
                exc,
            )
            return False

    def click_text_button(xpath: str, log_name: str, timeout: int) -> bool:
        labels_by_name = {
            "立即修正": [
                "立即修正", "fix now", "ayusin ngayon",
                "corriger maintenant", "แก้ไขเลย", "إصلاح الآن",
            ],
            "備份聊天內容": [
                "備份聊天內容", "back up chats", "back up chat history",
                "i-back up ang mga chat",
                "สำรองข้อมูลแชท", "نسخ المحادثات احتياطيًا",
            ],
            "Create PIN": [
                "create pin", "建立 pin", "建立個人識別碼",
                "gumawa ng pin", "créer un code pin",
                "สร้าง pin", "إنشاء رمز pin",
            ],
        }
        labels = labels_by_name.get(log_name, [log_name.lower()])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                candidate = driver.execute_script(
                    """
                    const wanted=arguments[0];
                    const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                    const visible=el=>{
                        const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                        return r.width>1&&r.height>1&&r.bottom>0&&
                               s.display!=='none'&&s.visibility!=='hidden';
                    };
                    const nodes=[...document.querySelectorAll(
                        'button,[role="button"],a,[role="menuitem"],span,div'
                    )].filter(visible);
                    const matches=[];
                    for(const el of nodes){
                        if(!visible(el))continue;
                        const t=norm(el.getAttribute('aria-label')||el.innerText||el.textContent);
                        if(wanted.some(w=>t===w||t.includes(w))){
                            const clickable=el.closest(
                                'button,[role="button"],a,[role="menuitem"],[tabindex="0"]'
                            );
                            if(!clickable||!visible(clickable))continue;
                            const own=norm(el.getAttribute('aria-label')||el.innerText||el.textContent);
                            const ct=norm(
                                clickable.getAttribute('aria-label')||
                                clickable.innerText||clickable.textContent
                            );
                            const exact=wanted.some(w=>own===w||ct===w)?0:1;
                            matches.push({
                                el:clickable,
                                score:exact*100000+ct.length,
                                depth:(()=>{
                                    let d=0,n=el;
                                    while(n&&n!==clickable){d++;n=n.parentElement;}
                                    return d;
                                })()
                            });
                        }
                    }
                    if(!matches.length)return null;
                    matches.sort((a,b)=>a.score-b.score||a.depth-b.depth);
                    return matches[0].el;
                    """,
                    labels,
                )
                if candidate:
                    clicked = driver.execute_script(
                        """
                        const el=arguments[0];
                        el.scrollIntoView({block:'center',inline:'center'});
                        try{el.focus({preventScroll:true});}catch(e){}
                        const r=el.getBoundingClientRect();
                        const x=r.left+r.width/2,y=r.top+r.height/2;
                        const opts={bubbles:true,cancelable:true,view:window,
                                    clientX:x,clientY:y,button:0};
                        for(const type of ['pointerdown','mousedown','pointerup','mouseup']){
                            try{el.dispatchEvent(new MouseEvent(type,opts));}catch(e){}
                        }
                        try{el.click();}catch(e){}
                        return {
                            tag:el.tagName,
                            role:el.getAttribute('role')||'',
                            aria:el.getAttribute('aria-label')||'',
                            text:(el.innerText||el.textContent||'').replace(/\\s+/g,' ').trim()
                        };
                        """,
                        candidate,
                    )
                    logger.info(
                        "[%s] 已點擊真正的「%s」按鈕：%s",
                        profile_name,
                        log_name,
                        clicked,
                    )
                    time.sleep(0.5)
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def has_text_button(log_name: str) -> bool:
        labels_by_name = {
            "立即修正": [
                "立即修正", "fix now", "ayusin ngayon",
                "corriger maintenant", "แก้ไขเลย", "إصلاح الآن",
            ],
            "備份聊天內容": [
                "備份聊天內容", "back up chats", "back up chat history",
                "i-back up ang mga chat",
                "sauvegarder les discussions",
                "สำรองข้อมูลแชท", "نسخ المحادثات احتياطيًا",
            ],
            "Create PIN": [
                "create pin", "建立 pin", "建立個人識別碼",
                "gumawa ng pin", "créer un code pin",
                "สร้าง pin", "إنشاء رمز pin",
            ],
        }
        try:
            return bool(driver.execute_script(
                """
                const wanted=arguments[0];
                const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                return [...document.querySelectorAll(
                    'button,[role="button"],a,[role="menuitem"],span,div'
                )].some(el=>{
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    if(r.width<1||r.height<1||s.display==='none'||s.visibility==='hidden')return false;
                    const t=norm(el.getAttribute('aria-label')||el.innerText||el.textContent);
                    if(!wanted.some(w=>t===w||t.includes(w)))return false;
                    return !!el.closest(
                        'button,[role="button"],a,[role="menuitem"],[tabindex="0"]'
                    );
                });
                """,
                labels_by_name[log_name],
            ))
        except Exception:
            return False

    def close_chat_popups() -> None:
        closed_count = 0
        close_terms = (
            "關閉聊天", "關閉與", "close chat", "close conversation with",
            "fermer la discussion", "fermer la conversation",
            "isara ang chat", "ปิดแชท", "ปิดการสนทนา",
            "إغلاق الدردشة", "إغلاق المحادثة",
        )

        for _ in range(5):
            buttons = driver.execute_script(
                """
                const terms=arguments[0].map(x=>String(x).toLowerCase());
                return [...document.querySelectorAll('[role="button"][aria-label]')]
                    .filter(el=>{
                        const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                        if(r.width<1||r.height<1||s.display==='none'||
                           s.visibility==='hidden') return false;
                        const label=(el.getAttribute('aria-label')||'')
                            .replace(/\\s+/g,' ').trim().toLowerCase();
                        return terms.some(term=>label===term||label.startsWith(term+' '));
                    });
                """,
                list(close_terms),
            )
            if not buttons:
                break

            for button in buttons:
                try:
                    driver.execute_script("arguments[0].click();", button)
                    closed_count += 1
                    time.sleep(0.3)
                except Exception:
                    continue

        if closed_count:
            logger.info(
                "[%s] 已關閉 %s 個右下角聊天 Popup。",
                profile_name,
                closed_count,
            )

    # ------------------------------------------------------------
    # 快速回首頁
    # ------------------------------------------------------------

    try:
        driver.execute_script("window.location.href='https://www.facebook.com/';")
        WebDriverWait(driver, 15).until(
            lambda d: (
                "facebook.com" in d.current_url.lower()
                and len(d.find_elements(By.TAG_NAME, "body")) > 0
            )
        )
        time.sleep(3)
        logger.info("[%s] 已快速回到 Facebook 首頁，準備處理聊天室 PIN。", profile_name)
    except Exception as exc:
        try:
            if "facebook.com" in driver.current_url.lower():
                logger.warning(
                    "[%s] 回首頁等待逾時，但目前仍在 Facebook，繼續處理：%s",
                    profile_name,
                    exc,
                )
            else:
                logger.warning("[%s] 無法回到 Facebook 首頁：%s", profile_name, exc)
                return False
        except Exception:
            logger.warning("[%s] 無法回到 Facebook 首頁：%s", profile_name, exc)
            return False

    close_chat_popups()

    # ------------------------------------------------------------
    # 點真正的頂部 Messenger
    # ------------------------------------------------------------

    messenger = None
    messenger_terms = (
        "messenger", "聊天室", "mga chat", "การแชท",
        "الدردشات", "ماسنجر",
    )
    excluded_messenger_terms = (
        "open chat with", "close chat", "開啟與", "關閉聊天",
        "ouvrir la discussion avec", "fermer la discussion",
        "buksan ang chat kay", "isara ang chat",
        "เปิดแชทกับ", "ปิดแชท",
        "فتح دردشة مع", "إغلاق الدردشة",
    )

    for attempt in range(1, 3):
        items = driver.execute_script(
            """
            const wanted=arguments[0].map(x=>String(x).toLowerCase());
            const excluded=arguments[1].map(x=>String(x).toLowerCase());
            return [...document.querySelectorAll('[role="button"][aria-label]')]
                .filter(el=>{
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    if(r.width<1||r.height<1||r.top>150||
                       s.display==='none'||s.visibility==='hidden') return false;
                    const label=(el.getAttribute('aria-label')||'')
                        .replace(/\\s+/g,' ').trim().toLowerCase();
                    return wanted.some(word=>label===word||label.startsWith(word+' ')) &&
                           !excluded.some(word=>label.includes(word));
                });
            """,
            list(messenger_terms),
            list(excluded_messenger_terms),
        )
        top_items = []

        for item in items:
            try:
                rect = driver.execute_script(
                    """
                    const r = arguments[0].getBoundingClientRect();
                    return {top:r.top, left:r.left, width:r.width, height:r.height};
                    """,
                    item,
                )
                if rect["top"] <= 150:
                    top_items.append(item)
            except Exception:
                continue

        if top_items:
            messenger = top_items[-1]
            break

        if attempt == 1:
            logger.warning(
                "[%s] 第一次找不到頂部 Messenger 按鈕，短暫等待後再試。",
                profile_name,
            )
            time.sleep(3)

    if messenger is None:
        logger.warning("[%s] 找不到真正的頂部 Messenger 按鈕。", profile_name)
        return False

    try:
        aria_label = messenger.get_attribute("aria-label") or ""
        _click_closest_button(driver, messenger)
        logger.info(
            "[%s] 已點擊頂部 Messenger：aria-label=%r",
            profile_name,
            aria_label,
        )
    except Exception as exc:
        logger.warning("[%s] Messenger 按鈕點擊失敗：%s", profile_name, exc)
        return False

    # ------------------------------------------------------------
    # 各種畫面定位
    # ------------------------------------------------------------

    fix_now_xpath = (
        "//*[@role='button' or self::button or self::div or self::span]"
        "[contains(normalize-space(.),'立即修正') "
        "or contains(normalize-space(.),'Fix now') "
        "or contains(normalize-space(.),'Ayusin ngayon')]"
    )

    backup_chats_xpath = (
        "//*[@role='button' or self::button or self::div or self::span]"
        "[contains(normalize-space(.),'備份聊天內容') "
        "or contains(normalize-space(.),'Back up chats') "
        "or contains(normalize-space(.),'Back up chat history') "
        "or contains(normalize-space(.),'I-back up ang mga chat')]"
    )

    create_pin_xpath = (
        "//*[self::span or self::div or self::button]"
        "[normalize-space()='Create PIN' "
        "or normalize-space()='建立 PIN' "
        "or normalize-space()='建立個人識別碼' "
        "or normalize-space()='Gumawa ng PIN' "
        "or normalize-space()='Créer un code PIN' "
        "or normalize-space()='สร้าง PIN' "
        "or normalize-space()='إنشاء رمز PIN']"
    )

    confirm_markers = (
        "Confirm your PIN",
        "Confirm PIN",
        "Re-enter your PIN",
        "Enter PIN again",
        "確認你的 PIN",
        "確認 PIN 碼以免遺失聊天紀錄",
        "再次輸入 PIN",
        "重新輸入 PIN",
        "Kumpirmahin ang iyong PIN",
        "ยืนยัน PIN ของคุณ",
        "تأكيد رمز PIN",
    )

    def wait_for_confirm_screen(timeout: int = 20) -> bool:
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: (
                    any(marker in body_text() for marker in confirm_markers)
                    and is_existing_pin_screen()
                )
            )
            return True
        except Exception:
            return False

    def wait_pin_screen(timeout: int = 15) -> bool:
        try:
            WebDriverWait(driver, timeout).until(
                lambda d: is_existing_pin_screen()
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------
    # 優先順序 1：立即修正
    # ------------------------------------------------------------

    if has_text_button("立即修正"):
        if not click_text_button(fix_now_xpath, "立即修正", timeout=5):
            logger.warning("[%s] 無法點擊立即修正。", profile_name)
            return False

        try:
            WebDriverWait(driver, 8).until(lambda d: has_text_button("備份聊天內容"))
        except Exception:
            logger.warning(
                "[%s] 已點擊立即修正，但沒有出現「備份聊天內容」。",
                profile_name,
            )
            return False

        if not click_text_button(backup_chats_xpath, "備份聊天內容", timeout=8):
            logger.warning("[%s] 無法點擊「備份聊天內容」。", profile_name)
            return False

        if not wait_pin_screen(timeout=15):
            logger.warning("[%s] 點擊備份聊天內容後沒有出現 PIN 畫面。", profile_name)
            return False

        if not send_pin_to_real_screen("第一次"):
            return False

        if not wait_for_confirm_screen(timeout=20):
            logger.warning("[%s] 第一次輸入 PIN 後沒有進入確認頁。", profile_name)
            return False

        if not send_pin_to_real_screen("第二次"):
            return False

        logger.info("[%s] 立即修正流程已完成兩次 PIN 輸入。", profile_name)
        time.sleep(2)
        return True

    # ------------------------------------------------------------
    # 優先順序 2：Create PIN
    # ------------------------------------------------------------

    if has_text_button("Create PIN"):
        if not click_text_button(create_pin_xpath, "Create PIN", timeout=5):
            logger.warning("[%s] 無法點擊 Create PIN。", profile_name)
            return False

        if not wait_pin_screen(timeout=15):
            logger.warning("[%s] 點擊 Create PIN 後沒有出現 PIN 畫面。", profile_name)
            return False

        if not send_pin_to_real_screen("第一次"):
            return False

        if not wait_for_confirm_screen(timeout=20):
            logger.warning("[%s] 第一次輸入 PIN 後沒有進入確認頁。", profile_name)
            return False

        if not send_pin_to_real_screen("第二次"):
            return False

        logger.info("[%s] Create PIN 流程已完成兩次 PIN 輸入。", profile_name)
        time.sleep(2)
        return True

    # ------------------------------------------------------------
    # 優先順序 3：真正存在的 PIN 六格／輸入欄位
    # ------------------------------------------------------------

    if is_existing_pin_screen():
        if not send_pin_to_real_screen("一次"):
            return False

        logger.info(
            "[%s] 已在既有 PIN／確認 PIN 畫面輸入一次 123789。",
            profile_name,
        )
        time.sleep(2)
        return True

    # ------------------------------------------------------------
    # 最後等待：Messenger 選單穩定後持續掃描 8 秒
    # 不再重新回首頁、不再重開 Messenger、不再遞迴呼叫自己。
    # ------------------------------------------------------------

    pin_discovery_wait = 8
    logger.info(
        "[%s] Messenger 已開啟，等待 PIN 相關畫面，最長 %s 秒。",
        profile_name,
        pin_discovery_wait,
    )

    deadline = time.time() + pin_discovery_wait

    while time.time() < deadline:
        # 1. 小字「立即修正」優先
        if has_text_button("立即修正"):
            if not click_text_button(fix_now_xpath, "立即修正", timeout=3):
                logger.warning("[%s] 偵測到立即修正，但無法點擊。", profile_name)
                return False

            try:
                WebDriverWait(driver, 8).until(
                    lambda d: has_text_button("備份聊天內容")
                )
            except Exception:
                logger.warning(
                    "[%s] 已點擊立即修正，但沒有出現「備份聊天內容」。",
                    profile_name,
                )
                return False

            if not click_text_button(backup_chats_xpath, "備份聊天內容", timeout=5):
                logger.warning("[%s] 無法點擊「備份聊天內容」。", profile_name)
                return False

            if not wait_pin_screen(timeout=15):
                logger.warning("[%s] 點擊備份聊天內容後沒有出現 PIN 畫面。", profile_name)
                return False

            if not send_pin_to_real_screen("第一次"):
                return False

            if not wait_for_confirm_screen(timeout=20):
                logger.warning("[%s] 第一次輸入 PIN 後沒有進入確認頁。", profile_name)
                return False

            if not send_pin_to_real_screen("第二次"):
                return False

            logger.info("[%s] 立即修正流程已完成兩次 PIN 輸入。", profile_name)
            time.sleep(2)
            return True

        # 2. Create PIN
        if has_text_button("Create PIN"):
            if not click_text_button(create_pin_xpath, "Create PIN", timeout=3):
                logger.warning("[%s] 偵測到 Create PIN，但無法點擊。", profile_name)
                return False

            if not wait_pin_screen(timeout=15):
                logger.warning("[%s] 點擊 Create PIN 後沒有出現 PIN 畫面。", profile_name)
                return False

            if not send_pin_to_real_screen("第一次"):
                return False

            if not wait_for_confirm_screen(timeout=20):
                logger.warning("[%s] 第一次輸入 PIN 後沒有進入確認頁。", profile_name)
                return False

            if not send_pin_to_real_screen("第二次"):
                return False

            logger.info("[%s] Create PIN 流程已完成兩次 PIN 輸入。", profile_name)
            time.sleep(2)
            return True

        # 3. 已有 PIN／確認 PIN 畫面
        if is_existing_pin_screen():
            if not send_pin_to_real_screen("一次"):
                return False

            logger.info(
                "[%s] 已在既有 PIN／確認 PIN 畫面輸入一次 123789。",
                profile_name,
            )
            time.sleep(2)
            return True

        time.sleep(0.5)

    # ------------------------------------------------------------
    # 優先順序 4：全部沒有，正常跳過
    # ------------------------------------------------------------

    logger.info(
        "[%s] 等待 8 秒後仍沒有立即修正、Create PIN 或 PIN 輸入畫面；"
        "此帳號不需要處理 PIN，直接跳過。",
        profile_name,
    )
    return True


def change_facebook_avatar(
    driver: webdriver.Chrome,
    profile: Profile,
    image_path: Path | None,
    enable_avatar: bool = True,
    enable_pin: bool = True,
) -> tuple[bool | None, bool | None]:
    """獨立執行頭像與 PIN；其中一項失敗不會阻止另一項。"""
    avatar_ok: bool | None = None
    pin_ok: bool | None = None

    if enable_avatar:
        try:
            if image_path is None:
                logger.warning("[%s] 已啟用換頭像，但沒有可使用的圖片。", profile.name)
                avatar_ok = False
            elif not switch_to_facebook_tab(driver, profile.name):
                avatar_ok = False
            elif not click_profile_entry(driver, profile.name):
                avatar_ok = False
            elif not open_avatar_editor(driver, profile.name):
                avatar_ok = False
            elif not upload_avatar_file(driver, image_path, profile.name):
                avatar_ok = False
            elif not save_avatar(driver, profile.name):
                avatar_ok = False
            else:
                avatar_ok = True
                logger.info("[%s] Facebook 頭像更換成功。", profile.name)
        except Exception as exc:
            avatar_ok = False
            logger.exception("[%s] Facebook 頭像更換發生錯誤，但仍繼續執行 PIN：%s", profile.name, exc)

        if avatar_ok is False:
            logger.warning("[%s] Facebook 頭像更換未完成，但仍繼續執行 PIN。", profile.name)
    else:
        logger.info("[%s] 自動更換 Facebook 頭像已停用。", profile.name)

    if enable_pin:
        try:
            pin_ok = create_messenger_pin(driver, profile.name, CHAT_PIN)
        except Exception as exc:
            pin_ok = False
            logger.exception("[%s] Messenger PIN 處理發生錯誤：%s", profile.name, exc)

        if pin_ok:
            logger.info("[%s] Messenger PIN 處理完成。", profile.name)
        else:
            logger.warning("[%s] Messenger 聊天室 PIN 建立失敗，但仍繼續後續養號。", profile.name)
    else:
        logger.info("[%s] Messenger PIN 已停用。", profile.name)

    return avatar_ok, pin_ok

def process_profile(profile: Profile) -> bool:
    logger.info("=" * 60)
    logger.info("[%s] 開始處理 Profile。", profile.name)

    image_path = find_matching_image(profile.name)
    if image_path is None:
        return False

    driver: webdriver.Chrome | None = None

    try:
        driver, _ = start_adspower_browser(profile)
        avatar_ok, pin_ok = change_facebook_avatar(driver, profile, image_path)
        return avatar_ok is not False and pin_ok is not False

    except requests.RequestException as exc:
        logger.error("[%s] AdsPower API 連線錯誤：%s", profile.name, exc)
        return False
    except WebDriverException as exc:
        logger.error("[%s] Selenium 錯誤：%s", profile.name, exc)
        return False
    except Exception as exc:
        logger.exception("[%s] 更換頭像發生錯誤：%s", profile.name, exc)
        return False
    finally:
        if driver is not None:
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
        logger.info("[%s] 操作完成，AdsPower Browser 保持開啟。", profile.name)


def main() -> None:
    print("=" * 62)
    print(" AdsPower Facebook 換頭像＋Messenger PIN｜V2.4.3 Stable")
    print("=" * 62)
    print(f"AdsPower API：{ADSPOWER_BASE_URL}")
    print(f"圖片資料夾：{IMAGE_FOLDER}")
    print(f"LOG：{LOG_FILE}")

    if not IMAGE_FOLDER.exists():
        print(f"\n❌ 找不到圖片資料夾：{IMAGE_FOLDER}")
        print("請先建立資料夾，並放入例如 33.jpg、34.jpg、001.jpg。")
        input("\n按 Enter 結束...")
        return

    try:
        profiles = get_all_profiles()
    except Exception as exc:
        logger.exception("讀取 AdsPower Profile 失敗：%s", exc)
        input("\n按 Enter 結束...")
        return

    if not profiles:
        print("\n❌ 沒有讀取到任何 Profile。")
        input("\n按 Enter 結束...")
        return

    selected_profiles = choose_profiles(profiles)
    if not selected_profiles:
        print("已取消執行。")
        return

    success_count = 0
    failed_count = 0

    for profile in selected_profiles:
        if process_profile(profile):
            success_count += 1
        else:
            failed_count += 1

    print("\n" + "=" * 62)
    print("試跑完成")
    print(f"成功：{success_count}")
    print(f"失敗／跳過：{failed_count}")
    print(f"LOG：{LOG_FILE}")
    print("=" * 62)
    input("\n按 Enter 結束...")


if __name__ == "__main__":
    main()
