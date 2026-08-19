# -*- coding: utf-8 -*-
"""
Facebook Group 留言框測試器 V6.4 Stable
================================

用途：
1. 使用者先在 AdsPower 的 Facebook 分頁手動開啟「Groups／社團」搜尋結果。
2. 程式連接指定 AdsPower 環境。
3. 自動掃描目前 Group 搜尋結果頁。
4. 找出顯示「今日貼文數 >= 10」的 Group。
5. 在 GUI 中列出，並匯出到桌面：
   - Facebook_Group_10Plus_結果.txt
   - Facebook_Group_10Plus_結果.csv

安裝：
    py -m pip install requests playwright
    py -m playwright install chromium

注意：
- V3.2 Stable 可擷取近期貼文作者，並自動跳過只有管理員發文的 Group，不會留言。
- 保留 AdsPower API 限速、Too many request 自動重試與 Browser Start 重試。
- 新增 Group 名稱清理、成員數解析、公開／私人狀態解析。
- 保留依序開啟符合條件 Group 的功能。
- 新增近期貼文作者擷取、作者網址正規化、跨 Group 去重及 TXT／CSV 匯出。
- Facebook 介面會改版；程式使用多種 DOM 與文字備援規則。
"""

from __future__ import annotations

import csv
import json
import queue
import random
import re
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse, urlunparse

import requests
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


# ============================================================
# 設定
# ============================================================

ADSPOWER_API = "http://local.adspower.net:50325/api/v1"
ADSPOWER_HEADERS: Dict[str, str] = {}
DEFAULT_GROUP_ID = "10085779"

MIN_TODAY_POSTS = 10
DEFAULT_MAX_SCROLLS = 40
DEFAULT_SCROLL_WAIT = 2.2
NO_NEW_RESULT_LIMIT = 6
DEFAULT_OPEN_WAIT = 4.0
DEFAULT_POSTS_TO_SCAN = 30
DEFAULT_AUTHOR_SCROLLS = 20
AUTHOR_SCROLL_WAIT = 2.0
AUTHOR_NO_GROWTH_LIMIT = 5
ADMIN_CHECK_POSTS = 5
MIN_POSTS_FOR_ADMIN_ONLY = 3
API_TIMEOUT = 20
PAGE_TIMEOUT_MS = 30_000

# AdsPower API 穩定設定
ADSPOWER_MIN_INTERVAL = 0.6
ADSPOWER_MAX_RETRIES = 6
ADSPOWER_RETRY_DELAYS = (1, 2, 4, 6, 10, 15)
BROWSER_START_MAX_RETRIES = 8
BROWSER_START_RETRY_DELAY = 3

DESKTOP = Path.home() / "Desktop"
AUTHOR_DEBUG_HTML = DESKTOP / "Facebook_Group_作者擷取_debug.html"
AUTHOR_DEBUG_PNG = DESKTOP / "Facebook_Group_作者擷取_debug.png"
COMMENT_DEBUG_HTML = DESKTOP / "Facebook_Group_留言框_debug.html"
COMMENT_DEBUG_PNG = DESKTOP / "Facebook_Group_留言框_debug.png"
RUN_LOG_FILE = DESKTOP / "Facebook_Group_留言執行.log"
COMMENT_TEXT_FILE = DESKTOP / "文一.txt"
COMMENT_TEST_TXT = DESKTOP / "Facebook_Group_留言框測試結果.txt"
COMMENT_TEST_CSV = DESKTOP / "Facebook_Group_留言框測試結果.csv"
OUTPUT_TXT = DESKTOP / "Facebook_Group_10Plus_結果.txt"
OUTPUT_CSV = DESKTOP / "Facebook_Group_10Plus_結果.csv"
AUTHOR_OUTPUT_TXT = DESKTOP / "Facebook_Group_作者結果.txt"
AUTHOR_OUTPUT_CSV = DESKTOP / "Facebook_Group_作者結果.csv"

DIAGNOSTIC_ROOT = DESKTOP / "Facebook_Group_留言診斷"
_diagnostic_failure_index = 0
_diagnostic_lock = threading.Lock()

GROUP_URL_RE = re.compile(
    r"https?://(?:www\.|m\.|web\.)?facebook\.com/groups/"
    r"(?P<group_id>[A-Za-z0-9_.-]+)",
    re.I,
)

# 多語言「今日貼文」相關文字。
TODAY_WORDS = (
    "today",
    "new post",
    "new posts",
    "posts today",
    "post today",
    "ngayon",
    "post ngayon",
    "mga post ngayon",
    "araw na ito",
    "ngayong araw",
    "hari ini",
    "hôm nay",
    "วันนี้",
    "今日",
    "今天",
    "今天的貼文",
    "今天的帖子",
    "本日",
    "오늘",
    "сегодня",
    "hoy",
    "aujourd’hui",
    "aujourd'hui",
    "heute",
    "oggi",
)

POST_WORDS = (
    "post",
    "posts",
    "mga post",
    "貼文",
    "帖子",
    "条帖子",
    "則貼文",
    "篇貼文",
    "則新貼文",
    "投稿",
    "게시물",
    "публикац",
    "publicacion",
    "publicación",
    "publication",
    "beitrag",
    "bài viết",
    "โพสต์",
)

# 排除不是 Group 首頁的子路徑。

PROFILE_RESERVED_PATHS = {
    "groups", "pages", "watch", "marketplace", "events", "gaming",
    "reel", "reels", "stories", "photo", "photos", "videos",
    "share", "sharer", "login", "help", "settings", "privacy",
    "friends", "messages", "notifications", "search", "hashtag",
    "profile.php", "permalink.php", "story.php",
}

POST_META_WORDS = {
    "like", "comment", "share", "react", "send", "follow",
    "讚", "留言", "分享", "傳送", "追蹤",
    "gusto", "komento", "ibahagi",
    "join", "joined", "member", "members", "admin", "anonymous participant",
    "加入", "已加入", "成員", "管理員", "匿名參與者",
}

GROUP_SUBPATHS = {
    "posts",
    "permalink",
    "media",
    "photos",
    "videos",
    "members",
    "about",
    "events",
    "files",
    "search",
    "admin_activities",
    "pending_posts",
    "buy_sell_discussion",
}


# ============================================================
# 資料結構
# ============================================================

@dataclass(frozen=True)
class GroupResult:
    name: str
    url: str
    today_posts: int
    members: Optional[int]
    privacy: str
    activity_text: str


@dataclass(frozen=True)
class AuthorResult:
    name: str
    url: str
    group_name: str
    group_url: str


@dataclass(frozen=True)
class CommentTestResult:
    author_name: str
    author_url: str
    group_name: str
    group_url: str
    post_url: str
    comment_box_found: bool
    input_success: bool
    submitted: bool
    test_text: str
    status: str
    created_at: str


# ============================================================
# 通用工具
# ============================================================

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_compact_number(raw: str) -> Optional[int]:
    """
    支援：
    10
    10+
    1.2K
    1,200
    1.2萬 / 1.2万
    """
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


def normalize_group_url(url: str) -> Optional[str]:
    if not url:
        return None

    url = url.replace("&amp;", "&").strip()

    # Facebook redirect URL。
    if "l.facebook.com/l.php" in url:
        try:
            query = parse_qs(urlparse(url).query)
            target = query.get("u", [None])[0]
            if target:
                url = target
        except Exception:
            pass

    match = GROUP_URL_RE.search(url)
    if not match:
        return None

    group_id = match.group("group_id").strip("/")
    if not group_id or group_id.lower() in GROUP_SUBPATHS:
        return None

    return f"https://www.facebook.com/groups/{group_id}/"


def group_id_from_url(url: str) -> str:
    match = GROUP_URL_RE.search(url or "")
    return match.group("group_id") if match else url


def clean_group_name(name: str) -> str:
    value = normalize_space(name)

    prefixes = (
        "Profile photo of ",
        "Group photo of ",
        "Photo of ",
        "Ảnh đại diện của ",
        "Larawan sa profile ni ",
        "Larawan ng profile ng ",
        "個人資料相片：",
        "個人檔案相片：",
        "群組相片：",
        "头像：",
        "群组头像：",
        "プロフィール写真：",
    )

    changed = True
    while changed:
        changed = False
        lower = value.casefold()
        for prefix in prefixes:
            if lower.startswith(prefix.casefold()):
                value = normalize_space(value[len(prefix):])
                changed = True
                break

    return value


def extract_member_count(text: str) -> Optional[int]:
    cleaned = normalize_space(text)
    if not cleaned:
        return None

    patterns = [
        r"(\d+(?:[.,]\d+)?\s*[kKmM萬万]?)\s*(?:members?|mga miyembro)",
        r"(\d+(?:[.,]\d+)?\s*[kKmM萬万]?)\s*(?:位成員|名成員|成員|成员)",
        r"(\d+(?:[.,]\d+)?\s*[kKmM萬万]?)\s*(?:anggota|thành viên)",
        r"(\d+(?:[.,]\d+)?\s*[kKmM萬万]?)\s*(?:メンバー|명)",
    ]

    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.I)
        if match:
            return parse_compact_number(match.group(1))

    return None


def extract_privacy(text: str) -> str:
    cleaned = normalize_space(text).casefold()

    public_words = (
        "public",
        "publiko",
        "公开",
        "公開",
        "công khai",
        "público",
        "publique",
        "öffentlich",
    )
    private_words = (
        "private",
        "pribado",
        "私密",
        "私人",
        "riêng tư",
        "privado",
        "privé",
        "privat",
    )

    if any(word.casefold() in cleaned for word in public_words):
        return "Public"

    if any(word.casefold() in cleaned for word in private_words):
        return "Private"

    return "Unknown"


def contains_activity_words(text: str) -> bool:
    lower = normalize_space(text).casefold()
    return (
        any(word.casefold() in lower for word in POST_WORDS)
        and any(word.casefold() in lower for word in TODAY_WORDS)
    )


def extract_today_post_count(text: str) -> Optional[Tuple[int, str]]:
    """
    從卡片文字擷取「今日貼文數」。

    支援例子：
    - 10+ posts today
    - 18 posts today
    - 18 new posts today
    - 10+ post ngayon
    - 今天有 12 則貼文
    - 今日 15 篇貼文
    """
    cleaned = normalize_space(text)
    if not cleaned:
        return None

    lower = cleaned.casefold()

    # 避免把成員數、留言數誤判成貼文數。
    if not contains_activity_words(cleaned):
        return None

    number_token = r"(\d+(?:[.,]\d+)?\s*[kKmM萬万]?\s*\+?)"
    post_token = (
        r"(?:posts?|mga\s+post|貼文|帖子|条帖子|則貼文|篇貼文|"
        r"投稿|게시물|публикац\w*|publicaci[oó]n(?:es)?|"
        r"publications?|beitr[aä]ge?|bài\s+viết|โพสต์)"
    )
    today_token = (
        r"(?:today|ngayon|araw\s+na\s+ito|ngayong\s+araw|"
        r"hari\s+ini|hôm\s+nay|วันนี้|今日|今天|本日|오늘|сегодня|"
        r"hoy|aujourd['’]hui|heute|oggi)"
    )

    patterns = [
        rf"{number_token}\s*(?:new\s+)?{post_token}\s*(?:•|-|·)?\s*{today_token}",
        rf"{today_token}\s*(?:may|有|共有|新增|新しい)?\s*{number_token}\s*(?:new\s+)?{post_token}",
        rf"{number_token}\s*(?:new\s+)?{post_token}.*?{today_token}",
        rf"{today_token}.*?{number_token}\s*(?:new\s+)?{post_token}",
    ]

    candidates: List[Tuple[int, str]] = []

    for pattern in patterns:
        for match in re.finditer(pattern, lower, flags=re.I):
            raw_number = match.group(1)
            count = parse_compact_number(raw_number)
            if count is not None:
                snippet_start = max(0, match.start() - 20)
                snippet_end = min(len(cleaned), match.end() + 30)
                snippet = cleaned[snippet_start:snippet_end]
                candidates.append((count, snippet))

    if candidates:
        # 同一張卡片可能同時有多個符合文字，採最大值。
        return max(candidates, key=lambda item: item[0])

    # 備援：文字含 today + post，找最靠近 post 的數字。
    post_positions: List[int] = []
    for word in POST_WORDS:
        start = 0
        word_lower = word.casefold()
        while True:
            index = lower.find(word_lower, start)
            if index < 0:
                break
            post_positions.append(index)
            start = index + len(word_lower)

    number_matches = list(
        re.finditer(r"\b\d+(?:[.,]\d+)?\s*[kKmM萬万]?\s*\+?", lower)
    )

    best: Optional[Tuple[int, int, str]] = None
    for number_match in number_matches:
        count = parse_compact_number(number_match.group(0))
        if count is None:
            continue

        distance = min(
            (abs(number_match.start() - position) for position in post_positions),
            default=10_000,
        )
        if distance > 60:
            continue

        snippet_start = max(0, number_match.start() - 35)
        snippet_end = min(len(cleaned), number_match.end() + 60)
        snippet = cleaned[snippet_start:snippet_end]

        item = (distance, count, snippet)
        if best is None or item[0] < best[0]:
            best = item

    if best:
        return best[1], best[2]

    return None


def append_run_log(message: str) -> None:
    try:
        DESKTOP.mkdir(parents=True, exist_ok=True)
        with RUN_LOG_FILE.open("a", encoding="utf-8-sig") as handle:
            handle.write(f"[{now_text()}] {message}\n")
    except Exception:
        pass


def reset_run_log() -> None:
    try:
        RUN_LOG_FILE.write_text(
            f"Facebook Group 留言框測試器 V6.4 Stable 執行 LOG\n"
            f"開始時間：{now_text()}\n"
            + "=" * 90 + "\n",
            encoding="utf-8-sig",
        )
    except Exception:
        pass


def normalize_profile_url(url: str) -> Optional[str]:
    if not url:
        return None

    value = url.replace("&amp;", "&").strip()
    if value.startswith("/"):
        value = "https://www.facebook.com" + value

    try:
        parsed = urlparse(value)
    except Exception:
        return None

    if "facebook.com" not in parsed.netloc.casefold():
        return None

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    lower_parts = [part.casefold() for part in parts]

    # /groups/GROUP_ID/user/USER_ID/
    if (
        len(parts) >= 4
        and lower_parts[0] == "groups"
        and lower_parts[2] == "user"
        and parts[3].isdigit()
    ):
        return f"https://www.facebook.com/profile.php?id={parts[3]}"

    if parts and lower_parts[0] == "profile.php":
        query = parse_qs(parsed.query)
        profile_id = (query.get("id") or [""])[0].strip()
        if profile_id.isdigit():
            return f"https://www.facebook.com/profile.php?id={profile_id}"
        return None

    if len(parts) >= 2 and lower_parts[0] == "user" and parts[1].isdigit():
        return f"https://www.facebook.com/profile.php?id={parts[1]}"

    if len(parts) >= 3 and lower_parts[0] == "people" and parts[-1].isdigit():
        return f"https://www.facebook.com/profile.php?id={parts[-1]}"

    if parts and re.fullmatch(r"[A-Za-z0-9._-]{2,}", parts[0]):
        if lower_parts[0] not in PROFILE_RESERVED_PATHS:
            return f"https://www.facebook.com/{parts[0]}"

    return None


