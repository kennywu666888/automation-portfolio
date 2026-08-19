# -*- coding: utf-8 -*-
"""
Facebook 搜尋工具箱 V1.0 Ultimate
Part 5.4.1：Core Functions Restored Stable

本階段完成：
1. 共用 AdsPower 設定區
2. AdsPower 環境讀取與多選
3. Group 網址收集頁籤 GUI
4. KOL 網址收集頁籤 GUI
5. KOL TELEGRAM 收集器（複製 KOL 網址收集功能）
6. 共用狀態列、Treeview、ProgressBar、LOG
7. 執行狀態互鎖架構
8. 安全停止與關閉流程

注意：
- Part 2 已接入 Group V1.1 10Plus Stable。
- Part 3 已接入 KOL People 搜尋、粉絲數解析、網址清理與多環境並行。
- Part 4 已加入 KOL 首頁最新貼文日期解析；只有符合最近發文天數才寫入 kolurl.txt。
"""

from __future__ import annotations

import csv
import os
import queue
import re
import threading
import time
import tkinter as tk
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Set, Tuple, Any
from urllib.parse import parse_qs, quote, urljoin, urlparse, urlunparse

import requests
from playwright.sync_api import Browser, Page, sync_playwright


# ============================================================
# 基本設定
# ============================================================
APP_TITLE = "Facebook 搜尋工具箱 V1.0 Ultimate"
APP_VERSION = "Part 5.4.1 Core Functions Restored"

ADSPOWER_API = "http://local.adspower.net:50325/api/v1"
DEFAULT_ADSPOWER_GROUP_ID = "10085779"

DESKTOP = Path.home() / "Desktop"
GROUP_OUTPUT_FILE = DESKTOP / "group.txt"
KOL_OUTPUT_FILE = DESKTOP / "kolurl.txt"

GROUP_KEYWORDS_FILE = DESKTOP / "group_keywords.txt"
KOL_KEYWORDS_FILE = DESKTOP / "kol_keywords.txt"

GROUP_LOG_DIR = DESKTOP / "Group_URL_LOG"
KOL_LOG_DIR = DESKTOP / "KOL_URL_LOG"

GROUP_RUN_LOG = GROUP_LOG_DIR / "group_collector.log"
GROUP_KEYWORD_HISTORY_FILE = GROUP_LOG_DIR / "group_keyword_history.txt"
GROUP_DETAILS_FILE = GROUP_LOG_DIR / "group_details.csv"
GROUP_GENERATED_KEYWORDS_FILE = GROUP_LOG_DIR / "generated_group_keywords.txt"
KOL_RUN_LOG = KOL_LOG_DIR / "kol_collector.log"
KOL_DETAILS_FILE = KOL_LOG_DIR / "kol_details.csv"
KOL_FAILED_FILE = KOL_LOG_DIR / "kol_failed.csv"
KOL_KEYWORD_HISTORY_FILE = KOL_LOG_DIR / "kol_keyword_history.txt"
KOL_GENERATED_KEYWORDS_FILE = KOL_LOG_DIR / "generated_kol_keywords.txt"
KOL_DATE_DEBUG_DIR = KOL_LOG_DIR / "kol_date_debug"
KOL_CHECKED_FILE = KOL_LOG_DIR / "kol_checked.csv"
KOL_DIAGNOSTICS_DIR = KOL_LOG_DIR / "diagnostics"
KOL_FOLLOWER_DIAGNOSTICS_DIR = KOL_LOG_DIR / "follower_diagnostics"

REQUEST_TIMEOUT = 25
API_RETRIES = 5
GROUP_KEYWORD_HISTORY_HOURS = 24
GROUP_NO_GROWTH_LIMIT = 4
GROUP_BETWEEN_KEYWORDS_SECONDS = 3
GROUP_BROWSER_START_RETRIES = 10
GROUP_BROWSER_START_WAIT = 5
KOL_KEYWORD_HISTORY_HOURS = 24
KOL_NO_GROWTH_LIMIT = 8
KOL_BETWEEN_KEYWORDS_SECONDS = 4
KOL_BROWSER_START_RETRIES = 10
KOL_BROWSER_START_WAIT = 5
KOL_DEFAULT_MAX_SCROLLS = 60
KOL_DEFAULT_SCROLL_DISTANCE = 1800
KOL_DEFAULT_SCROLL_WAIT_MS = 2500
KOL_PH_TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=PH"
KOL_MAX_TREND_KEYWORDS = 40
KOL_FAST_DATE_TIMEOUT_SECONDS = 8  # 原本 5 秒；因新增「等貼文容器出現內容」最多多花 2.5 秒，一併放寬預算
KOL_FAST_DATE_MAX_LINKS = 120


# ============================================================
# 資料類別
# ============================================================
@dataclass
class AdsPowerProfile:
    user_id: str
    name: str
    group_name: str


class KolFastDateTimeout(Exception):
    """快速日期分析超過限制時間。"""


@dataclass
class KolDateResult:
    success: bool
    post_date: Optional[datetime]
    raw_text: str
    source: str
    days_old: Optional[int]
    reason: str
    candidates: List[str]


@dataclass
class RuntimeStats:
    keyword_total: int = 0
    processed: int = 0
    found: int = 0
    added: int = 0
    failed: int = 0

    def reset(self) -> None:
        self.keyword_total = 0
        self.processed = 0
        self.found = 0
        self.added = 0
        self.failed = 0


# ============================================================
# 共用工具
# ============================================================
def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_paths() -> None:
    GROUP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    KOL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    KOL_DATE_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    KOL_DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    KOL_FOLLOWER_DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)

    GROUP_OUTPUT_FILE.touch(exist_ok=True)
    KOL_OUTPUT_FILE.touch(exist_ok=True)
    GROUP_RUN_LOG.touch(exist_ok=True)
    KOL_RUN_LOG.touch(exist_ok=True)


def count_nonempty_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(
            1
            for line in path.read_text(
                encoding="utf-8",
                errors="ignore",
            ).splitlines()
            if line.strip()
        )
    except Exception:
        return 0


def read_nonempty_lines(path: Path) -> List[str]:
    if not path.exists():
        return []

    try:
        return [
            line.strip()
            for line in path.read_text(
                encoding="utf-8",
                errors="ignore",
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except Exception:
        return []


def append_csv_rows(
    path: Path,
    header: List[str],
    rows: List[List[object]],
    lock: "threading.RLock",
    fsync: bool = False,
) -> None:
    """
    共用 CSV 附加寫入（優化項目 5）。

    原本 group_append_results / kol_append_verified / kol_record_checked /
    kol_record_failed 四處各自重複「檔案不存在或為空就先寫 header，
    再開檔寫入」的樣板，合併成這一個函式，行為完全相同：
    - 用 utf-8-sig 開檔（Excel 開啟中文不會亂碼），
    - 檔案不存在/大小為 0 時，先寫入 header，
    - 一次寫入多列 rows，
    - 預設只 flush，不強制 fsync（見優化項目 2：高頻寫入的明細/紀錄檔
      不需要每筆都強制落盤；只有最終產出的網址清單才需要 fsync=True）。
    """
    if not rows:
        return

    with lock:
        is_new = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            if is_new:
                writer.writerow(header)
            writer.writerows(rows)
            file.flush()
            if fsync:
                os.fsync(file.fileno())


def open_path(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()

    try:
        os.startfile(str(path))
    except AttributeError:
        messagebox.showinfo("檔案位置", str(path))
    except Exception as exc:
        messagebox.showerror("開啟失敗", str(exc))


def api_get(
    endpoint: str,
    params: Optional[Dict[str, object]] = None,
) -> Optional[dict]:
    url = f"{ADSPOWER_API}/{endpoint.lstrip('/')}"

    for attempt in range(1, API_RETRIES + 1):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            data = response.json()

            if data.get("code") == 0:
                return data

            message = str(data.get("msg", "未知錯誤"))
            lower_message = message.casefold()

            if "too many" in lower_message and attempt < API_RETRIES:
                time.sleep(2 + attempt)
                continue

            return data

        except Exception:
            if attempt < API_RETRIES:
                time.sleep(2)

    return None


def get_ads_profiles(group_id: str) -> List[AdsPowerProfile]:
    profiles: List[AdsPowerProfile] = []
    page_number = 1

    while True:
        params: Dict[str, object] = {
            "page": page_number,
            "page_size": 100,
        }
        if group_id:
            params["group_id"] = group_id

        data = api_get("user/list", params)
        if not data or data.get("code") != 0:
            break

        items = (data.get("data") or {}).get("list") or []

        for item in items:
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                continue

            profiles.append(
                AdsPowerProfile(
                    user_id=user_id,
                    name=str(item.get("name") or user_id).strip(),
                    group_name=str(
                        item.get("group_name") or "未分組"
                    ).strip(),
                )
            )

        if len(items) < 100:
            break

        page_number += 1
        time.sleep(0.7)

    return profiles



# ============================================================
# Part 2：Group 收集引擎
# ============================================================
GROUP_URL_RE = re.compile(
    r"https?://(?:www\.|web\.|m\.)?facebook\.com/groups/"
    r"(?P<group_id>[A-Za-z0-9_.-]+)",
    re.I,
)

GROUP_EXCLUDED_PATHS = {
    "feed", "discover", "joins", "create", "search", "yourgroups",
    "notifications", "membership", "admin", "suggested",
}

GROUP_SUBPATHS = {
    "posts", "permalink", "media", "photos", "videos", "members",
    "about", "events", "files", "search", "admin_activities",
    "pending_posts", "buy_sell_discussion",
}

GROUP_TODAY_WORDS = (
    "today", "new post", "new posts", "posts today", "post today",
    "a day", "per day", "daily", "ngayon", "post ngayon",
    "mga post ngayon", "araw na ito", "ngayong araw", "hari ini",
    "hôm nay", "วันนี้", "今日", "今天", "本日", "오늘", "сегодня",
    "hoy", "aujourd'hui", "aujourd’hui", "heute", "oggi",
)

GROUP_POST_WORDS = (
    "post", "posts", "mga post", "貼文", "帖子", "条帖子", "則貼文",
    "篇貼文", "投稿", "게시물", "публикац", "publicacion",
    "publicación", "publication", "beitrag", "bài viết", "โพสต์",
)

# 注意：這三把鎖改用 RLock（可重入鎖）。
# 因為 append_csv_rows() 共用 helper 內部也會取用同一把鎖，
# 而部分呼叫者（如 group_append_results）本身已經在外層持有該鎖，
# 一般 Lock 在同一執行緒重複取用會直接死鎖，RLock 才允許同執行緒重入。
group_output_lock = threading.RLock()
group_history_lock = threading.RLock()
group_log_lock = threading.RLock()


def group_write_log(message: str) -> None:
    line = f"[{now_text()}] {message}"
    print(line)
    with group_log_lock:
        with GROUP_RUN_LOG.open("a", encoding="utf-8") as file:
            file.write(line + "\n")


def group_normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def group_parse_compact_number(raw: str) -> Optional[int]:
    if not raw:
        return None
    value = raw.strip().replace(",", "").replace("，", "")
    value = value.rstrip("+").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kKmM萬万]?)", value)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = 1
    if unit == "k":
        multiplier = 1_000
    elif unit == "m":
        multiplier = 1_000_000
    elif unit in ("萬", "万"):
        multiplier = 10_000
    return int(number * multiplier)


def group_contains_activity_words(text: str) -> bool:
    lower = group_normalize_space(text).casefold()
    return (
        any(word.casefold() in lower for word in GROUP_POST_WORDS)
        and any(word.casefold() in lower for word in GROUP_TODAY_WORDS)
    )


def group_extract_today_post_count(text: str) -> Optional[int]:
    cleaned = group_normalize_space(text)
    if not cleaned or not group_contains_activity_words(cleaned):
        return None

    lower = cleaned.casefold()
    number_token = r"(\d+(?:[.,]\d+)?\s*[kKmM萬万]?\s*\+?)"
    post_token = (
        r"(?:posts?|mga\s+post|貼文|帖子|条帖子|則貼文|篇貼文|"
        r"投稿|게시물|публикац\w*|publicaci[oó]n(?:es)?|"
        r"publications?|beitr[aä]ge?|bài\s+viết|โพสต์)"
    )
    today_token = (
        r"(?:today|a\s+day|per\s+day|daily|ngayon|araw\s+na\s+ito|"
        r"ngayong\s+araw|hari\s+ini|hôm\s+nay|วันนี้|今日|今天|本日|"
        r"오늘|сегодня|hoy|aujourd['’]hui|heute|oggi)"
    )

    patterns = [
        rf"{number_token}\s*(?:new\s+)?{post_token}\s*(?:•|-|·)?\s*{today_token}",
        rf"{number_token}\s*(?:new\s+)?{post_token}\s+(?:a|per)\s+day",
        rf"{today_token}\s*(?:may|有|共有|新增)?\s*{number_token}\s*(?:new\s+)?{post_token}",
        rf"{number_token}\s*(?:new\s+)?{post_token}.*?{today_token}",
        rf"{today_token}.*?{number_token}\s*(?:new\s+)?{post_token}",
    ]

    counts: List[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, lower, flags=re.I):
            count = group_parse_compact_number(match.group(1))
            if count is not None:
                counts.append(count)
    return max(counts) if counts else None


def group_normalize_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None
    raw_url = raw_url.strip()
    if raw_url.startswith("/"):
        raw_url = "https://www.facebook.com" + raw_url

    if "l.facebook.com/l.php" in raw_url:
        parsed = urlparse(raw_url)
        target = parse_qs(parsed.query).get("u", [None])[0]
        if target:
            raw_url = target

    match = GROUP_URL_RE.search(raw_url)
    if not match:
        return None

    group_id = match.group("group_id").strip()
    if not group_id or group_id.casefold() in GROUP_EXCLUDED_PATHS:
        return None
    return f"https://www.facebook.com/groups/{group_id}/"



# 效能優化（優化項目 1）：
# 舊版 group_nearest_result_container() 對每個連結各自呼叫最多 4 個
# XPath locator + inner_text()，一頁數百個連結會產生數千次 Playwright
# 往返，是 Group 收集最大的效能瓶頸。
#
# 改成單次 page.evaluate()，在瀏覽器端一次完成「找最近的結果容器 +
# 讀卡片文字」，只做一次 IPC 往返即可拿到整頁的候選資料，Python 端
# 只做純文字判斷（不再碰 DOM）。邏輯與舊版 XPath 完全對應：
#   1) 最近的 [role="article"] 祖先
#   2) 最近的 [data-virtualized="false"] 祖先
#   3) 最近的 [role="listitem"] 祖先
#   4) 最近同時含有 <a> 與 <span> 的 <div> 祖先
#   5) 都找不到就用 anchor 本身
_GROUP_SCAN_JS = """
() => {
    const nearestContainer = (anchor) => {
        const tryClosest = (selector) => {
            const el = anchor.closest(selector);
            if (el && el.innerText && el.innerText.trim()) return el;
            return null;
        };
        let found =
            tryClosest('[role="article"]') ||
            tryClosest('[data-virtualized="false"]') ||
            tryClosest('[role="listitem"]');
        if (found) return found;

        let node = anchor.parentElement;
        while (node && node !== document.body) {
            if (
                node.tagName === 'DIV' &&
                node.querySelector('a') &&
                node.querySelector('span') &&
                node.innerText &&
                node.innerText.trim()
            ) {
                return node;
            }
            node = node.parentElement;
        }
        return anchor;
    };

    const anchors = Array.from(
        document.querySelectorAll(
            "a[href*='facebook.com/groups/'], a[href^='/groups/']"
        )
    ).slice(0, 2500);

    const rows = [];
    for (const anchor of anchors) {
        const href = anchor.getAttribute('href') || '';
        if (!href) continue;
        let text = '';
        try {
            const container = nearestContainer(anchor);
            text = (container.innerText || '').replace(/\\s+/g, ' ').trim();
        } catch (e) {
            text = '';
        }
        rows.push([href, text]);
    }
    return rows;
}
"""


def group_scan_page_links(page: Page) -> List[Tuple[str, str]]:
    """單次往返掃描整頁的社團連結與其卡片文字（見優化項目 1 說明）。"""
    try:
        rows = page.evaluate(_GROUP_SCAN_JS)
    except Exception:
        return []
    return [(str(href), str(text)) for href, text in (rows or [])]


def group_read_existing() -> Set[str]:
    with group_output_lock:
        return {
            normalized
            for line in read_nonempty_lines(GROUP_OUTPUT_FILE)
            if (normalized := group_normalize_url(line))
        }


def group_append_results(
    keyword: str,
    profile_name: str,
    items: Dict[str, Tuple[int, str]],
) -> int:
    if not items:
        return 0

    with group_output_lock:
        existing = group_read_existing_unlocked()
        new_rows = [
            (url, count, card_text)
            for url, (count, card_text) in items.items()
            if url not in existing
        ]
        if not new_rows:
            return 0

        # 主要產出檔（group.txt）是最終交付內容，維持 fsync 確保落盤。
        with GROUP_OUTPUT_FILE.open("a", encoding="utf-8") as file:
            for url, _count, _card_text in sorted(new_rows):
                file.write(url + "\n")
            file.flush()
            os.fsync(file.fileno())

        # 明細 CSV 只是紀錄用途，改用共用 helper，不強制 fsync（優化項目 2/5）。
        append_csv_rows(
            GROUP_DETAILS_FILE,
            ["關鍵字", "Group網址", "每日貼文數", "來源環境", "搜尋卡片文字", "時間"],
            [
                [keyword, url, count, profile_name, card_text.replace("\n", " "), now_text()]
                for url, count, card_text in sorted(new_rows)
            ],
            group_output_lock,
        )

        return len(new_rows)


def group_read_existing_unlocked() -> Set[str]:
    return {
        normalized
        for line in read_nonempty_lines(GROUP_OUTPUT_FILE)
        if (normalized := group_normalize_url(line))
    }


def group_collect_visible(
    page: Page,
    minimum_posts: int,
    public_only: bool,
) -> Dict[str, Tuple[int, str]]:
    results: Dict[str, Tuple[int, str]] = {}

    # 優化項目 1：一次 JS 掃描拿到整頁候選連結，之後全部是純文字比對，
    # 不再對每個連結各自往返瀏覽器。
    for href, raw_text in group_scan_page_links(page):
        try:
            if href.startswith("/"):
                href = "https://www.facebook.com" + href

            normalized = group_normalize_url(href)
            if not normalized:
                continue

            parsed_path = urlparse(href).path.strip("/").split("/")
            if len(parsed_path) >= 3 and parsed_path[0].casefold() == "groups":
                if parsed_path[2].casefold() in GROUP_SUBPATHS:
                    continue

            card_text = group_normalize_space(raw_text)

            today_posts = group_extract_today_post_count(card_text)
            if today_posts is None or today_posts < minimum_posts:
                continue

            if public_only:
                lower = card_text.casefold()
                public_markers = (
                    "public group", "public", "公開社團", "公開群組",
                    "公开小组", "pampublikong grupo",
                )
                private_markers = (
                    "private group", "private", "私人社團",
                    "私密社團", "私密群組", "pribadong grupo",
                )
                if any(marker in lower for marker in private_markers):
                    continue
                if not any(marker in lower for marker in public_markers):
                    continue

            previous = results.get(normalized)
            if previous is None or today_posts > previous[0]:
                results[normalized] = (today_posts, card_text)

        except Exception:
            continue

    return results


def group_find_facebook_page(browser: Browser) -> Page:
    context = browser.contexts[0]
    for page in context.pages:
        try:
            if "facebook.com" in page.url.casefold():
                return page
        except Exception:
            continue
    if context.pages:
        return context.pages[0]
    return context.new_page()


def group_start_browser(profile: AdsPowerProfile) -> Optional[str]:
    for attempt in range(1, GROUP_BROWSER_START_RETRIES + 1):
        data = api_get(
            "browser/start",
            {
                "user_id": profile.user_id,
                "open_tabs": 1,
                "headless": 0,
            },
        )
        if data and data.get("code") == 0:
            ws = ((data.get("data") or {}).get("ws") or {})
            ws_url = ws.get("puppeteer") or ws.get("selenium")
            if ws_url:
                return str(ws_url)

        group_write_log(
            f"[{profile.name}] 瀏覽器尚未就緒，"
            f"{GROUP_BROWSER_START_WAIT} 秒後重試 "
            f"{attempt}/{GROUP_BROWSER_START_RETRIES}"
        )
        time.sleep(GROUP_BROWSER_START_WAIT)
    return None


def group_generate_keywords() -> List[str]:
    base = [
        "Philippines community", "Filipino community", "Pinoy community",
        "Philippines buy and sell", "Pinoy buy and sell",
        "Philippines online seller", "Filipino business group",
        "Philippines jobs", "OFW community", "Tagalog community",
        "Bisaya community", "Philippines lotto", "PCSO lotto result",
        "Swertres lotto result", "3D lotto result Philippines",
    ]
    locations = [
        "Manila", "Quezon City", "Cebu", "Davao", "Baguio", "Iloilo",
        "Bacolod", "Cagayan de Oro", "General Santos", "Pasig", "Makati",
        "Taguig", "Antipolo", "Cavite", "Laguna", "Bulacan", "Pampanga",
        "Batangas",
    ]
    group_types = [
        "community", "buy and sell", "online seller", "business",
        "jobs", "marketplace", "support group", "local group",
    ]
    prefixes = ["Filipino", "Pinoy", "Philippines", "Tagalog", "Bisaya"]
    niches = [
        "food", "travel", "gaming", "comedy", "lifestyle", "fitness",
        "music", "dance", "family", "mom", "business", "tech",
        "photography", "sports", "beauty", "fashion", "lotto",
        "online selling", "work from home",
    ]
    group_words = ["group", "community", "club"]

    result: List[str] = []
    seen: Set[str] = set()

    def add(value: str) -> None:
        cleaned = re.sub(r"\s+", " ", value).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)

    for item in base:
        add(item)
    for location in locations:
        for group_type in group_types:
            add(f"{location} {group_type}")
    for prefix in prefixes:
        for niche in niches:
            for word in group_words:
                add(f"{prefix} {niche} {word}")

    return result[:180]