def clean_author_name(name: str) -> str:
    value = normalize_space(name)
    prefixes = (
        "Profile photo of ",
        "Photo of ",
        "個人資料相片：",
        "個人檔案相片：",
        "頭像：",
        "头像：",
    )
    for prefix in prefixes:
        if value.casefold().startswith(prefix.casefold()):
            value = normalize_space(value[len(prefix):])
            break
    return value


def is_probable_author_name(name: str) -> bool:
    value = normalize_space(name)
    if not value or len(value) < 2 or len(value) > 120:
        return False

    lower = value.casefold()
    if lower in POST_META_WORDS:
        return False

    if any(token in lower for token in (
        "profile photo", "cover photo", "group photo",
        "facebook", "sponsored", "recommended for you",
        "online status indicator", "active now",
        "anonymous member", "anonymous participant",
        "moderator", "管理員", "管理员", "匿名成員", "匿名成员",
    )):
        return False

    return True


def author_link_has_admin_badge(link) -> bool:
    """
    精準判斷該作者是否有 Admin 徽章。

    Facebook 實際 DOM：
    - 作者名稱位於 data-ad-rendering-role="profile_name"
    - Admin 徽章位於作者名稱旁邊的兄弟區塊
    - 徽章常見 aria-label：
      "Admin, view badge details"

    因此不能只掃作者連結本身或第一層父元素，
    必須在同一篇貼文的作者標頭範圍內向上逐層檢查。
    """
    try:
        return bool(
            link.evaluate(
                """
                (anchor) => {
                    const adminWords = new Set([
                        'admin',
                        'administrator',
                        'group admin',
                        '管理員',
                        '管理员',
                        '社團管理員',
                        '群组管理员',
                        'tagapangasiwa'
                    ]);

                    let node = anchor;

                    // 最多往上 12 層，但遇到貼文 article 就停止。
                    for (let depth = 0; node && depth < 12; depth++, node = node.parentElement) {
                        if (!node || node === document.body) break;

                        // 1. 最可靠：Admin 徽章 aria-label
                        const ariaBadge = node.querySelector(
                            '[aria-label^="Admin"],' +
                            '[aria-label*="Admin, view badge details"],' +
                            '[aria-label^="Administrator"],' +
                            '[aria-label^="管理員"],' +
                            '[aria-label^="管理员"],' +
                            '[aria-label^="Tagapangasiwa"]'
                        );
                        if (ariaBadge) {
                            return true;
                        }

                        // 2. 備援：role=link 的徽章文字
                        const badgeLinks = node.querySelectorAll(
                            '[role="link"][aria-label], [role="link"] span'
                        );

                        for (const badge of badgeLinks) {
                            const aria = (badge.getAttribute?.('aria-label') || '')
                                .trim()
                                .toLowerCase();
                            const txt = (badge.textContent || '')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .toLowerCase();

                            if (
                                aria.startsWith('admin') ||
                                aria.startsWith('administrator') ||
                                aria.startsWith('管理員') ||
                                aria.startsWith('管理员') ||
                                aria.startsWith('tagapangasiwa') ||
                                adminWords.has(txt)
                            ) {
                                return true;
                            }
                        }

                        // 已到完整貼文容器，不再往外，避免誤抓別篇貼文的 Admin。
                        if (
                            node.getAttribute?.('role') === 'article' ||
                            node.matches?.('div[data-pagelet^="FeedUnit_"]')
                        ) {
                            break;
                        }
                    }

                    return false;
                }
                """
            )
        )
    except Exception:
        return False


def extract_authors_directly_from_page(
    page: Page,
    group: GroupResult,
) -> Tuple[List[AuthorResult], int, int]:
    """
    直接掃描 Facebook Group 頁面中的作者連結，不依賴 role=article。

    作者格式：
      /groups/GROUP_ID/user/USER_ID/
    """
    results: Dict[str, AuthorResult] = {}
    detected_links = 0
    admin_links = 0

    selectors = [
        "a[href*='/groups/'][href*='/user/']",
        "a[href*='facebook.com/groups/'][href*='/user/']",
    ]

    seen_hrefs: Set[str] = set()

    for selector in selectors:
        try:
            links = page.locator(selector)
            count = min(links.count(), 1000)
        except Exception:
            continue

        for index in range(count):
            link = links.nth(index)

            try:
                href = link.get_attribute("href", timeout=500) or ""
            except Exception:
                continue

            if not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            profile_url = normalize_profile_url(href)
            if not profile_url:
                continue

            name_candidates: List[str] = []

            try:
                name_candidates.append(link.inner_text(timeout=500))
            except Exception:
                pass

            for attr in ("aria-label", "title"):
                try:
                    name_candidates.append(
                        link.get_attribute(attr, timeout=300) or ""
                    )
                except Exception:
                    pass

            author_name = ""
            for candidate in name_candidates:
                cleaned = clean_author_name(candidate)
                if is_probable_author_name(cleaned):
                    author_name = cleaned
                    break

            if not author_name:
                continue

            detected_links += 1

            if author_link_has_admin_badge(link):
                admin_links += 1
                append_run_log(
                    f"排除 Admin 作者：{author_name}｜{profile_url}｜"
                    f"來源 Group：{group.name}"
                )
                continue

            results.setdefault(
                profile_url.casefold(),
                AuthorResult(
                    name=author_name,
                    url=profile_url,
                    group_name=group.name,
                    group_url=group.url,
                ),
            )

    return list(results.values()), detected_links, admin_links


def scan_group_authors(
    page: Page,
    group: GroupResult,
    max_posts: int,
    max_scrolls: int,
    stop_event: threading.Event,
    progress_callback,
) -> Tuple[List[AuthorResult], bool]:
    page.goto(
        group.url,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT_MS,
    )
    page.wait_for_timeout(4_000)

    authors: Dict[str, AuthorResult] = {}
    no_growth = 0
    admin_only_rounds = 0

    append_run_log(f"開始 Group：{group.name}｜{group.url}")

    for scroll_index in range(max_scrolls + 1):
        if stop_event.is_set():
            break

        visible_authors, detected_links, admin_links = (
            extract_authors_directly_from_page(page, group)
        )

        before = len(authors)
        for author in visible_authors:
            authors.setdefault(author.url.casefold(), author)

        new_count = len(authors) - before

        # 若有抓到作者連結，但全部都是 Admin，連續兩輪後跳過。
        all_admin = detected_links >= 3 and admin_links >= detected_links
        if all_admin:
            admin_only_rounds += 1
        else:
            admin_only_rounds = 0

        admin_only = admin_only_rounds >= 2

        progress_callback(
            scroll_index,
            len(authors),
            new_count,
            detected_links,
            admin_links,
            admin_only,
        )

        append_run_log(
            f"{group.name}｜下滑 {scroll_index}｜"
            f"作者連結 {detected_links}｜Admin {admin_links}｜"
            f"非 Admin 作者 {len(authors)}｜新增 {new_count}"
        )

        if admin_only:
            append_run_log(f"判定 Admin-only，跳過 Group：{group.name}")
            return list(authors.values()), True

        if len(authors) >= max_posts:
            break

        if new_count == 0:
            no_growth += 1
        else:
            no_growth = 0

        if no_growth >= AUTHOR_NO_GROWTH_LIMIT:
            break

        scroll_once(page)
        page.wait_for_timeout(int(AUTHOR_SCROLL_WAIT * 1000))

    if not authors:
        try:
            AUTHOR_DEBUG_HTML.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            page.screenshot(path=str(AUTHOR_DEBUG_PNG), full_page=True)
        except Exception:
            pass

    append_run_log(
        f"完成 Group：{group.name}｜非 Admin 作者 {len(authors)}"
    )
    return list(authors.values()), False


def export_author_results(
    authors: Iterable[AuthorResult],
) -> Tuple[Path, Path]:
    DESKTOP.mkdir(parents=True, exist_ok=True)
    rows = list(authors)

    with AUTHOR_OUTPUT_TXT.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write("Facebook Group 貼文作者結果\n")
        handle.write(f"擷取時間：{now_text()}\n")
        handle.write(f"作者數量：{len(rows)}\n")
        handle.write("=" * 80 + "\n\n")

        for index, item in enumerate(rows, start=1):
            handle.write(f"{index}. {item.name}\n")
            handle.write(f"   作者網址：{item.url}\n")
            handle.write(f"   來源 Group：{item.group_name}\n")
            handle.write(f"   Group 網址：{item.group_url}\n\n")

    with AUTHOR_OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "序號", "作者名稱", "作者網址", "來源 Group", "Group 網址"
        ])
        for index, item in enumerate(rows, start=1):
            writer.writerow([
                index,
                item.name,
                item.url,
                item.group_name,
                item.group_url,
            ])

    return AUTHOR_OUTPUT_TXT, AUTHOR_OUTPUT_CSV


def load_comment_texts() -> List[str]:
    if not COMMENT_TEXT_FILE.exists():
        raise FileNotFoundError(f"找不到文案檔：{COMMENT_TEXT_FILE}")

    lines = [
        normalize_space(line)
        for line in COMMENT_TEXT_FILE.read_text(
            encoding="utf-8-sig",
            errors="ignore",
        ).splitlines()
    ]
    result = [line for line in lines if line and not line.startswith("#")]

    if not result:
        raise RuntimeError("桌面 文一.txt 沒有可用文案。")

    return result


def _safe_attr(locator, name: str, timeout: int = 500) -> str:
    try:
        return locator.get_attribute(name, timeout=timeout) or ""
    except Exception:
        return ""


def _safe_text(locator, timeout: int = 500) -> str:
    try:
        return normalize_space(locator.inner_text(timeout=timeout))
    except Exception:
        return ""


def article_signature(article, group_url: str) -> str:
    post_url = get_post_url(article)
    if post_url:
        return post_url.casefold()
    try:
        return article.evaluate(
            """(el) => {
                const txt = (el.innerText || '').replace(/\\s+/g,' ').trim().slice(0,500);
                const box = el.getBoundingClientRect();
                return `${location.pathname}|${Math.round(box.top + window.scrollY)}|${txt}`;
            }"""
        )
    except Exception:
        return f"{group_url}|{time.monotonic_ns()}"


def get_visible_articles(page: Page) -> List[object]:
    selectors = [
        "div[role='article']",
        "div[data-pagelet^='FeedUnit_']",
        "div[data-ad-rendering-role='story_message'] >> xpath=ancestor::div[@role='article'][1]",
    ]
    results: List[object] = []
    seen: Set[str] = set()
    for selector in selectors:
        try:
            items = page.locator(selector)
            count = min(items.count(), 300)
        except Exception:
            continue
        for index in range(count):
            item = items.nth(index)
            try:
                if not item.is_visible(timeout=300):
                    continue
                handle = item.element_handle(timeout=500)
                if handle is None:
                    continue
                key = str(handle)
                if key in seen:
                    continue
                seen.add(key)
                results.append(item)
            except Exception:
                continue
        if results:
            break
    return results


def extract_author_from_article(article, group: GroupResult) -> Optional[AuthorResult]:
    selectors = [
        "[data-ad-rendering-role='profile_name'] a[href]",
        "h2 a[href]",
        "h3 a[href]",
        "a[href*='/groups/'][href*='/user/']",
        "a[href*='profile.php?id=']",
    ]
    for selector in selectors:
        try:
            links = article.locator(selector)
            count = min(links.count(), 20)
        except Exception:
            continue
        for index in range(count):
            link = links.nth(index)
            href = _safe_attr(link, "href")
            profile_url = normalize_profile_url(href)
            if not profile_url:
                continue
            candidates = [
                _safe_text(link),
                _safe_attr(link, "aria-label"),
                _safe_attr(link, "title"),
            ]
            for value in candidates:
                name = clean_author_name(value)
                if is_probable_author_name(name):
                    return AuthorResult(name, profile_url, group.name, group.url)
    return None


def article_has_admin_badge(article) -> bool:
    """
    V4.2.2.1 Admin 判斷：
    以作者 Header 為中心，精準尋找：
      aria-label="Admin, view badge details"

    只掃作者名稱附近的標頭範圍，避免誤抓留言區其他人的 Admin。
    """
    try:
        return bool(article.evaluate(
            """(root) => {
                const profile = root.querySelector(
                    '[data-ad-rendering-role="profile_name"]'
                );
                if (!profile) return false;

                const adminSelectors = [
                    '[aria-label^="Admin"]',
                    '[aria-label*="Admin, view badge details"]',
                    '[aria-label^="Administrator"]',
                    '[aria-label^="Group admin"]',
                    '[aria-label^="Moderator"]',
                    '[aria-label^="Group moderator"]',
                    '[aria-label^="管理員"]',
                    '[aria-label^="管理员"]',
                    '[aria-label^="版主"]',
                    '[aria-label^="Tagapangasiwa"]'
                ].join(',');

                let node = profile;

                // 從 profile_name 往上找有限範圍的作者 Header。
                for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                    if (!node || node === root.parentElement) break;

                    if (node.querySelector(adminSelectors)) {
                        return true;
                    }

                    const badgeNodes = node.querySelectorAll(
                        '[role="link"][aria-label], [role="button"][aria-label]'
                    );

                    for (const badge of badgeNodes) {
                        const aria = (badge.getAttribute('aria-label') || '')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .toLowerCase();

                        if (
                            aria.startsWith('admin') ||
                            aria.startsWith('administrator') ||
                            aria.startsWith('group admin') ||
                            aria.startsWith('moderator') ||
                            aria.startsWith('group moderator') ||
                            aria.startsWith('管理員') ||
                            aria.startsWith('管理员') ||
                            aria.startsWith('版主') ||
                            aria.startsWith('tagapangasiwa')
                        ) {
                            return true;
                        }
                    }

                    // 避免掃到整篇貼文的留言區。
                    if (
                        node !== profile &&
                        (
                            node.getAttribute?.('role') === 'article' ||
                            node.matches?.('div[data-pagelet^="FeedUnit_"]')
                        )
                    ) {
                        break;
                    }
                }

                return false;
            }"""
        ))
    except Exception:
        return False


def get_post_url(container) -> str:
    for selector in (
        "a[href*='/posts/']",
        "a[href*='/permalink/']",
        "a[href*='story_fbid=']",
        "a[href*='/photo/?fbid=']",
    ):
        try:
            links = container.locator(selector)
            for index in range(min(links.count(), 30)):
                href = _safe_attr(links.nth(index), "href")
                if href.startswith("/"):
                    href = "https://www.facebook.com" + href
                if href:
                    return normalize_post_url(href.split("&__cft__")[0])
        except Exception:
            continue
    return ""