def group_load_recent_history() -> Set[str]:
    if not GROUP_KEYWORD_HISTORY_FILE.exists():
        return set()

    cutoff = time.time() - GROUP_KEYWORD_HISTORY_HOURS * 3600
    recent: Set[str] = set()
    kept: List[str] = []

    with group_history_lock:
        for line in GROUP_KEYWORD_HISTORY_FILE.read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                timestamp = float(parts[0])
            except ValueError:
                continue
            keyword = parts[1].strip()
            if timestamp >= cutoff and keyword:
                recent.add(keyword.casefold())
                kept.append(f"{timestamp}\t{keyword}")

        GROUP_KEYWORD_HISTORY_FILE.write_text(
            "\n".join(kept) + ("\n" if kept else ""),
            encoding="utf-8",
        )
    return recent


def group_record_keyword(keyword: str) -> None:
    with group_history_lock:
        with GROUP_KEYWORD_HISTORY_FILE.open("a", encoding="utf-8") as file:
            file.write(f"{time.time()}\t{keyword}\n")


def group_prepare_keywords() -> List[str]:
    manual = read_nonempty_lines(GROUP_KEYWORDS_FILE)
    recent = group_load_recent_history()
    source = manual if manual else group_generate_keywords()

    result: List[str] = []
    seen: Set[str] = set()
    for keyword in source:
        cleaned = re.sub(r"\s+", " ", keyword).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen and key not in recent:
            seen.add(key)
            result.append(cleaned)

    if not result and not manual:
        with group_history_lock:
            GROUP_KEYWORD_HISTORY_FILE.write_text("", encoding="utf-8")
        result = group_generate_keywords()

    GROUP_GENERATED_KEYWORDS_FILE.write_text(
        f"# 生成時間：{now_text()}\n" + "\n".join(result) + "\n",
        encoding="utf-8",
    )
    return result


def group_search_keyword(
    worker_name: str,
    profile_name: str,
    page: Page,
    keyword: str,
    stop_event: threading.Event,
    minimum_posts: int,
    public_only: bool,
    max_scrolls: int,
    scroll_distance: int,
    scroll_wait_ms: int,
    event_queue: "queue.Queue[tuple]",
    worker_id: int,
) -> Tuple[int, int]:
    search_url = (
        "https://www.facebook.com/search/groups/?q="
        + quote(keyword, safe="")
    )

    try:
        page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=40000,
        )
    except Exception:
        try:
            page.evaluate("window.stop()")
        except Exception:
            pass

    page.wait_for_timeout(2500)

    all_items: Dict[str, Tuple[int, str]] = {}
    no_growth = 0
    previous_count = 0

    for scroll_index in range(max_scrolls):
        if stop_event.is_set():
            break

        current = group_collect_visible(
            page,
            minimum_posts,
            public_only,
        )
        all_items.update(current)
        current_count = len(all_items)

        if current_count <= previous_count:
            no_growth += 1
        else:
            no_growth = 0
            previous_count = current_count

        message = (
            f"{keyword}｜下滑 {scroll_index + 1}/{max_scrolls}"
            f"｜符合 {minimum_posts}+：{current_count}"
            f"｜停滯 {no_growth}/{GROUP_NO_GROWTH_LIMIT}"
        )
        group_write_log(f"[{worker_name}] {message}")
        event_queue.put(("group_log", message))
        event_queue.put(
            (
                "group_worker_update",
                worker_id,
                keyword,
                "下滑收集中",
                current_count,
                0,
            )
        )

        if no_growth >= GROUP_NO_GROWTH_LIMIT:
            break

        try:
            page.mouse.wheel(0, scroll_distance)
        except Exception:
            try:
                page.evaluate(f"window.scrollBy(0, {scroll_distance})")
            except Exception:
                pass

        page.wait_for_timeout(scroll_wait_ms)

    all_items.update(
        group_collect_visible(page, minimum_posts, public_only)
    )
    added = group_append_results(
        keyword,
        profile_name,
        all_items,
    )
    return len(all_items), added


def group_worker_main(
    worker_id: int,
    profile: AdsPowerProfile,
    task_queue: "queue.Queue[str]",
    stop_event: threading.Event,
    event_queue: "queue.Queue[tuple]",
    settings: Dict[str, object],
) -> None:
    worker_name = f"環境{worker_id} {profile.name}"
    event_queue.put(
        ("group_worker_state", worker_id, profile.name, "啟動中")
    )
    group_write_log(f"[{worker_name}] Worker 啟動｜{profile.user_id}")

    ws_url = group_start_browser(profile)
    if not ws_url:
        event_queue.put(
            ("group_worker_state", worker_id, profile.name, "啟動失敗")
        )
        event_queue.put(("group_stat", "failed", 1))
        return

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(ws_url)
            page = group_find_facebook_page(browser)
            page.set_default_timeout(30000)

            event_queue.put(
                ("group_worker_state", worker_id, profile.name, "執行中")
            )
            group_write_log(f"[{worker_name}] Playwright 已成功連接")

            while not stop_event.is_set():
                try:
                    keyword = task_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    event_queue.put(
                        (
                            "group_worker_update",
                            worker_id,
                            keyword,
                            "搜尋中",
                            0,
                            0,
                        )
                    )

                    found, added = group_search_keyword(
                        worker_name=worker_name,
                        profile_name=profile.name,
                        page=page,
                        keyword=keyword,
                        stop_event=stop_event,
                        minimum_posts=int(settings["minimum_posts"]),
                        public_only=bool(settings["public_only"]),
                        max_scrolls=int(settings["max_scrolls"]),
                        scroll_distance=int(settings["scroll_distance"]),
                        scroll_wait_ms=int(settings["scroll_wait_ms"]),
                        event_queue=event_queue,
                        worker_id=worker_id,
                    )
                    group_record_keyword(keyword)

                    event_queue.put(("group_stat", "processed", 1))
                    event_queue.put(("group_stat", "found", found))
                    event_queue.put(("group_stat", "added", added))
                    event_queue.put(
                        (
                            "group_worker_update",
                            worker_id,
                            keyword,
                            "完成",
                            found,
                            added,
                        )
                    )
                    event_queue.put(
                        (
                            "group_log",
                            f"[{profile.name}] {keyword} 完成｜"
                            f"找到 {found}｜新增 {added}",
                        )
                    )

                except Exception as exc:
                    group_record_keyword(keyword)
                    event_queue.put(("group_stat", "processed", 1))
                    event_queue.put(("group_stat", "failed", 1))
                    event_queue.put(
                        (
                            "group_worker_update",
                            worker_id,
                            keyword,
                            f"失敗：{exc}",
                            0,
                            0,
                        )
                    )
                    event_queue.put(
                        (
                            "group_log",
                            f"[{profile.name}] {keyword} 失敗：{exc}",
                        )
                    )
                finally:
                    task_queue.task_done()

                if not stop_event.is_set():
                    time.sleep(GROUP_BETWEEN_KEYWORDS_SECONDS)

            # CDP 連線只斷開，不主動關閉 AdsPower Profile。
            try:
                browser.close()
            except Exception:
                pass

    except Exception as exc:
        event_queue.put(("group_stat", "failed", 1))
        event_queue.put(
            ("group_worker_state", worker_id, profile.name, f"異常：{exc}")
        )
        event_queue.put(
            ("group_log", f"[{profile.name}] Worker 異常：{exc}")
        )
        return

    event_queue.put(
        ("group_worker_state", worker_id, profile.name, "完成")
    )




# ============================================================
# Part 3：KOL People 搜尋引擎
# ============================================================
# 同上，改用 RLock 避免 append_csv_rows() 與外層呼叫者對同一把鎖重入時死鎖。
kol_output_lock = threading.RLock()
kol_history_lock = threading.RLock()
kol_log_lock = threading.RLock()


def kol_write_log(message: str) -> None:
    line = f"[{now_text()}] {message}"
    print(line)
    with kol_log_lock:
        with KOL_RUN_LOG.open("a", encoding="utf-8") as file:
            file.write(line + "\n")


def kol_safe_filename(value: Optional[str], max_length: int = 40) -> str:
    """
    修補：原始檔案在 kol_save_diagnostics / kol_save_follower_diagnostics
    呼叫了這個函式，但整份程式從未定義它，執行到 Diagnostics 儲存時
    會直接丟出 NameError（"name 'kol_safe_filename' is not defined"），
    導致該筆關鍵字被當成失敗跳過。

    邏輯取自 kol_save_date_debug() 裡原本就存在、行為相同的內嵌寫法：
    把檔名中不合法的字元換成底線，並截斷到指定長度。
    """
    cleaned = re.sub(
        r'[\\/:*?"<>|]+',
        "_",
        (value or "unknown").strip(),
    )
    cleaned = cleaned.strip("_") or "unknown"
    return cleaned[:max_length]


def kol_parse_number(number_text: str) -> Optional[int]:
    text = (
        number_text.strip()
        .replace(",", "")
        .replace("，", "")
        .replace(" ", "")
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KkMm萬万]?)", text)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = {
        "": 1,
        "k": 1_000,
        "m": 1_000_000,
        "萬": 10_000,
        "万": 10_000,
    }.get(unit, 1)
    return int(value * multiplier)


def kol_parse_followers(text: str) -> Optional[int]:
    """
    解析 Facebook People 搜尋卡片中的粉絲數。

    已確認支援：
    - 1,000 followers
    - 2.1K followers
    - followers: 2.1K
    - 1.2萬粉絲 / 1.2万粉丝
    - 1.7K na follower
    - 117K na follower
    - 83 na follower
    """
    cleaned = re.sub(r"\s+", " ", text or "").strip()

    patterns = [
        # 菲律賓語介面：1.7K na follower
        r"([\d,.]+\s*[KkMm萬万]?)\s+na\s+followers?",
        r"([\d,.]+\s*[KkMm萬万]?)\s+na\s+tagasubaybay",
        # 英文
        r"([\d,.]+\s*[KkMm萬万]?)\s*followers?",
        r"followers?\s*[:：]?\s*([\d,.]+\s*[KkMm萬万]?)",
        # 繁中、簡中
        r"([\d,.]+\s*[KkMm萬万]?)\s*位?粉絲",
        r"([\d,.]+\s*[KkMm萬万]?)\s*粉丝",
        r"粉絲\s*[:：]?\s*([\d,.]+\s*[KkMm萬万]?)",
        r"粉丝\s*[:：]?\s*([\d,.]+\s*[KkMm萬万]?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue

        value = kol_parse_number(match.group(1))
        if value is not None:
            return value

    return None


def kol_normalize_facebook_url(raw_url: Optional[str]) -> Optional[str]:
    if not raw_url:
        return None

    raw_url = raw_url.strip().replace("\\/", "/")
    if raw_url.startswith("/"):
        raw_url = urljoin("https://www.facebook.com", raw_url)
    elif raw_url.startswith("www."):
        raw_url = "https://" + raw_url

    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None

    host = parsed.netloc.lower().split(":")[0]
    allowed_hosts = {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "web.facebook.com",
    }
    if host not in allowed_hosts:
        return None

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    path_lower = path.lower()

    blocked_prefixes = (
        "/search", "/groups", "/events", "/marketplace", "/watch",
        "/reel", "/reels", "/story.php", "/share", "/login",
        "/help", "/privacy", "/policies", "/gaming", "/pages",
    )
    if any(path_lower.startswith(prefix) for prefix in blocked_prefixes):
        return None

    query = ""
    if path_lower == "/profile.php":
        profile_id = parse_qs(parsed.query).get("id", [""])[0].strip()
        if not profile_id:
            return None
        query = f"id={profile_id}"
    else:
        path = path.rstrip("/")
        if not path:
            return None

    return urlunparse(("https", "www.facebook.com", path, "", query, ""))


def kol_read_existing_unlocked() -> Set[str]:
    existing: Set[str] = set()
    for line in read_nonempty_lines(KOL_OUTPUT_FILE):
        candidate = line
        if not candidate.startswith("http"):
            match = re.search(r"https?://[^\s|]+", candidate)
            candidate = match.group(0) if match else ""
        normalized = kol_normalize_facebook_url(candidate)
        if normalized:
            existing.add(normalized)
    return existing


def kol_read_existing() -> Set[str]:
    with kol_output_lock:
        return kol_read_existing_unlocked()


def _kol_date_fields(date_result: KolDateResult) -> Tuple[str, object]:
    """
    date_result 的日期字串/天數格式化邏輯，原本在三個函式裡各重複一次
    （優化項目 5），統一在這裡算一次。
    """
    date_str = (
        date_result.post_date.strftime("%Y-%m-%d %H:%M:%S")
        if date_result.post_date
        else ""
    )
    days_old = date_result.days_old if date_result.days_old is not None else ""
    return date_str, days_old


def kol_append_verified(
    keyword: str,
    url: str,
    display_name: str,
    followers: int,
    source_profile: str,
    card_text: str,
    date_result: KolDateResult,
) -> bool:
    """只有日期判斷通過的 KOL 才寫入 kolurl.txt。"""
    with kol_output_lock:
        existing = kol_read_existing_unlocked()
        if url in existing:
            return False

        # 主要產出檔（kolurl.txt）是最終交付內容，維持 fsync 確保落盤。
        with KOL_OUTPUT_FILE.open("a", encoding="utf-8") as file:
            file.write(url + "\n")
            file.flush()
            os.fsync(file.fileno())

        date_str, days_old = _kol_date_fields(date_result)
        append_csv_rows(
            KOL_DETAILS_FILE,
            [
                "關鍵字", "KOL網址", "顯示名稱", "粉絲數", "最新貼文日期",
                "距今天數", "日期來源", "來源環境", "判斷結果", "搜尋卡片文字", "時間",
            ],
            [[
                keyword, url, display_name, followers, date_str, days_old,
                date_result.source, source_profile, "符合最近發文條件",
                card_text.replace("\n", " "), now_text(),
            ]],
            kol_output_lock,
        )

        kol_record_checked(
            keyword=keyword,
            url=url,
            display_name=display_name,
            followers=followers,
            source_profile=source_profile,
            date_result=date_result,
            passed=True,
        )
        return True


def kol_record_checked(
    keyword: str,
    url: str,
    display_name: str,
    followers: int,
    source_profile: str,
    date_result: KolDateResult,
    passed: bool,
) -> None:
    date_str, days_old = _kol_date_fields(date_result)
    append_csv_rows(
        KOL_CHECKED_FILE,
        [
            "關鍵字", "KOL網址", "顯示名稱", "粉絲數", "最新貼文日期",
            "原始日期文字", "距今天數", "日期來源", "來源環境", "通過", "原因", "檢查時間",
        ],
        [[
            keyword, url, display_name, followers, date_str,
            date_result.raw_text, days_old, date_result.source,
            source_profile, "是" if passed else "否", date_result.reason, now_text(),
        ]],
        kol_output_lock,
    )


def kol_record_failed(
    keyword: str,
    url: str,
    display_name: str,
    followers: int,
    source_profile: str,
    date_result: KolDateResult,
) -> None:
    date_str, days_old = _kol_date_fields(date_result)
    append_csv_rows(
        KOL_FAILED_FILE,
        [
            "關鍵字", "KOL網址", "顯示名稱", "粉絲數", "最新貼文日期",
            "原始日期文字", "距今天數", "日期來源", "來源環境", "失敗原因", "時間",
        ],
        [[
            keyword, url, display_name, followers, date_str,
            date_result.raw_text, days_old, date_result.source,
            source_profile, date_result.reason, now_text(),
        ]],
        kol_output_lock,
    )

    kol_record_checked(
        keyword=keyword,
        url=url,
        display_name=display_name,
        followers=followers,
        source_profile=source_profile,
        date_result=date_result,
        passed=False,
    )



def kol_read_card_text(article) -> str:
    """
    快速讀取 Facebook People 卡片文字。

    舊版會逐一讀取最多 350 個 span/div/a，
    每張卡片可能產生數百次 Playwright 往返，速度非常慢。

    本版改成一次 JavaScript 呼叫，在瀏覽器端合併：
    - innerText
    - textContent
    - aria-label
    - title
    """
    try:
        result = article.evaluate(
            """
            (el) => {
                const values = [];
                const seen = new Set();

                const add = (value) => {
                    const cleaned = String(value || '')
                        .replace(/\\s+/g, ' ')
                        .trim();
                    const key = cleaned.toLowerCase();
                    if (cleaned && !seen.has(key)) {
                        seen.add(key);
                        values.push(cleaned);
                    }
                };

                add(el.innerText);
                add(el.textContent);
                add(el.getAttribute('aria-label'));
                add(el.getAttribute('title'));

                return values.join('\\n');
            }
            """
        )
        return str(result or "").strip()
    except Exception:
        try:
            return article.inner_text(timeout=500).strip()
        except Exception:
            return ""


def kol_profile_link_score(raw_url: str, normalized: str, link_text: str) -> int:
    """
    分數越高越可能是 People 個人首頁。
    排除貼文、相片、影片等內容網址。
    """
    try:
        parsed = urlparse(raw_url)
        path = (parsed.path or "").casefold()
    except Exception:
        path = raw_url.casefold()

    blocked_parts = (
        "/posts/", "/photos/", "/photo/", "/videos/", "/video/",
        "/reel/", "/reels/", "/permalink/", "/stories/",
        "/friends/", "/about", "/followers", "/following",
    )
    if any(part in path for part in blocked_parts):
        return -100

    score = 0
    normalized_path = urlparse(normalized).path.strip("/")

    if normalized_path == "profile.php":
        score += 100
    elif normalized_path and "/" not in normalized_path:
        score += 90
    elif normalized_path.count("/") == 0:
        score += 80

    text = re.sub(r"\s+", " ", link_text or "").strip()
    if text and len(text) <= 120:
        score += 10

    if "profile.php" in raw_url.casefold():
        score += 20

    return score



def kol_extract_display_name(article) -> str:
    selectors = [
        "h2 a[role='link']",
        "h3 a[role='link']",
        "a[role='link'] span",
        "a[role='link']",
    ]
    for selector in selectors:
        try:
            locator = article.locator(selector)
            count = min(locator.count(), 8)
            for index in range(count):
                value = re.sub(
                    r"\s+",
                    " ",
                    locator.nth(index).inner_text(timeout=800),
                ).strip()
                if value and len(value) <= 120:
                    return value
        except Exception:
            continue
    return ""


def kol_get_best_article_link(article) -> Optional[str]:
    """
    從 People 搜尋卡片挑選真正的個人首頁網址。
    舊版取第一個合法網址，可能拿到照片、貼文或其他內容網址；
    這版會對全部候選網址評分後再選最高分。
    """
    try:
        links = article.locator("a[role='link'], a[href]")
        count = min(links.count(), 80)
    except Exception:
        return None

    candidates: List[Tuple[int, str]] = []
    seen: Set[str] = set()

    for index in range(count):
        link = links.nth(index)
        try:
            href = link.get_attribute("href", timeout=500) or ""
            normalized = kol_normalize_facebook_url(href)
            if not normalized or normalized in seen:
                continue

            try:
                link_text = link.inner_text(timeout=300)
            except Exception:
                link_text = ""

            score = kol_profile_link_score(
                raw_url=href,
                normalized=normalized,
                link_text=link_text,
            )
            if score < 0:
                continue

            seen.add(normalized)
            candidates.append((score, normalized))
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def kol_find_facebook_page(browser: Browser) -> Page:
    context = browser.contexts[0]
    for page in context.pages:
        try:
            if "facebook.com" in page.url.casefold():
                return page
        except Exception:
            continue
    if context.pages:
        return context.pages[0]
    return context.new_page()


def kol_start_browser(profile: AdsPowerProfile) -> Optional[str]:
    for attempt in range(1, KOL_BROWSER_START_RETRIES + 1):
        data = api_get(
            "browser/start",
            {
                "user_id": profile.user_id,
                "open_tabs": 1,
                "headless": 0,
            },
        )
        if data and data.get("code") == 0:
            ws = ((data.get("data") or {}).get("ws") or {})
            ws_url = ws.get("puppeteer") or ws.get("selenium")
            if ws_url:
                return str(ws_url)

        kol_write_log(
            f"[{profile.name}] 瀏覽器尚未就緒，"
            f"{KOL_BROWSER_START_WAIT} 秒後重試 "
            f"{attempt}/{KOL_BROWSER_START_RETRIES}"
        )
        time.sleep(KOL_BROWSER_START_WAIT)
    return None


def kol_fetch_philippines_trending_keywords() -> List[str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/150 Safari/537.36"
        )
    }
    try:
        response = requests.get(
            KOL_PH_TRENDS_RSS_URL,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)

        keywords: List[str] = []
        seen: Set[str] = set()
        for item in root.findall(".//item"):
            title_node = item.find("title")
            if title_node is None or not title_node.text:
                continue
            keyword = re.sub(r"\s+", " ", title_node.text).strip()
            key = keyword.casefold()
            if keyword and key not in seen:
                seen.add(key)
                keywords.append(keyword)
            if len(keywords) >= KOL_MAX_TREND_KEYWORDS:
                break
        return keywords
    except Exception as exc:
        kol_write_log(f"Google Trends 取得失敗，使用備援關鍵字：{exc}")
        return []


def kol_fallback_keywords() -> List[str]:
    return [
        "Filipino influencer",
        "Filipino content creator",
        "Filipino vlogger",
        "Pinoy vlogger",
        "Pinoy influencer",
        "Philippines lifestyle blogger",
        "Philippines beauty influencer",
        "Filipino beauty vlogger",
        "Philippines fashion influencer",
        "Filipino food vlogger",
        "Pinoy food blogger",
        "Philippines travel vlogger",
        "Pinoy travel blogger",
        "Filipino gaming creator",
        "Pinoy gamer",
        "Filipino comedy creator",
        "Pinoy comedy",
        "Filipino mom blogger",
        "Philippines fitness influencer",
        "Filipino fitness coach",
        "Filipino singer",
        "Filipino dancer",
        "Filipino actor",
        "Filipino actress",
        "Manila influencer",
        "Cebu influencer",
        "Davao influencer",
        "Tagalog content creator",
        "Bisaya vlogger",
        "OFW vlogger",
    ]


def kol_unique_keywords(items: List[str]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for item in items:
        cleaned = re.sub(r"\s+", " ", item).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def kol_load_recent_history() -> Set[str]:
    if not KOL_KEYWORD_HISTORY_FILE.exists():
        return set()

    cutoff = time.time() - KOL_KEYWORD_HISTORY_HOURS * 3600
    recent: Set[str] = set()
    kept: List[str] = []

    with kol_history_lock:
        for line in KOL_KEYWORD_HISTORY_FILE.read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                timestamp = float(parts[0])
            except ValueError:
                continue
            keyword = parts[1].strip()
            if timestamp >= cutoff and keyword:
                recent.add(keyword.casefold())
                kept.append(f"{timestamp}\t{keyword}")

        KOL_KEYWORD_HISTORY_FILE.write_text(
            "\n".join(kept) + ("\n" if kept else ""),
            encoding="utf-8",
        )
    return recent


def kol_record_keyword(keyword: str) -> None:
    with kol_history_lock:
        with KOL_KEYWORD_HISTORY_FILE.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(f"{time.time()}\t{keyword}\n")


def kol_prepare_keywords() -> List[str]:
    manual = read_nonempty_lines(KOL_KEYWORDS_FILE)
    recent = kol_load_recent_history()

    if manual:
        source = manual
        trends: List[str] = []
    else:
        trends = kol_fetch_philippines_trending_keywords()
        source = kol_unique_keywords(trends + kol_fallback_keywords())

    result: List[str] = []
    seen: Set[str] = set()
    for keyword in source:
        cleaned = re.sub(r"\s+", " ", keyword).strip()
        key = cleaned.casefold()
        if cleaned and key not in seen and key not in recent:
            seen.add(key)
            result.append(cleaned)

    if not result and not manual:
        with kol_history_lock:
            KOL_KEYWORD_HISTORY_FILE.write_text("", encoding="utf-8")
        result = kol_unique_keywords(trends + kol_fallback_keywords())

    KOL_GENERATED_KEYWORDS_FILE.write_text(
        f"# 生成日期：{date.today().isoformat()}\n"
        + "\n".join(result)
        + "\n",
        encoding="utf-8",
    )
    return result



# ============================================================
# Part 4：KOL 最近貼文日期引擎
# ============================================================
KOL_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

KOL_MONTH_PATTERN = (
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December|Jan|Feb|Mar|Apr|Jun|"
    r"Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
)


def kol_clean_date_text(value: str) -> str:
    value = re.sub(r"[\u200b\u200e\u200f\u2060]", "", value or "")
    value = value.replace("·", " ").replace("•", " ")
    return re.sub(r"\s+", " ", value).strip()


def kol_parse_time_parts(
    hour_text: Optional[str],
    minute_text: Optional[str],
    ampm: Optional[str],
) -> Tuple[int, int]:
    hour = int(hour_text or "0")
    minute = int(minute_text or "0")
    if ampm:
        marker = ampm.casefold()
        if marker == "pm" and hour < 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0
    return hour, minute


def kol_parse_date_candidate(
    raw_value: str,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    now = now or datetime.now()
    value = kol_clean_date_text(raw_value)
    if not value:
        return None

    # Facebook Tooltip 常見格式：
    # Wednesday, March 25, 2026 at 8:21 PM
    # 先移除星期前綴，再交給原本月份日期解析。
    value = re.sub(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
        r"\s*,?\s*",
        "",
        value,
        flags=re.I,
    )

    lower = value.casefold()

    # 避免把粉絲數、電話、貼文內容中的普通數字當日期。
    if len(value) > 180:
        return None

    # 即時／今天／昨天。
    if re.search(r"\bjust now\b|\bnow\b|剛剛|刚刚", lower):
        return now
    if re.search(r"\btoday\b|今天|今日|ngayon", lower):
        time_match = re.search(
            r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?",
            value,
            flags=re.I,
        )
        if time_match:
            hour, minute = kol_parse_time_parts(
                time_match.group(1),
                time_match.group(2),
                time_match.group(3),
            )
            return now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    if re.search(r"\byesterday\b|昨天|昨日|kahapon", lower):
        target = now - timedelta(days=1)
        time_match = re.search(
            r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?",
            value,
            flags=re.I,
        )
        if time_match:
            hour, minute = kol_parse_time_parts(
                time_match.group(1),
                time_match.group(2),
                time_match.group(3),
            )
            return target.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
        return target.replace(hour=0, minute=0, second=0, microsecond=0)

    # 相對日期：5m / 3h / 2d / 4w，以及完整英文單位。
    relative_patterns = [
        (r"^\s*(\d+)\s*(?:m|min|mins|minute|minutes|分鐘|分钟)\s*$", "minutes"),
        (r"^\s*(\d+)\s*(?:h|hr|hrs|hour|hours|小時|小时)\s*$", "hours"),
        (r"^\s*(\d+)\s*(?:d|day|days|天)\s*$", "days"),
        (r"^\s*(\d+)\s*(?:w|wk|wks|week|weeks|週|周|星期)\s*$", "weeks"),
    ]
    for pattern, unit in relative_patterns:
        match = re.match(pattern, lower, flags=re.I)
        if not match:
            continue
        amount = int(match.group(1))
        if unit == "minutes":
            return now - timedelta(minutes=amount)
        if unit == "hours":
            return now - timedelta(hours=amount)
        if unit == "days":
            return now - timedelta(days=amount)
        return now - timedelta(weeks=amount)

    # 中文日期：2026年8月1日 [時間]
    match = re.search(
        r"(?P<year>20\d{2})\s*年\s*"
        r"(?P<month>\d{1,2})\s*月\s*"
        r"(?P<day>\d{1,2})\s*日?"
        r"(?:\s*(?:at|於|于)?\s*"
        r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
        r"\s*(?P<ampm>AM|PM)?)?",
        value,
        flags=re.I,
    )
    if match:
        hour, minute = kol_parse_time_parts(
            match.group("hour"),
            match.group("minute"),
            match.group("ampm"),
        )
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                hour,
                minute,
            )
        except ValueError:
            return None

    # 數字完整日期：2026/08/01、2026-08-01、08/01/2026。
    match = re.search(
        r"(?P<year>20\d{2})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})"
        r"(?:[ T,]+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
        r"\s*(?P<ampm>AM|PM)?)?",
        value,
        flags=re.I,
    )
    if match:
        hour, minute = kol_parse_time_parts(
            match.group("hour"),
            match.group("minute"),
            match.group("ampm"),
        )
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                hour,
                minute,
            )
        except ValueError:
            return None

    match = re.search(
        r"(?P<month>\d{1,2})[./-](?P<day>\d{1,2})[./-](?P<year>20\d{2})"
        r"(?:[ T,]+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?"
        r"\s*(?P<ampm>AM|PM)?)?",
        value,
        flags=re.I,
    )
    if match:
        hour, minute = kol_parse_time_parts(
            match.group("hour"),
            match.group("minute"),
            match.group("ampm"),
        )
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                hour,
                minute,
            )
        except ValueError:
            return None

    # 英文完整日期：August 16, 2025 [at 8:30 PM]
    match = re.search(
        rf"(?P<month>{KOL_MONTH_PATTERN})\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
        r"(?:,\s*|\s+)(?P<year>20\d{2})"
        r"(?:\s+(?:at\s+)?(?P<hour>\d{1,2})"
        r"(?::(?P<minute>\d{2}))?\s*(?P<ampm>AM|PM)?)?",
        value,
        flags=re.I,
    )
    if match:
        month = KOL_MONTHS.get(match.group("month").casefold())
        hour, minute = kol_parse_time_parts(
            match.group("hour"),
            match.group("minute"),
            match.group("ampm"),
        )
        if month:
            try:
                return datetime(
                    int(match.group("year")),
                    month,
                    int(match.group("day")),
                    hour,
                    minute,
                )
            except ValueError:
                return None

    # 英文無年份：August 1 at 8:30 PM。
    match = re.search(
        rf"(?P<month>{KOL_MONTH_PATTERN})\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
        r"(?:\s+(?:at\s+)?(?P<hour>\d{1,2})"
        r"(?::(?P<minute>\d{2}))?\s*(?P<ampm>AM|PM)?)?",
        value,
        flags=re.I,
    )
    if match:
        month = KOL_MONTHS.get(match.group("month").casefold())
        hour, minute = kol_parse_time_parts(
            match.group("hour"),
            match.group("minute"),
            match.group("ampm"),
        )
        if month:
            try:
                candidate = datetime(
                    now.year,
                    month,
                    int(match.group("day")),
                    hour,
                    minute,
                )
                # 無年份而日期落在未來，視為上一年。
                if candidate > now + timedelta(days=2):
                    candidate = candidate.replace(year=now.year - 1)
                return candidate
            except ValueError:
                return None

    return None


def kol_candidate_priority(
    raw_text: str,
    source: str,
    parsed: datetime,
    now: datetime,
) -> Tuple[int, float]:
    """
    越小越優先：
    1. 貼文永久網址元素的 aria/title/innerText
    2. time / abbr
    3. Tooltip
    4. 貼文容器內其他隱藏文字
    同級優先選較新的日期。
    """
    source_lower = source.casefold()
    if "post_link" in source_lower:
        level = 0
    elif "time" in source_lower or "abbr" in source_lower:
        level = 1
    elif "tooltip" in source_lower:
        level = 2
    elif "attribute" in source_lower:
        level = 3
    else:
        level = 4

    age_seconds = max(0.0, (now - parsed).total_seconds())
    return level, age_seconds


def kol_is_probable_post_container(locator) -> bool:
    try:
        text = locator.inner_text(timeout=1000)
    except Exception:
        text = ""

    try:
        link_count = locator.locator(
            "a[href*='/posts/'], "
            "a[href*='story_fbid='], "
            "a[href*='/permalink/'], "
            "a[href*='/photo/?fbid='], "
            "a[href*='/videos/'], "
            "a[href*='/reel/']"
        ).count()
    except Exception:
        link_count = 0

    try:
        message_count = locator.locator(
            "[data-ad-rendering-role='story_message'], "
            "[data-ad-preview='message']"
        ).count()
    except Exception:
        message_count = 0

    return bool(link_count or message_count or len(text.strip()) >= 20)



KOL_PINNED_MARKERS = (
    "pinned comment",
    "pinned post",
    "pinned",
    "置頂留言",
    "置顶留言",
    "置頂貼文",
    "置顶帖子",
    "naka-pin na komento",
    "nakapin na komento",
)

KOL_SHORT_DATE_TEXT_RE = re.compile(
    r"^(?:"
    r"\d+\s*(?:m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks)"
    r"|today|yesterday|just now"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"\s+\d{1,2}"
    r")$",
    re.I,
)


def kol_is_pinned_container(locator) -> bool:
    try:
        text = re.sub(
            r"\s+",
            " ",
            locator.inner_text(timeout=700),
        ).strip().casefold()
    except Exception:
        text = ""

    if not text:
        return False

    return any(marker in text for marker in KOL_PINNED_MARKERS)


def kol_has_short_date_text(locator) -> bool:
    """
    快速辨識 Facebook 作者列旁常見短日期：
    21h、1h、5m、Aug 4、May 15。
    """
    try:
        elements = locator.locator("a, span")
        count = min(elements.count(), 220)
    except Exception:
        return False

    for index in range(count):
        element = elements.nth(index)
        try:
            value = re.sub(
                r"\s+",
                " ",
                element.inner_text(timeout=180),
            ).strip()
        except Exception:
            value = ""

        if value and len(value) <= 40 and KOL_SHORT_DATE_TEXT_RE.match(value):
            return True

    return False



def kol_find_first_post_container(page: Page):
    """
    尋找第一個真正可解析日期的貼文容器。

    規則：
    1. 跳過 Pinned Comment / Pinned Post。
    2. 不再找到第一個 article 就返回。
    3. 逐一檢查 article，只有含可解析日期或短日期文字才接受。
    4. 沒有 role='article' 時，再由 story_message 反向尋找祖先。
    """

    # 第一優先：逐一檢查 role=article。
    try:
        articles = page.locator("div[role='article']")
        article_count = min(articles.count(), 80)
    except Exception:
        article_count = 0

    for index in range(article_count):
        item = articles.nth(index)
        try:
            if not item.is_visible(timeout=300):
                continue

            if kol_is_pinned_container(item):
                continue

            if kol_locator_has_parseable_date(item):
                return item

            if kol_has_short_date_text(item):
                return item
        except Exception:
            continue

    # 第二優先：從貼文訊息節點反向尋找祖先。
    message_selectors = [
        "[data-ad-rendering-role='story_message']",
        "[data-ad-preview='message']",
        "[data-ad-comet-preview='message']",
    ]

    for selector in message_selectors:
        try:
            messages = page.locator(selector)
            message_count = min(messages.count(), 30)
        except Exception:
            continue

        for message_index in range(message_count):
            message = messages.nth(message_index)

            try:
                if not message.is_visible(timeout=500):
                    continue
            except Exception:
                continue

            for level in range(1, 16):
                try:
                    candidate = message.locator(
                        f"xpath=ancestor::div[{level}]"
                    )
                    if candidate.count() == 0:
                        continue
                    if not candidate.is_visible(timeout=300):
                        continue
                    if kol_is_pinned_container(candidate):
                        continue

                    candidate_text = candidate.inner_text(
                        timeout=800
                    ).strip()
                    if len(candidate_text) < 10:
                        continue
                    if len(candidate_text) > 15000:
                        continue

                    if (
                        kol_locator_has_parseable_date(candidate)
                        or kol_has_short_date_text(candidate)
                    ):
                        return candidate
                except Exception:
                    continue

    # 第三優先：舊選擇器備援，但仍要求有日期且排除置頂區塊。
    selectors = [
        "div[role='feed'] > div",
        "div[role='main'] div[role='article']",
        "[data-pagelet^='FeedUnit_']",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 80)
        except Exception:
            continue

        for index in range(count):
            item = locator.nth(index)
            try:
                if not item.is_visible(timeout=300):
                    continue
                if kol_is_pinned_container(item):
                    continue

                if (
                    kol_locator_has_parseable_date(item)
                    or kol_has_short_date_text(item)
                ):
                    return item
            except Exception:
                continue

    return None


def kol_add_candidate(
    candidates: List[Tuple[str, str]],
    seen: Set[str],
    value: Optional[str],
    source: str,
) -> None:
    # 反混淆：innerText 是從畫面 DOM 組回來的文字，可能命中 Facebook
    # 拆字混淆手法（見 kol_remove_obfuscation_chars 說明）。
    # aria-label / title / data-tooltip-content 是原始屬性字串，不受
    # 影響，不用檢查。跟 kol_fast_extract_time_candidates 裡
    # hasObfuscationMarks() 是同一套規則。
    if source.endswith("_innerText") and kol_text_has_obfuscation_marks(
        value or ""
    ):
        return

    cleaned = kol_clean_date_text(value or "")
    if not cleaned:
        return
    key = cleaned.casefold()
    if key in seen:
        return
    seen.add(key)
    candidates.append((cleaned, source))



def kol_is_comment_date_link(raw_href: Optional[str]) -> bool:
    """帶 comment_id / reply_comment_id 的連結不是貼文發布日期。"""
    href = (raw_href or "").casefold()
    return (
        "comment_id=" in href
        or "reply_comment_id=" in href
        or "comment_tracking=" in href
    )