def _editor_hint(box) -> str:
    return normalize_space(
        _safe_attr(box, "aria-label")
        + " "
        + _safe_attr(box, "aria-placeholder")
        + " "
        + _safe_attr(box, "data-placeholder")
        + " "
        + _safe_attr(box, "placeholder")
    ).casefold()


def _is_reply_editor(box) -> bool:
    hint = _editor_hint(box)
    return any(word in hint for word in (
        "write an answer",
        "write a reply",
        "reply",
        "回覆",
        "回复",
        "tumugon",
        "sagot",
    ))


def _is_public_comment_editor(box) -> bool:
    try:
        if not box.is_visible(timeout=500):
            return False

        role = (_safe_attr(box, "role") or "").casefold()
        editable = (_safe_attr(box, "contenteditable") or "").casefold()
        lexical = (_safe_attr(box, "data-lexical-editor") or "").casefold()
        hint = _editor_hint(box)

        if role != "textbox" or editable != "true":
            return False

        if _is_reply_editor(box):
            return False

        public_words = (
            "write a public comment",
            "public comment",
            "write a comment",
            "comment",
            "留言",
            "發表留言",
            "发表评论",
            "komento",
        )

        if any(word in hint for word in public_words):
            return True

        # Facebook 新版有時只有 Lexical 屬性，沒有明確 placeholder。
        # 這時只接受位於 form 內、且不是回覆框的 textbox。
        if lexical == "true":
            try:
                in_form = box.evaluate("(el) => !!el.closest('form')")
            except Exception:
                in_form = False
            return bool(in_form)

        return False
    except Exception:
        return False


def _collect_comment_editors(scope):
    selectors = (
        "form div[data-lexical-editor='true'][contenteditable='true'][role='textbox']",
        "div[data-lexical-editor='true'][contenteditable='true'][role='textbox']",
        "[role='textbox'][contenteditable='true'][aria-label*='public comment' i]",
        "[role='textbox'][contenteditable='true'][aria-placeholder*='public comment' i]",
        "[role='textbox'][contenteditable='true'][aria-label*='write a comment' i]",
        "[role='textbox'][contenteditable='true'][aria-placeholder*='write a comment' i]",
        "[role='textbox'][contenteditable='true'][aria-label*='留言']",
        "[role='textbox'][contenteditable='true'][aria-placeholder*='留言']",
        "[role='textbox'][contenteditable='true'][aria-label*='komento' i]",
        "[role='textbox'][contenteditable='true'][aria-placeholder*='komento' i]",
        "form [contenteditable='true'][role='textbox']",
    )

    results = []
    seen = set()

    for selector in selectors:
        try:
            boxes = scope.locator(selector)
            for index in range(min(boxes.count(), 50)):
                box = boxes.nth(index)
                try:
                    handle = box.element_handle(timeout=500)
                    key = str(handle)
                except Exception:
                    key = f"{selector}:{index}"

                if key in seen:
                    continue
                seen.add(key)

                if _is_public_comment_editor(box):
                    results.append(box)
        except Exception:
            continue

    return results


def _nearest_editor_to_article(page: Page, article):
    try:
        article_box = article.bounding_box(timeout=1000)
    except Exception:
        article_box = None

    candidates = _collect_comment_editors(page)
    if not candidates:
        return None

    if article_box is None:
        return candidates[0]

    article_top = article_box["y"]
    article_bottom = article_box["y"] + article_box["height"]

    scored = []
    for box in candidates:
        try:
            box_rect = box.bounding_box(timeout=700)
            if not box_rect:
                continue

            box_y = box_rect["y"]
            # 留言框通常在貼文底部附近，優先 article 內或緊接其下方。
            if article_top - 30 <= box_y <= article_bottom + 260:
                distance = abs(box_y - article_bottom)
                scored.append((distance, box))
        except Exception:
            continue

    if scored:
        scored.sort(key=lambda item: item[0])
        return scored[0][1]

    return None


def find_comment_box(container, page: Optional[Page] = None):
    # V4.2.2.1 第一優先：
    # 只在目前 article 內的 form 尋找真正公開留言框。
    try:
        form_candidates = _collect_comment_editors(
            container.locator("form")
        )
    except Exception:
        form_candidates = []

    if form_candidates:
        return form_candidates[0]

    # 第二階段：保留 V4.2.2 原本 article 內搜尋。
    local_candidates = _collect_comment_editors(container)
    if local_candidates:
        return local_candidates[0]

    try:
        container.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass

    if page is not None:
        page.wait_for_timeout(400)

    # 再次優先目前 article 的 form。
    try:
        form_candidates = _collect_comment_editors(
            container.locator("form")
        )
    except Exception:
        form_candidates = []

    if form_candidates:
        return form_candidates[0]

    local_candidates = _collect_comment_editors(container)
    if local_candidates:
        return local_candidates[0]

    # 保留原本幾何位置備援，不改主架構。
    if page is not None:
        nearby = _nearest_editor_to_article(page, container)
        if nearby is not None:
            return nearby

    return None


def expand_comment_box(page: Page, article) -> bool:
    selectors = [
        "[role='button'][aria-label*='comment' i]",
        "[role='button'][aria-label*='留言']",
        "[role='button'][aria-label*='komento' i]",
        "[role='button'][aria-label*='reply' i]",
        "[role='button'][aria-label*='回覆']",
        "[role='button'][aria-label*='tumugon' i]",
        "span[role='button']",
        "div[role='button']",
    ]
    for selector in selectors:
        try:
            buttons = article.locator(selector)
            for index in range(min(buttons.count(), 120)):
                button = buttons.nth(index)
                if not button.is_visible(timeout=300):
                    continue
                label = normalize_space(
                    _safe_attr(button, "aria-label")
                    + " "
                    + _safe_attr(button, "title")
                    + " "
                    + _safe_text(button, 300)
                ).casefold()
                is_reply = any(word in label for word in (
                    "reply", "回覆", "回复", "tumugon", "answer", "sagot"
                ))
                is_comment = any(word in label for word in (
                    "comment", "留言", "komento"
                ))
                if is_comment and not is_reply:
                    try:
                        button.scroll_into_view_if_needed(timeout=1200)
                    except Exception:
                        pass
                    try:
                        button.click(timeout=1500)
                    except Exception:
                        button.evaluate("(el) => el.click()")
                    page.wait_for_timeout(900)
                    return True
        except Exception:
            continue
    return False


def clear_test_text(box) -> None:
    try:
        box.click(timeout=1500)
        box.focus(timeout=1500)
        box.press("Control+A")
        box.press("Backspace")
        return
    except Exception:
        pass

    try:
        box.evaluate(
            """(el) => {
                el.focus();
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(el);
                selection.removeAllRanges();
                selection.addRange(range);
                document.execCommand('delete', false);
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    inputType: 'deleteContentBackward',
                    data: null
                }));
            }"""
        )
    except Exception:
        pass


def fill_contenteditable(box, text: str) -> None:
    box.scroll_into_view_if_needed(timeout=2500)
    box.click(timeout=2500)
    try:
        box.focus(timeout=1800)
    except Exception:
        pass

    clear_test_text(box)

    complex_text = "\n" in text or "\r" in text or any(
        ord(char) > 127 for char in text
    )
    if complex_text:
        # Playwright fill/insert_text sends Unicode text as an input event. It
        # preserves multiline text and emoji without treating a newline as the
        # Enter key (which would submit the Facebook comment prematurely).
        try:
            box.fill(text, timeout=15000)
            return
        except Exception:
            try:
                box.insert_text(text, timeout=15000)
                return
            except Exception:
                pass

    try:
        box.press_sequentially(text, delay=18)
        return
    except Exception:
        try:
            box.type(text, delay=18, timeout=15000)
            return
        except Exception:
            pass

    box.evaluate(
        """(el, value) => {
            el.focus();
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);
            document.execCommand('insertText', false, value);
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                inputType: 'insertText',
                data: value
            }));
        }""",
        text,
    )


def get_editor_text(box) -> str:
    try:
        return normalize_space(
            box.evaluate(
                """(el) => {
                    const txt = (el.innerText || el.textContent || '');
                    return txt.replace(/\\u200B/g, '').trim();
                }"""
            )
        )
    except Exception:
        return ""


def _comment_media_state(article) -> dict:
    """Return attachment state without reading any Facebook account data."""
    try:
        state = article.evaluate(
            """(root) => {
                const norm = (value) => String(value || '').trim().toLowerCase();
                const controls = Array.from(root.querySelectorAll('[aria-label], [title]'));
                const removePattern = /(remove|delete|discard|cancel).*(photo|video|attachment|media)|^(remove|移除|刪除|取消)$/i;
                const busyPattern = /(uploading|processing|正在上傳|上傳中|處理中)/i;
                const fileInputs = Array.from(root.querySelectorAll('input[type="file"]'));
                return {
                    images: root.querySelectorAll('img').length,
                    videos: root.querySelectorAll('video').length,
                    remove_buttons: controls.filter((el) => {
                        const text = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`;
                        return removePattern.test(text.trim());
                    }).length,
                    busy: root.querySelectorAll('[role="progressbar"]').length + controls.filter((el) => {
                        const text = `${el.getAttribute('aria-label') || ''} ${el.getAttribute('title') || ''}`;
                        return busyPattern.test(text);
                    }).length,
                    file_count: fileInputs.reduce((total, el) => total + ((el.files && el.files.length) || 0), 0),
                };
            }"""
        )
        return {
            "images": int(state.get("images", 0)),
            "videos": int(state.get("videos", 0)),
            "remove_buttons": int(state.get("remove_buttons", 0)),
            "busy": int(state.get("busy", 0)),
            "file_count": int(state.get("file_count", 0)),
        }
    except Exception:
        return {
            "images": 0,
            "videos": 0,
            "remove_buttons": 0,
            "busy": 0,
            "file_count": 0,
        }


def comment_media_draft_present(article) -> bool:
    state = _comment_media_state(article)
    return bool(state["file_count"] or state["remove_buttons"])


def clear_comment_media(article, page=None) -> None:
    """Remove only the unsent attachment inside this article's comment composer."""
    remove_names = re.compile(
        r"remove|移除|刪除|取消|discard|remove attachment|remove photo|remove video",
        re.IGNORECASE,
    )
    try:
        candidates = article.get_by_role("button", name=remove_names)
        count = candidates.count()
        if count:
            candidates.last.click(timeout=2500)
            if page is not None:
                page.wait_for_timeout(350)
    except Exception:
        pass

    # Clearing the file input is a safe fallback if Facebook's remove button
    # wording changes.  It cannot delete a posted photo/video.
    try:
        inputs = article.locator('input[type="file"]')
        for index in range(inputs.count()):
            try:
                inputs.nth(index).set_input_files([], timeout=2500)
            except Exception:
                continue
    except Exception:
        pass


def attach_comment_media(page: Page, article, media_path: str | Path) -> str:
    """Attach a local photo/video and wait until Facebook renders a stable draft."""
    from 媒體來源 import media_kind

    path = Path(media_path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"媒體檔不存在或為空：{path}")
    kind = media_kind(path)
    if kind not in {"photo", "video"}:
        raise ValueError(f"不支援的媒體格式：{path.suffix}")

    before = _comment_media_state(article)
    inputs = article.locator('input[type="file"]')
    if inputs.count() == 0:
        attach_names = re.compile(
            r"attach.*(photo|video)|photo.*video|附加.*(相片|影片)|相片.*影片",
            re.IGNORECASE,
        )
        buttons = article.get_by_role("button", name=attach_names)
        if buttons.count() == 0:
            raise RuntimeError("找不到 Facebook 的『附加相片或影片』按鈕")
        buttons.last.click(timeout=3000)
        page.wait_for_timeout(400)
        inputs = article.locator('input[type="file"]')

    if inputs.count() == 0:
        raise RuntimeError("點擊附件按鈕後仍找不到檔案輸入欄位")

    selected_input = None
    expected_accept = "image" if kind == "photo" else "video"
    for index in range(inputs.count()):
        candidate = inputs.nth(index)
        accept = (candidate.get_attribute("accept") or "").lower()
        if not accept or expected_accept in accept:
            selected_input = candidate
            break
    if selected_input is None:
        raise RuntimeError(f"Facebook 留言框不接受此{('相片' if kind == 'photo' else '影片')}格式")

    selected_input.set_input_files(str(path), timeout=30_000)

    timeout_seconds = 120 if kind == "video" else 45
    deadline = time.monotonic() + timeout_seconds
    stable_ready_samples = 0
    while time.monotonic() < deadline:
        page.wait_for_timeout(600)
        state = _comment_media_state(article)
        preview_changed = bool(
            state["file_count"]
            or state["remove_buttons"] > before["remove_buttons"]
            or state["images"] > before["images"]
            or state["videos"] > before["videos"]
        )
        if preview_changed and state["busy"] == 0:
            stable_ready_samples += 1
            if stable_ready_samples >= 2:
                # Video previews can appear slightly before Facebook finishes
                # preparing the composer.  One extra pause avoids pressing
                # Enter during that transition.
                if kind == "video":
                    page.wait_for_timeout(1500)
                return kind
        else:
            stable_ready_samples = 0

    clear_comment_media(article, page)
    raise TimeoutError(f"{('相片' if kind == 'photo' else '影片')}預覽等待逾時")


def save_comment_debug(page: Page, article, reason: str) -> None:
    try:
        html = article.evaluate("(el) => el.outerHTML")
        COMMENT_DEBUG_HTML.write_text(
            f"<!-- {now_text()} | {reason} -->\n{html}",
            encoding="utf-8",
        )
    except Exception:
        try:
            COMMENT_DEBUG_HTML.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass

    try:
        article.screenshot(path=str(COMMENT_DEBUG_PNG), timeout=5000)
    except Exception:
        try:
            page.screenshot(path=str(COMMENT_DEBUG_PNG), full_page=False)
        except Exception:
            pass



def reset_failure_diagnostic() -> None:
    global _diagnostic_failure_index
    DIAGNOSTIC_ROOT.mkdir(parents=True, exist_ok=True)
    with _diagnostic_lock:
        _diagnostic_failure_index = 0


def save_failure_diagnostic(
    page: Page,
    article,
    author: AuthorResult,
    group: GroupResult,
    reason: str,
    box=None,
) -> None:
    """
    只在失敗時保存診斷。
    不再對成功案例執行 page.content() 或整頁截圖，
    避免輸入完成後卡在診斷寫檔。
    """
    global _diagnostic_failure_index

    try:
        DIAGNOSTIC_ROOT.mkdir(parents=True, exist_ok=True)
        with _diagnostic_lock:
            _diagnostic_failure_index += 1
            folder = DIAGNOSTIC_ROOT / f"失敗_{_diagnostic_failure_index:03d}"
            folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    def outer_html(locator) -> str:
        if locator is None:
            return ""
        try:
            return locator.evaluate("(el) => el.outerHTML")
        except Exception:
            return ""

    try:
        author_locator = article.locator(
            "[data-ad-rendering-role='profile_name']"
        ).first
        if author_locator.count() == 0:
            author_locator = None
    except Exception:
        author_locator = None

    try:
        form_locator = (
            box.locator("xpath=ancestor::form[1]").first
            if box is not None else None
        )
        if form_locator is not None and form_locator.count() == 0:
            form_locator = None
    except Exception:
        form_locator = None

    try:
        article_html = outer_html(article)
        (folder / "處理區塊.html").write_text(article_html, encoding="utf-8")
    except Exception:
        pass

    try:
        (folder / "作者.html").write_text(
            outer_html(author_locator), encoding="utf-8"
        )
    except Exception:
        pass

    try:
        (folder / "留言框.html").write_text(
            outer_html(box), encoding="utf-8"
        )
    except Exception:
        pass

    try:
        (folder / "form.html").write_text(
            outer_html(form_locator), encoding="utf-8"
        )
    except Exception:
        pass

    try:
        article_type = article.evaluate(
            """(el) => {
                const aria = (el.getAttribute('aria-label') || '').trim();
                if (/^(Comment|Reply) by /i.test(aria)) return 'Comment';
                if (el.querySelector('[data-ad-rendering-role="story_message"]')) return 'Post';
                return 'Unknown';
            }"""
        )
    except Exception:
        article_type = "Unknown"

    try:
        scroll_y = page.evaluate("() => window.scrollY")
    except Exception:
        scroll_y = ""

    info = [
        f"時間：{now_text()}",
        f"原因：{reason}",
        f"Group：{group.name}",
        f"Group網址：{group.url}",
        f"作者：{author.name}",
        f"作者網址：{author.url}",
        f"貼文網址：{get_post_url(article) or '未取得'}",
        f"Article型態：{article_type}",
        f"留言框：{'找到' if box is not None else '未找到'}",
        f"aria-label：{_safe_attr(box, 'aria-label') if box is not None else ''}",
        f"aria-placeholder：{_safe_attr(box, 'aria-placeholder') if box is not None else ''}",
        f"lexical：{_safe_attr(box, 'data-lexical-editor') if box is not None else ''}",
        f"目前ScrollY：{scroll_y}",
    ]
    try:
        (folder / "資訊.txt").write_text(
            "\\n".join(info), encoding="utf-8-sig"
        )
    except Exception:
        pass

    # 只截目前畫面，不截 full_page。
    try:
        page.screenshot(
            path=str(folder / "畫面.png"),
            full_page=False,
            timeout=3000,
        )
    except Exception:
        pass


def normalize_post_url(url: str) -> str:
    if not url:
        return ""
    value = url.replace("&amp;", "&").strip()
    try:
        parsed = urlparse(value)
        clean = parsed._replace(query="", fragment="")
        return urlunparse(clean)
    except Exception:
        return value.split("?")[0]



def collect_public_comment_boxes(page: Page) -> List[object]:
    """
    V5.0 核心：
    直接從頁面找真正公開留言框，不再先依賴 role=article 掃描。
    """
    return _collect_comment_editors(page)


@dataclass(frozen=True)
class CommentBoxSnapshot:
    token: str
    document_y: float
    aria_label: str


def collect_comment_box_snapshots(page: Page) -> List[CommentBoxSnapshot]:
    """
    V6.4 留言框快照收集器。

    重點：
    1. 一次收集目前 DOM 中所有真正的公開留言框。
    2. 不使用「每次重新抓清單後再用相同 index」的方式。
    3. 每個留言框加上暫時 token，之後用 token 重新定位。
    4. 按頁面實際 Y 座標由上到下排序。
    5. 可收集畫面下方已載入、但尚未完全進入可視區的留言框。
    """
    selector = (
        "div[role='textbox'][contenteditable='true']"
        "[data-lexical-editor='true']"
    )

    snapshots: List[CommentBoxSnapshot] = []
    seen_tokens: Set[str] = set()

    try:
        boxes = page.locator(selector)
        count = min(boxes.count(), 300)
    except Exception:
        return snapshots

    for index in range(count):
        box = boxes.nth(index)

        try:
            data = box.evaluate(
                """(el, index) => {
                    if (!el || !el.isConnected) return null;

                    const aria = (
                        el.getAttribute('aria-label') ||
                        el.getAttribute('aria-placeholder') ||
                        el.getAttribute('data-placeholder') ||
                        ''
                    ).replace(/\\s+/g, ' ').trim();

                    const hint = aria.toLowerCase();
                    const replyWords = [
                        'write a reply',
                        'write an answer',
                        'reply',
                        '回覆',
                        '回复',
                        'tumugon',
                        'sagot'
                    ];

                    if (replyWords.some(word => hint.includes(word))) {
                        return null;
                    }

                    const style = window.getComputedStyle(el);
                    const rect = el.getBoundingClientRect();

                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        rect.width <= 0 ||
                        rect.height <= 0
                    ) {
                        return null;
                    }

                    let token = el.getAttribute('data-v64-comment-token');
                    if (!token) {
                        token = `v64_${Date.now()}_${index}_${Math.random()
                            .toString(36).slice(2, 10)}`;
                        el.setAttribute('data-v64-comment-token', token);
                    }

                    return {
                        token,
                        documentY: rect.top + window.scrollY,
                        aria
                    };
                }""",
                index,
            )
        except Exception:
            continue

        if not data:
            continue

        token = str(data.get("token") or "")
        if not token or token in seen_tokens:
            continue

        seen_tokens.add(token)
        snapshots.append(
            CommentBoxSnapshot(
                token=token,
                document_y=float(data.get("documentY") or 0),
                aria_label=normalize_space(str(data.get("aria") or "")),
            )
        )

    snapshots.sort(key=lambda item: item.document_y)
    return snapshots


def get_comment_box_by_token(page: Page, token: str):
    try:
        locator = page.locator(
            f'[data-v64-comment-token="{token}"]'
        ).first
        if locator.count() == 0:
            return None
        return locator
    except Exception:
        return None


def article_from_comment_box(page: Page, box):
    """
    V5.2 核心：
    Facebook 貼文與公開留言框通常共同位於 role=feed 的同一個直接子容器內。
    由留言框逐層往上，找到「父層是 role=feed」的節點，將其視為整篇貼文容器。

    這個方法：
    - 不依賴 role=article
    - 不依賴 Comment by / Reply by
    - 不依賴畫面 Y 座標
    - 不受縮放比例與視窗大小明顯影響
    """
    try:
        handle = box.evaluate_handle(
            """(el) => {
                let node = el;

                while (node && node.parentElement) {
                    const parent = node.parentElement;

                    if (parent.getAttribute?.('role') === 'feed') {
                        return node;
                    }

                    node = parent;
                }

                return null;
            }"""
        )
        element = handle.as_element()
        if element is None:
            return None

        return _locator_from_element_handle(page, element)
    except Exception:
        return None


def _locator_from_element_handle(page: Page, element_handle):
    if element_handle is None:
        return None
    try:
        token = f"__v5_target_{time.monotonic_ns()}"
        page.evaluate(
            """([el, token]) => {
                el.setAttribute('data-v5-target', token);
            }""",
            [element_handle, token],
        )
        return page.locator(f'[data-v5-target="{token}"]').first
    except Exception:
        return None


def extract_author_from_comment_box(
    page: Page,
    box,
    group: GroupResult,
) -> Tuple[Optional[AuthorResult], Optional[object]]:
    article = article_from_comment_box(page, box)
    if article is None:
        return None, None

    author = extract_author_from_article(article, group)
    return author, article