def kol_locator_has_parseable_date(locator) -> bool:
    """快速確認容器內是否存在可解析的貼文日期。"""
    now = datetime.now()
    selectors = [
        "a[href*='/posts/']",
        "a[href*='story_fbid=']",
        "a[href*='/permalink/']",
        "a[href*='/photo/?fbid=']",
        "a[href*='/videos/']",
        "a[href*='/reel/']",
        "time",
        "abbr",
        "[aria-label]",
        "[title]",
    ]

    for selector in selectors:
        try:
            elements = locator.locator(selector)
            count = min(elements.count(), 80)
        except Exception:
            continue

        for index in range(count):
            element = elements.nth(index)
            try:
                href = element.get_attribute("href", timeout=150)
                if kol_is_comment_date_link(href):
                    continue
            except Exception:
                pass

            values: List[str] = []
            for attribute in ("aria-label", "title", "data-tooltip-content"):
                try:
                    value = element.get_attribute(attribute, timeout=150)
                    if value:
                        values.append(value)
                except Exception:
                    pass

            try:
                inner = element.inner_text(timeout=200)
                if inner:
                    values.append(inner)
            except Exception:
                pass

            for value in values:
                if kol_parse_date_candidate(value, now) is not None:
                    return True

    return False



def kol_collect_element_values(
    element,
    source_prefix: str,
    candidates: List[Tuple[str, str]],
    seen: Set[str],
) -> None:
    for attribute in ("aria-label", "title", "data-tooltip-content"):
        try:
            value = element.get_attribute(attribute, timeout=500)
            kol_add_candidate(
                candidates,
                seen,
                value,
                f"{source_prefix}_attribute_{attribute}",
            )
        except Exception:
            pass

    try:
        value = element.inner_text(timeout=700)
        kol_add_candidate(
            candidates,
            seen,
            value,
            f"{source_prefix}_innerText",
        )
    except Exception:
        pass


def kol_collect_date_candidates(
    page: Page,
    post,
) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    seen: Set[str] = set()

    selectors = [
        (
            "a[href*='/posts/'], "
            "a[href*='story_fbid='], "
            "a[href*='/permalink/'], "
            "a[href*='/photo/?fbid='], "
            "a[href*='/videos/'], "
            "a[href*='/reel/']",
            "post_link",
        ),
        ("time", "time"),
        ("abbr", "abbr"),
        (
            "[aria-label], [title], [data-tooltip-content]",
            "attribute",
        ),
    ]

    for selector, source in selectors:
        try:
            elements = post.locator(selector)
            count = min(elements.count(), 120)
        except Exception:
            continue

        for index in range(count):
            element = elements.nth(index)

            if source == "post_link":
                try:
                    href = element.get_attribute("href", timeout=300)
                    if kol_is_comment_date_link(href):
                        continue
                except Exception:
                    pass

            kol_collect_element_values(
                element,
                source,
                candidates,
                seen,
            )

    # Hover 貼文日期候選，嘗試讀 Tooltip。
    try:
        hover_targets = post.locator(
            "a[href*='/posts/'], "
            "a[href*='story_fbid='], "
            "a[href*='/permalink/'], "
            "a[href*='/photo/?fbid='], "
            "a[href*='/videos/'], "
            "a[href*='/reel/'], "
            "time, abbr"
        )
        hover_count = min(hover_targets.count(), 12)
    except Exception:
        hover_count = 0

    for index in range(hover_count):
        target = hover_targets.nth(index)

        try:
            href = target.get_attribute("href", timeout=250)
            if kol_is_comment_date_link(href):
                continue
        except Exception:
            pass

        try:
            target.hover(timeout=1000)
            page.wait_for_timeout(450)
        except Exception:
            continue

        for selector in (
            "[role='tooltip']",
            "div[aria-live='polite']",
        ):
            try:
                tips = page.locator(selector)
                count = min(tips.count(), 20)
            except Exception:
                continue

            for tip_index in range(count):
                tip = tips.nth(tip_index)
                try:
                    if not tip.is_visible(timeout=200):
                        continue
                except Exception:
                    continue
                kol_collect_element_values(
                    tip,
                    "tooltip",
                    candidates,
                    seen,
                )

    # Facebook 作者列旁的短日期，例如 21h、1h、5m、Aug 4、May 15。
    try:
        short_date_elements = post.locator("a, span")
        short_count = min(short_date_elements.count(), 220)
    except Exception:
        short_count = 0

    for index in range(short_count):
        element = short_date_elements.nth(index)
        try:
            value = kol_clean_date_text(
                element.inner_text(timeout=180)
            )
        except Exception:
            value = ""

        if value and len(value) <= 40 and KOL_SHORT_DATE_TEXT_RE.match(value):
            kol_add_candidate(
                candidates,
                seen,
                value,
                "short_date_text",
            )

    # 最後備援：同一篇貼文內全部短文字與屬性。
    try:
        elements = post.locator("span, div, a")
        count = min(elements.count(), 500)
    except Exception:
        count = 0

    for index in range(count):
        element = elements.nth(index)
        try:
            value = element.inner_text(timeout=250)
        except Exception:
            value = ""
        value = kol_clean_date_text(value)
        if value and len(value) <= 120:
            kol_add_candidate(
                candidates,
                seen,
                value,
                "hidden_text",
            )

        for attribute in ("aria-label", "title"):
            try:
                attribute_value = element.get_attribute(
                    attribute,
                    timeout=200,
                )
            except Exception:
                attribute_value = ""
            if attribute_value and len(attribute_value) <= 180:
                kol_add_candidate(
                    candidates,
                    seen,
                    attribute_value,
                    f"hidden_attribute_{attribute}",
                )

    return candidates


def kol_save_date_debug(
    page: Page,
    post,
    url: str,
    display_name: str,
    candidates: List[Tuple[str, str]],
    reason: str,
) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = kol_safe_filename(display_name, 60)
    folder = KOL_DATE_DEBUG_DIR / f"{timestamp}_{safe_name}"
    folder.mkdir(parents=True, exist_ok=True)

    try:
        page.screenshot(
            path=str(folder / "page.png"),
            full_page=False,
        )
    except Exception:
        pass

    try:
        (folder / "page.html").write_text(
            page.content(),
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        pass

    try:
        if post is not None:
            outer_html = post.evaluate("(element) => element.outerHTML")
            (folder / "post.html").write_text(
                str(outer_html),
                encoding="utf-8",
                errors="ignore",
            )
    except Exception:
        pass

    try:
        lines = [
            f"{source}\t{value}"
            for value, source in candidates
        ]
        (folder / "date_candidates.txt").write_text(
            "\n".join(lines),
            encoding="utf-8",
        )
    except Exception:
        pass

    (folder / "result.txt").write_text(
        f"網址：{url}\n"
        f"名稱：{display_name}\n"
        f"原因：{reason}\n"
        f"目前頁面：{page.url}\n"
        f"時間：{now_text()}\n",
        encoding="utf-8",
    )



def kol_fast_date_deadline_check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise KolFastDateTimeout(
            f"快速日期分析超過 {KOL_FAST_DATE_TIMEOUT_SECONDS} 秒"
        )


def kol_remove_obfuscation_chars(value: str) -> str:
    """
    移除 Facebook 拆字 span 常見的零寬字元／組合字元，
    並合併被拆散的單字。

    範圍從原本只涵蓋 \\u034f 一個字元，擴大到整個
    U+0300–U+036F「組合用附加符號」區段，涵蓋這類混淆手法可能用到的
    其他組合字元變體（不只 \\u034f COMBINING GRAPHEME JOINER）。

    注意：這裡只是清掉雜訊字元，不會、也無法把被拆散打亂順序的文字
    重新組回正確順序——那是 Facebook 刻意設計來對付自動化擷取的機制。
    真正需要日期時，應該優先採信 aria-label / title /
    data-tooltip-content 這類原始屬性字串（見
    kol_fast_extract_time_candidates 的混淆偵測邏輯），而不是嘗試
    「修好」被打亂的可見文字。
    """
    cleaned = re.sub(
        r"[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u0300-\u036f]",
        "",
        value or "",
    )
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def kol_text_has_obfuscation_marks(value: str) -> bool:
    """
    偵測字串本身是否帶有 Facebook 拆字混淆常用的組合字元
    （U+0300–U+036F，包含 \\u034f）。

    給非 JS（Python 端）路徑，例如 kol_collect_date_candidates 讀取
    Playwright locator 文字時，用同一套規則過濾掉可能是拆字亂碼的
    候選文字，行為對齊 kol_fast_extract_time_candidates 裡的
    hasObfuscationMarks()。
    """
    return bool(re.search(r"[\u0300-\u036f]", value or ""))


def kol_fast_extract_time_candidates(page: Page, deadline: float) -> List[Tuple[str, str]]:
    """
    只掃描頁面頂部目前可見範圍內，最可能是貼文時間的連結與文字。

    主要來源：
    - /posts/
    - story_fbid=
    - /permalink/
    - /photo/?fbid=
    - /videos/
    - /reel/
    - aria-labelledby 對應的拆字文字
    - aria-label / title / data-tooltip-content
    - 短 innerText

    不再掃描 80 個 article，也不逐層反查祖先。
    """
    kol_fast_date_deadline_check(deadline)

    try:
        rows = page.evaluate(
            r"""
            (maxLinks) => {
                const clean = (value) => String(value || '')
                    .replace(/[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u0300-\u036f]/g, '')
                    .replace(/\u00a0/g, ' ')
                    .replace(/\s+/g, ' ')
                    .trim();

                // 反混淆偵測（對應「時間位置.txt」那種拆字亂碼問題）：
                // Facebook 有些文字（例如 Reels 卡片上的角標）會把每個字母
                // 拆成獨立 <span>，中間穿插零寬 / 組合用 Unicode 字元
                // （\u0300-\u036f 這個範圍，包含 \u034f），畫面看起來正常，
                // 但 innerText / textContent 讀出來的順序跟畫面顯示不同，
                // 完全是亂碼、且無法可靠地重組回原文。
                //
                // 這種手法只會出現在「從 DOM 組回來的文字」：innerText、
                // 或 aria-labelledby 指到的元素。aria-label / title /
                // data-tooltip-content 是原始屬性字串，不是由這些拆字
                // span 組成，不受影響、可以信任。
                const hasObfuscationMarks = (raw) =>
                    /[\u0300-\u036f]/.test(String(raw || ''));

                const looksLikeScrambledSpans = (el) => {
                    if (!el) return false;
                    const spans = el.querySelectorAll('span');
                    if (spans.length < 6) return false;
                    let singleCharCount = 0;
                    for (const s of spans) {
                        const t = (s.textContent || '').replace(
                            /[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u0300-\u036f\s]/g,
                            ''
                        );
                        if (t.length === 1) singleCharCount += 1;
                    }
                    return singleCharCount >= Math.max(6, spans.length * 0.6);
                };

                const pinnedMarkers = [
                    'pinned comment',
                    'pinned post',
                    '置頂留言',
                    '置顶留言',
                    '置頂貼文',
                    '置顶帖子',
                    'naka-pin na komento',
                    'nakapin na komento'
                ];

                // comment_id= 這類參數通常代表這個連結是「留言」的時間戳
                // （例如 /photo/?fbid=...&comment_id=...），但 Reels／影片
                // 的貼文永久連結（/reel/、/videos/）有時也會被 Facebook
                // 加上 comment_id= 當「打開後順便定位到某則留言」的追蹤
                // 參數，這種情況下連結本身其實是貼文自己的時間戳，不是
                // 留言的。兩者用位置區分：貼文自己的時間戳一定緊跟在
                // 作者名字旁邊（文章開頭 200px 內）；留言時間戳則在留言
                // 串裡，位置明顯更靠下面。
                const isCommentDateLink = (hrefLower, el) => {
                    const hasCommentParam =
                        hrefLower.includes('comment_id=') ||
                        hrefLower.includes('reply_comment_id=') ||
                        hrefLower.includes('comment_tracking=');
                    if (!hasCommentParam) return false;

                    if (
                        hrefLower.includes('/reel/') ||
                        hrefLower.includes('/videos/')
                    ) {
                        const article =
                            el.closest("div[role='article']") ||
                            el.closest("[data-pagelet^='FeedUnit_']");
                        if (article) {
                            const articleTop =
                                article.getBoundingClientRect().top;
                            const elTop = el.getBoundingClientRect().top;
                            const offset = elTop - articleTop;
                            if (offset >= 0 && offset < 200) {
                                // 文章開頭附近，視為貼文自己的時間戳。
                                return false;
                            }
                        }
                    }
                    return true;
                };

                const root =
                    document.querySelector("div[role='main']") ||
                    document.querySelector("main") ||
                    document.body;

                const selectors = [
                    "a[href*='/posts/']",
                    "a[href*='story_fbid=']",
                    "a[href*='/permalink/']",
                    "a[href*='/photo/?fbid=']",
                    "a[href*='/videos/']",
                    "a[href*='/reel/']",
                    "a[aria-labelledby]",
                    "time",
                    "abbr"
                ];

                const nodeSet = new Set(
                    Array.from(root.querySelectorAll(selectors.join(',')))
                );

                // 補強：像「更新大頭貼照」這類貼文，時間戳連結的 href
                // 只有 __cft__ 追蹤參數，不符合上面任何一個樣式，選不到。
                // 原本想法是「只找文章開頭區域（作者名字旁邊那排連結）」，
                // 但實測發現這類貼文的時間戳連結，在擷取當下有時根本還
                // 沒被包進 div[role='article'] 或 FeedUnit 容器裡（不是
                // 位置比較後面，是結構上完全不是它們的子元素）——用
                // 「文章容器內的前幾個連結」這個條件找不到它。
                //
                // 改成不依賴容器歸屬，直接對整個可視範圍內的連結做拆字
                // 特徵偵測：只要「結構上符合拆字混淆」且「目前在畫面
                // 可視範圍附近（跟其他候選同一套視窗判斷）」，就記錄成
                // 「疑似時間戳但要 hover 才能拿到乾淨文字」的候選，不管
                // 它有沒有被包在某個特定容器裡。
                const allLinks = Array.from(root.querySelectorAll('a'));
                let scrambledFound = 0;
                for (const a of allLinks) {
                    if (scrambledFound >= 12) break;
                    // 便宜的前置判斷（修正：原本用 a.children.length
                    // 只算「直接」子元素數量，但拆字的 span 有時是包在
                    // 一層 <span> 容器裡面、不是 <a> 的直接子元素——像
                    // <a><span><span aria-labelledby>...<span>被拆字文字
                    // </span></span></span></a> 這種結構，<a> 的直接子
                    // 元素只有 1 個，會被錯誤跳過。改成只看「底下有沒有
                    // 任何深度的 span」，不管巢狀多深都抓得到，效能代價
                    // 也很小（只是存在性檢查，不是完整比對）。
                    if (!a.querySelector('span')) continue;
                    if (!looksLikeScrambledSpans(a)) continue;
                    const rect = a.getBoundingClientRect();
                    if (
                        rect.width <= 0 ||
                        rect.height <= 0 ||
                        rect.top > window.innerHeight * 1.8 ||
                        rect.bottom < -50
                    ) {
                        continue;
                    }
                    nodeSet.add(a);
                    scrambledFound += 1;
                }

                const nodes = Array.from(nodeSet).slice(0, maxLinks);

                const output = [];
                const skipped = [];
                const seen = new Set();
                const seenSkipped = new Set();

                const add = (el, value, source, href='', rawSourceEl=null) => {
                    const rawValue = String(value || '');

                    // 反混淆：原始字串裡就帶組合字元，或來源元素本身
                    // 是「一堆單字元 span」組成的，直接整條候選丟棄，
                    // 不要嘗試拼湊或拿去解析日期。記錄到 skipped，讓
                    // Python 端在真的沒有其他候選時，考慮對它做一次
                    // hover 補救（觸發 Facebook 自己的 tooltip 機制）。
                    if (
                        hasObfuscationMarks(rawValue) ||
                        looksLikeScrambledSpans(rawSourceEl)
                    ) {
                        if (href) {
                            // 用「原始屬性字串」而不是瀏覽器解析過的絕對
                            // 網址，因為之後 Python 端要用
                            // page.locator("a[href=...]") 依「DOM 上寫的
                            // 原始值」重新定位這個元素做 hover——兩者不
                            // 一致的話（例如相對路徑 vs 解析後的絕對
                            // 網址）會定位不到。
                            const rawHrefAttr =
                                el.getAttribute('href') || href;
                            const skey = rawHrefAttr;
                            if (!seenSkipped.has(skey)) {
                                seenSkipped.add(skey);
                                const rect = el.getBoundingClientRect();
                                skipped.push({
                                    href: clean(rawHrefAttr),
                                    top: rect.top + window.scrollY,
                                });
                            }
                        }
                        return;
                    }

                    const cleaned = clean(rawValue);
                    if (!cleaned || cleaned.length > 180) return;

                    const hrefLower = String(href || '').toLowerCase();
                    if (isCommentDateLink(hrefLower, el)) {
                        return;
                    }

                    const article =
                        el.closest("div[role='article']") ||
                        el.closest("[data-pagelet^='FeedUnit_']") ||
                        el.parentElement;

                    const articleText = clean(
                        article ? article.innerText : ''
                    ).toLowerCase();

                    if (
                        pinnedMarkers.some(
                            marker => articleText.includes(marker)
                        )
                    ) {
                        return;
                    }

                    const rect = el.getBoundingClientRect();
                    if (
                        rect.width <= 0 ||
                        rect.height <= 0 ||
                        rect.top > window.innerHeight * 1.8 ||
                        rect.bottom < -50
                    ) {
                        return;
                    }

                    const key = `${source}|${cleaned}|${href}`;
                    if (seen.has(key)) return;
                    seen.add(key);

                    output.push({
                        value: cleaned,
                        source,
                        href: clean(href),
                        top: rect.top + window.scrollY,
                        left: rect.left
                    });
                };

                for (const el of nodes) {
                    const href =
                        el.href ||
                        el.getAttribute('href') ||
                        '';

                    // aria-label / title / tooltip：原始屬性字串，不是
                    // 拆字 span 組成，不用做混淆偵測。
                    add(el, el.getAttribute('aria-label'), 'aria-label', href);
                    add(el, el.getAttribute('title'), 'title', href);
                    add(
                        el,
                        el.getAttribute('data-tooltip-content'),
                        'tooltip',
                        href
                    );

                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        const ids = labelledBy.split(/\s+/).filter(Boolean);
                        for (const id of ids) {
                            const labelEl = document.getElementById(id);
                            if (labelEl) {
                                // aria-labelledby 指到的元素常常就是畫面上
                                // 那段可能被拆字的 span 本身，要做混淆偵測。
                                add(
                                    el,
                                    labelEl.innerText || labelEl.textContent,
                                    'aria-labelledby',
                                    href,
                                    labelEl
                                );
                            }
                        }
                    }

                    const inner = clean(el.innerText || el.textContent);
                    if (inner.length <= 80) {
                        // innerText 一樣要對 el 本身做混淆偵測。
                        add(el, inner, 'innerText', href, el);
                    }
                }

                output.sort((a, b) => {
                    if (a.top !== b.top) return a.top - b.top;
                    return a.left - b.left;
                });
                skipped.sort((a, b) => a.top - b.top);

                return {candidates: output, skipped};
            }
            """,
            KOL_FAST_DATE_MAX_LINKS,
        )
    except Exception:
        rows = {}

    kol_fast_date_deadline_check(deadline)

    candidates: List[Tuple[str, str]] = []
    seen: Set[str] = set()

    if not isinstance(rows, dict):
        return candidates

    candidate_rows = rows.get("candidates")
    if not isinstance(candidate_rows, list):
        candidate_rows = []

    for row in candidate_rows:
        raw_value = kol_remove_obfuscation_chars(
            str(row.get("value") or "")
        )
        source = str(row.get("source") or "fast_time_block")

        if not raw_value:
            continue

        key = raw_value.casefold()
        if key in seen:
            continue
        seen.add(key)

        candidates.append((raw_value, source))

    if not candidates:
        # 保底方案：完全沒有找到候選，但頁面上有偵測到「疑似時間戳、
        # 但因為拆字混淆被丟棄」的連結時，對它做一次 hover——
        # 模擬真人滑鼠移過去看 tooltip 的動作，讓 Facebook 把乾淨文字
        # 接上 tooltip 再讀一次。只在真的沒有其他候選、且只做一次，
        # 控制在 deadline 預算內，不影響原本就找得到候選的多數情況。
        skipped_rows = rows.get("skipped")
        target_href = ""
        if isinstance(skipped_rows, list):
            for item in skipped_rows:
                href = str((item or {}).get("href") or "")
                if href:
                    target_href = href
                    break

        if target_href:
            try:
                kol_fast_date_deadline_check(deadline)

                escaped_href = target_href.replace('"', '\\"')
                locator = page.locator(f'a[href="{escaped_href}"]').first
                locator.hover(timeout=800)
                page.wait_for_timeout(400)
                kol_fast_date_deadline_check(deadline)

                tooltip_rows = page.evaluate(
                    r"""
                    (href) => {
                        const clean = (value) => String(value || '')
                            .replace(/[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u0300-\u036f]/g, '')
                            .replace(/\u00a0/g, ' ')
                            .replace(/\s+/g, ' ')
                            .trim();
                        const results = [];
                        const seenVals = new Set();
                        const pushVal = (value, source) => {
                            const v = clean(value);
                            if (!v || seenVals.has(v)) return;
                            seenVals.add(v);
                            results.push({value: v, source});
                        };
                        const el = document.querySelector(
                            `a[href="${href}"]`
                        );
                        if (el) {
                            pushVal(
                                el.getAttribute('aria-label'),
                                'hover_aria-label'
                            );
                            pushVal(el.getAttribute('title'), 'hover_title');
                            const labelledBy = el.getAttribute(
                                'aria-labelledby'
                            );
                            if (labelledBy) {
                                for (const id of labelledBy
                                    .split(/\s+/)
                                    .filter(Boolean)) {
                                    const labelEl =
                                        document.getElementById(id);
                                    if (labelEl) {
                                        pushVal(
                                            labelEl.innerText ||
                                                labelEl.textContent,
                                            'hover_aria-labelledby'
                                        );
                                    }
                                }
                            }
                        }
                        for (const tip of document.querySelectorAll(
                            "[role='tooltip'], div[aria-live='polite']"
                        )) {
                            pushVal(
                                tip.innerText || tip.textContent,
                                'hover_tooltip'
                            );
                        }
                        return results;
                    }
                    """,
                    target_href,
                )

                if isinstance(tooltip_rows, list):
                    for row in tooltip_rows:
                        raw_value = kol_remove_obfuscation_chars(
                            str(row.get("value") or "")
                        )
                        source = str(
                            row.get("source") or "hover_tooltip"
                        )
                        if not raw_value:
                            continue
                        key = raw_value.casefold()
                        if key in seen:
                            continue
                        seen.add(key)
                        candidates.append((raw_value, source))
            except KolFastDateTimeout:
                raise
            except Exception:
                pass

    return candidates



def kol_check_latest_post_date(
    page: Page,
    url: str,
    display_name: str,
    recent_days: Optional[int],
    save_debug: bool,
) -> KolDateResult:
    """
    Part 5.4 快速日期檢查。

    每個 KOL：
    - page.goto 最多 7 秒
    - 載入後等待貼文容器出現內容（最多 2.5 秒，等不到才退回固定等待 900ms）
    - window.stop()
    - 只掃描頁面頂部可見時間區塊
    - 整體日期分析最多 KOL_FAST_DATE_TIMEOUT_SECONDS 秒
    """
    # 修正：deadline（日期分析預算）原本從「開始載入頁面之前」就起算，
    # 但 page.goto 本身 timeout 就到 7 秒，加上後面等貼文內容出現最多
    # 再 2.5 秒，光是載入頁面最壞情況就要 9.5 秒，比整體預算還長——
    # 代表還沒開始真正掃描候選，預算就可能已經燒光，直接誤判成
    # 「超時」（尤其容易發生在個人檔案殼子先跑完、貼文區塊還在載入
    # 的情況）。
    #
    # 改成：先用網路層自己的 timeout（goto 7 秒、等待內容最多 2.5 秒）
    # 把頁面載入這段處理完，deadline 只從「頁面載入完成之後」開始算，
    # 專門管「掃描候選 + 必要時 hover 補救」這段，符合原本註解「整體
    # 日期分析最多 X 秒」的本意，不會因為單純網路慢就被誤判超時。
    candidates_with_source: List[Tuple[str, str]] = []

    try:
        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=7000,
            )
        except Exception:
            try:
                page.evaluate("window.stop()")
            except Exception:
                pass

        # 修正（根因：window.stop() 提早砍斷還在飛行中的貼文請求）：
        # Facebook 個人檔案頁是靠 GraphQL 非同步載入動態牆的 SPA，
        # domcontentloaded 觸發時通常只有外殼／個人資料卡，貼文內容是
        # 之後才用背景請求抓回來的。原本固定只等 900ms 就呼叫
        # window.stop()，常常會在貼文請求還沒回來前就把它砍斷，導致
        # 掃到的是空殼 role="article" 或只有先載入的留言通知卡，抓不到
        # 真正的貼文時間戳。
        #
        # 改成：優先等到「頁面上出現有實際內容的 article 容器」再繼續，
        # 最多等 2.5 秒；等不到才退回原本的固定等待，行為不會比舊版差，
        # 但貼文提早載入完成時反而比固定等 900ms 更快。
        try:
            page.wait_for_function(
                """
                () => {
                    const articles = document.querySelectorAll('[role="article"]');
                    for (const el of articles) {
                        if ((el.innerText || '').trim().length > 20) return true;
                    }
                    return false;
                }
                """,
                timeout=2500,
            )
        except Exception:
            page.wait_for_timeout(900)

        try:
            page.evaluate("window.stop()")
        except Exception:
            pass

        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

        page.wait_for_timeout(200)

        # deadline 從這裡才開始算（見上方修正說明）：只管接下來的
        # 「掃描候選 + 必要時 hover 補救」，不包含前面的頁面載入時間。
        deadline = time.monotonic() + KOL_FAST_DATE_TIMEOUT_SECONDS
        kol_fast_date_deadline_check(deadline)

        candidates_with_source = kol_fast_extract_time_candidates(
            page,
            deadline,
        )

        now = datetime.now()
        parsed_items: List[
            Tuple[Tuple[int, float], datetime, str, str]
        ] = []
        slow_path_attempted = False

        for raw_text, source in candidates_with_source:
            kol_fast_date_deadline_check(deadline)

            parsed = kol_parse_date_candidate(raw_text, now)
            if parsed is None:
                continue
            if parsed > now + timedelta(days=2):
                continue
            if parsed.year < 2004:
                continue

            priority = kol_candidate_priority(
                raw_text,
                source,
                parsed,
                now,
            )
            parsed_items.append(
                (priority, parsed, raw_text, source)
            )

        if not parsed_items:
            # 保底方案：快速路徑完全沒找到能解析的日期時，在真的判定
            # 失敗之前，退回原本就寫好、更完整的慢速掃描邏輯——
            # kol_find_first_post_container()（逐一檢查 article、跳過
            # 置頂、必要時反向找 story_message 祖先）+
            # kol_collect_date_candidates()（含 hover 拿 tooltip）。
            #
            # 這套邏輯本來就存在於程式裡，只是 Part 5.4 改用快速路徑後
            # 沒有接上，一直是沒人呼叫的孤兒程式碼。現在只在快速路徑
            # 真的失敗時才會啟用一次：多數帳號快速路徑就成功了，平均
            # 速度不受影響；只有真正棘手的案例才會多花這幾秒去換一次
            # 更完整、更慢的掃描機會。
            slow_path_attempted = True
            slow_candidates: List[Tuple[str, str]] = []
            try:
                slow_container = kol_find_first_post_container(page)
                if slow_container is not None:
                    slow_candidates = kol_collect_date_candidates(
                        page,
                        slow_container,
                    )
            except Exception:
                slow_candidates = []

            if slow_candidates:
                candidates_with_source = (
                    candidates_with_source + slow_candidates
                )
                for raw_text, source in slow_candidates:
                    parsed = kol_parse_date_candidate(raw_text, now)
                    if parsed is None:
                        continue
                    if parsed > now + timedelta(days=2):
                        continue
                    if parsed.year < 2004:
                        continue

                    priority = kol_candidate_priority(
                        raw_text,
                        source,
                        parsed,
                        now,
                    )
                    parsed_items.append(
                        (priority, parsed, raw_text, source)
                    )

        if not parsed_items:
            result = KolDateResult(
                success=False,
                post_date=None,
                raw_text="",
                source="fast_time_block",
                days_old=None,
                reason=(
                    "快速與慢速掃描都未取得可解析日期"
                    if slow_path_attempted
                    else "快速時間區塊未取得可解析日期"
                ),
                candidates=[
                    f"{source}: {value}"
                    for value, source in candidates_with_source
                ],
            )
            if save_debug:
                kol_save_date_debug(
                    page,
                    None,
                    url,
                    display_name,
                    candidates_with_source,
                    result.reason,
                )
            return result

        parsed_items.sort(key=lambda item: item[0])
        _priority, post_date, raw_text, source = parsed_items[0]

        days_old = max(
            0,
            (now.date() - post_date.date()).days,
        )

        if recent_days is not None and days_old > recent_days:
            return KolDateResult(
                success=False,
                post_date=post_date,
                raw_text=raw_text,
                source=source,
                days_old=days_old,
                reason=f"最新貼文超過 {recent_days} 天",
                candidates=[
                    f"{candidate_source}: {candidate_value}"
                    for candidate_value, candidate_source
                    in candidates_with_source
                ],
            )

        return KolDateResult(
            success=True,
            post_date=post_date,
            raw_text=raw_text,
            source=source,
            days_old=days_old,
            reason="符合最近發文條件",
            candidates=[
                f"{candidate_source}: {candidate_value}"
                for candidate_value, candidate_source
                in candidates_with_source
            ],
        )

    except KolFastDateTimeout as exc:
        result = KolDateResult(
            success=False,
            post_date=None,
            raw_text="",
            source="fast_timeout",
            days_old=None,
            reason=str(exc),
            candidates=[
                f"{source}: {value}"
                for value, source in candidates_with_source
            ],
        )
        if save_debug:
            kol_save_date_debug(
                page,
                None,
                url,
                display_name,
                candidates_with_source,
                result.reason,
            )
        return result

    except Exception as exc:
        result = KolDateResult(
            success=False,
            post_date=None,
            raw_text="",
            source="fast_exception",
            days_old=None,
            reason=f"快速日期檢查例外：{exc}",
            candidates=[
                f"{source}: {value}"
                for value, source in candidates_with_source
            ],
        )
        if save_debug:
            kol_save_date_debug(
                page,
                None,
                url,
                display_name,
                candidates_with_source,
                result.reason,
            )
        return result