def extract_post_id_from_url(url: str) -> str:
    value = (url or "").replace("&amp;", "&")
    patterns = (
        r"/groups/[^/]+/posts/(\d+)",
        r"/posts/(\d+)",
        r"/permalink/(\d+)",
        r"[?&]story_fbid=(\d+)",
        r"[?&]fbid=(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.I)
        if match:
            return match.group(1)
    return ""


def build_post_key(article, author: AuthorResult, group_url: str) -> Tuple[str, str]:
    """
    優先使用真正貼文 ID / 貼文網址。
    沒有網址時才用作者 + 貼文文字摘要。
    """
    post_url = get_post_url(article)
    post_id = extract_post_id_from_url(post_url)

    if post_id:
        return f"post_id:{post_id}", post_id

    if post_url:
        normalized = normalize_post_url(post_url).casefold()
        return f"post_url:{normalized}", normalized

    try:
        preview = normalize_space(
            article.evaluate(
                """(el) => {
                    const story = el.querySelector(
                        '[data-ad-rendering-role="story_message"]'
                    );
                    const root = story || el;
                    return (root.innerText || root.textContent || '')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .slice(0, 500);
                }"""
            )
        )
    except Exception:
        preview = ""

    author_key = (author.url or author.name or "unknown").casefold()

    if preview:
        value = f"{author_key}|{preview.casefold()}"
        return f"author_text:{value}", value

    # 最後備援才使用舊 signature 概念。
    value = f"{group_url}|{author_key}|{time.monotonic_ns()}"
    return f"fallback:{value}", value



def comment_box_signature(box, group_url: str) -> str:
    try:
        return box.evaluate(
            """(el) => {
                const form = el.closest('form');
                const article = el.closest('[role="article"]');
                const root = form || article || el;
                const txt = (root.innerText || '')
                    .replace(/\\s+/g, ' ')
                    .trim()
                    .slice(0, 300);
                const rect = el.getBoundingClientRect();
                return `${location.pathname}|${Math.round(rect.top + window.scrollY)}|${txt}`;
            }"""
        )
    except Exception:
        return f"{group_url}|box|{time.monotonic_ns()}"


def test_one_comment_box(
    page: Page,
    box,
    group: GroupResult,
    test_text: str,
    mode: str,
    media_path: str | Path | None = None,
) -> CommentTestResult:
    started_at = time.monotonic()
    created_at = now_text()

    author, article = extract_author_from_comment_box(page, box, group)

    if article is None:
        placeholder_author = AuthorResult(
            name="未取得作者",
            url="",
            group_name=group.name,
            group_url=group.url,
        )
        save_failure_diagnostic(
            page, page.locator("body"), placeholder_author, group,
            "由留言框找不到貼文容器", box
        )
        return CommentTestResult(
            placeholder_author.name, placeholder_author.url,
            group.name, group.url, "",
            True, False, False, test_text,
            "由留言框找不到貼文容器", created_at
        )

    if author is None:
        placeholder_author = AuthorResult(
            name="未取得作者",
            url="",
            group_name=group.name,
            group_url=group.url,
        )
        save_failure_diagnostic(
            page, article, placeholder_author, group,
            "找到留言框但找不到貼文作者", box
        )
        return CommentTestResult(
            placeholder_author.name, placeholder_author.url,
            group.name, group.url, get_post_url(article),
            True, False, False, test_text,
            "找到留言框但找不到貼文作者", created_at
        )

    if article_has_admin_badge(article):
        return CommentTestResult(
            author.name, author.url, group.name, group.url,
            get_post_url(article),
            True, False, False, test_text,
            "Admin 貼文，已略過", created_at
        )

    post_url = get_post_url(article)

    try:
        append_run_log(
            "V5 找到公開留言框｜"
            f"作者：{author.name}｜"
            f"aria-label：{_safe_attr(box, 'aria-label')}｜"
            f"aria-placeholder：{_safe_attr(box, 'aria-placeholder')}｜"
            f"lexical：{_safe_attr(box, 'data-lexical-editor')}"
        )

        fill_contenteditable(box, test_text)
        page.wait_for_timeout(700)

        current = get_editor_text(box)
        success = bool(current) and (
            current == normalize_space(test_text)
            or normalize_space(test_text) in current
            or current in normalize_space(test_text)
        )

        if not success:
            try:
                box.click(timeout=1200)
                box.focus(timeout=1200)
                box.press("Control+A")
                box.press("Backspace")
                if "\n" in test_text or "\r" in test_text or any(
                    ord(char) > 127 for char in test_text
                ):
                    box.insert_text(test_text, timeout=15000)
                else:
                    box.press_sequentially(test_text, delay=24)
                page.wait_for_timeout(700)
                current = get_editor_text(box)
                success = bool(current)
            except Exception:
                pass

        attached_kind = "none"
        if success and media_path:
            try:
                attached_kind = attach_comment_media(page, article, media_path)
                append_run_log(
                    "V5 留言媒體預覽完成｜"
                    f"類型：{attached_kind}｜檔名：{Path(media_path).name}"
                )
            except Exception as exc:
                clear_test_text(box)
                clear_comment_media(article, page)
                status = f"媒體附加失敗：{type(exc).__name__}：{exc}"
                append_run_log(
                    "V5 公開留言結果｜"
                    f"作者：{author.name}｜群組：{group.name}｜"
                    "輸入成功：True｜確認送出：False｜"
                    f"媒體：{Path(media_path).name}｜狀態：{status}"
                )
                return CommentTestResult(
                    author.name, author.url, group.name, group.url, post_url,
                    True, True, False, test_text, status, created_at
                )

        submitted = False
        if success and mode == "正式留言":
            before_text = current
            box.press("Enter")
            if attached_kind == "none":
                page.wait_for_timeout(1200)
                after_text = get_editor_text(box)
                submitted = not after_text or after_text != before_text
            else:
                deadline = time.monotonic() + (60 if attached_kind == "video" else 35)
                while time.monotonic() < deadline:
                    page.wait_for_timeout(750)
                    after_text = get_editor_text(box)
                    text_cleared = not after_text or after_text != before_text
                    if text_cleared and not comment_media_draft_present(article):
                        submitted = True
                        break
            if submitted:
                media_label = {"photo": "相片", "video": "影片"}.get(attached_kind, "")
                status = f"留言與{media_label}已送出" if media_label else "留言已送出"
            else:
                status = "已按 Enter，但未確認送出"
        else:
            clear_test_text(box)
            if attached_kind != "none":
                clear_comment_media(article, page)
            page.wait_for_timeout(350)
            status = (
                "輸入、附件預覽並清空成功"
                if success and attached_kind != "none"
                else "輸入並清空成功"
                if success
                else "找到留言框但輸入驗證失敗"
            )

        if not success:
            save_failure_diagnostic(
                page, article, author, group,
                "留言框輸入驗證失敗", box
            )

        append_run_log(
            "V5 公開留言結果｜"
            f"作者：{author.name}｜"
            f"群組：{group.name}｜"
            f"輸入成功：{success}｜"
            f"確認送出：{submitted}｜"
            f"媒體：{Path(media_path).name if media_path else '無'}｜"
            f"狀態：{status}"
        )

        return CommentTestResult(
            author.name, author.url, group.name, group.url, post_url,
            True, success, submitted, test_text, status, created_at
        )

    except Exception as exc:
        clear_test_text(box)
        clear_comment_media(article, page)
        save_failure_diagnostic(
            page, article, author, group,
            f"輸入失敗：{exc}", box
        )
        append_run_log(
            "V5 公開留言結果｜"
            f"作者：{author.name}｜"
            f"群組：{group.name}｜"
            "輸入成功：False｜確認送出：False｜"
            f"狀態：輸入失敗（{type(exc).__name__}）"
        )
        return CommentTestResult(
            author.name, author.url, group.name, group.url, post_url,
            True, False, False, test_text, f"輸入失敗：{exc}", created_at
        )



def test_one_article(
    page: Page,
    article,
    author: AuthorResult,
    group: GroupResult,
    test_text: str,
    mode: str,
) -> CommentTestResult:
    created_at = now_text()
    post_url = get_post_url(article)

    box = find_comment_box(article, page)
    if box is None:
        expand_comment_box(page, article)
        box = find_comment_box(article, page)

    if box is None:
        try:
            article.scroll_into_view_if_needed(timeout=2000)
            page.mouse.wheel(0, 350)
            page.wait_for_timeout(500)
        except Exception:
            pass
        box = find_comment_box(article, page)

    if box is None:
        save_comment_debug(page, article, "找不到留言框")
        save_failure_diagnostic(page, article, author, group, "找不到留言框")
        return CommentTestResult(
            author.name, author.url, group.name, group.url, post_url,
            False, False, False, test_text, "找不到留言框", created_at
        )

    try:
        if _is_reply_editor(box):
            save_comment_debug(page, article, "誤抓到回覆框 Write an answer")
            save_failure_diagnostic(page, article, author, group, "誤抓到回覆框", box)
            return CommentTestResult(
                author.name, author.url, group.name, group.url, post_url,
                False, False, False, test_text,
                "找到回覆框但不是公開留言框，已略過",
                created_at,
            )

        append_run_log(
            "找到公開留言框｜"
            f"作者：{author.name}｜"
            f"aria-label：{_safe_attr(box, 'aria-label')}｜"
            f"aria-placeholder：{_safe_attr(box, 'aria-placeholder')}｜"
            f"lexical：{_safe_attr(box, 'data-lexical-editor')}"
        )

        fill_contenteditable(box, test_text)
        page.wait_for_timeout(700)

        current = get_editor_text(box)
        success = bool(current) and (
            current == normalize_space(test_text)
            or normalize_space(test_text) in current
            or current in normalize_space(test_text)
        )

        if not success:
            try:
                box.click(timeout=1200)
                box.focus(timeout=1200)
                box.press("Control+A")
                box.press("Backspace")
                if "\n" in test_text or "\r" in test_text or any(
                    ord(char) > 127 for char in test_text
                ):
                    box.insert_text(test_text, timeout=15000)
                else:
                    box.press_sequentially(test_text, delay=24)
                page.wait_for_timeout(700)
                current = get_editor_text(box)
                success = bool(current)
            except Exception:
                pass

        submitted = False
        if success and mode == "正式留言":
            before_text = current
            box.press("Enter")
            page.wait_for_timeout(1200)
            after_text = get_editor_text(box)
            submitted = not after_text or after_text != before_text
            status = "留言已送出" if submitted else "已按 Enter，但未確認送出"
        else:
            clear_test_text(box)
            page.wait_for_timeout(350)
            status = "輸入並清空成功" if success else "找到留言框但輸入驗證失敗"

        if not success:
            save_comment_debug(page, article, "留言框輸入驗證失敗")
            save_failure_diagnostic(page, article, author, group, "留言框輸入驗證失敗", box)

        return CommentTestResult(
            author.name, author.url, group.name, group.url, post_url,
            True, success, submitted, test_text, status, created_at
        )
    except Exception as exc:
        clear_test_text(box)
        save_comment_debug(page, article, f"輸入失敗：{exc}")
        save_failure_diagnostic(page, article, author, group, f"輸入失敗：{exc}", box)
        return CommentTestResult(
            author.name, author.url, group.name, group.url, post_url,
            True, False, False, test_text, f"輸入失敗：{exc}", created_at
        )


def reset_comment_result_files(mode: str) -> None:
    DESKTOP.mkdir(parents=True, exist_ok=True)
    with COMMENT_TEST_TXT.open("w", encoding="utf-8-sig") as handle:
        handle.write("Facebook Group 留言結果\n")
        handle.write(f"開始時間：{now_text()}\n")
        handle.write(f"模式：{mode}\n")
        handle.write("=" * 90 + "\n\n")
    with COMMENT_TEST_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerow([
            "序號", "時間", "模式", "作者名稱", "作者網址", "來源 Group",
            "Group 網址", "貼文網址", "留言框", "輸入", "已送出", "文案", "狀態"
        ])


def append_comment_result(item: CommentTestResult, index: int, mode: str) -> None:
    with COMMENT_TEST_TXT.open("a", encoding="utf-8-sig") as handle:
        handle.write(f"{index}. {item.author_name}\n")
        handle.write(f"   時間：{item.created_at}\n")
        handle.write(f"   模式：{mode}\n")
        handle.write(f"   作者網址：{item.author_url}\n")
        handle.write(f"   來源 Group：{item.group_name}\n")
        handle.write(f"   Group 網址：{item.group_url}\n")
        handle.write(f"   貼文網址：{item.post_url or '未取得'}\n")
        handle.write(f"   留言框：{'找到' if item.comment_box_found else '未找到'}\n")
        handle.write(f"   輸入：{'成功' if item.input_success else '失敗'}\n")
        handle.write(f"   已送出：{'是' if item.submitted else '否'}\n")
        handle.write(f"   文案：{item.test_text}\n")
        handle.write(f"   狀態：{item.status}\n\n")
    with COMMENT_TEST_CSV.open("a", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerow([
            index, item.created_at, mode, item.author_name, item.author_url,
            item.group_name, item.group_url, item.post_url,
            "找到" if item.comment_box_found else "未找到",
            "成功" if item.input_success else "失敗",
            "是" if item.submitted else "否",
            item.test_text, item.status,
        ])


def export_comment_tests(rows: Iterable[CommentTestResult], mode: str = "測試模式") -> Tuple[Path, Path]:
    data = list(rows)
    reset_comment_result_files(mode)
    for index, item in enumerate(data, 1):
        append_comment_result(item, index, mode)
    return COMMENT_TEST_TXT, COMMENT_TEST_CSV


# ============================================================
# AdsPower API 節流狀態
# ============================================================

_ads_api_lock = threading.Lock()
_ads_api_last_call = 0.0


def _wait_for_ads_api_slot() -> None:
    """確保 AdsPower API 呼叫之間至少間隔指定秒數。"""
    global _ads_api_last_call

    with _ads_api_lock:
        now = time.monotonic()
        wait_seconds = ADSPOWER_MIN_INTERVAL - (now - _ads_api_last_call)

        if wait_seconds > 0:
            time.sleep(wait_seconds)

        _ads_api_last_call = time.monotonic()


def _is_rate_limit_error(message: str) -> bool:
    value = (message or "").casefold()
    return (
        "too many request" in value
        or "rate limit" in value
        or "requests per second" in value
        or "429" in value
    )


# ============================================================
# AdsPower
# ============================================================

def adspower_get(
    path: str,
    params: Optional[Dict[str, str]] = None,
    *,
    max_retries: int = ADSPOWER_MAX_RETRIES,
) -> Dict:
    """
    AdsPower Local API 穩定呼叫：
    - 全域限速
    - Too many request / HTTP 429 自動重試
    - 暫時性連線錯誤自動重試
    """
    url = f"{ADSPOWER_API}/{path.lstrip('/')}"
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        _wait_for_ads_api_slot()

        try:
            response = requests.get(
                url,
                params=params or {},
                headers=ADSPOWER_HEADERS,
                timeout=API_TIMEOUT,
            )

            if response.status_code == 429:
                raise RuntimeError(
                    f"HTTP 429 Too Many Requests：{response.text[:300]}"
                )

            response.raise_for_status()
            payload = response.json()

            code = payload.get("code")
            message = str(payload.get("msg") or "")

            if code == 0:
                return payload

            if _is_rate_limit_error(message):
                raise RuntimeError(message)

            raise RuntimeError(message or f"AdsPower API 錯誤：{payload}")

        except (
            requests.Timeout,
            requests.ConnectionError,
            requests.HTTPError,
            ValueError,
            RuntimeError,
        ) as exc:
            last_error = exc
            message = str(exc)

            retryable = (
                _is_rate_limit_error(message)
                or isinstance(
                    exc,
                    (
                        requests.Timeout,
                        requests.ConnectionError,
                    ),
                )
                or "502" in message
                or "503" in message
                or "504" in message
                or "updating" in message.casefold()
                or "download" in message.casefold()
            )

            if not retryable or attempt >= max_retries - 1:
                break

            delay = ADSPOWER_RETRY_DELAYS[
                min(attempt, len(ADSPOWER_RETRY_DELAYS) - 1)
            ]
            time.sleep(delay)

    raise RuntimeError(
        f"AdsPower API 呼叫失敗（已重試 {max_retries} 次）：{last_error}"
    )


def get_all_profiles(group_id: str = "") -> List[Dict[str, str]]:
    profiles: List[Dict[str, str]] = []
    page = 1

    while True:
        params = {
            "page": str(page),
            "page_size": "100",
        }
        if group_id.strip():
            params["group_id"] = group_id.strip()

        payload = adspower_get("user/list", params)
        time.sleep(0.2)
        data = payload.get("data") or {}
        page_list = data.get("list") or []

        for item in page_list:
            user_id = str(item.get("user_id") or "").strip()
            name = str(item.get("name") or item.get("remark") or user_id).strip()
            if user_id:
                profiles.append({"user_id": user_id, "name": name})

        if len(page_list) < 100:
            break

        page += 1

    profiles.sort(key=lambda item: item["name"].casefold())
    return profiles


def start_profile(user_id: str) -> str:
    """
    啟動 AdsPower 環境並等待 CDP 位址。
    遇到 SunBrowser 更新、下載中或限流時自動重試。
    """
    last_error: Optional[Exception] = None

    for attempt in range(BROWSER_START_MAX_RETRIES):
        try:
            payload = adspower_get(
                "browser/start",
                {
                    "user_id": user_id,
                    "open_tabs": "1",
                    "ip_tab": "0",
                },
            )
            data = payload.get("data") or {}

            ws = data.get("ws") or {}
            ws_url = ws.get("puppeteer") or ws.get("selenium")
            if not ws_url:
                ws_url = data.get("ws_endpoint") or data.get("puppeteer")

            if ws_url:
                return str(ws_url)

            raise RuntimeError(
                "AdsPower 未回傳 CDP 位址："
                + json.dumps(data, ensure_ascii=False)
            )

        except Exception as exc:
            last_error = exc

            if attempt >= BROWSER_START_MAX_RETRIES - 1:
                break

            time.sleep(BROWSER_START_RETRY_DELAY)

    raise RuntimeError(
        f"AdsPower 環境啟動失敗（已重試 "
        f"{BROWSER_START_MAX_RETRIES} 次）：{last_error}"
    )


def prepare_facebook_notification_permission(
    context: BrowserContext,
    page: Optional[Page] = None,
) -> bool:
    """Grant notifications and dismiss Facebook's matching in-page overlay.

    RC19 group flows connect through Playwright instead of BrowserSession's
    Selenium path, so they need the same preflight at the shared page chooser.
    """
    granted = False
    for origin in (
        "https://www.facebook.com",
        "https://facebook.com",
        "https://m.facebook.com",
        "https://web.facebook.com",
    ):
        try:
            context.grant_permissions(["notifications"], origin=origin)
            granted = True
        except Exception:
            continue

    if page is None:
        return granted
    try:
        result = page.evaluate(
            r"""
            () => {
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
              for(const dialog of [...document.querySelectorAll(
                '[role="dialog"],[aria-modal="true"]'
              )].filter(visible)){
                if(!dialogTerms.some(term=>textOf(dialog).includes(term)))continue;
                for(const control of [...dialog.querySelectorAll(
                  'button,[role="button"],[aria-label],[tabindex="0"]'
                )].filter(visible)){
                  const label=textOf(control);
                  if(closeTerms.some(term=>label===term||label.includes(term))){
                    control.click();
                    return {matched:true,clicked:true};
                  }
                }
                return {matched:true,clicked:false};
              }
              return {matched:false,clicked:false};
            }
            """
        ) or {}
        if result.get("matched") and not result.get("clicked"):
            page.keyboard.press("Escape")
    except Exception:
        pass
    return granted


def choose_facebook_page(context: BrowserContext) -> Page:
    pages = context.pages
    if not pages:
        selected = context.new_page()
        prepare_facebook_notification_permission(context, selected)
        return selected

    # 優先目前已開啟 Facebook Groups 頁面。
    for page in reversed(pages):
        url = (page.url or "").lower()
        if "facebook.com" in url and (
            "/groups" in url or "/search/groups" in url or "filter=groups" in url
        ):
            prepare_facebook_notification_permission(context, page)
            return page

    # 其次任意 Facebook 分頁。
    for page in reversed(pages):
        if "facebook.com" in (page.url or "").lower():
            prepare_facebook_notification_permission(context, page)
            return page

    selected = pages[-1]
    prepare_facebook_notification_permission(context, selected)
    return selected


# ============================================================
# Facebook Group 掃描
# ============================================================

def nearest_result_container(anchor) -> object:
    """
    從 Group 連結往上找最可能的搜尋結果卡片。
    Facebook DOM 常改版，因此提供多層備援。
    """
    candidates = [
        "xpath=ancestor::*[@role='article'][1]",
        "xpath=ancestor::*[@data-virtualized='false'][1]",
        "xpath=ancestor::div[@role='listitem'][1]",
        "xpath=ancestor::div[count(.//a) >= 1][.//span][1]",
    ]

    for selector in candidates:
        try:
            locator = anchor.locator(selector)
            if locator.count() > 0:
                text = normalize_space(locator.first.inner_text(timeout=1_500))
                if text:
                    return locator.first
        except Exception:
            continue

    return anchor


def extract_group_name(anchor, container, group_url: str) -> str:
    # 優先使用 anchor 文字。
    try:
        text = normalize_space(anchor.inner_text(timeout=1_500))
        if text and len(text) <= 180:
            # 排除只有「加入／Join」的按鈕文字。
            if text.casefold() not in {
                "join", "joined", "加入", "已加入", "sumali", "joined group"
            }:
                return clean_group_name(text)
    except Exception:
        pass

    # aria-label/title。
    for attr in ("aria-label", "title"):
        try:
            value = normalize_space(anchor.get_attribute(attr) or "")
            if value and len(value) <= 180:
                return clean_group_name(value)
        except Exception:
            pass

    # 從卡片內挑選較像名稱的連結文字。
    try:
        links = container.locator("a[href*='/groups/']")
        for index in range(min(links.count(), 8)):
            link = links.nth(index)
            text = normalize_space(link.inner_text(timeout=1_000))
            if (
                text
                and 2 <= len(text) <= 180
                and text.casefold() not in {
                    "join", "joined", "加入", "已加入", "sumali"
                }
            ):
                return clean_group_name(text)
    except Exception:
        pass

    return clean_group_name(group_id_from_url(group_url))


def scan_visible_groups(page: Page) -> List[GroupResult]:
    anchors = page.locator("a[href*='facebook.com/groups/'], a[href^='/groups/']")
    count = min(anchors.count(), 2_000)

    results_by_url: Dict[str, GroupResult] = {}

    for index in range(count):
        anchor = anchors.nth(index)

        try:
            href = anchor.get_attribute("href", timeout=1_000) or ""
        except Exception:
            continue

        if href.startswith("/"):
            href = "https://www.facebook.com" + href

        group_url = normalize_group_url(href)
        if not group_url:
            continue

        # 排除目前貼文、媒體、成員等子頁連結。
        parsed_path = urlparse(href).path.strip("/").split("/")
        if len(parsed_path) >= 3 and parsed_path[0].lower() == "groups":
            third = parsed_path[2].lower()
            if third in GROUP_SUBPATHS:
                continue

        try:
            container = nearest_result_container(anchor)
            card_text = normalize_space(container.inner_text(timeout=2_000))
        except Exception:
            continue

        parsed = extract_today_post_count(card_text)
        if not parsed:
            continue

        today_posts, activity_text = parsed
        if today_posts < MIN_TODAY_POSTS:
            continue

        name = extract_group_name(anchor, container, group_url)

        result = GroupResult(
            name=clean_group_name(name),
            url=group_url,
            today_posts=today_posts,
            members=extract_member_count(card_text),
            privacy=extract_privacy(card_text),
            activity_text=activity_text,
        )

        previous = results_by_url.get(group_url)
        if previous is None or result.today_posts > previous.today_posts:
            results_by_url[group_url] = result

    return sorted(
        results_by_url.values(),
        key=lambda item: (-item.today_posts, item.name.casefold()),
    )


def current_page_signature(page: Page) -> Tuple[int, str]:
    try:
        group_links = page.locator(
            "a[href*='facebook.com/groups/'], a[href^='/groups/']"
        )
        count = group_links.count()
        last_href = ""
        if count:
            last_href = group_links.nth(count - 1).get_attribute("href") or ""
        return count, last_href
    except Exception:
        return 0, ""



def verified_small_scroll(page: Page, group_name: str = "") -> bool:
    """
    V6.2 小距離確認式滑動：
    - 每次只滑約 320～480 px
    - 滑動後確認 window.scrollY 確實增加
    - 若第一次沒動，改用 PageDown 小幅備援
    - 最多重試 3 次，不做大距離跳躍
    """
    distances = (360, 420, 480)

    try:
        before_y = float(page.evaluate("() => window.scrollY") or 0)
    except Exception:
        before_y = 0.0

    before_boxes = len(collect_public_comment_boxes(page))

    for attempt, distance in enumerate(distances, 1):
        try:
            page.evaluate(
                """(distance) => {
                    window.scrollBy({
                        top: distance,
                        left: 0,
                        behavior: 'smooth'
                    });
                }""",
                distance,
            )
        except Exception:
            try:
                page.mouse.wheel(0, distance)
            except Exception:
                pass

        page.wait_for_timeout(850)

        try:
            after_y = float(page.evaluate("() => window.scrollY") or 0)
        except Exception:
            after_y = before_y

        moved = after_y - before_y
        after_boxes = len(collect_public_comment_boxes(page))

        append_run_log(
            f"V6.4 確認滑動：{group_name}｜"
            f"第{attempt}次｜距離設定 {distance}px｜"
            f"ScrollY {int(before_y)}→{int(after_y)}｜"
            f"實際移動 {int(moved)}px｜"
            f"留言框 {before_boxes}→{after_boxes}"
        )

        if moved >= 180:
            return True

        # 小幅備援，不使用大距離 wheel。
        try:
            page.keyboard.press("PageDown")
            page.wait_for_timeout(700)
            fallback_y = float(page.evaluate("() => window.scrollY") or 0)
        except Exception:
            fallback_y = after_y

        fallback_moved = fallback_y - before_y
        append_run_log(
            f"V6.4 PageDown備援：{group_name}｜"
            f"ScrollY {int(before_y)}→{int(fallback_y)}｜"
            f"實際移動 {int(fallback_moved)}px"
        )

        if fallback_moved >= 180:
            return True

    append_run_log(f"V6.4 滑動失敗：{group_name}｜頁面沒有明顯移動")
    return False



def scroll_once(page: Page) -> None:
    try:
        page.mouse.move(800, 700)
        page.mouse.wheel(0, 1800)
    except Exception:
        pass

    try:
        page.evaluate(
            """
            () => {
                const amount = Math.max(1200, Math.floor(window.innerHeight * 1.6));
                window.scrollBy({top: amount, left: 0, behavior: 'instant'});
            }
            """
        )
    except Exception:
        pass


def scan_groups_with_scrolling(
    page: Page,
    max_scrolls: int,
    wait_seconds: float,
    progress_callback,
    stop_event: threading.Event,
) -> List[GroupResult]:
    all_results: Dict[str, GroupResult] = {}
    no_new_rounds = 0
    previous_signature: Optional[Tuple[int, str]] = None

    for round_index in range(max_scrolls + 1):
        if stop_event.is_set():
            break

        visible = scan_visible_groups(page)
        new_count = 0

        for result in visible:
            previous = all_results.get(result.url)
            if previous is None:
                all_results[result.url] = result
                new_count += 1
            elif result.today_posts > previous.today_posts:
                all_results[result.url] = result

        signature = current_page_signature(page)

        progress_callback(
            round_index,
            len(all_results),
            new_count,
            signature[0],
        )

        if round_index >= max_scrolls:
            break

        if signature == previous_signature and new_count == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0

        previous_signature = signature

        if no_new_rounds >= NO_NEW_RESULT_LIMIT:
            break

        scroll_once(page)
        page.wait_for_timeout(int(wait_seconds * 1000))

    return sorted(
        all_results.values(),
        key=lambda item: (-item.today_posts, item.name.casefold()),
    )


def export_results(results: Iterable[GroupResult]) -> Tuple[Path, Path]:
    DESKTOP.mkdir(parents=True, exist_ok=True)
    rows = list(results)

    with OUTPUT_TXT.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write(f"Facebook Group 今日貼文 >= {MIN_TODAY_POSTS}\n")
        handle.write(f"掃描時間：{now_text()}\n")
        handle.write(f"符合數量：{len(rows)}\n")
        handle.write("=" * 80 + "\n\n")

        for index, item in enumerate(rows, start=1):
            handle.write(f"{index}. {item.name}\n")
            handle.write(f"   今日貼文：{item.today_posts}\n")
            handle.write(f"   成員數：{item.members if item.members is not None else '未知'}\n")
            handle.write(f"   類型：{item.privacy}\n")
            handle.write(f"   網址：{item.url}\n")
            handle.write(f"   判斷文字：{item.activity_text}\n\n")

    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["序號", "Group 名稱", "今日貼文數", "成員數", "類型", "Group 網址", "判斷文字"])
        for index, item in enumerate(rows, start=1):
            writer.writerow([
                index,
                item.name,
                item.today_posts,
                item.members if item.members is not None else "",
                item.privacy,
                item.url,
                item.activity_text,
            ])

    return OUTPUT_TXT, OUTPUT_CSV


def import_groups_file(path: Path) -> List[GroupResult]:
    results: Dict[str, GroupResult] = {}

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                url = normalize_group_url(
                    row.get("Group 網址")
                    or row.get("網址")
                    or ""
                )
                if not url:
                    continue

                name = normalize_space(
                    row.get("Group 名稱")
                    or row.get("名稱")
                    or group_id_from_url(url)
                )

                try:
                    today_posts = int(float(
                        row.get("今日貼文數")
                        or row.get("今日貼文")
                        or 10
                    ))
                except Exception:
                    today_posts = 10

                try:
                    raw_members = row.get("成員數") or ""
                    members = int(float(raw_members)) if raw_members else None
                except Exception:
                    members = None

                results[url] = GroupResult(
                    name=name,
                    url=url,
                    today_posts=today_posts,
                    members=members,
                    privacy=normalize_space(row.get("類型") or "Unknown"),
                    activity_text=normalize_space(row.get("判斷文字") or ""),
                )

    else:
        content = path.read_text(encoding="utf-8-sig", errors="ignore")
        blocks = re.split(r"\n\s*\n", content)

        for block in blocks:
            match = re.search(
                r"(?:網址|Group 網址)\s*：\s*(https?://\S+)",
                block,
                flags=re.I,
            )
            if not match:
                continue

            url = normalize_group_url(match.group(1))
            if not url:
                continue

            lines = [normalize_space(line) for line in block.splitlines() if normalize_space(line)]
            name = re.sub(r"^\d+\.\s*", "", lines[0]) if lines else group_id_from_url(url)

            post_match = re.search(r"今日貼文(?:數)?\s*：\s*(\d+)", block)
            member_match = re.search(r"成員數\s*：\s*(\d+)", block)
            privacy_match = re.search(r"類型\s*：\s*(\S+)", block)

            results[url] = GroupResult(
                name=name,
                url=url,
                today_posts=int(post_match.group(1)) if post_match else 10,
                members=int(member_match.group(1)) if member_match else None,
                privacy=privacy_match.group(1) if privacy_match else "Unknown",
                activity_text="",
            )

    return sorted(
        results.values(),
        key=lambda item: (-item.today_posts, item.name.casefold()),
    )


# ============================================================
# GUI
# ============================================================

class GroupScannerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Facebook Group 留言框測試器 V6.4 Stable")
        self.root.geometry("1080x720")
        self.root.minsize(900, 600)

        self.profile_items: List[Dict[str, str]] = []
        self.message_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.last_results: List[GroupResult] = []
        self.last_authors: List[AuthorResult] = []
        self.last_comment_tests: List[CommentTestResult] = []

        self.group_id_var = tk.StringVar(value=DEFAULT_GROUP_ID)
        self.profile_var = tk.StringVar()
        self.max_scrolls_var = tk.IntVar(value=DEFAULT_MAX_SCROLLS)
        self.wait_var = tk.DoubleVar(value=DEFAULT_SCROLL_WAIT)
        self.open_wait_var = tk.DoubleVar(value=DEFAULT_OPEN_WAIT)
        self.posts_to_scan_var = tk.IntVar(value=DEFAULT_POSTS_TO_SCAN)
        self.author_scrolls_var = tk.IntVar(value=DEFAULT_AUTHOR_SCROLLS)
        self.comment_mode_var = tk.StringVar(value="測試模式")
        self.status_var = tk.StringVar(value="請先讀取 AdsPower 環境")
        self.summary_var = tk.StringVar(value="符合 Group：0")
        self.comment_stats = {
            "scanned": 0, "success": 0, "failed": 0,
            "admin": 0, "skipped": 0, "no_box": 0,
        }

        self._build_ui()
        self.root.after(100, self._process_messages)

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.configure("Treeview", rowheight=30, font=("Microsoft JhengHei UI", 11))
            style.configure(
                "Treeview.Heading",
                font=("Microsoft JhengHei UI", 11, "bold"),
            )
            style.configure("TButton", font=("Microsoft JhengHei UI", 11, "bold"))
            style.configure("TLabel", font=("Microsoft JhengHei UI", 11))
        except Exception:
            pass

        top = ttk.Frame(self.root, padding=12)
        top.pack(fill="x")

        ttk.Label(top, text="AdsPower 群組 ID：").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=5
        )
        ttk.Entry(top, textvariable=self.group_id_var, width=18).grid(
            row=0, column=1, sticky="w", pady=5
        )
        self.load_button = ttk.Button(
            top,
            text="讀取環境",
            command=self.load_profiles,
        )
        self.load_button.grid(row=0, column=2, padx=8, pady=5)

        ttk.Label(top, text="AdsPower 環境：").grid(
            row=0, column=3, sticky="w", padx=(18, 6), pady=5
        )
        self.profile_combo = ttk.Combobox(
            top,
            textvariable=self.profile_var,
            width=42,
            state="readonly",
        )
        self.profile_combo.grid(row=0, column=4, sticky="ew", pady=5)

        ttk.Label(top, text="最多下滑：").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=5
        )
        ttk.Spinbox(
            top,
            from_=1,
            to=300,
            textvariable=self.max_scrolls_var,
            width=10,
        ).grid(row=1, column=1, sticky="w", pady=5)

        ttk.Label(top, text="每次等待（秒）：").grid(
            row=1, column=3, sticky="w", padx=(18, 6), pady=5
        )
        ttk.Spinbox(
            top,
            from_=0.5,
            to=10.0,
            increment=0.1,
            textvariable=self.wait_var,
            width=10,
        ).grid(row=1, column=4, sticky="w", pady=5)

        ttk.Label(top, text="開啟 Group 等待（秒）：").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=5
        )
        ttk.Spinbox(
            top,
            from_=1.0,
            to=30.0,
            increment=0.5,
            textvariable=self.open_wait_var,
            width=10,
        ).grid(row=2, column=1, sticky="w", pady=5)

        ttk.Label(top, text="每個 Group 掃描貼文：").grid(
            row=2, column=3, sticky="w", padx=(18, 6), pady=5
        )
        ttk.Spinbox(
            top,
            from_=5,
            to=200,
            textvariable=self.posts_to_scan_var,
            width=10,
        ).grid(row=2, column=4, sticky="w", pady=5)

        ttk.Label(top, text="作者掃描最多下滑：").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=5
        )
        ttk.Spinbox(
            top,
            from_=1,
            to=100,
            textvariable=self.author_scrolls_var,
            width=10,
        ).grid(row=3, column=1, sticky="w", pady=5)

        mode_frame = ttk.LabelFrame(top, text="留言模式", padding=(10, 4))
        mode_frame.grid(row=3, column=3, columnspan=2, sticky="w", padx=(18, 0), pady=5)
        ttk.Radiobutton(
            mode_frame, text="測試模式（輸入後清空）",
            variable=self.comment_mode_var, value="測試模式"
        ).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(
            mode_frame, text="正式留言（Enter 送出）",
            variable=self.comment_mode_var, value="正式留言"
        ).pack(side="left")

        top.columnconfigure(4, weight=1)

        note = ttk.Label(
            self.root,
            text=(
                "操作：先在指定 AdsPower 環境手動開到 Facebook 的 Groups 搜尋結果頁，"
                "再按「重新搜尋 Group」。V6.4 Stable 使用留言框快照收集器：一次收集全部留言框、依頁面位置排序並用 token 逐一處理，避免每次重新抓清單時總是回到第一個舊留言框；保留同作者只留言一次及完整 LOG。"
            ),
            padding=(12, 0, 12, 10),
            wraplength=1000,
        )
        note.pack(fill="x")

        controls = ttk.Frame(self.root, padding=(12, 0, 12, 10))
        controls.pack(fill="x")

        self.import_button = ttk.Button(
            controls,
            text="匯入之前找到的 Group",
            command=self.import_previous_groups,
        )
        self.import_button.pack(side="left", padx=(0, 8))

        self.start_button = ttk.Button(
            controls,
            text="重新搜尋 Group",
            command=self.start_scan,
        )
        self.start_button.pack(side="left")

        self.stop_button = ttk.Button(
            controls,
            text="停止",
            command=self.stop_scan,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=8)

        self.export_button = ttk.Button(
            controls,
            text="重新匯出結果",
            command=self.export_again,
            state="disabled",
        )
        self.export_button.pack(side="left")

        self.open_groups_button = ttk.Button(
            controls,
            text="依序開啟 Group",
            command=self.open_all_groups,
            state="disabled",
        )
        self.open_groups_button.pack(side="left", padx=8)

        self.extract_authors_button = ttk.Button(
            controls,
            text="擷取貼文作者",
            command=self.extract_all_authors,
            state="disabled",
        )
        self.extract_authors_button.pack(side="left")

        self.comment_test_button = ttk.Button(
            controls,
            text="執行留言流程",
            command=self.test_comment_boxes,
            state="disabled",
        )
        self.comment_test_button.pack(side="left", padx=8)

        ttk.Label(
            controls,
            textvariable=self.summary_var,
            font=("Microsoft JhengHei UI", 12, "bold"),
        ).pack(side="right")

        table_frame = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        table_frame.pack(fill="both", expand=True)

        columns = ("index", "name", "posts", "members", "privacy", "url", "activity")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("index", text="序號")
        self.tree.heading("name", text="Group 名稱")
        self.tree.heading("posts", text="今日貼文")
        self.tree.heading("members", text="成員數")
        self.tree.heading("privacy", text="類型")
        self.tree.heading("url", text="Group 網址")
        self.tree.heading("activity", text="判斷文字")

        self.tree.column("index", width=60, anchor="center", stretch=False)
        self.tree.column("name", width=270, anchor="w")
        self.tree.column("posts", width=100, anchor="center", stretch=False)
        self.tree.column("members", width=110, anchor="center", stretch=False)
        self.tree.column("privacy", width=90, anchor="center", stretch=False)
        self.tree.column("url", width=330, anchor="w")
        self.tree.column("activity", width=260, anchor="w")

        vertical = ttk.Scrollbar(
            table_frame,
            orient="vertical",
            command=self.tree.yview,
        )
        horizontal = ttk.Scrollbar(
            table_frame,
            orient="horizontal",
            command=self.tree.xview,
        )
        self.tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )

        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        status_frame = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        status_frame.pack(fill="x")

        self.progress = ttk.Progressbar(status_frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 7))

        ttk.Label(
            status_frame,
            textvariable=self.status_var,
            anchor="w",
        ).pack(fill="x")

        self.tree.bind("<Double-1>", self.open_selected_url)

    def set_busy(self, busy: bool) -> None:
        self.start_button.configure(state="disabled" if busy else "normal")
        self.load_button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def log_status(self, text: str) -> None:
        self.status_var.set(text)

    def load_profiles(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.set_busy(True)
        self.log_status("正在讀取 AdsPower 環境……")

        def task() -> None:
            try:
                profiles = get_all_profiles(self.group_id_var.get())
                self.message_queue.put(("profiles", profiles))
            except Exception as exc:
                self.message_queue.put(("error", f"讀取 AdsPower 環境失敗：{exc}"))
            finally:
                self.message_queue.put(("idle", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def selected_profile(self) -> Optional[Dict[str, str]]:
        selected = self.profile_var.get()
        for item in self.profile_items:
            display = f"{item['name']}  [{item['user_id']}]"
            if display == selected:
                return item
        return None

    def import_previous_groups(self) -> None:
        selected = filedialog.askopenfilename(
            title="選擇之前的 Group 結果",
            filetypes=[
                ("Group 結果", "*.csv *.txt"),
                ("CSV", "*.csv"),
                ("TXT", "*.txt"),
                ("全部檔案", "*.*"),
            ],
        )
        if not selected:
            return

        try:
            results = import_groups_file(Path(selected))
            if not results:
                raise RuntimeError("檔案內沒有讀到有效的 Group 網址。")

            self.last_results = results
            self.show_results(results)
            self.log_status(
                f"已匯入 {len(results)} 個 Group，可直接按「擷取貼文作者」。"
            )
            append_run_log(
                f"匯入 Group 檔案：{selected}｜共 {len(results)} 個"
            )
        except Exception as exc:
            messagebox.showerror("匯入失敗", str(exc))

    def start_scan(self) -> None:
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("提醒", "請先讀取並選擇一個 AdsPower 環境。")
            return

        try:
            max_scrolls = int(self.max_scrolls_var.get())
            wait_seconds = float(self.wait_var.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("提醒", "下滑次數或等待秒數格式不正確。")
            return

        self.stop_event.clear()
        self.last_results = []
        self.clear_table()
        self.summary_var.set("符合 Group：0")
        self.set_busy(True)
        self.log_status("正在連接 AdsPower……")

        def progress_callback(
            round_index: int,
            total_found: int,
            new_count: int,
            visible_links: int,
        ) -> None:
            self.message_queue.put(
                (
                    "progress",
                    (
                        round_index,
                        total_found,
                        new_count,
                        visible_links,
                    ),
                )
            )

        def task() -> None:
            playwright: Optional[Playwright] = None
            browser: Optional[Browser] = None

            try:
                ws_url = start_profile(profile["user_id"])
                playwright = sync_playwright().start()
                browser = playwright.chromium.connect_over_cdp(
                    ws_url,
                    timeout=PAGE_TIMEOUT_MS,
                )

                if not browser.contexts:
                    raise RuntimeError("找不到 AdsPower 瀏覽器 Context。")

                context = browser.contexts[0]
                page = choose_facebook_page(context)
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                current_url = page.url or ""
                if "facebook.com" not in current_url.lower():
                    raise RuntimeError(
                        "目前分頁不是 Facebook。請先在此 AdsPower 環境開啟 Facebook Group 搜尋頁。"
                    )

                self.message_queue.put(("page", current_url))

                results = scan_groups_with_scrolling(
                    page=page,
                    max_scrolls=max_scrolls,
                    wait_seconds=wait_seconds,
                    progress_callback=progress_callback,
                    stop_event=self.stop_event,
                )

                txt_path, csv_path = export_results(results)
                self.message_queue.put(
                    ("done", (results, txt_path, csv_path))
                )

            except PlaywrightTimeoutError as exc:
                self.message_queue.put(
                    ("error", f"連接或掃描逾時：{exc}")
                )
            except Exception as exc:
                self.message_queue.put(("error", f"掃描失敗：{exc}"))
            finally:
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                try:
                    if playwright:
                        playwright.stop()
                except Exception:
                    pass
                self.message_queue.put(("idle", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def stop_scan(self) -> None:
        self.stop_event.set()
        self.log_status("正在停止；完成目前這一步後結束……")

    def clear_table(self) -> None:
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

    def show_results(self, results: List[GroupResult]) -> None:
        self.clear_table()

        for index, item in enumerate(results, start=1):
            self.tree.insert(
                "",
                "end",
                values=(
                    index,
                    item.name,
                    item.today_posts,
                    item.members if item.members is not None else "未知",
                    item.privacy,
                    item.url,
                    item.activity_text,
                ),
            )

        self.summary_var.set(f"符合 Group：{len(results)}")
        self.export_button.configure(
            state="normal" if results else "disabled"
        )
        self.open_groups_button.configure(
            state="normal" if results else "disabled"
        )
        self.extract_authors_button.configure(
            state="normal" if results else "disabled"
        )
        self.comment_test_button.configure(
            state="normal" if results else "disabled"
        )

    def export_again(self) -> None:
        if not self.last_results:
            return

        try:
            txt_path, csv_path = export_results(self.last_results)
            messagebox.showinfo(
                "匯出完成",
                f"已匯出：\n{txt_path}\n{csv_path}",
            )
        except Exception as exc:
            messagebox.showerror("匯出失敗", str(exc))

    def test_comment_boxes(self) -> None:
        if not self.last_results:
            messagebox.showwarning("提醒", "請先匯入或搜尋 Group。")
            return

        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("提醒", "請先選擇 AdsPower 環境。")
            return

        try:
            comments = load_comment_texts()
            max_per_group = int(self.posts_to_scan_var.get())
            max_scrolls = int(self.author_scrolls_var.get())
            mode = self.comment_mode_var.get()
        except Exception as exc:
            messagebox.showerror("設定錯誤", str(exc))
            return

        if mode == "正式留言":
            confirmed = messagebox.askyesno(
                "正式留言確認",
                "正式留言模式會真的按 Enter 送出留言。\n\n確定要繼續嗎？",
            )
            if not confirmed:
                return

        self.stop_event.clear()
        reset_run_log()
        reset_comment_result_files(mode)
        reset_failure_diagnostic()
        self.last_comment_tests = []
        self.comment_stats = {
            "scanned": 0, "success": 0, "failed": 0,
            "admin": 0, "skipped": 0, "no_box": 0,
        }
        self.set_busy(True)
        self.comment_test_button.configure(state="disabled")
        self.log_status(
            f"已讀取 文一.txt 共 {len(comments)} 條，開始 {mode}……"
        )

        groups = list(self.last_results)

        def task() -> None:
            playwright = None
            browser = None
            try:
                ws_url = start_profile(profile["user_id"])
                playwright = sync_playwright().start()
                browser = playwright.chromium.connect_over_cdp(
                    ws_url, timeout=PAGE_TIMEOUT_MS
                )
                if not browser.contexts:
                    raise RuntimeError("找不到 AdsPower 瀏覽器 Context。")
                context = browser.contexts[0]
                page = choose_facebook_page(context)
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                rows: List[CommentTestResult] = []
                tested_posts: Set[str] = set()
                admin_profiles: Set[str] = set()
                seen_authors: Set[str] = set()

                for group_index, group in enumerate(groups, 1):
                    if self.stop_event.is_set():
                        break

                    self.message_queue.put(
                        ("comment_group", (group_index, len(groups), group.name))
                    )
                    try:
                        page.goto(
                            group.url,
                            wait_until="domcontentloaded",
                            timeout=PAGE_TIMEOUT_MS,
                        )
                        page.wait_for_timeout(4000)
                    except Exception as exc:
                        append_run_log(f"開啟 Group 失敗：{group.name}｜{exc}")
                        continue

                    group_count = 0
                    group_admin_count = 0
                    no_new = 0
                    failed_scrolls = 0

                    for scroll_index in range(max_scrolls + 1):
                        if self.stop_event.is_set():
                            break

                        added = 0
                        snapshots = collect_comment_box_snapshots(page)
                        box_count = len(snapshots)

                        append_run_log(
                            f"V6.4 掃描：{group.name}｜"
                            f"目前留言框快照 {box_count} 個｜"
                            f"由上到下完整處理"
                        )

                        for box_index, snapshot in enumerate(snapshots):
                            if self.stop_event.is_set():
                                break
                            if group_count >= max_per_group:
                                break

                            try:
                                box = get_comment_box_by_token(
                                    page, snapshot.token
                                )
                                if box is None:
                                    append_run_log(
                                        f"V6.4 留言框已被 Facebook DOM 更新移除｜"
                                        f"群組：{group.name}｜"
                                        f"快照序號：{box_index + 1}/{box_count}｜"
                                        f"原始Y：{int(snapshot.document_y)}"
                                    )
                                    continue

                                try:
                                    if not box.is_visible(timeout=500):
                                        box.scroll_into_view_if_needed(
                                            timeout=1800
                                        )
                                        page.wait_for_timeout(350)
                                except Exception:
                                    pass

                                author, article = extract_author_from_comment_box(
                                    page, box, group
                                )

                                if article is None:
                                    self.comment_stats["skipped"] += 1
                                    append_run_log(
                                        f"V6.4 掃描結果｜群組：{group.name}｜"
                                        f"作者：未取得｜貼文ID：未取得｜"
                                        f"Admin：未知｜結果：略過（找不到貼文容器）"
                                    )
                                    continue

                                if author is None:
                                    self.comment_stats["skipped"] += 1
                                    append_run_log(
                                        f"V6.4 掃描結果｜群組：{group.name}｜"
                                        f"作者：未取得｜貼文ID：{get_post_url(article) or '未取得'}｜"
                                        f"Admin：未知｜結果：略過（找不到作者）"
                                    )
                                    continue

                                append_run_log(
                                    f"V6.4 已抓到留言框快照｜"
                                    f"群組：{group.name}｜"
                                    f"快照：{box_index + 1}/{box_count}｜"
                                    f"Y：{int(snapshot.document_y)}｜"
                                    f"作者：{author.name}｜"
                                    f"提示：{snapshot.aria_label or '無'}"
                                )

                                author_key = author.url or author.name.casefold()
                                post_key, post_display = build_post_key(
                                    article, author, group.url
                                )

                                if post_key in tested_posts:
                                    self.comment_stats["skipped"] += 1
                                    append_run_log(
                                        f"V6.4 掃描結果｜群組：{group.name}｜"
                                        f"作者：{author.name}｜"
                                        f"貼文ID：{post_display or '未取得'}｜"
                                        f"Admin：未知｜結果：略過（同一貼文）"
                                    )
                                    continue

                                tested_posts.add(post_key)

                                is_admin = article_has_admin_badge(article)

                                if is_admin:
                                    self.comment_stats["admin"] += 1
                                    group_admin_count += 1
                                    append_run_log(
                                        f"V6.4 掃描結果｜群組：{group.name}｜"
                                        f"作者：{author.name}｜"
                                        f"貼文ID：{post_display or '未取得'}｜"
                                        f"Admin：是｜結果：略過（Admin）｜"
                                        f"本群 Admin {group_admin_count}/4"
                                    )

                                    if group_admin_count > 3:
                                        append_run_log(
                                            f"V6.4 本群已發現 Admin 超過 3 個，立即切換下一群："
                                            f"{group.name}"
                                        )
                                        break
                                    continue

                                if author_key and author_key in seen_authors:
                                    self.comment_stats["skipped"] += 1
                                    append_run_log(
                                        f"V6.4 掃描結果｜群組：{group.name}｜"
                                        f"作者：{author.name}｜"
                                        f"貼文ID：{post_display or '未取得'}｜"
                                        f"Admin：否｜結果：略過（同作者已留言過）"
                                    )
                                    continue

                                append_run_log(
                                    f"V6.4 掃描結果｜群組：{group.name}｜"
                                    f"作者：{author.name}｜"
                                    f"貼文ID：{post_display or '未取得'}｜"
                                    f"Admin：否｜結果：開始留言"
                                )

                                result = test_one_comment_box(
                                    page,
                                    box,
                                    group,
                                    random.choice(comments),
                                    mode,
                                )

                                if result.input_success and author_key:
                                    seen_authors.add(author_key)

                                rows.append(result)

                                if result.input_success:
                                    group_count += 1
                                    added += 1
                                self.comment_stats["scanned"] += 1

                                if result.input_success:
                                    self.comment_stats["success"] += 1
                                else:
                                    self.comment_stats["failed"] += 1

                                if not result.comment_box_found:
                                    self.comment_stats["no_box"] += 1

                                append_comment_result(result, len(rows), mode)
                                append_run_log(
                                    f"V6.4 留言結果｜群組：{group.name}｜"
                                    f"作者：{result.author_name}｜"
                                    f"貼文ID：{post_display or '未取得'}｜"
                                    f"結果：{result.status}｜"
                                    f"本群進度：{group_count}/{max_per_group}"
                                )
                                self.message_queue.put(
                                    (
                                        "comment_progress",
                                        (
                                            group_index, len(groups), group.name,
                                            group_count, len(rows),
                                            result.status,
                                            dict(self.comment_stats),
                                        ),
                                    )
                                )

                            except Exception as exc:
                                append_run_log(
                                    f"V6.4 單一留言框處理失敗：{group.name}｜{exc}"
                                )
                                continue

                        if group_admin_count > 3:
                            break

                        if group_count >= max_per_group:
                            append_run_log(
                                f"V6.4 已完成本群設定留言數："
                                f"{group.name}｜{group_count}/{max_per_group}"
                            )
                            break

                        no_new = no_new + 1 if added == 0 else 0
                        if no_new >= AUTHOR_NO_GROWTH_LIMIT:
                            append_run_log(
                                f"V6.4 連續無新增達上限，切換下一群：{group.name}"
                            )
                            break

                        moved = verified_small_scroll(page, group.name)
                        if moved:
                            failed_scrolls = 0
                        else:
                            failed_scrolls += 1

                        if failed_scrolls >= 2:
                            append_run_log(
                                f"V6.4 連續 2 次無法確實滑動，切換下一群："
                                f"{group.name}"
                            )
                            break

                        page.wait_for_timeout(900)

                self.message_queue.put(
                    ("comment_done", (rows, COMMENT_TEST_TXT, COMMENT_TEST_CSV, mode))
                )

            except Exception as exc:
                self.message_queue.put(("error", f"留言流程失敗：{exc}"))
            finally:
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                try:
                    if playwright:
                        playwright.stop()
                except Exception:
                    pass
                self.message_queue.put(("idle", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def extract_all_authors(self) -> None:
        if not self.last_results:
            return

        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("提醒", "請先選擇 AdsPower 環境。")
            return

        try:
            max_posts = int(self.posts_to_scan_var.get())
            max_scrolls = int(self.author_scrolls_var.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("提醒", "貼文數量或下滑次數格式不正確。")
            return

        self.stop_event.clear()
        reset_run_log()
        self.last_authors = []
        self.set_busy(True)
        self.open_groups_button.configure(state="disabled")
        self.extract_authors_button.configure(state="disabled")
        self.log_status("正在連接 AdsPower，準備擷取貼文作者……")

        groups = list(self.last_results)

        def task() -> None:
            playwright: Optional[Playwright] = None
            browser: Optional[Browser] = None

            try:
                ws_url = start_profile(profile["user_id"])
                playwright = sync_playwright().start()
                browser = playwright.chromium.connect_over_cdp(
                    ws_url,
                    timeout=PAGE_TIMEOUT_MS,
                )

                if not browser.contexts:
                    raise RuntimeError("找不到 AdsPower 瀏覽器 Context。")

                context = browser.contexts[0]
                page = choose_facebook_page(context)
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                all_authors: Dict[str, AuthorResult] = {}

                for group_index, group in enumerate(groups, start=1):
                    if self.stop_event.is_set():
                        break

                    self.message_queue.put(
                        (
                            "author_group",
                            (group_index, len(groups), group.name),
                        )
                    )

                    def progress_callback(
                        scroll_index: int,
                        group_author_count: int,
                        new_count: int,
                        detected_posts: int,
                        admin_posts: int,
                        admin_only: bool,
                    ) -> None:
                        self.message_queue.put(
                            (
                                "author_progress",
                                (
                                    group_index,
                                    len(groups),
                                    group.name,
                                    scroll_index,
                                    group_author_count,
                                    new_count,
                                    detected_posts,
                                    admin_posts,
                                    admin_only,
                                ),
                            )
                        )

                    try:
                        group_authors, admin_only = scan_group_authors(
                            page=page,
                            group=group,
                            max_posts=max_posts,
                            max_scrolls=max_scrolls,
                            stop_event=self.stop_event,
                            progress_callback=progress_callback,
                        )

                        if admin_only:
                            self.message_queue.put(
                                (
                                    "admin_skip",
                                    f"偵測到前幾篇都是 Admin，已跳過：{group.name}",
                                )
                            )
                            continue

                        for author in group_authors:
                            all_authors.setdefault(
                                author.url.casefold(),
                                author,
                            )

                    except Exception as exc:
                        self.message_queue.put(
                            (
                                "open_warning",
                                f"作者擷取失敗：{group.name}｜{exc}",
                            )
                        )

                authors = sorted(
                    all_authors.values(),
                    key=lambda item: (
                        item.group_name.casefold(),
                        item.name.casefold(),
                    ),
                )
                txt_path, csv_path = export_author_results(authors)

                self.message_queue.put(
                    (
                        "authors_done",
                        (
                            authors,
                            txt_path,
                            csv_path,
                            self.stop_event.is_set(),
                        ),
                    )
                )

            except Exception as exc:
                self.message_queue.put(
                    ("error", f"擷取貼文作者失敗：{exc}")
                )
            finally:
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                try:
                    if playwright:
                        playwright.stop()
                except Exception:
                    pass
                self.message_queue.put(("idle", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def open_all_groups(self) -> None:
        if not self.last_results:
            return

        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("提醒", "請先選擇 AdsPower 環境。")
            return

        try:
            wait_seconds = float(self.open_wait_var.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("提醒", "開啟等待秒數格式不正確。")
            return

        self.stop_event.clear()
        self.set_busy(True)
        self.open_groups_button.configure(state="disabled")
        self.log_status("正在連接 AdsPower，準備依序開啟 Group……")

        results = list(self.last_results)

        def task() -> None:
            playwright: Optional[Playwright] = None
            browser: Optional[Browser] = None

            try:
                ws_url = start_profile(profile["user_id"])
                playwright = sync_playwright().start()
                browser = playwright.chromium.connect_over_cdp(
                    ws_url,
                    timeout=PAGE_TIMEOUT_MS,
                )

                if not browser.contexts:
                    raise RuntimeError("找不到 AdsPower 瀏覽器 Context。")

                context = browser.contexts[0]
                page = choose_facebook_page(context)
                page.set_default_timeout(PAGE_TIMEOUT_MS)

                success_count = 0

                for index, item in enumerate(results, start=1):
                    if self.stop_event.is_set():
                        break

                    self.message_queue.put(
                        (
                            "open_progress",
                            (index, len(results), item.name, item.url),
                        )
                    )

                    try:
                        page.goto(
                            item.url,
                            wait_until="domcontentloaded",
                            timeout=PAGE_TIMEOUT_MS,
                        )
                        page.wait_for_timeout(int(wait_seconds * 1000))
                        success_count += 1
                    except Exception as exc:
                        self.message_queue.put(
                            (
                                "open_warning",
                                f"開啟失敗：{item.name}｜{exc}",
                            )
                        )

                self.message_queue.put(
                    (
                        "open_done",
                        (success_count, len(results), self.stop_event.is_set()),
                    )
                )

            except Exception as exc:
                self.message_queue.put(
                    ("error", f"依序開啟 Group 失敗：{exc}")
                )
            finally:
                try:
                    if browser:
                        browser.close()
                except Exception:
                    pass
                try:
                    if playwright:
                        playwright.stop()
                except Exception:
                    pass
                self.message_queue.put(("idle", None))

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def open_selected_url(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return

        values = self.tree.item(selection[0], "values")
        if len(values) < 6:
            return

        url = str(values[5])
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    def _process_messages(self) -> None:
        try:
            while True:
                kind, payload = self.message_queue.get_nowait()

                if kind == "profiles":
                    profiles = payload
                    assert isinstance(profiles, list)
                    self.profile_items = profiles
                    display_values = [
                        f"{item['name']}  [{item['user_id']}]"
                        for item in profiles
                    ]
                    self.profile_combo["values"] = display_values
                    if display_values:
                        self.profile_combo.current(0)
                        self.log_status(
                            f"讀取完成，共 {len(display_values)} 個環境。"
                        )
                    else:
                        self.log_status("沒有讀到 AdsPower 環境。")

                elif kind == "page":
                    self.log_status(f"已接管目前 Facebook 分頁：{payload}")

                elif kind == "progress":
                    round_index, total_found, new_count, visible_links = payload
                    self.log_status(
                        f"下滑第 {round_index} 次｜畫面 Group 連結 {visible_links}｜"
                        f"本次新增 {new_count}｜目前符合 {total_found}"
                    )
                    self.summary_var.set(f"符合 Group：{total_found}")

                elif kind == "comment_group":
                    index, total, name = payload
                    self.log_status(
                        f"留言框測試 Group {index}/{total}：{name}"
                    )

                elif kind == "comment_progress":
                    (
                        group_index, group_total, group_name,
                        group_count, total_count, status, stats,
                    ) = payload
                    self.log_status(
                        f"Group {group_index}/{group_total}｜本 Group {group_count}｜"
                        f"總處理 {total_count}｜成功 {stats['success']}｜"
                        f"失敗 {stats['failed']}｜Admin {stats['admin']}｜"
                        f"無留言框 {stats['no_box']}｜{status}｜{group_name}"
                    )

                elif kind == "comment_done":
                    rows, txt_path, csv_path, mode = payload
                    self.last_comment_tests = list(rows)
                    success = sum(1 for row in rows if row.input_success)
                    submitted = sum(1 for row in rows if row.submitted)
                    self.log_status(
                        f"{mode}完成：{success}/{len(rows)} 輸入成功，送出 {submitted}。"
                    )
                    messagebox.showinfo(
                        "留言流程完成",
                        (
                            f"模式：{mode}\n"
                            f"處理數量：{len(rows)}\n"
                            f"輸入成功：{success}\n"
                            f"實際送出：{submitted}\n"
                            f"Admin 跳過：{self.comment_stats['admin']}\n"
                            f"找不到留言框：{self.comment_stats['no_box']}\n\n"
                            f"TXT：{txt_path}\nCSV：{csv_path}"
                        ),
                    )

                elif kind == "author_group":
                    index, total, name = payload
                    self.log_status(
                        f"正在擷取第 {index}/{total} 個 Group：{name}"
                    )

                elif kind == "author_progress":
                    (
                        group_index,
                        group_total,
                        group_name,
                        scroll_index,
                        group_author_count,
                        new_count,
                        detected_posts,
                        admin_posts,
                        admin_only,
                    ) = payload
                    suffix = "｜判定 Admin-only，準備跳過" if admin_only else ""
                    self.log_status(
                        f"Group {group_index}/{group_total}｜下滑 {scroll_index}｜"
                        f"辨識貼文 {detected_posts}｜Admin {admin_posts}｜"
                        f"作者 {group_author_count}｜新增 {new_count}{suffix}｜"
                        f"{group_name}"
                    )

                elif kind == "admin_skip":
                    self.log_status(str(payload))

                elif kind == "authors_done":
                    authors, txt_path, csv_path, stopped = payload
                    self.last_authors = list(authors)
                    self.log_status(
                        f"作者擷取完成，共 {len(self.last_authors)} 位不同作者。"
                    )
                    extra_debug = ""
                    if not self.last_authors:
                        extra_debug = (
                            f"\n\nDebug HTML：{AUTHOR_DEBUG_HTML}"
                            f"\nDebug 截圖：{AUTHOR_DEBUG_PNG}"
                        )

                    messagebox.showinfo(
                        "作者擷取完成",
                        (
                            f"不同作者：{len(self.last_authors)} 位\n"
                            f"{'使用者已停止，已匯出目前結果' if stopped else '全部 Group 已完成'}\n\n"
                            f"TXT：{txt_path}\n"
                            f"CSV：{csv_path}"
                            f"{extra_debug}"
                        ),
                    )

                elif kind == "open_progress":
                    index, total, name, url = payload
                    self.log_status(
                        f"正在開啟第 {index}/{total} 個 Group：{name}"
                    )

                elif kind == "open_warning":
                    self.log_status(str(payload))

                elif kind == "open_done":
                    success_count, total, stopped = payload
                    if stopped:
                        self.log_status(
                            f"已停止，成功開啟 {success_count}/{total} 個 Group。"
                        )
                    else:
                        self.log_status(
                            f"依序開啟完成，成功 {success_count}/{total}。"
                        )
                    messagebox.showinfo(
                        "開啟完成",
                        f"成功開啟：{success_count}/{total}\n"
                        f"{'使用者已停止' if stopped else '全部流程完成'}",
                    )

                elif kind == "done":
                    results, txt_path, csv_path = payload
                    self.last_results = list(results)
                    self.show_results(self.last_results)
                    self.log_status(
                        f"掃描完成，符合 {len(self.last_results)} 個 Group。"
                    )
                    messagebox.showinfo(
                        "掃描完成",
                        (
                            f"符合今日貼文 ≥ {MIN_TODAY_POSTS}："
                            f"{len(self.last_results)} 個 Group\n\n"
                            f"TXT：{txt_path}\n"
                            f"CSV：{csv_path}"
                        ),
                    )

                elif kind == "error":
                    self.log_status(str(payload))
                    messagebox.showerror("錯誤", str(payload))

                elif kind == "idle":
                    self.set_busy(False)
                    self.open_groups_button.configure(
                        state="normal" if self.last_results else "disabled"
                    )
                    self.extract_authors_button.configure(
                        state="normal" if self.last_results else "disabled"
                    )
                    self.comment_test_button.configure(
                        state="normal" if self.last_results else "disabled"
                    )

        except queue.Empty:
            pass

        self.root.after(100, self._process_messages)


def main() -> None:
    root = tk.Tk()
    app = GroupScannerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