def kol_search_keyword(
    worker_name: str,
    profile_name: str,
    page: Page,
    keyword: str,
    stop_event: threading.Event,
    minimum_followers: int,
    max_scrolls: int,
    scroll_distance: int,
    scroll_wait_ms: int,
    no_growth_limit: int,
    recent_days_limit: Optional[int],
    save_date_debug: bool,
    event_queue: "queue.Queue[tuple]",
    worker_id: int,
) -> Tuple[int, int]:
    search_url = (
        "https://www.facebook.com/search/people/?q="
        + quote(keyword, safe="")
    )

    try:
        page.goto(
            search_url,
            wait_until="domcontentloaded",
            timeout=45000,
        )
    except Exception:
        try:
            page.evaluate("window.stop()")
        except Exception:
            pass

    page.wait_for_timeout(2500)

    collected: Dict[str, Tuple[str, int, str]] = {}
    follower_samples: List[Dict[str, str]] = []
    no_growth = 0
    previous_article_count = 0
    previous_height = 0
    previous_fingerprint = ""
    highest_article_count = 0
    parsed_follower_total = 0
    processed_article_count = 0

    for scroll_index in range(max_scrolls):
        if stop_event.is_set():
            break

        articles = page.locator(
            "div[role='article'], "
            "div[role='listitem'], "
            "div[data-virtualized='false']"
        )
        (
            article_count,
            parsed_follower_cards,
            processed_article_count,
        ) = kol_collect_articles(
            articles=articles,
            minimum_followers=minimum_followers,
            collected=collected,
            follower_samples=follower_samples,
            keyword=keyword,
            profile_name=profile_name,
            start_index=processed_article_count,
        )
        parsed_follower_total = max(
            parsed_follower_total,
            parsed_follower_cards,
        )
        highest_article_count = max(
            highest_article_count,
            article_count,
        )

        try:
            page_state = page.evaluate(
                """
                () => ({
                    height: document.documentElement.scrollHeight || 0,
                    y: window.scrollY || document.documentElement.scrollTop || 0,
                    viewport: window.innerHeight || 0
                })
                """
            )
            current_height = int(page_state.get("height", 0))
            current_y = int(page_state.get("y", 0))
        except Exception:
            current_height = 0
            current_y = 0

        fingerprint = kol_get_last_result_fingerprint(articles)

        article_grew = article_count > previous_article_count
        height_grew = current_height > previous_height + 50
        fingerprint_changed = bool(
            fingerprint and fingerprint != previous_fingerprint
        )

        same_height_as_previous = bool(
            scroll_index > 0
            and previous_height > 0
            and current_height == previous_height
        )

        if article_grew or height_grew or fingerprint_changed:
            no_growth = 0
        else:
            no_growth += 1

        event_queue.put(
            (
                "kol_worker_update",
                worker_id,
                keyword,
                "",
                "",
                f"下滑 {scroll_index + 1}/{max_scrolls}｜"
                f"結果 {article_count}｜粉絲可解析 {parsed_follower_cards}｜"
                f"符合門檻 {len(collected)}",
            )
        )
        event_queue.put(
            (
                "kol_log",
                f"[{worker_name}] {keyword}｜"
                f"下滑 {scroll_index + 1}/{max_scrolls}｜"
                f"搜尋結果 {article_count}｜"
                f"粉絲可解析 {parsed_follower_cards}｜"
                f"符合 {minimum_followers}+ 粉絲：{len(collected)}｜"
                f"頁面高度 {current_height}｜位置 {current_y}｜"
                f"停滯 {no_growth}/{no_growth_limit}",
            )
        )

        if same_height_as_previous:
            event_queue.put(
                (
                    "kol_log",
                    f"[{worker_name}] {keyword}｜"
                    f"本輪頁面高度與上一輪相同 "
                    f"({current_height})，立即停止下滑並進入首頁日期檢查。",
                )
            )
            break

        if no_growth >= no_growth_limit:
            break

        previous_article_count = max(
            previous_article_count,
            article_count,
        )
        previous_height = max(
            previous_height,
            current_height,
        )
        previous_fingerprint = fingerprint or previous_fingerprint

        kol_scroll_search_results(
            page=page,
            articles=articles,
            scroll_distance=scroll_distance,
            scroll_wait_ms=scroll_wait_ms,
        )

    # 最後再分析一次目前完整 DOM。
    articles = page.locator(
        "div[role='article'], "
        "div[role='listitem'], "
        "div[data-virtualized='false']"
    )
    (
        final_article_count,
        final_parsed_count,
        processed_article_count,
    ) = kol_collect_articles(
        articles=articles,
        minimum_followers=minimum_followers,
        collected=collected,
        follower_samples=follower_samples,
        keyword=keyword,
        profile_name=profile_name,
        start_index=processed_article_count,
    )
    highest_article_count = max(
        highest_article_count,
        final_article_count,
    )
    parsed_follower_total = max(
        parsed_follower_total,
        final_parsed_count,
    )

    matched = len(collected)
    added = 0
    recent_days_value: Optional[int] = recent_days_limit

    if save_date_debug:
        follower_diag = kol_save_follower_diagnostics(
            keyword=keyword,
            profile_name=profile_name,
            article_samples=follower_samples,
        )
        if follower_diag:
            event_queue.put(
                (
                    "kol_log",
                    f"[{profile_name}] 粉絲 Diagnostics 已保存："
                    f"{follower_diag}",
                )
            )

    if matched == 0 and save_date_debug:
        diagnostic_folder = kol_save_diagnostics(
            page=page,
            stage="people_search_zero_result",
            keyword=keyword,
            profile_name=profile_name,
            reason=(
                f"搜尋結果中沒有解析到粉絲數達 "
                f"{minimum_followers:,} 的 People；"
                f"最高搜尋結果 {highest_article_count}；"
                f"可解析粉絲卡片 {parsed_follower_total}"
            ),
            extra_lines=[
                f"最大下滑次數：{max_scrolls}",
                f"下滑距離：{scroll_distance}",
                f"等待毫秒：{scroll_wait_ms}",
                f"停滯上限：{no_growth_limit}",
                f"最高搜尋結果數：{highest_article_count}",
                f"可解析粉絲數卡片：{parsed_follower_total}",
            ],
        )
        if diagnostic_folder:
            event_queue.put(
                (
                    "kol_log",
                    f"[{profile_name}] Diagnostics 已保存："
                    f"{diagnostic_folder}",
                )
            )
    existing_urls = kol_read_existing()

    for url, (display_name, followers, card_text) in collected.items():
        if stop_event.is_set():
            break

        if url in existing_urls:
            event_queue.put(
                (
                    "kol_worker_update",
                    worker_id,
                    keyword,
                    f"{followers:,}",
                    "",
                    f"已存在，略過：{display_name or url}",
                )
            )
            continue

        event_queue.put(
            (
                "kol_worker_update",
                worker_id,
                keyword,
                f"{followers:,}",
                "檢查中",
                f"進入首頁：{display_name or url}",
            )
        )
        event_queue.put(
            (
                "kol_log",
                f"[{profile_name}] 日期檢查｜"
                f"{display_name or '未取得名稱'}｜{url}",
            )
        )

        date_result = kol_check_latest_post_date(
            page=page,
            url=url,
            display_name=display_name,
            recent_days=recent_days_value,
            save_debug=save_date_debug,
        )

        display_date = (
            date_result.post_date.strftime("%Y-%m-%d")
            if date_result.post_date
            else "無法辨識"
        )

        if date_result.success:
            if kol_append_verified(
                keyword=keyword,
                url=url,
                display_name=display_name,
                followers=followers,
                source_profile=profile_name,
                card_text=card_text,
                date_result=date_result,
            ):
                added += 1
                existing_urls.add(url)

            event_queue.put(
                (
                    "kol_worker_update",
                    worker_id,
                    keyword,
                    f"{followers:,}",
                    display_date,
                    f"通過｜{date_result.days_old} 天｜已收集",
                )
            )
            event_queue.put(
                (
                    "kol_log",
                    f"[{profile_name}] 通過日期｜"
                    f"{display_name or '未取得名稱'}｜"
                    f"{display_date}｜{date_result.days_old} 天｜{url}",
                )
            )
        else:
            kol_record_failed(
                keyword=keyword,
                url=url,
                display_name=display_name,
                followers=followers,
                source_profile=profile_name,
                date_result=date_result,
            )
            if save_date_debug:
                kol_save_diagnostics(
                    page=page,
                    stage="date_check_failed",
                    keyword=keyword,
                    profile_name=profile_name,
                    reason=date_result.reason,
                    extra_lines=[
                        f"KOL網址：{url}",
                        f"顯示名稱：{display_name}",
                        f"粉絲數：{followers}",
                        f"原始日期：{date_result.raw_text}",
                        f"日期來源：{date_result.source}",
                        f"距今天數：{date_result.days_old}",
                    ],
                )
            event_queue.put(
                (
                    "kol_worker_update",
                    worker_id,
                    keyword,
                    f"{followers:,}",
                    display_date,
                    f"略過｜{date_result.reason}",
                )
            )
            event_queue.put(
                (
                    "kol_log",
                    f"[{profile_name}] 日期不通過｜"
                    f"{display_name or '未取得名稱'}｜"
                    f"{display_date}｜{date_result.reason}｜{url}",
                )
            )

    return matched, added


def kol_worker_main(
    worker_id: int,
    profile: AdsPowerProfile,
    task_queue: "queue.Queue[str]",
    stop_event: threading.Event,
    event_queue: "queue.Queue[tuple]",
    settings: Dict[str, object],
) -> None:
    worker_name = f"環境{worker_id} {profile.name}"
    event_queue.put(
        ("kol_worker_state", worker_id, profile.name, "啟動中")
    )
    kol_write_log(f"[{worker_name}] Worker 啟動｜{profile.user_id}")

    ws_url = kol_start_browser(profile)
    if not ws_url:
        event_queue.put(
            ("kol_worker_state", worker_id, profile.name, "啟動失敗")
        )
        event_queue.put(("kol_stat", "failed", 1))
        return

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(ws_url)
            page = kol_find_facebook_page(browser)
            page.set_default_timeout(30000)

            event_queue.put(
                ("kol_worker_state", worker_id, profile.name, "執行中")
            )
            kol_write_log(f"[{worker_name}] Playwright 已成功連接")

            while not stop_event.is_set():
                try:
                    keyword = task_queue.get_nowait()
                except queue.Empty:
                    break

                try:
                    event_queue.put(
                        (
                            "kol_worker_update",
                            worker_id,
                            keyword,
                            "",
                            "",
                            "People 搜尋中",
                        )
                    )

                    found, added = kol_search_keyword(
                        worker_name=worker_name,
                        profile_name=profile.name,
                        page=page,
                        keyword=keyword,
                        stop_event=stop_event,
                        minimum_followers=int(
                            settings["minimum_followers"]
                        ),
                        max_scrolls=int(settings["max_scrolls"]),
                        scroll_distance=int(settings["scroll_distance"]),
                        scroll_wait_ms=int(settings["scroll_wait_ms"]),
                        no_growth_limit=int(settings["no_growth_limit"]),
                        recent_days_limit=settings["recent_days"],
                        save_date_debug=bool(settings["save_date_debug"]),
                        event_queue=event_queue,
                        worker_id=worker_id,
                    )
                    kol_record_keyword(keyword)

                    event_queue.put(("kol_stat", "processed", 1))
                    event_queue.put(("kol_stat", "found", found))
                    event_queue.put(("kol_stat", "added", added))
                    event_queue.put(
                        (
                            "kol_worker_update",
                            worker_id,
                            keyword,
                            "",
                            "日期已檢查",
                            f"完成｜候選 {found}｜通過 {added}",
                        )
                    )
                    event_queue.put(
                        (
                            "kol_log",
                            f"[{profile.name}] {keyword} 完成｜"
                            f"符合粉絲條件 {found}｜日期通過 {added}",
                        )
                    )

                except Exception as exc:
                    diagnostic_folder = None

                    if bool(settings.get("save_date_debug", False)):
                        try:
                            diagnostic_folder = kol_save_diagnostics(
                                page=page,
                                stage="keyword_exception",
                                keyword=keyword,
                                profile_name=profile.name,
                                reason=str(exc),
                            )
                        except Exception as diagnostic_exc:
                            event_queue.put(
                                (
                                    "kol_log",
                                    f"[{profile.name}] Diagnostics 儲存失敗："
                                    f"{diagnostic_exc}",
                                )
                            )

                    if diagnostic_folder:
                        event_queue.put(
                            (
                                "kol_log",
                                f"[{profile.name}] Diagnostics 已保存："
                                f"{diagnostic_folder}",
                            )
                        )

                    try:
                        kol_record_keyword(keyword)
                    except Exception as history_exc:
                        event_queue.put(
                            (
                                "kol_log",
                                f"[{profile.name}] 關鍵字歷史記錄失敗："
                                f"{history_exc}",
                            )
                        )

                    event_queue.put(("kol_stat", "processed", 1))
                    event_queue.put(("kol_stat", "failed", 1))
                    event_queue.put(
                        (
                            "kol_worker_update",
                            worker_id,
                            keyword,
                            "",
                            "",
                            f"失敗，已跳過：{exc}",
                        )
                    )
                    event_queue.put(
                        (
                            "kol_log",
                            f"[{profile.name}] {keyword} 失敗，"
                            f"已跳過並繼續下一個關鍵字：{exc}",
                        )
                    )

                    try:
                        page.evaluate("window.stop()")
                    except Exception:
                        pass

                finally:
                    try:
                        task_queue.task_done()
                    except Exception:
                        pass

                if not stop_event.is_set():
                    time.sleep(KOL_BETWEEN_KEYWORDS_SECONDS)

            try:
                browser.close()
            except Exception:
                pass

    except Exception as exc:
        event_queue.put(("kol_stat", "failed", 1))
        event_queue.put(
            ("kol_worker_state", worker_id, profile.name, f"異常：{exc}")
        )
        event_queue.put(
            ("kol_log", f"[{profile.name}] Worker 異常：{exc}")
        )
        return

    event_queue.put(
        ("kol_worker_state", worker_id, profile.name, "完成")
    )





# ============================================================
# Part 5.4.1：從 Part 5.0 Stable 還原核心函式
# ============================================================
def kol_save_diagnostics(
    page: Page,
    stage: str,
    keyword: str,
    profile_name: str,
    reason: str,
    extra_lines: Optional[List[str]] = None,
    target_locator=None,
) -> Optional[Path]:
    """
    儲存一般 KOL 偵錯資料：
    - screenshot.png
    - page.html
    - target.html（如有）
    - diagnostics.txt
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    folder_name = (
        f"{timestamp}_"
        f"{kol_safe_filename(stage, 30)}_"
        f"{kol_safe_filename(keyword, 40)}"
    )
    folder = KOL_DIAGNOSTICS_DIR / folder_name

    try:
        folder.mkdir(parents=True, exist_ok=True)

        try:
            page.screenshot(
                path=str(folder / "screenshot.png"),
                full_page=False,
            )
        except Exception:
            pass

        try:
            (folder / "page.html").write_text(
                page.content(),
                encoding="utf-8",
                errors="ignore",
            )
        except Exception:
            pass

        if target_locator is not None:
            try:
                outer_html = target_locator.evaluate(
                    "(element) => element.outerHTML"
                )
                (folder / "target.html").write_text(
                    str(outer_html),
                    encoding="utf-8",
                    errors="ignore",
                )
            except Exception:
                pass

        lines = [
            f"時間：{now_text()}",
            f"階段：{stage}",
            f"關鍵字：{keyword}",
            f"環境：{profile_name}",
            f"原因：{reason}",
            f"目前網址：{page.url}",
        ]
        if extra_lines:
            lines.extend(extra_lines)

        (folder / "diagnostics.txt").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )
        return folder
    except Exception as exc:
        kol_write_log(f"Diagnostics 儲存失敗：{exc}")
        return None


def kol_save_follower_diagnostics(
    keyword: str,
    profile_name: str,
    article_samples: List[Dict[str, str]],
) -> Optional[Path]:
    """
    儲存 People 搜尋卡片粉絲解析診斷。
    每個關鍵字最多輸出一份 CSV，避免產生大量 HTML。
    """
    if not article_samples:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_keyword = kol_safe_filename(keyword, 50)
    path = (
        KOL_FOLLOWER_DIAGNOSTICS_DIR
        / f"{timestamp}_{safe_keyword}_follower_samples.csv"
    )

    try:
        with path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "序號",
                    "環境",
                    "關鍵字",
                    "解析粉絲數",
                    "卡片文字",
                    "網址",
                ],
            )
            writer.writeheader()
            for row in article_samples:
                writer.writerow(row)
        return path
    except Exception as exc:
        kol_write_log(f"粉絲 Diagnostics 儲存失敗：{exc}")
        return None


def kol_get_last_result_fingerprint(articles) -> str:
    try:
        count = articles.count()
        if count <= 0:
            return ""
        last = articles.nth(count - 1)

        links = last.locator("a[role='link'], a[href]")
        for index in range(min(links.count(), 15)):
            try:
                href = links.nth(index).get_attribute("href")
                normalized = kol_normalize_facebook_url(href)
                if normalized:
                    return f"url:{normalized}"
            except Exception:
                continue

        value = re.sub(
            r"\s+",
            " ",
            last.inner_text(timeout=1800),
        ).strip()
        return f"text:{value[:180]}"
    except Exception:
        return ""


def kol_scroll_search_results(
    page: Page,
    articles,
    scroll_distance: int,
    scroll_wait_ms: int,
) -> None:
    count = articles.count()

    if count > 0:
        last = articles.nth(count - 1)
        try:
            last.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            try:
                last.evaluate(
                    "(el) => el.scrollIntoView({block:'end', inline:'nearest'})"
                )
            except Exception:
                pass

        try:
            box = last.bounding_box()
            viewport = page.viewport_size or {"width": 1280, "height": 720}
            if box:
                x = max(50, min(
                    box["x"] + box["width"] / 2,
                    viewport["width"] - 50,
                ))
                y = max(100, min(
                    box["y"] + min(box["height"] / 2, 220),
                    viewport["height"] - 100,
                ))
                page.mouse.move(x, y)
            else:
                page.mouse.move(
                    viewport["width"] * 0.65,
                    viewport["height"] * 0.68,
                )
        except Exception:
            pass

    try:
        page.mouse.wheel(0, scroll_distance)
    except Exception:
        pass

    page.wait_for_timeout(450)

    try:
        page.evaluate(
            """
            (amount) => {
                const pixels = Math.max(
                    Number(amount) || 0,
                    window.innerHeight * 0.9,
                    900
                );
                window.scrollBy({
                    top: pixels,
                    left: 0,
                    behavior: 'instant'
                });
            }
            """,
            scroll_distance,
        )
    except Exception:
        pass

    page.wait_for_timeout(scroll_wait_ms)


def kol_collect_articles(
    articles,
    minimum_followers: int,
    collected: Dict[str, Tuple[str, int, str]],
    follower_samples: Optional[List[Dict[str, str]]] = None,
    keyword: str = "",
    profile_name: str = "",
    start_index: int = 0,
) -> Tuple[int, int, int]:
    """
    快速收集目前 DOM 中新增的 People 搜尋卡片。

    回傳：
    - 目前卡片總數
    - 本輪有解析到粉絲數的卡片數
    - 本輪實際掃描到的最後索引

    重要優化：
    1. 不再每輪重新掃描全部舊卡片。
    2. 沒解析到粉絲數時，不再浪費時間尋找網址。
    3. 低於粉絲門檻時，也不再尋找網址。
    """
    try:
        article_count = min(articles.count(), 2500)
    except Exception:
        return 0, 0, start_index

    safe_start = max(0, min(start_index, article_count))
    parsed_follower_cards = 0

    for index in range(safe_start, article_count):
        article = articles.nth(index)
        try:
            card_text = kol_read_card_text(article).strip()
            followers = kol_parse_followers(card_text)

            if follower_samples is not None and len(follower_samples) < 300:
                follower_samples.append(
                    {
                        "序號": str(index + 1),
                        "環境": profile_name,
                        "關鍵字": keyword,
                        "解析粉絲數": (
                            str(followers)
                            if followers is not None
                            else ""
                        ),
                        "卡片文字": card_text.replace("\n", " "),
                        "網址": "",
                    }
                )

            if followers is None:
                continue

            parsed_follower_cards += 1

            if followers < minimum_followers:
                continue

            # 只有粉絲達標才解析網址，節省大量 DOM 操作。
            url = kol_get_best_article_link(article)
            if not url:
                continue

            if follower_samples is not None:
                for sample in reversed(follower_samples):
                    if sample.get("序號") == str(index + 1):
                        sample["網址"] = url
                        break

            display_name = kol_extract_display_name(article)
            previous = collected.get(url)
            if previous is None or followers > previous[1]:
                collected[url] = (
                    display_name,
                    followers,
                    card_text,
                )
        except Exception:
            continue

    return article_count, parsed_follower_cards, article_count


def kol_validate_runtime_integrity() -> Tuple[bool, List[str]]:
    """
    檢查 KOL 執行階段必要函式是否完整。

    本版已從 Part 5.0 Stable 還原缺失核心函式；
    啟動前再做一次完整性檢查，
    避免執行中才出現 NameError。
    """
    required_names = [
        "kol_scroll_search_results",
        "kol_get_last_result_fingerprint",
        "kol_save_diagnostics",
        "kol_save_follower_diagnostics",
        "kol_collect_articles",
        "kol_check_latest_post_date",
        "kol_fast_extract_time_candidates",
        "kol_remove_obfuscation_chars",
    ]

    missing: List[str] = []
    namespace = globals()

    for name in required_names:
        value = namespace.get(name)
        if not callable(value):
            missing.append(name)

    return len(missing) == 0, missing



# ============================================================
# GUI 主程式
# ============================================================
class FacebookSearchToolboxApp:
    def __init__(self, root: tk.Tk) -> None:
        ensure_paths()

        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1200x820")
        self.root.minsize(1080, 720)

        self.profiles: List[AdsPowerProfile] = []

        self.message_queue: "queue.Queue[tuple]" = queue.Queue()

        self.group_stop_event = threading.Event()
        self.kol_stop_event = threading.Event()

        self.group_running = False
        self.kol_running = False

        self.group_threads: List[threading.Thread] = []
        self.group_task_queue: Optional["queue.Queue[str]"] = None
        self.kol_threads: List[threading.Thread] = []
        self.kol_task_queue: Optional["queue.Queue[str]"] = None

        self.group_stats = RuntimeStats()
        self.kol_stats = RuntimeStats()

        self.group_id_var = tk.StringVar(
            value=DEFAULT_ADSPOWER_GROUP_ID
        )
        self.selected_count_var = tk.StringVar(value="已選環境：0")
        self.global_status_var = tk.StringVar(value="待命")

        self.group_status_var = tk.StringVar(value="尚未開始")
        self.kol_status_var = tk.StringVar(value="尚未開始")

        self.group_min_posts_var = tk.IntVar(value=10)
        self.group_public_only_var = tk.BooleanVar(value=False)
        self.group_max_scrolls_var = tk.IntVar(value=40)
        self.group_scroll_distance_var = tk.IntVar(value=3500)
        self.group_scroll_wait_var = tk.IntVar(value=1800)

        self.kol_min_followers_var = tk.IntVar(value=1000)
        self.kol_recent_days_var = tk.StringVar(value="30")
        self.kol_max_scrolls_var = tk.IntVar(value=60)
        self.kol_no_growth_limit_var = tk.IntVar(value=8)
        self.kol_scroll_distance_var = tk.IntVar(value=1800)
        self.kol_scroll_wait_var = tk.IntVar(value=2500)
        self.kol_save_debug_var = tk.BooleanVar(value=True)

        self.group_keyword_count_var = tk.StringVar(value="關鍵字：0")
        self.group_processed_var = tk.StringVar(value="已處理：0")
        self.group_found_var = tk.StringVar(value="找到：0")
        self.group_added_var = tk.StringVar(value="新增：0")
        self.group_failed_var = tk.StringVar(value="失敗：0")

        self.kol_keyword_count_var = tk.StringVar(value="關鍵字：0")
        self.kol_processed_var = tk.StringVar(value="已處理：0")
        self.kol_found_var = tk.StringVar(value="找到：0")
        self.kol_added_var = tk.StringVar(value="新增：0")
        self.kol_failed_var = tk.StringVar(value="失敗：0")

        self.group_total_count_var = tk.StringVar(value="Group：0")
        self.kol_total_count_var = tk.StringVar(value="KOL：0")

        # 優化項目 2：檔案計數改為「有新資料才重讀」，這個旗標記錄目前
        # 顯示的計數是否已過期。啟動時先算一次，之後只在有新增資料時才刷新。
        self._file_counts_dirty = True

        self._configure_styles()
        self._build_ui()
        self._refresh_file_counts()
        self._file_counts_dirty = False

        self.root.after(250, self._poll_messages)

    # --------------------------------------------------------
    # 樣式
    # --------------------------------------------------------
    def _configure_styles(self) -> None:
        style = ttk.Style()

        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        style.configure(
            "AppTitle.TLabel",
            font=("Microsoft JhengHei UI", 17, "bold"),
        )
        style.configure(
            "SectionTitle.TLabel",
            font=("Microsoft JhengHei UI", 11, "bold"),
        )
        style.configure(
            "Bold.TLabel",
            font=("Microsoft JhengHei UI", 10, "bold"),
        )
        style.configure(
            "Primary.TButton",
            font=("Microsoft JhengHei UI", 11, "bold"),
            padding=(12, 7),
        )
        style.configure(
            "Stop.TButton",
            font=("Microsoft JhengHei UI", 11, "bold"),
            padding=(12, 7),
        )
        style.configure(
            "Treeview",
            font=("Microsoft JhengHei UI", 9),
            rowheight=27,
        )
        style.configure(
            "Treeview.Heading",
            font=("Microsoft JhengHei UI", 9, "bold"),
        )

    # --------------------------------------------------------
    # 主介面
    # --------------------------------------------------------
    def _build_ui(self) -> None:
        title_frame = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        title_frame.pack(fill="x")

        ttk.Label(
            title_frame,
            text=APP_TITLE,
            style="AppTitle.TLabel",
        ).pack(side="left")

        ttk.Label(
            title_frame,
            text=APP_VERSION,
        ).pack(side="right", padx=8)

        self._build_adspower_section()

        body = ttk.Panedwindow(
            self.root,
            orient="horizontal",
        )
        body.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=(0, 8),
        )

        profile_frame = ttk.LabelFrame(
            body,
            text="AdsPower 環境",
            padding=8,
        )
        content_frame = ttk.Frame(body)

        body.add(profile_frame, weight=1)
        body.add(content_frame, weight=4)

        self._build_profile_list(profile_frame)
        self._build_notebook(content_frame)
        self._build_status_bar()

    def _build_adspower_section(self) -> None:
        frame = ttk.LabelFrame(
            self.root,
            text="共用 AdsPower 設定",
            padding=10,
        )
        frame.pack(
            fill="x",
            padx=12,
            pady=(0, 8),
        )

        ttk.Label(
            frame,
            text="AdsPower Group ID：",
            style="Bold.TLabel",
        ).grid(
            row=0,
            column=0,
            sticky="w",
        )

        ttk.Entry(
            frame,
            textvariable=self.group_id_var,
            width=24,
        ).grid(
            row=0,
            column=1,
            padx=(6, 12),
            sticky="w",
        )

        self.load_profiles_button = ttk.Button(
            frame,
            text="讀取環境",
            command=self.load_profiles,
        )
        self.load_profiles_button.grid(
            row=0,
            column=2,
            padx=4,
        )

        ttk.Button(
            frame,
            text="全選",
            command=self.select_all_profiles,
        ).grid(
            row=0,
            column=3,
            padx=4,
        )

        ttk.Button(
            frame,
            text="清除選取",
            command=self.clear_profile_selection,
        ).grid(
            row=0,
            column=4,
            padx=4,
        )

        ttk.Label(
            frame,
            textvariable=self.selected_count_var,
            style="Bold.TLabel",
        ).grid(
            row=0,
            column=5,
            padx=(15, 0),
            sticky="w",
        )

        ttk.Label(
            frame,
            text=(
                "Group 與 KOL 共用同一份環境清單；"
                "執行其中一個功能時，另一個功能會暫時禁止啟動。"
            ),
        ).grid(
            row=1,
            column=0,
            columnspan=6,
            pady=(8, 0),
            sticky="w",
        )

    def _build_profile_list(self, parent: ttk.LabelFrame) -> None:
        wrapper = ttk.Frame(parent)
        wrapper.pack(fill="both", expand=True)

        self.profile_listbox = tk.Listbox(
            wrapper,
            selectmode=tk.EXTENDED,
            exportselection=False,
            font=("Microsoft JhengHei UI", 10),
        )
        scrollbar = ttk.Scrollbar(
            wrapper,
            orient="vertical",
            command=self.profile_listbox.yview,
        )

        self.profile_listbox.configure(
            yscrollcommand=scrollbar.set
        )
        self.profile_listbox.pack(
            side="left",
            fill="both",
            expand=True,
        )
        scrollbar.pack(
            side="right",
            fill="y",
        )

        self.profile_listbox.bind(
            "<<ListboxSelect>>",
            self._on_profile_selection_changed,
        )

        ttk.Label(
            parent,
            text=(
                "可使用 Ctrl 或 Shift 複選。\n"
                "後續 Group 與 KOL 將依選取環境並行執行。"
            ),
            justify="left",
        ).pack(
            fill="x",
            pady=(8, 0),
        )

    def _build_notebook(self, parent: ttk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill="both", expand=True)

        self.group_tab = ttk.Frame(self.notebook, padding=10)
        self.kol_tab = ttk.Frame(self.notebook, padding=10)
        self.page_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(
            self.group_tab,
            text="Group 網址收集",
        )
        self.notebook.add(
            self.kol_tab,
            text="KOL 網址收集",
        )
        self.notebook.add(
            self.page_tab,
            text="KOL TELEGRAM 收集器",
        )

        self._build_group_tab()
        self._build_kol_tab()
        self._build_page_tab()

    # --------------------------------------------------------
    # Group 頁籤
    # --------------------------------------------------------
    def _build_group_tab(self) -> None:
        settings = ttk.LabelFrame(
            self.group_tab,
            text="Group 收集設定",
            padding=10,
        )
        settings.pack(fill="x")

        ttk.Label(
            settings,
            text="關鍵字檔：",
            style="Bold.TLabel",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            settings,
            text=str(GROUP_KEYWORDS_FILE),
        ).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="w",
            padx=6,
        )

        ttk.Button(
            settings,
            text="開啟關鍵字檔",
            command=lambda: open_path(GROUP_KEYWORDS_FILE),
        ).grid(
            row=0,
            column=4,
            padx=4,
        )

        ttk.Label(
            settings,
            text="最低每日貼文數：",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=1,
            to=100000,
            textvariable=self.group_min_posts_var,
            width=10,
        ).grid(
            row=1,
            column=1,
            sticky="w",
            padx=6,
            pady=(8, 0),
        )

        ttk.Checkbutton(
            settings,
            text="只收 Public Group",
            variable=self.group_public_only_var,
        ).grid(
            row=1,
            column=2,
            sticky="w",
            padx=10,
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="最大下滑：",
        ).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=1,
            to=500,
            textvariable=self.group_max_scrolls_var,
            width=10,
        ).grid(
            row=2,
            column=1,
            sticky="w",
            padx=6,
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="下滑距離：",
        ).grid(
            row=2,
            column=2,
            sticky="e",
            padx=(8, 4),
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=100,
            to=20000,
            increment=100,
            textvariable=self.group_scroll_distance_var,
            width=10,
        ).grid(
            row=2,
            column=3,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="等待毫秒：",
        ).grid(
            row=2,
            column=4,
            sticky="e",
            padx=(8, 4),
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=100,
            to=30000,
            increment=100,
            textvariable=self.group_scroll_wait_var,
            width=10,
        ).grid(
            row=2,
            column=5,
            sticky="w",
            pady=(8, 0),
        )

        controls = ttk.Frame(self.group_tab)
        controls.pack(fill="x", pady=(10, 8))

        self.group_start_button = ttk.Button(
            controls,
            text="開始 Group 收集",
            style="Primary.TButton",
            command=self.start_group_collection,
        )
        self.group_start_button.pack(side="left")

        self.group_stop_button = ttk.Button(
            controls,
            text="停止",
            style="Stop.TButton",
            command=self.stop_group_collection,
            state="disabled",
        )
        self.group_stop_button.pack(
            side="left",
            padx=8,
        )

        ttk.Button(
            controls,
            text="開啟 group.txt",
            command=lambda: open_path(GROUP_OUTPUT_FILE),
        ).pack(side="left", padx=4)

        ttk.Button(
            controls,
            text="開啟 Group LOG",
            command=lambda: open_path(GROUP_RUN_LOG),
        ).pack(side="left", padx=4)

        ttk.Label(
            controls,
            textvariable=self.group_status_var,
            style="Bold.TLabel",
        ).pack(side="right")

        self.group_progress = ttk.Progressbar(
            self.group_tab,
            mode="determinate",
            maximum=100,
        )
        self.group_progress.pack(
            fill="x",
            pady=(0, 8),
        )

        self._build_group_summary()
        self.group_tree = self._build_group_tree(self.group_tab)

        ttk.Label(
            self.group_tab,
            text="Group 即時 LOG",
            style="SectionTitle.TLabel",
        ).pack(anchor="w", pady=(8, 4))

        self.group_log_text = self._build_log_box(self.group_tab)

    def _build_group_summary(self) -> None:
        frame = ttk.Frame(self.group_tab)
        frame.pack(fill="x", pady=(0, 8))

        for variable in (
            self.group_keyword_count_var,
            self.group_processed_var,
            self.group_found_var,
            self.group_added_var,
            self.group_failed_var,
        ):
            ttk.Label(
                frame,
                textvariable=variable,
                style="Bold.TLabel",
            ).pack(side="left", padx=(0, 18))

    def _build_group_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)

        columns = (
            "worker",
            "profile",
            "keyword",
            "status",
            "found",
            "added",
        )

        tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=10,
        )

        definitions = {
            "worker": ("環境", 65),
            "profile": ("環境名稱", 170),
            "keyword": ("目前關鍵字", 260),
            "status": ("狀態", 180),
            "found": ("找到", 70),
            "added": ("新增", 70),
        }

        for column, (title, width) in definitions.items():
            tree.heading(column, text=title)
            tree.column(
                column,
                width=width,
                anchor="center",
            )

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=tree.yview,
        )
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(
            side="left",
            fill="both",
            expand=True,
        )
        scrollbar.pack(
            side="right",
            fill="y",
        )

        return tree

    # --------------------------------------------------------
    # KOL 頁籤
    # --------------------------------------------------------
    def _build_kol_tab(self) -> None:
        settings = ttk.LabelFrame(
            self.kol_tab,
            text="KOL 收集設定",
            padding=10,
        )
        settings.pack(fill="x")

        ttk.Label(
            settings,
            text="關鍵字檔：",
            style="Bold.TLabel",
        ).grid(row=0, column=0, sticky="w")

        ttk.Label(
            settings,
            text=str(KOL_KEYWORDS_FILE),
        ).grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="w",
            padx=6,
        )

        ttk.Button(
            settings,
            text="開啟關鍵字檔",
            command=lambda: open_path(KOL_KEYWORDS_FILE),
        ).grid(
            row=0,
            column=4,
            padx=4,
        )

        ttk.Label(
            settings,
            text="最低粉絲數：",
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=0,
            to=1000000000,
            increment=100,
            textvariable=self.kol_min_followers_var,
            width=12,
        ).grid(
            row=1,
            column=1,
            sticky="w",
            padx=6,
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="最近發文：",
        ).grid(
            row=1,
            column=2,
            sticky="e",
            padx=(15, 4),
            pady=(8, 0),
        )

        ttk.Combobox(
            settings,
            textvariable=self.kol_recent_days_var,
            values=("30", "60", "90", "不限"),
            state="readonly",
            width=10,
        ).grid(
            row=1,
            column=3,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Checkbutton(
            settings,
            text="保存 Diagnostics（搜尋／日期／例外）",
            variable=self.kol_save_debug_var,
        ).grid(
            row=1,
            column=4,
            columnspan=2,
            sticky="w",
            padx=12,
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="最大下滑：",
        ).grid(
            row=2,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=1,
            to=500,
            textvariable=self.kol_max_scrolls_var,
            width=10,
        ).grid(
            row=2,
            column=1,
            sticky="w",
            padx=6,
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="停滯上限：",
        ).grid(
            row=2,
            column=2,
            sticky="e",
            padx=(15, 4),
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=1,
            to=100,
            textvariable=self.kol_no_growth_limit_var,
            width=10,
        ).grid(
            row=2,
            column=3,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="下滑距離：",
        ).grid(
            row=3,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=100,
            to=20000,
            increment=100,
            textvariable=self.kol_scroll_distance_var,
            width=10,
        ).grid(
            row=3,
            column=1,
            sticky="w",
            padx=6,
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text="等待毫秒：",
        ).grid(
            row=3,
            column=2,
            sticky="e",
            padx=(15, 4),
            pady=(8, 0),
        )

        ttk.Spinbox(
            settings,
            from_=100,
            to=30000,
            increment=100,
            textvariable=self.kol_scroll_wait_var,
            width=10,
        ).grid(
            row=3,
            column=3,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Label(
            settings,
            text=(
                "固定搜尋 People；粉絲數達標後才進入個人頁，"
                "再確認最新貼文日期。"
            ),
        ).grid(
            row=4,
            column=0,
            columnspan=6,
            sticky="w",
            pady=(8, 0),
        )

        controls = ttk.Frame(self.kol_tab)
        controls.pack(fill="x", pady=(10, 8))

        self.kol_start_button = ttk.Button(
            controls,
            text="開始 KOL 收集",
            style="Primary.TButton",
            command=self.start_kol_collection,
        )
        self.kol_start_button.pack(side="left")

        self.kol_stop_button = ttk.Button(
            controls,
            text="停止",
            style="Stop.TButton",
            command=self.stop_kol_collection,
            state="disabled",
        )
        self.kol_stop_button.pack(
            side="left",
            padx=8,
        )

        ttk.Button(
            controls,
            text="開啟 kolurl.txt",
            command=lambda: open_path(KOL_OUTPUT_FILE),
        ).pack(side="left", padx=4)

        ttk.Button(
            controls,
            text="開啟 KOL LOG",
            command=lambda: open_path(KOL_RUN_LOG),
        ).pack(side="left", padx=4)

        ttk.Button(
            controls,
            text="開啟 Diagnostics",
            command=lambda: self._open_directory(KOL_DIAGNOSTICS_DIR),
        ).pack(side="left", padx=4)

        ttk.Button(
            controls,
            text="開啟粉絲 Diagnostics",
            command=lambda: self._open_directory(
                KOL_FOLLOWER_DIAGNOSTICS_DIR
            ),
        ).pack(side="left", padx=4)

        ttk.Label(
            controls,
            textvariable=self.kol_status_var,
            style="Bold.TLabel",
        ).pack(side="right")

        self.kol_progress = ttk.Progressbar(
            self.kol_tab,
            mode="determinate",
            maximum=100,
        )
        self.kol_progress.pack(
            fill="x",
            pady=(0, 8),
        )

        ttk.Label(
            self.kol_tab,
            text="KOL 即時 LOG",
            style="SectionTitle.TLabel",
        ).pack(anchor="w", pady=(2, 4))

        self.kol_log_text = self._build_log_box(self.kol_tab)
        self.kol_log_text.configure(height=10)

        self._build_kol_summary()
        self.kol_tree = self._build_kol_tree(self.kol_tab)

    def _build_kol_summary(self) -> None:
        frame = ttk.Frame(self.kol_tab)
        frame.pack(fill="x", pady=(0, 8))

        for variable in (
            self.kol_keyword_count_var,
            self.kol_processed_var,
            self.kol_found_var,
            self.kol_added_var,
            self.kol_failed_var,
        ):
            ttk.Label(
                frame,
                textvariable=variable,
                style="Bold.TLabel",
            ).pack(side="left", padx=(0, 18))

    def _build_kol_tree(self, parent: ttk.Frame) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)

        columns = (
            "worker",
            "profile",
            "keyword",
            "followers",
            "date",
            "status",
        )

        tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=10,
        )

        definitions = {
            "worker": ("環境", 65),
            "profile": ("環境名稱", 160),
            "keyword": ("目前關鍵字", 210),
            "followers": ("粉絲數", 90),
            "date": ("最新貼文日期", 150),
            "status": ("狀態", 210),
        }

        for column, (title, width) in definitions.items():
            tree.heading(column, text=title)
            tree.column(
                column,
                width=width,
                anchor="center",
            )

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=tree.yview,
        )
        tree.configure(yscrollcommand=scrollbar.set)

        tree.pack(
            side="left",
            fill="both",
            expand=True,
        )
        scrollbar.pack(
            side="right",
            fill="y",
        )

        return tree

    # --------------------------------------------------------
    # KOL TELEGRAM 頁籤（共用 KOL 收集引擎）
    # --------------------------------------------------------
    def _build_page_tab(self) -> None:
        settings = ttk.LabelFrame(
            self.page_tab,
            text="KOL TELEGRAM 收集設定",
            padding=10,
        )
        settings.pack(fill="x")

        ttk.Label(settings, text="關鍵字檔：", style="Bold.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(settings, text=str(KOL_KEYWORDS_FILE)).grid(
            row=0, column=1, columnspan=3, sticky="w", padx=6
        )
        ttk.Button(
            settings,
            text="開啟關鍵字檔",
            command=lambda: open_path(KOL_KEYWORDS_FILE),
        ).grid(row=0, column=4, padx=4)

        fields = (
            ("最低粉絲數：", self.kol_min_followers_var, 0, 1000000000, 100),
            ("最大下滑：", self.kol_max_scrolls_var, 1, 500, 1),
            ("下滑距離：", self.kol_scroll_distance_var, 100, 20000, 100),
        )
        for row, (label, variable, minimum, maximum, increment) in enumerate(
            fields, start=1
        ):
            ttk.Label(settings, text=label).grid(
                row=row, column=0, sticky="w", pady=(8, 0)
            )
            ttk.Spinbox(
                settings,
                from_=minimum,
                to=maximum,
                increment=increment,
                textvariable=variable,
                width=12,
            ).grid(row=row, column=1, sticky="w", padx=6, pady=(8, 0))

        ttk.Label(settings, text="最近發文：").grid(
            row=1, column=2, sticky="e", padx=(15, 4), pady=(8, 0)
        )
        ttk.Combobox(
            settings,
            textvariable=self.kol_recent_days_var,
            values=("30", "60", "90", "不限"),
            state="readonly",
            width=10,
        ).grid(row=1, column=3, sticky="w", pady=(8, 0))

        ttk.Label(settings, text="停滯上限：").grid(
            row=2, column=2, sticky="e", padx=(15, 4), pady=(8, 0)
        )
        ttk.Spinbox(
            settings,
            from_=1,
            to=100,
            textvariable=self.kol_no_growth_limit_var,
            width=10,
        ).grid(row=2, column=3, sticky="w", pady=(8, 0))

        ttk.Label(settings, text="等待毫秒：").grid(
            row=3, column=2, sticky="e", padx=(15, 4), pady=(8, 0)
        )
        ttk.Spinbox(
            settings,
            from_=100,
            to=30000,
            increment=100,
            textvariable=self.kol_scroll_wait_var,
            width=10,
        ).grid(row=3, column=3, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            settings,
            text="保存 Diagnostics（搜尋／日期／例外）",
            variable=self.kol_save_debug_var,
        ).grid(row=1, column=4, columnspan=2, sticky="w", padx=12, pady=(8, 0))

        ttk.Label(
            settings,
            text="複製 KOL People 搜尋功能；粉絲數達標後再檢查最新貼文日期。",
        ).grid(row=4, column=0, columnspan=6, sticky="w", pady=(8, 0))

        controls = ttk.Frame(self.page_tab)
        controls.pack(fill="x", pady=(10, 8))
        self.telegram_start_button = ttk.Button(
            controls,
            text="開始 KOL TELEGRAM 收集",
            style="Primary.TButton",
            command=self.start_kol_collection,
        )
        self.telegram_start_button.pack(side="left")
        self.telegram_stop_button = ttk.Button(
            controls,
            text="停止",
            style="Stop.TButton",
            command=self.stop_kol_collection,
            state="disabled",
        )
        self.telegram_stop_button.pack(side="left", padx=8)

        for label, target in (
            ("開啟 kolurl.txt", KOL_OUTPUT_FILE),
            ("開啟 KOL LOG", KOL_RUN_LOG),
        ):
            ttk.Button(
                controls, text=label, command=lambda path=target: open_path(path)
            ).pack(side="left", padx=4)
        ttk.Button(
            controls,
            text="開啟 Diagnostics",
            command=lambda: self._open_directory(KOL_DIAGNOSTICS_DIR),
        ).pack(side="left", padx=4)
        ttk.Button(
            controls,
            text="開啟粉絲 Diagnostics",
            command=lambda: self._open_directory(KOL_FOLLOWER_DIAGNOSTICS_DIR),
        ).pack(side="left", padx=4)
        ttk.Label(
            controls, textvariable=self.kol_status_var, style="Bold.TLabel"
        ).pack(side="right")

        self.telegram_progress = ttk.Progressbar(
            self.page_tab, mode="determinate", maximum=100
        )
        self.telegram_progress.pack(fill="x", pady=(0, 8))
        ttk.Label(
            self.page_tab,
            text="KOL TELEGRAM 即時 LOG",
            style="SectionTitle.TLabel",
        ).pack(anchor="w", pady=(2, 4))
        self.telegram_log_text = self._build_log_box(self.page_tab)
        self.telegram_log_text.configure(height=10)

        summary = ttk.Frame(self.page_tab)
        summary.pack(fill="x", pady=(0, 8))
        for variable in (
            self.kol_keyword_count_var,
            self.kol_processed_var,
            self.kol_found_var,
            self.kol_added_var,
            self.kol_failed_var,
        ):
            ttk.Label(summary, textvariable=variable, style="Bold.TLabel").pack(
                side="left", padx=(0, 18)
            )
        self.telegram_tree = self._build_kol_tree(self.page_tab)

    # --------------------------------------------------------
    # 共用 LOG
    # --------------------------------------------------------
    def _build_log_box(self, parent: ttk.Frame) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=False)

        text = tk.Text(
            frame,
            height=7,
            wrap="word",
            font=("Consolas", 9),
            state="disabled",
        )
        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=text.yview,
        )
        text.configure(yscrollcommand=scrollbar.set)

        text.pack(
            side="left",
            fill="both",
            expand=True,
        )
        scrollbar.pack(
            side="right",
            fill="y",
        )

        return text

    def _open_directory(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(path))
        except AttributeError:
            messagebox.showinfo("資料夾位置", str(path))
        except Exception as exc:
            messagebox.showerror("開啟失敗", str(exc))

    def _append_log(
        self,
        target: tk.Text,
        message: str,
    ) -> None:
        target.configure(state="normal")
        target.insert(
            tk.END,
            f"[{now_text()}] {message}\n",
        )
        target.see(tk.END)
        target.configure(state="disabled")

    # --------------------------------------------------------
    # 狀態列
    # --------------------------------------------------------
    def _build_status_bar(self) -> None:
        frame = ttk.Frame(
            self.root,
            padding=(12, 5),
        )
        frame.pack(fill="x")

        ttk.Label(
            frame,
            textvariable=self.group_total_count_var,
            style="Bold.TLabel",
        ).pack(side="left", padx=(0, 20))

        ttk.Label(
            frame,
            textvariable=self.kol_total_count_var,
            style="Bold.TLabel",
        ).pack(side="left", padx=(0, 20))

        ttk.Label(
            frame,
            text="目前執行：",
        ).pack(side="left")

        ttk.Label(
            frame,
            textvariable=self.global_status_var,
            style="Bold.TLabel",
        ).pack(side="left", padx=5)

        ttk.Label(
            frame,
            text=f"{APP_TITLE}｜{APP_VERSION}",
        ).pack(side="right")

    # --------------------------------------------------------
    # AdsPower 環境
    # --------------------------------------------------------
    def load_profiles(self) -> None:
        if self.group_running or self.kol_running:
            messagebox.showwarning(
                "目前執行中",
                "請先停止目前執行中的功能。",
            )
            return

        group_id = self.group_id_var.get().strip()

        self.load_profiles_button.configure(state="disabled")
        self.global_status_var.set("正在讀取 AdsPower 環境")

        threading.Thread(
            target=self._load_profiles_worker,
            args=(group_id,),
            daemon=True,
        ).start()

    def _load_profiles_worker(self, group_id: str) -> None:
        try:
            profiles = get_ads_profiles(group_id)
            self.message_queue.put(
                ("profiles_loaded", profiles, "")
            )
        except Exception as exc:
            self.message_queue.put(
                ("profiles_loaded", [], str(exc))
            )

    def _apply_profiles(
        self,
        profiles: List[AdsPowerProfile],
    ) -> None:
        self.profiles = profiles
        self.profile_listbox.delete(0, tk.END)

        for index, profile in enumerate(profiles, start=1):
            self.profile_listbox.insert(
                tk.END,
                (
                    f"{index:03d}. "
                    f"[{profile.group_name}] "
                    f"{profile.name}"
                ),
            )

        self._update_selected_count()

    def select_all_profiles(self) -> None:
        self.profile_listbox.select_set(0, tk.END)
        self._update_selected_count()

    def clear_profile_selection(self) -> None:
        self.profile_listbox.selection_clear(0, tk.END)
        self._update_selected_count()

    def _on_profile_selection_changed(self, _event=None) -> None:
        self._update_selected_count()

    def _update_selected_count(self) -> None:
        count = len(self.profile_listbox.curselection())
        self.selected_count_var.set(f"已選環境：{count}")

    def _selected_profiles(self) -> List[AdsPowerProfile]:
        indexes = list(self.profile_listbox.curselection())
        return [
            self.profiles[index]
            for index in indexes
            if 0 <= index < len(self.profiles)
        ]

    # --------------------------------------------------------
    # Group Part 2：實際收集
    # --------------------------------------------------------
    def start_group_collection(self) -> None:
        if self.kol_running:
            messagebox.showwarning(
                "KOL 執行中",
                "KOL 功能執行中，請先停止 KOL。",
            )
            return

        selected_profiles = self._selected_profiles()
        if not selected_profiles:
            messagebox.showwarning(
                "未選環境",
                "請至少選擇 1 個 AdsPower 環境。",
            )
            return

        try:
            minimum_posts = max(1, int(self.group_min_posts_var.get()))
            max_scrolls = max(1, int(self.group_max_scrolls_var.get()))
            scroll_distance = max(100, int(self.group_scroll_distance_var.get()))
            scroll_wait_ms = max(100, int(self.group_scroll_wait_var.get()))
        except (ValueError, tk.TclError):
            messagebox.showwarning(
                "設定錯誤",
                "Group 收集設定必須是有效數字。",
            )
            return

        keywords = group_prepare_keywords()
        if not keywords:
            messagebox.showwarning(
                "沒有關鍵字",
                (
                    f"{GROUP_KEYWORDS_FILE} 沒有可用關鍵字，"
                    "自動生成也沒有取得結果。"
                ),
            )
            return

        self.group_stats.reset()
        self.group_stats.keyword_total = len(keywords)

        self.group_keyword_count_var.set(
            f"關鍵字：{self.group_stats.keyword_total}"
        )
        self.group_processed_var.set("已處理：0")
        self.group_found_var.set("找到：0")
        self.group_added_var.set("新增：0")
        self.group_failed_var.set("失敗：0")

        self.group_progress.configure(maximum=max(1, len(keywords)))
        self.group_progress["value"] = 0

        self.group_tree.delete(*self.group_tree.get_children())

        self.group_task_queue = queue.Queue()
        for keyword in keywords:
            self.group_task_queue.put(keyword)

        settings: Dict[str, object] = {
            "minimum_posts": minimum_posts,
            "public_only": bool(self.group_public_only_var.get()),
            "max_scrolls": max_scrolls,
            "scroll_distance": scroll_distance,
            "scroll_wait_ms": scroll_wait_ms,
        }

        self.group_running = True
        self.group_stop_event.clear()
        self.group_threads = []

        self.group_start_button.configure(state="disabled")
        self.group_stop_button.configure(state="normal")
        self.kol_start_button.configure(state="disabled")
        self.telegram_start_button.configure(state="disabled")
        self.load_profiles_button.configure(state="disabled")

        self.group_status_var.set(
            f"執行中｜{len(selected_profiles)} 個環境"
        )
        self.global_status_var.set("Group 收集中")

        self._append_log(
            self.group_log_text,
            (
                f"開始 Group 收集｜環境 {len(selected_profiles)}｜"
                f"關鍵字 {len(keywords)}｜每日貼文至少 {minimum_posts}"
            ),
        )

        for worker_id, profile in enumerate(selected_profiles, start=1):
            self.group_tree.insert(
                "",
                tk.END,
                iid=str(worker_id),
                values=(
                    worker_id,
                    profile.name,
                    "",
                    "等待啟動",
                    0,
                    0,
                ),
            )

            thread = threading.Thread(
                target=group_worker_main,
                args=(
                    worker_id,
                    profile,
                    self.group_task_queue,
                    self.group_stop_event,
                    self.message_queue,
                    settings,
                ),
                daemon=True,
            )
            thread.start()
            self.group_threads.append(thread)

        threading.Thread(
            target=self._wait_group_workers,
            daemon=True,
        ).start()

    def _wait_group_workers(self) -> None:
        for thread in self.group_threads:
            thread.join()
        self.message_queue.put(
            ("group_done", self.group_stop_event.is_set())
        )

    def stop_group_collection(self) -> None:
        if not self.group_running:
            return

        self.group_stop_event.set()
        self.group_status_var.set("正在停止")
        self.group_stop_button.configure(state="disabled")
        self._append_log(
            self.group_log_text,
            "收到停止指令，等待目前動作結束。",
        )

    # --------------------------------------------------------
    # KOL Part 4：People 搜尋、粉絲數與最近貼文日期
    # --------------------------------------------------------
    def start_kol_collection(self) -> None:
        integrity_ok, missing_functions = kol_validate_runtime_integrity()
        if not integrity_ok:
            messagebox.showerror(
                "程式完整性錯誤",
                (
                    "KOL 核心函式不完整，已阻止啟動。\n\n"
                    + "\n".join(missing_functions)
                ),
            )
            self._append_log(
                self.kol_log_text,
                (
                    "KOL 啟動失敗｜缺少核心函式："
                    + ", ".join(missing_functions)
                ),
            )
            return

        if self.group_running:
            messagebox.showwarning(
                "Group 執行中",
                "Group 功能執行中，請先停止 Group。",
            )
            return

        selected_profiles = self._selected_profiles()
        if not selected_profiles:
            messagebox.showwarning(
                "未選環境",
                "請至少選擇 1 個 AdsPower 環境。",
            )
            return

        try:
            minimum_followers = max(
                0,
                int(self.kol_min_followers_var.get()),
            )
            max_scrolls = max(
                1,
                int(self.kol_max_scrolls_var.get()),
            )
            no_growth_limit = max(
                1,
                int(self.kol_no_growth_limit_var.get()),
            )
            scroll_distance = max(
                100,
                int(self.kol_scroll_distance_var.get()),
            )
            scroll_wait_ms = max(
                100,
                int(self.kol_scroll_wait_var.get()),
            )
        except (ValueError, tk.TclError):
            messagebox.showwarning(
                "設定錯誤",
                "KOL 粉絲數與下滑設定必須是有效整數。",
            )
            return

        keywords = kol_prepare_keywords()
        if not keywords:
            messagebox.showwarning(
                "沒有關鍵字",
                (
                    f"{KOL_KEYWORDS_FILE} 沒有可用關鍵字，"
                    "自動生成也沒有取得結果。"
                ),
            )
            return

        self.kol_stats.reset()
        self.kol_stats.keyword_total = len(keywords)

        self.kol_keyword_count_var.set(
            f"關鍵字：{self.kol_stats.keyword_total}"
        )
        self.kol_processed_var.set("已處理：0")
        self.kol_found_var.set("找到：0")
        self.kol_added_var.set("新增：0")
        self.kol_failed_var.set("失敗：0")

        self.kol_progress.configure(maximum=max(1, len(keywords)))
        self.kol_progress["value"] = 0
        self.telegram_progress.configure(maximum=max(1, len(keywords)))
        self.telegram_progress["value"] = 0

        self.kol_tree.delete(*self.kol_tree.get_children())
        self.telegram_tree.delete(*self.telegram_tree.get_children())

        self.kol_task_queue = queue.Queue()
        for keyword in keywords:
            self.kol_task_queue.put(keyword)

        recent_days_text = self.kol_recent_days_var.get().strip()
        if recent_days_text == "不限":
            recent_days: Optional[int] = None
        else:
            try:
                recent_days = max(0, int(recent_days_text))
            except ValueError:
                messagebox.showwarning(
                    "設定錯誤",
                    "最近發文天數設定無效。",
                )
                return

        settings: Dict[str, object] = {
            "minimum_followers": minimum_followers,
            "max_scrolls": max_scrolls,
            "no_growth_limit": no_growth_limit,
            "scroll_distance": scroll_distance,
            "scroll_wait_ms": scroll_wait_ms,
            "recent_days": recent_days,
            "save_date_debug": bool(self.kol_save_debug_var.get()),
        }

        self.kol_running = True
        self.kol_stop_event.clear()
        self.kol_threads = []

        self.kol_start_button.configure(state="disabled")
        self.kol_stop_button.configure(state="normal")
        self.telegram_start_button.configure(state="disabled")
        self.telegram_stop_button.configure(state="normal")
        self.group_start_button.configure(state="disabled")
        self.load_profiles_button.configure(state="disabled")

        self.kol_status_var.set(
            f"執行中｜{len(selected_profiles)} 個環境"
        )
        self.global_status_var.set("KOL 日期檢查中")

        self._append_log(
            self.kol_log_text,
            f"目前執行版本：{APP_VERSION}",
        )
        self._append_log(
            self.kol_log_text,
            (
                "Part 5.4.1 已從 Part 5.0 Stable 還原："
                "下滑、結果指紋、Diagnostics、粉絲 Diagnostics、"
                "People 卡片收集等核心函式。"
            ),
        )
        self._append_log(
            self.kol_log_text,
            (
                "本版直接以 Part 5.0 Stable 完整原始碼重建；"
                "保留 kol_scroll_search_results、"
                "kol_get_last_result_fingerprint、"
                "kol_save_diagnostics 等原有核心函式。"
            ),
        )
        self._append_log(
            self.kol_log_text,
            (
                "Part 5.4：KOL 首頁日期改為快速時間區塊分析；"
                "每頁最多分析 5 秒，不再掃描 80 個 article。"
            ),
        )
        self._append_log(
            self.kol_log_text,
            (
                f"開始 KOL People 搜尋｜環境 {len(selected_profiles)}｜"
                f"關鍵字 {len(keywords)}｜最低粉絲 {minimum_followers:,}｜"
                f"最近發文 {recent_days_text} 天｜"
                f"最大下滑 {max_scrolls}｜停滯上限 {no_growth_limit}｜"
                f"距離 {scroll_distance}｜等待 {scroll_wait_ms}ms"
            ),
        )
        self._append_log(
            self.kol_log_text,
            (
                "Part 3 會先把符合粉絲條件的 People 網址寫入 kolurl.txt；"
                "最近貼文日期過濾將於 Part 4 接入。"
            ),
        )

        for worker_id, profile in enumerate(selected_profiles, start=1):
            self.kol_tree.insert(
                "",
                tk.END,
                iid=str(worker_id),
                values=(
                    worker_id,
                    profile.name,
                    "",
                    "",
                    "",
                    "等待啟動",
                ),
            )
            self.telegram_tree.insert(
                "",
                tk.END,
                iid=str(worker_id),
                values=(
                    worker_id,
                    profile.name,
                    "",
                    "",
                    "",
                    "等待啟動",
                ),
            )

            thread = threading.Thread(
                target=kol_worker_main,
                args=(
                    worker_id,
                    profile,
                    self.kol_task_queue,
                    self.kol_stop_event,
                    self.message_queue,
                    settings,
                ),
                daemon=True,
            )
            thread.start()
            self.kol_threads.append(thread)

        threading.Thread(
            target=self._wait_kol_workers,
            daemon=True,
        ).start()

    def _wait_kol_workers(self) -> None:
        for thread in self.kol_threads:
            thread.join()
        self.message_queue.put(
            ("kol_done", self.kol_stop_event.is_set())
        )

    def stop_kol_collection(self) -> None:
        if not self.kol_running:
            return

        self.kol_stop_event.set()
        self.kol_status_var.set("正在停止")
        self.kol_stop_button.configure(state="disabled")
        self.telegram_stop_button.configure(state="disabled")
        self._append_log(
            self.kol_log_text,
            "收到停止指令，等待目前動作結束。",
        )
        self._append_log(
            self.telegram_log_text,
            "收到停止指令，等待目前動作結束。",
        )

    # --------------------------------------------------------
    # Queue
    # --------------------------------------------------------
    def _poll_messages(self) -> None:
        try:
            while True:
                message = self.message_queue.get_nowait()
                kind = message[0]

                if kind == "profiles_loaded":
                    _, profiles, error = message

                    self.load_profiles_button.configure(state="normal")

                    if error:
                        self.global_status_var.set("環境讀取失敗")
                        messagebox.showerror(
                            "讀取失敗",
                            error,
                        )
                        continue

                    self._apply_profiles(profiles)
                    self.global_status_var.set(
                        f"已讀取 {len(profiles)} 個環境"
                    )

                    if not profiles:
                        messagebox.showwarning(
                            "沒有環境",
                            "指定 AdsPower 群組內沒有讀取到環境。",
                        )

                elif kind == "group_worker_state":
                    _, worker_id, profile_name, status = message
                    iid = str(worker_id)
                    if self.group_tree.exists(iid):
                        values = list(self.group_tree.item(iid, "values"))
                        values[1] = profile_name
                        values[3] = status
                        self.group_tree.item(iid, values=values)

                elif kind == "group_worker_update":
                    _, worker_id, keyword, status, found, added = message
                    iid = str(worker_id)
                    if self.group_tree.exists(iid):
                        values = list(self.group_tree.item(iid, "values"))
                        values[2] = keyword
                        values[3] = status
                        values[4] = found
                        values[5] = added
                        self.group_tree.item(iid, values=values)

                elif kind == "group_stat":
                    _, field_name, amount = message
                    if field_name == "processed":
                        self.group_stats.processed += int(amount)
                    elif field_name == "found":
                        self.group_stats.found += int(amount)
                    elif field_name == "added":
                        self.group_stats.added += int(amount)
                        if int(amount) > 0:
                            # 優化項目 2：只有真的有新資料寫入時才標記需要
                            # 重新讀檔計數，而不是每 250ms 都重讀一次檔案。
                            self._file_counts_dirty = True
                    elif field_name == "failed":
                        self.group_stats.failed += int(amount)
                    self._refresh_group_stats_ui()

                elif kind == "group_log":
                    _, log_message = message
                    self._append_log(self.group_log_text, log_message)

                elif kind == "kol_worker_state":
                    _, worker_id, profile_name, status = message
                    iid = str(worker_id)
                    for tree in (self.kol_tree, self.telegram_tree):
                        if tree.exists(iid):
                            values = list(tree.item(iid, "values"))
                            values[1] = profile_name
                            values[5] = status
                            tree.item(iid, values=values)

                elif kind == "kol_worker_update":
                    (
                        _,
                        worker_id,
                        keyword,
                        followers,
                        date_text,
                        status,
                    ) = message
                    iid = str(worker_id)
                    for tree in (self.kol_tree, self.telegram_tree):
                        if tree.exists(iid):
                            values = list(tree.item(iid, "values"))
                            values[2] = keyword
                            values[3] = followers
                            values[4] = date_text
                            values[5] = status
                            tree.item(iid, values=values)

                elif kind == "kol_stat":
                    _, field_name, amount = message
                    if field_name == "processed":
                        self.kol_stats.processed += int(amount)
                    elif field_name == "found":
                        self.kol_stats.found += int(amount)
                    elif field_name == "added":
                        self.kol_stats.added += int(amount)
                        if int(amount) > 0:
                            self._file_counts_dirty = True
                    elif field_name == "failed":
                        self.kol_stats.failed += int(amount)
                    self._refresh_kol_stats_ui()

                elif kind == "kol_log":
                    _, log_message = message
                    self._append_log(self.kol_log_text, log_message)
                    self._append_log(self.telegram_log_text, log_message)

                elif kind == "group_done":
                    _, stopped = message
                    self._finish_group_foundation(stopped)

                elif kind == "kol_done":
                    _, stopped = message
                    self._finish_kol_foundation(stopped)

        except queue.Empty:
            pass

        # 優化項目 2：檔案計數只在有新資料寫入（_file_counts_dirty）時才
        # 重新讀檔，而不是不論有沒有變化每 250ms 都整份重讀。
        if self._file_counts_dirty:
            self._refresh_file_counts()
            self._file_counts_dirty = False
        self.root.after(250, self._poll_messages)

    def _refresh_group_stats_ui(self) -> None:
        self.group_processed_var.set(
            f"已處理：{self.group_stats.processed}"
        )
        self.group_found_var.set(
            f"找到：{self.group_stats.found}"
        )
        self.group_added_var.set(
            f"新增：{self.group_stats.added}"
        )
        self.group_failed_var.set(
            f"失敗：{self.group_stats.failed}"
        )
        self.group_progress["value"] = min(
            self.group_stats.processed,
            max(1, self.group_stats.keyword_total),
        )

    def _finish_group_foundation(self, stopped: bool) -> None:
        self.group_running = False

        self.group_start_button.configure(state="normal")
        self.group_stop_button.configure(state="disabled")
        self.kol_start_button.configure(state="normal")
        self.telegram_start_button.configure(state="normal")
        self.load_profiles_button.configure(state="normal")

        status = "已停止" if stopped else "Group 收集完成"
        self.group_status_var.set(status)
        self.global_status_var.set("待命")

        self._append_log(
            self.group_log_text,
            status,
        )

    def _refresh_kol_stats_ui(self) -> None:
        self.kol_processed_var.set(
            f"已處理：{self.kol_stats.processed}"
        )
        self.kol_found_var.set(
            f"找到：{self.kol_stats.found}"
        )
        self.kol_added_var.set(
            f"新增：{self.kol_stats.added}"
        )
        self.kol_failed_var.set(
            f"失敗：{self.kol_stats.failed}"
        )
        self.kol_progress["value"] = min(
            self.kol_stats.processed,
            max(1, self.kol_stats.keyword_total),
        )
        self.telegram_progress["value"] = min(
            self.kol_stats.processed,
            max(1, self.kol_stats.keyword_total),
        )

    def _finish_kol_foundation(self, stopped: bool) -> None:
        self.kol_running = False

        self.kol_start_button.configure(state="normal")
        self.kol_stop_button.configure(state="disabled")
        self.telegram_start_button.configure(state="normal")
        self.telegram_stop_button.configure(state="disabled")
        self.group_start_button.configure(state="normal")
        self.load_profiles_button.configure(state="normal")

        status = "已停止" if stopped else "KOL 日期篩選收集完成"
        self.kol_status_var.set(status)
        self.global_status_var.set("待命")

        self._append_log(
            self.kol_log_text,
            status,
        )

    # --------------------------------------------------------
    # 檔案數量
    # --------------------------------------------------------
    def _refresh_file_counts(self) -> None:
        self.group_total_count_var.set(
            f"Group：{count_nonempty_lines(GROUP_OUTPUT_FILE)}"
        )
        self.kol_total_count_var.set(
            f"KOL：{count_nonempty_lines(KOL_OUTPUT_FILE)}"
        )

    # --------------------------------------------------------
    # 關閉
    # --------------------------------------------------------
    def request_close(self) -> None:
        if self.group_running or self.kol_running:
            confirm = messagebox.askyesno(
                "確認關閉",
                (
                    "目前仍有功能執行中。\n"
                    "確定送出停止指令並關閉程式嗎？"
                ),
            )
            if not confirm:
                return

            self.group_stop_event.set()
            self.kol_stop_event.set()

        self.root.destroy()


def main() -> None:
    ensure_paths()

    root = tk.Tk()
    app = FacebookSearchToolboxApp(root)

    root.protocol(
        "WM_DELETE_WINDOW",
        app.request_close,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
