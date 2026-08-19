"""Facebook Reels 發布任務：依環境尾碼配對影片，並隨機選取多行描述。"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from 日誌 import get_logger
from 多行文字 import random_text_block

_log = get_logger(__name__)

_BUTTON_LABEL_GROUPS = (
    ("close chat", "關閉聊天", "fermer la discussion", "isara ang chat", "ปิดแชท", "إغلاق الدردشة"),
    ("continue", "繼續", "繼續操作", "continuer", "magpatuloy", "ดำเนินการต่อ", "متابعة"),
    (
        "public", "公開",
        "pampubliko", "naka-public",
        "สาธารณะ",
        "عام",
    ),
    ("friends", "好友", "amis", "mga kaibigan", "เพื่อน", "الأصدقاء"),
    ("only me", "只限本人", "moi uniquement", "ako lang", "เฉพาะฉัน", "أنا فقط"),
    ("audience", "分享對象", "sélecteur d’audience", "tagapakinig", "กลุ่มเป้าหมาย", "الجمهور"),
    ("save", "儲存", "保存", "enregistrer", "i-save", "บันทึก", "حفظ"),
    ("done", "完成", "terminé", "tapos", "tapos na", "เสร็จสิ้น", "تم"),
    ("next", "下一步", "suivant", "susunod", "ถัดไป", "التالي"),
    ("post", "發布", "publier", "i-post", "โพสต์", "نشر"),
    ("upload", "上傳", "importer", "mag-upload", "อัปโหลด", "تحميل"),
    ("create reel", "create a reel", "建立連續短片", "建立 reel",
     "créer un reel", "gumawa ng reel", "สร้างรีล", "إنشاء ريل", "إنشاء مقطع ريلز"),
    ("edit reel", "編輯連續短片", "編輯 reel",
     "modifier le reel", "i-edit ang reel", "แก้ไขรีล", "تعديل ريل", "تعديل مقطع ريلز"),
    ("reel settings", "reels settings", "reel 設定",
     "paramètres du reel", "mga setting ng reel", "การตั้งค่ารีล", "إعدادات ريل", "إعدادات مقطع ريلز"),
    ("create post", "建立貼文", "créer une publication",
     "gumawa ng post", "สร้างโพสต์", "إنشاء منشور"),
    ("add video", "新增影片", "ajouter une vidéo",
     "magdagdag ng video", "เพิ่มวิดีโอ", "إضافة فيديو"),
    ("reel settings", "إعدادات مقاطع ريلز"),
    ("public", "العامة"),
)


def _expanded_button_labels(labels: tuple[str, ...]) -> set[str]:
    wanted = {label.casefold() for label in labels}
    for group in _BUTTON_LABEL_GROUPS:
        folded = {label.casefold() for label in group}
        if wanted & folded:
            wanted.update(folded)
    return wanted


VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".webm")
RECORD_FILE = Path(__file__).with_name("reels_history.json")
DIAGNOSTIC_DIR = Path(__file__).with_name("diagnostics") / "reels"
PROFILE_URL_CACHE_FILE = Path(__file__).with_name("facebook_profile_urls.json")


@dataclass(frozen=True)
class ReelsMaterial:
    number: int
    raw_number: str
    video: Path
    description: str


def profile_number(name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)\s*$", name.strip())
    if not match:
        raise ValueError("環境名稱尾端沒有數字")
    return int(match.group(1)), match.group(1)


def find_video(video_dir: str, profile_name: str) -> tuple[int, str, Path]:
    number, raw = profile_number(profile_name)
    folder = Path(video_dir)
    if not folder.is_dir():
        raise FileNotFoundError(f"Reels 影片資料夾不存在：{folder}")
    stems = list(dict.fromkeys((raw, str(number), f"{number:02d}", f"{number:03d}")))
    for stem in stems:
        for ext in VIDEO_EXTENSIONS:
            for candidate in (folder / f"{stem}{ext}", folder / f"{stem}{ext.upper()}"):
                if candidate.is_file():
                    return number, raw, candidate.resolve()
    raise FileNotFoundError(f"找不到編號 {raw} 的影片（搜尋路徑：{folder}）")


def read_description(text_file: str, line_number: int) -> str:
    """以 RC19 規則隨機選取一篇；保留參數以相容既有呼叫端。"""
    if not str(text_file or "").strip():
        raise FileNotFoundError("Reels 描述文字檔尚未設定")
    selected, total = random_text_block(text_file)
    _log.info(
        "[ReelsText] 已從描述 TXT 隨機選取 1 篇（共 %d 篇、%d 行、%d 字元）。",
        total,
        selected.count("\n") + 1,
        len(selected),
    )
    return selected


def resolve_material(video_dir: str, text_file: str, profile_name: str) -> ReelsMaterial:
    number, raw, video = find_video(video_dir, profile_name)
    return ReelsMaterial(number, raw, video, read_description(text_file, number))


def _history() -> list[dict]:
    try:
        data = json.loads(RECORD_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _fingerprint(profile_id: str, material: ReelsMaterial) -> dict:
    stat = material.video.stat()
    return {"profile_id": profile_id, "video_path": str(material.video),
            "video_size": stat.st_size, "video_mtime_ns": stat.st_mtime_ns}


def already_posted(profile_id: str, material: ReelsMaterial) -> bool:
    # 允許同一環境重複發布同一影片，不因舊紀錄跳過。
    return False


def record_success(profile_id: str, profile_name: str, material: ReelsMaterial) -> None:
    # 依需求不寫入 reels_history.json，避免影響同日第二次發布。
    return None


class ReelsPublisher:
    def __init__(self, driver, profile_id: str, profile_name: str, video_dir: str,
                 text_file: str, stop_event: threading.Event | None = None,
                 timeout: int = 600, dry_run: bool = False) -> None:
        self.driver, self.profile_id, self.profile_name = driver, profile_id, profile_name
        self.video_dir, self.text_file = video_dir, text_file
        self.stop_event, self.timeout = stop_event or threading.Event(), timeout
        self.dry_run = dry_run
        self.stage = "初始化"
        self.last_diagnostic: Path | None = None

    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        _log.info("[%s] Reels 階段：%s", self.profile_name, stage)

    def _dismiss_notification_prompt(self) -> None:
        """允許 Facebook 通知並收起頁面內的通知請求遮罩。"""
        for origin in ("https://www.facebook.com", "https://facebook.com"):
            try:
                self.driver.execute_cdp_cmd("Browser.setPermission", {
                    "permission": {"name": "notifications"},
                    "setting": "granted",
                    "origin": origin,
                })
            except Exception:
                pass
        try:
            self.driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        except Exception:
            try:
                self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            except Exception:
                pass
        self._wait(0.25)
        _log.info("[%s] 已處理 Facebook 通知權限彈窗。", self.profile_name)

    def _dismiss_floating_chats(self) -> int:
        """關閉會遮住 Reels 操作區的 Messenger 浮動聊天視窗。"""
        closed = 0
        labels = (
            "Close chat", "關閉聊天", "Cerrar chat", "Fermer la discussion",
            "Schließen", "Isara ang chat", "ปิดแชท", "إغلاق الدردشة",
        )
        wanted = {label.casefold() for label in labels}
        # 每次點擊後 DOM 會重建，因此逐輪重新尋找，避免沿用 stale element。
        for _ in range(12):
            target = None
            for element in self.driver.find_elements(
                By.CSS_SELECTOR, "[aria-label][role='button'], div[aria-label]"
            ):
                try:
                    label = " ".join(
                        (element.get_attribute("aria-label") or "").split()
                    ).casefold()
                    if label in wanted and self._visible(element) and self._enabled(element):
                        target = element
                        break
                except StaleElementReferenceException:
                    continue
            if target is None:
                break
            try:
                self.driver.execute_script("arguments[0].click();", target)
                closed += 1
                self._wait(0.25)
            except StaleElementReferenceException:
                continue
        if closed:
            _log.info(
                "[%s] 已關閉 %d 個 Messenger 浮動聊天視窗，避免遮擋 Reels。",
                self.profile_name,
                closed,
            )
        return closed

    def save_diagnostic(self, reason: str) -> Path | None:
        """保存截圖、完整 DOM、網址與錯誤資訊，並打包成 ZIP。"""
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", self.profile_name).strip(" ._") or "profile"
            folder = DIAGNOSTIC_DIR / f"{timestamp}_{safe_name}_{self.profile_id}"
            folder.mkdir(parents=True, exist_ok=True)
            screenshot = folder / "screenshot.png"
            dom_file = folder / "page_source.html"
            info_file = folder / "diagnostic.txt"
            try:
                self.driver.save_screenshot(str(screenshot))
            except Exception as exc:
                (folder / "screenshot_error.txt").write_text(str(exc), encoding="utf-8")
            try:
                dom_file.write_text(self.driver.page_source or "", encoding="utf-8")
            except Exception as exc:
                (folder / "dom_error.txt").write_text(str(exc), encoding="utf-8")
            try:
                current_url = self.driver.current_url
                title = self.driver.title
            except Exception:
                current_url, title = "", ""
            info_file.write_text(
                "\n".join((
                    f"時間：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                    f"環境：{self.profile_name}",
                    f"Profile ID：{self.profile_id}",
                    f"Reels 階段：{self.stage}",
                    f"原因：{reason}",
                    f"頁面標題：{title}",
                    f"網址：{current_url}",
                )),
                encoding="utf-8",
            )
            archive = Path(shutil.make_archive(str(folder), "zip", root_dir=folder))
            self.last_diagnostic = archive
            _log.error("[%s] Reels 異常診斷包已保存：%s", self.profile_name, archive)
            return archive
        except Exception as exc:
            _log.warning("[%s] Reels 診斷資料保存失敗：%s", self.profile_name, exc)
            return None

    def _stop(self) -> None:
        if self.stop_event.is_set():
            raise InterruptedError("Reels 任務已停止")

    def _wait(self, seconds: float) -> None:
        if self.stop_event.wait(seconds):
            raise InterruptedError("Reels 任務已停止")

    @staticmethod
    def _visible(element) -> bool:
        try:
            return element.is_displayed()
        except StaleElementReferenceException:
            return False

    @staticmethod
    def _enabled(element) -> bool:
        try:
            return (element.is_enabled() and element.get_attribute("disabled") is None and
                    str(element.get_attribute("aria-disabled")).lower() != "true")
        except StaleElementReferenceException:
            return False

    def _switch_to_facebook_tab(self) -> None:
        """Attach to Facebook even when AdsPower opens its info tab first."""
        original = self.driver.current_window_handle
        for handle in reversed(self.driver.window_handles):
            try:
                self.driver.switch_to.window(handle)
                url = (self.driver.current_url or "").casefold()
                if "facebook.com" in url:
                    _log.info(
                        "[%s] 已自動切換至 Facebook 分頁。",
                        self.profile_name,
                    )
                    return
            except Exception:
                continue
        self.driver.switch_to.window(original)
        raise RuntimeError("AdsPower 目前沒有可用的 Facebook 分頁")

    def _ensure_personal_profile(self) -> str:
        """不論接管時位於哪個 Facebook 頁面，都先回到本人個人主頁。"""
        profile_url = getattr(self.driver, "_facebook_personal_profile_url", "")
        if not profile_url and PROFILE_URL_CACHE_FILE.exists():
            try:
                cached = json.loads(
                    PROFILE_URL_CACHE_FILE.read_text(encoding="utf-8")
                )
                profile_url = str(cached.get(self.profile_id, "")).strip()
                if profile_url:
                    self.driver._facebook_personal_profile_url = profile_url
                    _log.info(
                        "[%s] 已讀取個人主頁網址快取：%s",
                        self.profile_name,
                        profile_url,
                    )
            except Exception:
                profile_url = ""
        current_url = self.driver.current_url or ""
        if profile_url and current_url.startswith(profile_url):
            _log.info(
                "[%s] 已在本人個人主頁，不重複導向。",
                self.profile_name,
            )
            return profile_url

        if not profile_url:
            self.driver.get("https://www.facebook.com/")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self._stop()
                profile_url = self.driver.execute_script(
                    """
                    const links = [...document.querySelectorAll(
                        'a[href*="profile.php?id="]'
                    )];
                    const urls = [...new Set(links.filter(a =>
                        ['timeline', 'journal', 'ไทม์ไลน์', 'يوميات'].some(word =>
                            ((a.getAttribute('aria-label') || a.innerText || '') + '')
                                .toLowerCase().includes(word)
                        )
                    ).map(a => (a.href || '')
                        .split('&__cft__')[0].split('&__tn__')[0]))];
                    return urls.length === 1 ? urls[0] : '';
                    """
                )
                if profile_url:
                    self.driver._facebook_personal_profile_url = profile_url
                    try:
                        cached = {}
                        if PROFILE_URL_CACHE_FILE.exists():
                            cached = json.loads(
                                PROFILE_URL_CACHE_FILE.read_text(encoding="utf-8")
                            )
                        cached[self.profile_id] = profile_url
                        PROFILE_URL_CACHE_FILE.write_text(
                            json.dumps(cached, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception as exc:
                        _log.warning(
                            "[%s] 個人主頁網址快取寫入失敗：%s",
                            self.profile_name,
                            exc,
                        )
                    _log.info(
                        "[%s] 已從 Facebook 首頁讀取本人個人主頁網址：%s",
                        self.profile_name,
                        profile_url,
                    )
                    break
                self._wait(0.2)
        if not profile_url:
            raise RuntimeError("Facebook 首頁找不到唯一的本人 Timeline 連結")

        self.driver.get(profile_url)
        verify_end = time.monotonic() + 20
        while time.monotonic() < verify_end:
            self._stop()
            current_url = self.driver.current_url or ""
            if current_url.startswith(profile_url):
                try:
                    body = self.driver.find_element(By.TAG_NAME, "body").text.casefold()
                except Exception:
                    body = ""
                if "temporarily blocked" in body:
                    raise RuntimeError("Facebook 顯示 Temporarily Blocked")
                _log.info(
                    "[%s] 已使用固定網址進入本人個人主頁：%s",
                    self.profile_name,
                    profile_url,
                )
                return profile_url
            self._wait(0.2)
        raise RuntimeError("已取得本人個人主頁網址，但無法進入 Profile")

    def _buttons(self, labels: tuple[str, ...]):
        wanted = tuple(_expanded_button_labels(labels))
        try:
            atomic_buttons = self.driver.execute_script(
                """
                const wanted = new Set(arguments[0]);
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                return Array.from(document.querySelectorAll(
                    'button, [role="button"], a'
                )).filter((element) => {
                    if (!visible(element)) return false;
                    const text = (
                        element.innerText ||
                        element.getAttribute('aria-label') ||
                        ''
                    ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                    return wanted.has(text) || Array.from(wanted).some(
                        (label) => text.startsWith(label + ' ')
                    );
                });
                """,
                list(wanted),
            ) or []
            if atomic_buttons:
                return atomic_buttons
        except (StaleElementReferenceException, WebDriverException):
            pass

        # 舊版 Chromium／React 換頁時的後備路徑。
        result = []
        for element in self.driver.find_elements(By.XPATH, "//button | //*[@role='button'] | //a"):
            try:
                text = " ".join((element.text or element.get_attribute("aria-label") or "").split()).casefold()
                if self._visible(element) and any(text == x or text.startswith(x + " ") for x in wanted):
                    result.append(element)
            except StaleElementReferenceException:
                pass
        return result

    def _click(self, labels: tuple[str, ...], timeout: int = 30) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._stop()
            for element in self._buttons(labels):
                if not self._enabled(element):
                    continue
                try:
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                    element.click()
                    return True
                except (ElementClickInterceptedException, StaleElementReferenceException):
                    pass
            self._wait(0.35)
        return False

    def _is_profile_reels_page(self) -> bool:
        """只接受個人檔案的 Reels 分頁，不接受全站 Reels 或單支影片頁。"""
        try:
            url = (self.driver.current_url or "").lower()
        except Exception:
            return False
        return (
            "facebook.com" in url
            and (
                "sk=reels_tab" in url
                or re.search(r"facebook\.com/[^/?#]+/reels(?:[/?#]|$)", url) is not None
            )
            and "/reel/" not in url
            and "/reel?" not in url
        )

    def _open_create_reel_directly(self, timeout: float = 25.0):
        """直接開啟 Facebook Create reel，不經個人頁 Reels 分頁。

        ``/reels/create/`` 會由 Facebook 導向目前可用的 Reels 頁面並自動
        疊上 Create reel 視窗。使用 CDP 導航可避免慢速頁面讓
        ``driver.get`` 長時間卡在 renderer；若本來已在 Reels 建立流程，
        則直接沿用目前視窗，避免開出第二層。
        """
        try:
            current = self._require_reel_dialog()
        except (RuntimeError, StaleElementReferenceException, WebDriverException):
            current = None
        if current is not None:
            _log.info(
                "[%s] 已在 Create reel／Edit reel／Reel settings，直接接續目前流程。",
                self.profile_name,
            )
            return current

        direct_url = "https://www.facebook.com/reels/create/"
        _log.info(
            "[%s] 直接開啟 Facebook Create reel：%s",
            self.profile_name,
            direct_url,
        )
        try:
            self.driver.execute_cdp_cmd("Page.navigate", {"url": direct_url})
        except WebDriverException:
            # 舊版 Chromium 若不接受 Page.navigate，仍以非阻塞的頁面指令導向。
            self.driver.execute_script(
                "window.location.assign(arguments[0]);",
                direct_url,
            )

        end = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < end:
            self._stop()
            try:
                dialog = self._require_reel_dialog()
                _log.info(
                    "[%s] 已直接開啟 Create reel 視窗：%s",
                    self.profile_name,
                    self.driver.current_url,
                )
                return dialog
            except (RuntimeError, StaleElementReferenceException, WebDriverException) as exc:
                last_error = exc
            self._wait(0.25)
        raise RuntimeError(f"直接開啟 Create reel 視窗逾時：{last_error}")

    def _click_profile_reels_tab(self, timeout: int = 30) -> bool:
        """點擊個人頁導覽區的 Reels tab，明確排除頂端全站 Reels 圖示。"""
        if self._is_profile_reels_page():
            _log.info(
                "[%s] 目前已在個人主頁 Reels 分頁，不重複尋找或點擊。",
                self.profile_name,
            )
            self.driver.execute_script("window.scrollTo(0, 0);")
            self._wait(0.2)
            return True
        scroll_count = 0
        max_scrolls = 5
        end = time.monotonic() + 3
        while scroll_count <= max_scrolls:
            self._stop()
            candidates = self.driver.find_elements(
                By.CSS_SELECTOR,
                "a[role='tab'][href*='sk=reels_tab'], "
                "a[role='tab'][href$='/reels'], "
                "a[role='tab'][href$='/reels/'], "
                "a[role='tab'][href*='/reels?']",
            )
            for element in candidates:
                try:
                    text = " ".join((
                        element.text or element.get_attribute("aria-label") or ""
                    ).split()).casefold()
                    href = (element.get_attribute("href") or "").lower()
                    if text not in (
                        "reels", "連續短片", "短片", "mga reel", "รีล", "ريلز"
                    ):
                        continue
                    if "/reel/" in href or "/reel?" in href:
                        continue
                    if not self._visible(element) or not self._enabled(element):
                        continue
                    old_url = self.driver.current_url
                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block:'center'});", element
                    )
                    element.click()
                    verify_end = time.monotonic() + 12
                    while time.monotonic() < verify_end:
                        self._stop()
                        if self._is_profile_reels_page():
                            _log.info(
                                "[%s] 已確認進入個人主頁 Reels 分頁：%s",
                                self.profile_name,
                                self.driver.current_url,
                            )
                            return True
                    self._wait(0.2)
                    _log.warning(
                        "[%s] 點擊 Reels tab 後未進入個人 Reels；原網址=%s，目前網址=%s",
                        self.profile_name,
                        old_url,
                        self.driver.current_url,
                    )
                except (ElementClickInterceptedException, StaleElementReferenceException):
                    pass
            self._wait(0.35)
            if time.monotonic() >= end:
                if scroll_count >= max_scrolls:
                    break
                scroll_count += 1
                self.driver.execute_script("window.scrollBy(0, 420);")
                _log.info(
                    "[%s] 3 秒內找不到個人主頁 Reels，向下滑動一小段（第 %d/5 次）。",
                    self.profile_name,
                    scroll_count,
                )
                self._wait(0.6)
                end = time.monotonic() + 3
        return False

    def _visible_enabled_button(self, labels: tuple[str, ...]):
        return next((x for x in self._buttons(labels) if self._enabled(x)), None)

    def _has_visible_button(self, labels: tuple[str, ...]) -> bool:
        return any(self._visible(x) for x in self._buttons(labels))

    def _exact_buttons(self, labels: tuple[str, ...], root=None):
        """只接受按鈕完整文字相符，避免把 Post audience 當成 Post。"""
        wanted = _expanded_button_labels(labels)
        dom_root = None if root is None or root is self.driver else root
        try:
            atomic_buttons = self.driver.execute_script(
                """
                const root = arguments[0] || document;
                const wanted = new Set(arguments[1]);
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                return Array.from(root.querySelectorAll(
                    'button, [role="button"], a'
                )).filter((element) => {
                    if (!visible(element)) return false;
                    const text = (
                        element.innerText ||
                        element.getAttribute('aria-label') ||
                        ''
                    ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                    return wanted.has(text);
                });
                """,
                dom_root,
                sorted(wanted),
            ) or []
            if atomic_buttons:
                return atomic_buttons
        except (StaleElementReferenceException, WebDriverException):
            pass

        scope = root if root is not None else self.driver
        result = []
        for element in scope.find_elements(By.XPATH, ".//button | .//*[@role='button'] | .//a"):
            try:
                text = " ".join(
                    (element.text or element.get_attribute("aria-label") or "").split()
                ).casefold()
                if self._visible(element) and text in wanted:
                    result.append(element)
            except StaleElementReferenceException:
                pass
        return result

    def _add_video_buttons(self, root):
        """尋找 Create reel 內的 Add video 上傳區塊。

        Facebook 實際節點的完整文字通常是
        ``Add video or drag and drop``，不能用一般的完整文字比對。
        """
        try:
            atomic_buttons = self.driver.execute_script(
                """
                const root = arguments[0];
                if (!root || !root.isConnected) return [];
                const visible = (element) => {
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                return Array.from(root.querySelectorAll(
                    'button, [role="button"], label'
                )).filter((element) => {
                    if (!visible(element)) return false;
                    const text = (
                        element.innerText ||
                        element.getAttribute('aria-label') ||
                        ''
                    ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                    const addVideo = (
                        text === 'add video' || text.startsWith('add video ') ||
                        text === '新增影片' || text.startsWith('新增影片 ') ||
                        text === 'إضافة فيديو' || text.startsWith('إضافة فيديو ')
                    );
                    return addVideo &&
                        !text.includes('add photos/video') &&
                        !text.includes('新增相片／影片') &&
                        !text.includes('新增相片/影片');
                });
                """,
                root,
            ) or []
            if atomic_buttons:
                return atomic_buttons
        except (StaleElementReferenceException, WebDriverException):
            pass

        result = []
        for element in root.find_elements(
            By.XPATH, ".//button | .//*[@role='button'] | .//label"
        ):
            try:
                text = " ".join(
                    (element.text or element.get_attribute("aria-label") or "").split()
                ).casefold()
                is_add_video = (
                    text == "add video"
                    or text.startswith("add video ")
                    or text == "新增影片"
                    or text.startswith("新增影片 ")
                )
                if text == "إضافة فيديو" or text.startswith("إضافة فيديو "):
                    is_add_video = True
                if (
                    self._visible(element)
                    and is_add_video
                    and "add photos/video" not in text
                    and "新增相片／影片" not in text
                    and "新增相片/影片" not in text
                ):
                    result.append(element)
            except StaleElementReferenceException:
                pass
        return result

    def _audience_dialog(self):
        """取得目前可見的 Post audience 對話框。"""
        # Facebook 新版會把 ``Post audience`` 改名為 ``Select audience``，
        # 且開啟後 React 可能立即替換 dialog 節點。先在同一次 JavaScript
        # 執行中完成可見性、標題／問題文字、受眾選項與 Done 的結構判斷，
        # 避免畫面已開啟卻因多次 Selenium 查詢命中舊節點。
        option_groups = [
            sorted(_expanded_button_labels(labels))
            for labels in (
                ("Public", "公開"),
                ("Friends", "好友"),
                ("Only me", "只限本人"),
            )
        ]
        done_labels = sorted(
            _expanded_button_labels(("Done", "Save", "完成", "儲存"))
        )
        try:
            atomic_dialog = self.driver.execute_script(
                """
                const optionGroups = arguments[0].map(
                    (labels) => new Set(labels)
                );
                const doneLabels = new Set(arguments[1]);
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const labelOf = (element) => (
                    element.getAttribute('aria-label') ||
                    element.innerText ||
                    element.textContent ||
                    ''
                ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                const matches = (text, wanted) => (
                    wanted.has(text) ||
                    Array.from(wanted).some(
                        (label) => text.startsWith(label + ' ')
                    )
                );
                const dialogs = Array.from(
                    document.querySelectorAll('[role="dialog"]')
                ).reverse();
                for (const dialog of dialogs) {
                    if (!visible(dialog)) continue;
                    const text = (dialog.innerText || '')
                        .replace(/\\s+/g, ' ')
                        .trim()
                        .toLocaleLowerCase();
                    const knownAudienceText = (
                        text.includes('post audience') ||
                        text.includes('select audience') ||
                        text.includes('who can see your post') ||
                        text.includes('貼文分享對象') ||
                        text.includes('選擇分享對象') ||
                        text.includes('誰可以看到你的貼文')
                    );
                    const choices = Array.from(dialog.querySelectorAll(
                        '[role="radio"], [role="option"], ' +
                        '[role="button"], label'
                    )).filter(visible);
                    const optionCount = optionGroups.filter(
                        (wanted) => choices.some(
                            (element) => matches(labelOf(element), wanted)
                        )
                    ).length;
                    const actions = Array.from(dialog.querySelectorAll(
                        'button, [role="button"], a'
                    )).filter(visible);
                    const hasDone = actions.some(
                        (element) => matches(labelOf(element), doneLabels)
                    );
                    if (
                        knownAudienceText ||
                        (optionCount >= 2 && hasDone)
                    ) return dialog;
                }
                return null;
                """,
                option_groups,
                done_labels,
            )
            if atomic_dialog is not None:
                return atomic_dialog
        except StaleElementReferenceException:
            pass

        # 舊版頁面的 Selenium 後備路徑。
        # Facebook keeps older modal nodes mounted behind the active modal.
        # Search from the newest/topmost dialog to avoid acting on stale flows.
        for dialog in reversed(
            self.driver.find_elements(By.CSS_SELECTOR, "[role='dialog']")
        ):
            try:
                if not self._visible(dialog):
                    continue
                text = " ".join((dialog.text or "").split()).casefold()
                if (
                    "post audience" in text
                    or "who can see your post" in text
                    or "貼文分享對象" in text
                    or "誰可以看到你的貼文" in text
                ):
                    return dialog
                # 部分語系（例如菲律賓語、泰語、阿拉伯語）的標題不叫
                # ``Post audience``。這類視窗仍會同時列出至少兩種受眾，
                # 並提供 Done／Save；以結構辨識可避免只靠翻譯字串。
                audience_groups = (
                    ("Public", "公開"),
                    ("Friends", "好友"),
                    ("Only me", "只限本人"),
                )
                option_group_count = sum(
                    bool(self._audience_options(dialog, labels))
                    for labels in audience_groups
                )
                has_done_or_save = any(
                    self._enabled(element)
                    for element in self._exact_buttons(
                        ("Done", "Save", "完成", "儲存"), root=dialog
                    )
                )
                if option_group_count >= 2 and has_done_or_save:
                    return dialog
            except StaleElementReferenceException:
                pass
        return None

    def _dismiss_review_audience_prompt(self) -> bool:
        """關閉 Reel settings 上方一次性的 Review audience 提示。

        這不是 Post audience 選擇視窗；Facebook 會在部分帳號第一次
        發布新版 Reels 時顯示它，並用 Continue 遮住描述欄。
        """
        names = (
            "Review audience",
            "檢查分享對象",
            "檢視分享對象",
            "確認分享對象",
        )
        dialog = self._named_dialog(names)
        if dialog is None:
            return False
        continue_button = next(
            (
                element
                for element in self._exact_buttons(
                    ("Continue", "繼續", "繼續操作"), root=dialog
                )
                if self._enabled(element)
            ),
            None,
        )
        if continue_button is None:
            raise RuntimeError("Review audience 視窗內找不到可用的 Continue")
        continue_button.click()
        end = time.monotonic() + 15
        while time.monotonic() < end:
            self._stop()
            if self._named_dialog(names) is None:
                _log.info(
                    "[%s] 已關閉 Review audience 提示，等待 Update settings。",
                    self.profile_name,
                )
                return True
            self._wait(0.2)
        raise RuntimeError("已點擊 Review audience 的 Continue，但提示視窗仍未關閉")

    def _update_settings_dialog(self):
        """取得 Review audience 之後出現的 Update settings 視窗。"""
        dialog = self._named_dialog(("Update settings", "更新設定", "更新設置"))
        if dialog is None:
            return None
        has_public = bool(self._audience_options(dialog, ("Public", "公開")))
        has_save = any(
            self._enabled(element)
            for element in self._exact_buttons(
                ("Save", "儲存", "保存"), root=dialog
            )
        )
        return dialog if has_public and has_save else None

    @staticmethod
    def _audience_option_selected(element) -> bool:
        """檢查選項列、內層或相鄰容器的原生 radio 是否已選中。"""
        candidates = [element]
        try:
            candidates.extend(
                element.find_elements(
                    By.CSS_SELECTOR,
                    "[role='radio'], input[type='radio']",
                )
            )
            # Facebook 新版 Public 文字列與真正的 radio 可能位於
            # 同一祖先容器的不同分支，不能只搜尋 element 的子節點。
            parent = element
            for _ in range(10):
                parent = parent.find_element(By.XPATH, "..")
                radios = parent.find_elements(
                    By.CSS_SELECTOR,
                    "[role='radio'], input[type='radio']",
                )
                if radios:
                    candidates.extend(radios)
                    break
        except StaleElementReferenceException:
            return False
        except Exception:
            pass
        for candidate in candidates:
            try:
                if (
                    str(candidate.get_attribute("aria-checked")).lower() == "true"
                    or str(candidate.get_attribute("aria-selected")).lower() == "true"
                    or str(candidate.get_attribute("checked")).lower() == "true"
                    or bool(
                        candidate.get_property("checked")
                        if candidate.tag_name.lower() == "input"
                        else False
                    )
                ):
                    return True
            except StaleElementReferenceException:
                return False
        return False

    def _complete_update_settings(self) -> None:
        """在 Update settings 選擇 Public、按 Save 並確認返回 Reel settings。"""
        end = time.monotonic() + 20
        dialog = None
        while time.monotonic() < end:
            self._stop()
            dialog = self._update_settings_dialog()
            if dialog is not None:
                break
            self._wait(0.5)
        if dialog is None:
            raise RuntimeError(
                "Review audience 的 Continue 後未出現含 Public／Save 的 Update settings"
            )

        public_options = self._audience_options(dialog, ("Public", "公開"))
        if not public_options:
            raise RuntimeError("Update settings 內找不到 Public／公開")
        public = public_options[0]
        # 與後面的 Post audience 成功流程使用相同方式：
        # 只讀取 Public 選項列本身的狀態；不可往共同祖先搜尋 radio，
        # 否則會把同一視窗內已勾選的 Friends 誤認成 Public 已勾選，
        # 造成程式完全沒有點擊第一個 Public 就直接按 Save。
        try:
            selected = (
                str(public.get_attribute("aria-checked")).lower() == "true"
                or str(public.get_attribute("aria-selected")).lower() == "true"
            )
            if not selected:
                public.click()
                self._wait(0.8)
                _log.info(
                    "[%s] 已實際點擊 Update settings 的 Public 選項列。",
                    self.profile_name,
                )
        except (ElementClickInterceptedException, StaleElementReferenceException):
            # React 重新渲染後，沿用 Post audience 的成功策略重新定位一次。
            dialog = self._update_settings_dialog()
            public_options = (
                self._audience_options(dialog, ("Public", "公開"))
                if dialog is not None
                else []
            )
            if not public_options:
                raise RuntimeError("Update settings 重新整理後找不到 Public／公開")
            public_options[0].click()
            self._wait(0.8)
            _log.info(
                "[%s] 重新定位後已實際點擊 Update settings 的 Public 選項列。",
                self.profile_name,
            )

        dialog = self._update_settings_dialog()
        save = next(
            (
                element
                for element in self._exact_buttons(
                    ("Save", "儲存", "保存"), root=dialog
                )
                if self._enabled(element)
            ),
            None,
        ) if dialog is not None else None
        if save is None:
            raise RuntimeError("Update settings 內找不到可用的 Save")
        save.click()
        _log.info("[%s] Update settings 已選擇 Public 並點擊 Save。", self.profile_name)

        close_end = time.monotonic() + 15
        while time.monotonic() < close_end:
            self._stop()
            if (
                self._update_settings_dialog() is None
                and self._reel_settings_page() is not None
            ):
                _log.info(
                    "[%s] Update settings 已關閉，已返回 Reel settings。",
                    self.profile_name,
                )
                return
            self._wait(0.5)
        raise RuntimeError("已點擊 Save，但 Update settings 未關閉或未返回 Reel settings")

    def _named_dialog(self, names: tuple[str, ...]):
        """取得標題完全相符的可見對話框，避免跨到背景頁面的同名元件。"""
        wanted = _expanded_button_labels(names)
        try:
            atomic_dialog = self.driver.execute_script(
                """
                const wanted = new Set(arguments[0]);
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const dialogs = Array.from(
                    document.querySelectorAll('[role="dialog"]')
                ).reverse();
                for (const dialog of dialogs) {
                    if (!visible(dialog)) continue;
                    const headings = dialog.querySelectorAll(
                        '[role="heading"], h1, h2, h3'
                    );
                    for (const heading of headings) {
                        if (!visible(heading)) continue;
                        const title = (heading.innerText || heading.textContent || '')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .toLocaleLowerCase();
                        if (wanted.has(title)) return dialog;
                    }
                }
                return null;
                """,
                sorted(wanted),
            )
            if atomic_dialog is not None:
                return atomic_dialog
        except (StaleElementReferenceException, WebDriverException):
            pass

        for dialog in reversed(
            self.driver.find_elements(By.CSS_SELECTOR, "[role='dialog']")
        ):
            try:
                if not self._visible(dialog):
                    continue
                headings = dialog.find_elements(
                    By.CSS_SELECTOR, "[role='heading'], h1, h2, h3"
                )
                for heading in headings:
                    title = " ".join((heading.text or "").split()).casefold()
                    if self._visible(heading) and title in wanted:
                        return dialog
            except StaleElementReferenceException:
                pass
        return None

    def _create_reel_dialog(self):
        return self._named_dialog(
            (
                "Create reel",
                "Create a reel",
                "Edit reel",
                "建立連續短片",
                "建立 Reel",
                "編輯連續短片",
                "編輯 Reel",
            )
        )

    def _reel_settings_page(self):
        """取得第二個 Next 後的最終 Reel settings 發布頁。

        Facebook 新版會把最終頁標題改為 ``Reel settings``，不再保留
        ``Create reel``／``Edit reel``。此頁必須同時具有描述欄與真正的
        Post，避免把背景頁面的普通貼文誤判為 Reels 發布頁。部分環境會
        保留左側面板的捲動位置，導致標題和描述欄被裁出畫面；此時改用
        對話框內的結構辨識，不能只依賴可見標題。
        """
        # Reel settings 是 React 換頁後動態建立的。若先找 dialog、再分開
        # 找描述欄與 Post，Facebook 可能在兩次 Selenium 查詢之間替換節點，
        # 使畫面明明已到最終頁卻持續被判定為仍在等待 Next。先在同一次
        # JavaScript 執行中完成全部結構判斷，並直接回傳當下的新 dialog。
        post_labels = sorted(
            _expanded_button_labels(("Post", "發布", "I-post", "โพสต์", "نشر"))
        )
        try:
            atomic_root = self.driver.execute_script(
                """
                const wanted = new Set(arguments[0]);
                const normalizedLabel = (element) => (
                    element.getAttribute('aria-label') ||
                    element.innerText ||
                    element.textContent ||
                    ''
                ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = window.getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const dialogs = Array.from(
                    document.querySelectorAll('[role="dialog"]')
                ).reverse();
                for (const dialog of dialogs) {
                    if (!visible(dialog)) continue;
                    const description = Array.from(dialog.querySelectorAll(
                        'textarea, ' +
                        '[contenteditable="true"][role="textbox"], ' +
                        '[aria-label*="describe your reel" i], ' +
                        '[aria-label*="描述你的 reel" i], ' +
                        '[aria-label*="描述連續短片" i]'
                    )).find(visible);
                    if (!description) continue;
                    const post = Array.from(dialog.querySelectorAll(
                        'button, [role="button"], a'
                    )).find((element) => (
                        visible(element) &&
                        wanted.has(normalizedLabel(element)) &&
                        element.getAttribute('aria-disabled') !== 'true' &&
                        !element.hasAttribute('disabled') &&
                        element.disabled !== true
                    ));
                    if (post) return dialog;
                }
                return null;
                """,
                post_labels,
            )
            if atomic_root is not None:
                return atomic_root
        except StaleElementReferenceException:
            # React 恰好在 JavaScript 回傳後再次換節點時，交給下次輪詢。
            pass

        # 保留 Selenium 結構辨識作為舊版頁面／瀏覽器的後備路徑。
        named_root = self._named_dialog(
            (
                "Reel settings",
                "Reels settings",
                "Reel 設定",
                "連續短片設定",
                "การตั้งค่ารีล",
                "إعدادات ريلز",
            )
        )
        candidates = [named_root] if named_root is not None else []
        for dialog in reversed(
            self.driver.find_elements(By.CSS_SELECTOR, "[role='dialog']")
        ):
            if self._visible(dialog):
                candidates.append(dialog)

        for scope in candidates:
            try:
                description_fields = [
                    element
                    for selector in (
                        "textarea",
                        "[contenteditable='true'][role='textbox']",
                        "[aria-label*='describe your reel' i]",
                        "[aria-label*='描述你的 reel' i]",
                        "[aria-label*='描述連續短片' i]",
                    )
                    for element in scope.find_elements(By.CSS_SELECTOR, selector)
                    if str(element.get_attribute("aria-hidden")).lower() != "true"
                ]
                has_post = any(
                    self._enabled(element)
                    for element in self._exact_buttons(
                        ("Post", "發布", "I-post", "โพสต์", "نشر"), root=scope
                    )
                )
            except StaleElementReferenceException:
                # Facebook 在 Create reel／Edit reel／Reel settings 換頁時會
                # 直接替換整個 role=dialog。舊節點失效代表正在正常換頁，
                # 交給外層輪詢重新抓取，不應終止本次 Reels。
                continue
            if description_fields and has_post:
                return scope
        return None

    def _second_edit_reel_page(self) -> bool:
        """辨識第一次 Next 後新增的第二個 Edit reel 編輯頁。

        這一頁仍有 Next，但 Facebook 可能不再把內容放在原本的
        role=dialog 節點內，因此不能只靠 `_create_reel_dialog()`。
        """
        body = " ".join(str(self.driver.execute_script(
            "return (document.body && document.body.innerText) || '';"
        ) or "").split()).casefold()
        english_markers = (
            "trim video",
            "closed captions",
            "audio description",
            "text transcript",
            "optimization",
        )
        chinese_markers = (
            "裁剪影片",
            "修剪影片",
            "隱藏式字幕",
            "音訊說明",
            "文字記錄",
            "最佳化",
            "優化",
        )
        other_language_markers = (
            "rogner la vidéo", "sous-titres", "description audio",
            "transcription", "optimisation",
            "i-trim ang video", "mga closed caption", "paglalarawan ng audio",
            "transcript ng teksto", "pag-optimize",
            "ตัดวิดีโอ", "คำบรรยาย", "คำอธิบายเสียง",
            "ข้อความถอดเสียง", "การปรับให้เหมาะสม",
            "قص الفيديو", "شرح مكتوب", "وصف صوتي",
            "نسخة نصية", "تحسين",
        )
        marker_count = sum(
            x in body for x in english_markers + chinese_markers + other_language_markers
        )
        has_edit_title = any(x in body for x in (
            "edit reel", "編輯 reel", "編輯連續短片",
            "modifier le reel", "i-edit ang reel", "แก้ไขรีล",
            "تعديل ريل", "تعديل مقطع ريلز",
        ))
        return has_edit_title and marker_count >= 2 and self._has_visible_button(
            ("Next", "下一步", "Suivant", "Susunod", "ถัดไป")
        )

    def _create_post_dialog(self):
        return self._named_dialog(
            ("Create post", "建立貼文", "Gumawa ng post", "สร้างโพสต์", "إنشاء منشور")
        )

    def _require_reel_dialog(self):
        if self._create_post_dialog() is not None:
            raise RuntimeError(
                "偵測到不該出現的 Create post：已誤入普通貼文流程，停止 Reels"
            )
        settings = self._reel_settings_page()
        if settings is not None:
            return settings
        dialog = self._create_reel_dialog()
        if dialog is not None:
            return dialog
        if self._second_edit_reel_page():
            # 新版 Facebook 的第二個 Edit reel 是全頁式 overlay，
            # 不一定存在 role=dialog；回傳 driver 讓後續只找精確 Next。
            return self.driver
        raise RuntimeError(
            "Create reel／Edit reel／Reel settings 未出現或已離開正確 Reels 流程"
        )

    def _wait_for_reel_dialog(self, timeout: float = 15.0):
        """等待 Reels 視窗實際出現；出現即返回，不使用固定長等待。"""
        end = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < end:
            self._stop()
            try:
                return self._require_reel_dialog()
            except (RuntimeError, StaleElementReferenceException, WebDriverException) as exc:
                last_error = exc
            self._wait(0.25)
        raise RuntimeError(f"等待 Reels 視窗逾時：{last_error}")

    def _audience_options(self, dialog, labels: tuple[str, ...]):
        """在 Audience 視窗內找選項列；允許 Public 下方帶有說明文字。"""
        wanted = tuple(_expanded_button_labels(labels))
        try:
            atomic_options = self.driver.execute_script(
                """
                const root = arguments[0];
                const wanted = new Set(arguments[1]);
                if (!root || !root.isConnected) return [];
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const semanticOptions = Array.from(root.querySelectorAll(
                    '[role="radio"], [role="option"], ' +
                    '[role="button"], label'
                )).filter((element) => {
                    if (!visible(element)) return false;
                    const text = (
                        element.getAttribute('aria-label') ||
                        element.innerText ||
                        element.textContent ||
                        ''
                    ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                    return (
                        wanted.has(text) ||
                        Array.from(wanted).some(
                            (label) => text.startsWith(label + ' ')
                        )
                    );
                });
                if (semanticOptions.length) return semanticOptions;

                // 部分 Facebook 帳號的 Select audience 不再替選項列加
                // role="radio"，而是只保留原生 input[type="radio"]。
                // 從每個 radio 往上找最小的文字列，以 Public 標題辨識，
                // 最後回傳實際 radio，避免依賴易變動的 Facebook class。
                for (const radio of root.querySelectorAll('input[type="radio"]')) {
                    let row = radio.parentElement;
                    while (row && row !== root) {
                        const text = (
                            row.getAttribute('aria-label') ||
                            row.innerText ||
                            row.textContent ||
                            ''
                        ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                        if (
                            visible(radio) &&
                            (
                                wanted.has(text) ||
                                Array.from(wanted).some(
                                    (label) => text.startsWith(label + ' ')
                                )
                            )
                        ) {
                            return [radio];
                        }
                        // 超過單一選項列後會包含整個視窗的所有受眾，
                        // 不可再用 startsWith 判定，以免抓到其他 radio。
                        if (text.length > 300) break;
                        row = row.parentElement;
                    }
                }
                return [];
                """,
                dialog,
                list(wanted),
            ) or []
            if atomic_options:
                return atomic_options
        except StaleElementReferenceException:
            # Audience 開啟時 React 可能剛好替換整個視窗，
            # 不可因舊 root 失效就直接判定找不到 Public。
            pass

        # 某些帳號的 Select audience 會嵌在 Reel settings 的
        # 外層 dialog，並在開啟後立即重建節點。改從當下全頁的
        # 可見精確標籤重新定位，找到包含 radio 的最小 label；
        # 這個查詢不使用上一次傳入的 dialog，因此不會命中舊節點。
        try:
            live_option = self.driver.execute_script(
                """
                const wanted = new Set(arguments[0]);
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const normalized = (element) => (
                    element.getAttribute('aria-label') ||
                    element.innerText ||
                    element.textContent ||
                    ''
                ).replace(/\\s+/g, ' ').trim().toLocaleLowerCase();
                const exactLabels = Array.from(document.querySelectorAll(
                    'span, label, [role="radio"], [role="option"], [role="button"]'
                )).filter((element) => (
                    visible(element) && wanted.has(normalized(element))
                ));
                for (const labelText of exactLabels) {
                    let row = labelText;
                    while (row && row !== document.body) {
                        const text = normalized(row);
                        if (text.length > 300) break;
                        if (
                            row.matches('label, [role="radio"], [role="option"]') &&
                            row.querySelector('input[type="radio"], [role="radio"]')
                        ) return row;
                        row = row.parentElement;
                    }
                    // 若 Facebook 沒有 label 容器，點精確文字仍會向上
                    // bubble 到受眾選項列。
                    return labelText;
                }
                return null;
                """,
                list(wanted),
            )
            if live_option is not None:
                return [live_option]
        except StaleElementReferenceException:
            pass

        result = []
        try:
            elements = dialog.find_elements(
                By.XPATH,
                ".//*[@role='radio'] | .//*[@role='option'] | "
                ".//*[@role='button'] | .//label",
            )
        except StaleElementReferenceException:
            return []
        for element in elements:
            try:
                text = " ".join(
                    (element.text or element.get_attribute("aria-label") or "").split()
                ).casefold()
                if self._visible(element) and any(
                    text == x or text.startswith(x + " ") for x in wanted
                ):
                    result.append(element)
            except StaleElementReferenceException:
                pass
        return result

    def _scroll_audience_options(self, dialog) -> bool:
        """向下捲動 Select audience 內的選項清單。

        Facebook 部分新版面只渲染目前可見的受眾選項；Public 位於
        Friends／Specific friends 下方時，必須先捲動清單才會出現在 DOM。
        """
        try:
            result = self.driver.execute_script(
                """
                const root = arguments[0];
                if (!root || !root.isConnected) return false;
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 80;
                };
                const candidates = [root, ...root.querySelectorAll('*')]
                    .filter((element) => {
                        if (!visible(element)) return false;
                        if (element.scrollHeight <= element.clientHeight + 8) {
                            return false;
                        }
                        const overflowY = getComputedStyle(element).overflowY;
                        return overflowY === 'auto' || overflowY === 'scroll';
                    })
                    .sort((a, b) =>
                        (b.scrollHeight - b.clientHeight) -
                        (a.scrollHeight - a.clientHeight)
                    );
                for (const scroller of candidates) {
                    const before = scroller.scrollTop;
                    const step = Math.max(140, Math.floor(scroller.clientHeight * 0.7));
                    scroller.scrollTop = Math.min(
                        scroller.scrollHeight - scroller.clientHeight,
                        before + step
                    );
                    if (scroller.scrollTop > before + 1) {
                        scroller.dispatchEvent(new Event('scroll', {bubbles: true}));
                        return true;
                    }
                }
                return false;
                """,
                dialog,
            )
            return bool(result)
        except StaleElementReferenceException:
            return False

    def _reset_audience_options_to_top(self, dialog) -> None:
        """將 Select audience 內所有可捲動清單移回頂端。"""
        try:
            self.driver.execute_script(
                """
                const root = arguments[0];
                if (!root || !root.isConnected) return;
                for (const element of [root, ...root.querySelectorAll('*')]) {
                    if (element.scrollHeight > element.clientHeight + 8) {
                        element.scrollTop = 0;
                        element.dispatchEvent(new Event('scroll', {bubbles: true}));
                    }
                }
                """,
                dialog,
            )
        except StaleElementReferenceException:
            pass

    def _first_audience_option(self, dialog):
        """取得 Select audience 最上方的第一個 radio 選項。"""
        try:
            return self.driver.execute_script(
                """
                const root = arguments[0];
                if (!root || !root.isConnected) return null;
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.getAttribute('aria-hidden') === 'true') return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const options = Array.from(root.querySelectorAll(
                    '[role="radio"], input[type="radio"]'
                )).filter(visible);
                return options.length ? options[0] : null;
                """,
                dialog,
            )
        except StaleElementReferenceException:
            return None

    def _exact_post_buttons(self):
        """只找真正的發布按鈕，排除 Post audience 標題／入口。"""
        result = []
        for element in self._exact_buttons(("Post", "發布", "I-post", "โพสต์", "نشر")):
            try:
                in_audience = self.driver.execute_script(
                    """
                    const el = arguments[0];
                    const dialog = el.closest('[role="dialog"]');
                    if (!dialog) return false;
                    const text = (dialog.innerText || '').toLowerCase();
                    return text.includes('post audience') ||
                           text.includes('who can see your post') ||
                           text.includes('貼文分享對象') ||
                           text.includes('誰可以看到你的貼文');
                    """,
                    element,
                )
                if not in_audience:
                    result.append(element)
            except StaleElementReferenceException:
                pass
        return result

    def _description_page(self) -> bool:
        """只在真正的分享／發布頁判定成功。"""
        # 最終頁的描述欄與 Post 結構優先於全頁面的 Next。Facebook 可能暫時
        # 保留上一頁的隱藏／過場 Next，不能因此否定已確認的 Reel settings。
        return self._reel_settings_page() is not None

    @staticmethod
    def _copy_to_windows_clipboard(text: str) -> None:
        """用 64-bit 安全的 Windows Unicode 剪貼簿傳遞 emoji 與多行文案。"""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
        kernel32.GlobalAlloc.restype = pointer
        kernel32.GlobalLock.argtypes = (pointer,)
        kernel32.GlobalLock.restype = pointer
        kernel32.GlobalUnlock.argtypes = (pointer,)
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = (pointer,)
        kernel32.GlobalFree.restype = pointer
        user32.OpenClipboard.argtypes = (wintypes.HWND,)
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = (wintypes.UINT, pointer)
        user32.SetClipboardData.restype = pointer
        user32.CloseClipboard.restype = wintypes.BOOL

        data = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(0x0002, len(data))
        if not handle:
            raise OSError("GlobalAlloc 失敗")
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            raise OSError("GlobalLock 失敗")
        try:
            ctypes.memmove(locked, data, len(data))
        finally:
            kernel32.GlobalUnlock(handle)

        opened = False
        for _ in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03)
        if not opened:
            kernel32.GlobalFree(handle)
            raise OSError("OpenClipboard 失敗")
        try:
            if not user32.EmptyClipboard():
                raise OSError("EmptyClipboard 失敗")
            if not user32.SetClipboardData(13, handle):
                raise OSError("SetClipboardData 失敗")
            handle = None
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

    def _description_text(self, element) -> str:
        try:
            value = self.driver.execute_script(
                """
                const el = arguments[0];
                return el.value || el.innerText || el.textContent || '';
                """,
                element,
            )
            return " ".join(str(value or "").split())
        except Exception:
            return ""

    def _description_matches(self, element, text: str) -> bool:
        return self._description_text(element) == " ".join(text.split())

    def _insert_description_with_javascript(self, element, text: str) -> None:
        """剪貼簿無法使用時，以輸入事件寫入 Lexical／textarea。"""
        inserted = self.driver.execute_script(
            """
            const el = arguments[0];
            const text = arguments[1];
            el.focus();
            if ('value' in el) {
                const proto = el.tagName === 'TEXTAREA'
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                setter.call(el, text);
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true, inputType: 'insertText', data: text
                }));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            selection.removeAllRanges();
            selection.addRange(range);
            let ok = false;
            try { ok = document.execCommand('insertText', false, text); } catch (e) {}
            if (!ok) {
                el.textContent = text;
                ok = true;
            }
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true, inputType: 'insertText', data: text
            }));
            return ok;
            """,
            element,
            text,
        )
        if not inserted:
            raise RuntimeError("JavaScript 描述輸入失敗")

    def _enter_description(self, text: str) -> None:
        selectors = ("textarea", "[contenteditable='true'][role='textbox']",
                     "[contenteditable='true'][aria-label*='description' i]",
                     "[contenteditable='true'][aria-label*='描述' i]")
        settings = self._reel_settings_page()
        scope = settings if settings is not None else self.driver
        for selector in selectors:
            for element in scope.find_elements(By.CSS_SELECTOR, selector):
                if not self._visible(element):
                    try:
                        self.driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});",
                            element,
                        )
                        self._wait(0.15)
                    except StaleElementReferenceException:
                        continue
                    if not self._visible(element):
                        continue
                label = (element.get_attribute("aria-label") or "").casefold()
                if any(x in label for x in ("search", "搜尋", "comment", "留言")):
                    continue
                try:
                    element.click()
                    element.send_keys(Keys.CONTROL, "a")
                    element.send_keys(Keys.BACKSPACE)
                    if text:
                        pasted = False
                        try:
                            self._copy_to_windows_clipboard(text)
                            element.send_keys(Keys.CONTROL, "v")
                            end = time.monotonic() + 2
                            while time.monotonic() < end:
                                if self._description_matches(element, text):
                                    pasted = True
                                    break
                                self._wait(0.08)
                        except Exception as exc:
                            _log.warning(
                                "[%s] Unicode 剪貼簿貼上失敗，改用 JavaScript：%s",
                                self.profile_name,
                                exc,
                            )
                        if not pasted:
                            element.send_keys(Keys.CONTROL, "a")
                            element.send_keys(Keys.BACKSPACE)
                            self._insert_description_with_javascript(element, text)
                    if self._description_matches(element, text):
                        _log.info(
                            "[%s] 已確認描述文案僅有一份（支援 emoji）。",
                            self.profile_name,
                        )
                        return
                except (
                    ElementClickInterceptedException,
                    StaleElementReferenceException,
                ):
                    pass
        raise RuntimeError("找不到描述輸入欄，或輸入後未生效")

    def _ensure_public(self) -> None:
        dialog = self._audience_dialog()

        # Audience 視窗尚未開啟時，先判斷發布頁目前顯示的分享對象。
        if dialog is None:
            current_public = self._exact_buttons(("Public", "公開"))
            if current_public:
                _log.info("[%s] 已確認分享對象為公開。", self.profile_name)
                return
            settings = self._reel_settings_page()
            if settings is not None and self._audience_options(
                settings, ("Public", "公開")
            ):
                _log.info(
                    "[%s] Reel settings 已顯示 Public，分享對象為公開。",
                    self.profile_name,
                )
                return
            if not self._click(
                ("Friends", "好友", "Only me", "只限本人", "Audience", "分享對象"),
                15,
            ):
                raise RuntimeError("找不到分享對象設定")
            self._wait(1)
            dialog = self._audience_dialog()
            if dialog is None:
                raise RuntimeError("點擊分享對象後，Post audience 視窗未出現")

        # 視窗已開啟時，即使 Public 原本已勾選，也必須按 Done 關閉視窗。
        # Audience 開啟後可能再次重建，最長 6 秒內持續重抓
        # 最新 dialog 和 Public，避免當次 React 換頁造成偶發失敗。
        public_options = []
        public_deadline = time.monotonic() + 10
        audience_scrolled = False
        while time.monotonic() < public_deadline and not public_options:
            self._stop()
            dialog = self._audience_dialog()
            if dialog is not None:
                public_options = self._audience_options(
                    dialog, ("Public", "公開", "Pampubliko", "สาธารณะ", "عام")
                )
            if not public_options:
                if dialog is not None and self._scroll_audience_options(dialog):
                    if not audience_scrolled:
                        _log.info(
                            "[%s] Public 不在目前可見範圍，向下捲動分享對象清單。",
                            self.profile_name,
                        )
                    audience_scrolled = True
                self._wait(0.25)
        if public_options:
            public = public_options[0]
        else:
            # 未滿 18 歲帳號不提供 Public。依需求回到清單頂端，
            # 選擇第一個 Facebook 允許的分享對象（通常為 Friends of friends）。
            dialog = self._audience_dialog()
            if dialog is None:
                raise RuntimeError("Post audience 視窗內找不到 Public／公開")
            self._reset_audience_options_to_top(dialog)
            self._wait(0.5)
            dialog = self._audience_dialog()
            public = self._first_audience_option(dialog) if dialog else None
            if public is None:
                raise RuntimeError("分享對象沒有 Public，且找不到第一個可用選項")
            _log.info(
                "[%s] 此帳號沒有 Public，已改選分享對象清單最上方選項。",
                self.profile_name,
            )
        try:
            selected = (
                str(public.get_attribute("aria-checked")).lower() == "true"
                or str(public.get_attribute("aria-selected")).lower() == "true"
            )
            if not selected:
                public.click()
                self._wait(0.8)
        except (ElementClickInterceptedException, StaleElementReferenceException):
            # React 重新渲染後重新定位一次。
            dialog = self._audience_dialog()
            public_options = self._audience_options(
                dialog, ("Public", "公開", "Pampubliko", "สาธารณะ", "عام")
            ) if dialog else []
            if not public_options:
                raise RuntimeError("無法選擇 Public／公開")
            public_options[0].click()
            self._wait(0.8)

        dialog = self._audience_dialog()
        if dialog is None:
            # 少數版面在選擇 Public 後會立即套用並自動關閉 Audience。
            # 此時不應因找不到 Done 而誤判失敗。
            _log.info(
                "[%s] 分享對象已套用，Post audience 視窗已自動關閉。",
                self.profile_name,
            )
            return

        done_buttons = self._exact_buttons(
            (
                "Done", "Save", "完成", "儲存", "Tapos", "Tapos na", "I-save",
                "เรียบร้อย", "บันทึก", "تم", "حفظ",
            ), root=dialog
        )
        done = next((x for x in done_buttons if self._enabled(x)), None)
        if done is None:
            # Facebook 偶爾先推出新翻譯，按鈕文字會早於程式的語系表更新。
            # Audience 視窗的確認鍵固定是視窗底部、寬度最大的可用按鈕；
            # 以結構作最後後備，避免再次卡在未知語系的「完成」。
            try:
                done = self.driver.execute_script(
                    """
                    const dialog = arguments[0];
                    if (!dialog || !dialog.isConnected) return null;
                    const rect = dialog.getBoundingClientRect();
                    const visible = (element) => {
                        if (!element || !element.isConnected) return false;
                        const style = getComputedStyle(element);
                        const box = element.getBoundingClientRect();
                        return (
                            box.width > 0 && box.height > 0 &&
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            element.getAttribute('aria-disabled') !== 'true' &&
                            !element.disabled
                        );
                    };
                    const candidates = Array.from(dialog.querySelectorAll(
                        'button, [role="button"]'
                    )).filter((element) => {
                        if (!visible(element)) return false;
                        const box = element.getBoundingClientRect();
                        return (
                            box.top >= rect.top + rect.height * 0.68 &&
                            box.width >= rect.width * 0.55
                        );
                    });
                    candidates.sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (br.top - ar.top) || (br.width - ar.width);
                    });
                    return candidates[0] || null;
                    """,
                    dialog,
                )
            except (StaleElementReferenceException, WebDriverException):
                done = None
            if done is not None and self._enabled(done):
                _log.info(
                    "[%s] 依 Audience 視窗底部結構找到完成按鈕。",
                    self.profile_name,
                )
            else:
                done = None
        if done is None:
            raise RuntimeError("Post audience 視窗內找不到可用的 Done／完成")
        done.click()

        end = time.monotonic() + 10
        while time.monotonic() < end:
            self._stop()
            if self._audience_dialog() is None:
                _log.info("[%s] 已設定分享對象並關閉 Post audience 視窗。", self.profile_name)
                return
            self._wait(0.5)
        raise RuntimeError("已點擊 Done，但 Post audience 視窗仍未關閉")

    def run(self) -> str:
        try:
            return self._run()
        except InterruptedError as exc:
            self.save_diagnostic(f"{exc}（停止時自動保存）")
            raise
        except Exception as exc:
            self.save_diagnostic(str(exc))
            raise

    def _run(self) -> str:
        self._switch_to_facebook_tab()
        self._set_stage("檢查影片與描述")
        material = resolve_material(self.video_dir, self.text_file, self.profile_name)
        _log.info("[%s] Reels 對應編號：%s", self.profile_name, material.raw_number)
        _log.info("[%s] 已找到 Reels 影片：%s", self.profile_name, material.video)
        _log.info(
            "[%s] 已隨機讀取 Reels 描述（%d 行、%d 字元）。",
            self.profile_name,
            material.description.count("\n") + 1,
            len(material.description),
        )
        if already_posted(self.profile_id, material):
            _log.info("[%s] 同一影片已有成功紀錄，本次跳過。", self.profile_name)
            return "skipped"
        self._set_stage("處理通知權限彈窗")
        self._dismiss_notification_prompt()
        self._dismiss_floating_chats()
        self._set_stage("直接開啟 Create reel")
        reel_dialog = self._open_create_reel_directly(25)
        self._set_stage("上傳影片")
        # 只允許 Create reel 視窗內的影片欄，絕不使用整頁或 Create post 的 input。
        add_video = next(
            (x for x in self._add_video_buttons(reel_dialog) if self._enabled(x)),
            None,
        )
        if add_video is None:
            raise RuntimeError("Create reel 視窗內找不到 Add video")

        # 不點擊 Add video，避免叫出 Windows 原生選檔視窗。Facebook
        # 實際 DOM 中 input 常是 Add video 按鈕的同層前一個節點，
        # 並不是按鈕的子節點，因此要由共同容器反查後直接 send_keys。
        self._upload_without_file_dialog(add_video, material)

        _log.info("[%s] 已送入影片，等待 Facebook 上傳。", self.profile_name)
        deadline = time.monotonic() + self.timeout
        self._set_stage("確認 Add video／Upload")
        upload_wait_logged = False
        while time.monotonic() < deadline:
            self._stop()
            reel_dialog = self._require_reel_dialog()
            if self._description_page() or self._has_visible_button(("Next", "下一步")):
                break
            upload = next(
                (
                    x
                    for x in self._exact_buttons(("Upload", "上傳"), root=reel_dialog)
                    if self._enabled(x)
                ),
                None,
            )
            if upload is not None:
                upload.click()
                _log.info("[%s] 已點擊 Upload，等待 Next 出現。", self.profile_name)
                self._wait(0.5)
                break
            if not upload_wait_logged:
                _log.info("[%s] 尚未出現 Upload／Next，等待影片預覽載入。", self.profile_name)
                upload_wait_logged = True
            self._wait(0.5)
        self._set_stage("等待上傳完成與 Next 換頁")
        last_next_log = 0.0
        second_edit_logged = False
        next_click_count = 0
        while time.monotonic() < deadline:
            self._stop()
            if self._description_page():
                _log.info(
                    "[%s] 已進入 Reel settings 最終發布頁。",
                    self.profile_name,
                )
                break
            reel_dialog = self._require_reel_dialog()
            if self._second_edit_reel_page() and not second_edit_logged:
                _log.info(
                    "[%s] 已進入第二個 Edit reel 編輯頁，準備點擊第二個 Next。",
                    self.profile_name,
                )
                second_edit_logged = True
            next_button = next(
                (
                    x
                    for x in self._exact_buttons(("Next", "下一步"), root=reel_dialog)
                    if self._enabled(x)
                ),
                None,
            )
            if next_button is not None:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center', inline:'nearest'});",
                    next_button,
                )
                try:
                    next_button.click()
                except (ElementClickInterceptedException, StaleElementReferenceException):
                    # 新版 Edit reel 的底部操作列可能覆蓋 Next 的中心點；
                    # 重新定位後使用 DOM click，仍只點擊精確標籤的 Next。
                    reel_dialog = self._require_reel_dialog()
                    next_button = next(
                        (
                            x
                            for x in self._exact_buttons(
                                ("Next", "下一步"), root=reel_dialog
                            )
                            if self._enabled(x)
                        ),
                        None,
                    )
                    if next_button is None:
                        raise RuntimeError("Next 被遮擋後重新定位失敗")
                    self.driver.execute_script("arguments[0].click();", next_button)
                    _log.info(
                        "[%s] Next 被底部容器遮擋，已改用 DOM click。",
                        self.profile_name,
                    )
                next_click_count += 1
                _log.info(
                    "[%s] 已點擊第 %d 個 Next，正在確認下一階段。",
                    self.profile_name,
                    next_click_count,
                )
                # 不固定等待三秒；Facebook 一完成換頁，下一輪立即辨識。
                self._wait(0.2)
                if not self._description_page():
                    if self._second_edit_reel_page():
                        _log.info(
                            "[%s] 第一次 Next 後出現第二個 Edit reel 頁面，此為正常流程。",
                            self.profile_name,
                        )
                    else:
                        _log.info(
                            "[%s] 尚未進入描述與發布頁，繼續等待目前階段。",
                            self.profile_name,
                        )
            else:
                if time.monotonic() - last_next_log > 20:
                    _log.info("[%s] Next 尚未可用，繼續等待影片上傳／處理。", self.profile_name)
                    last_next_log = time.monotonic()
                self._wait(0.2)

        if not self._description_page():
            raise TimeoutError("影片上傳或 Next 等待超過 10 分鐘")
        self._set_stage("輸入描述")
        if self._dismiss_review_audience_prompt():
            self._set_stage("Update settings 選擇 Public")
            self._complete_update_settings()
            self._set_stage("輸入描述")
        self._enter_description(material.description)
        _log.info("[%s] 已輸入描述，共 %d 個字元。", self.profile_name, len(material.description))
        self._set_stage("確認公開分享對象")
        self._ensure_public()
        last_log = 0.0
        post = None
        self._set_stage("等待 Post 啟用")
        while time.monotonic() < deadline:
            self._stop()
            body = str(self.driver.execute_script(
                "return (document.body && document.body.innerText) || '';"
            ) or "").casefold()
            processing = any(x in body for x in (
                "uploading", "processing", "正在上傳", "處理中", "正在處理",
                "téléchargement", "traitement", "ina-upload", "pinoproseso",
                "กำลังอัปโหลด", "กำลังประมวลผล",
                "جارٍ التحميل", "جاري التحميل", "جارٍ المعالجة", "جاري المعالجة",
            ))
            if self._audience_dialog() is not None:
                raise RuntimeError("Post audience 視窗尚未關閉，禁止尋找發布按鈕")
            post = next((x for x in self._exact_post_buttons() if self._enabled(x)), None)
            if post is not None and not processing:
                break
            if time.monotonic() - last_log > 20:
                _log.info("[%s] Post 尚未啟用，繼續等待。", self.profile_name)
                last_log = time.monotonic()
            self._wait(0.2)
        if post is None:
            raise TimeoutError("等待 Post 啟用超過 10 分鐘")
        if self.dry_run:
            self._set_stage("安全測試完成：Post 已啟用但不點擊")
            _log.info(
                "[%s] Reels 安全測試成功：最終 Post 按鈕已可用，依設定不發布。",
                self.profile_name,
            )
            return "ready"
        self._set_stage("點擊 Post 並確認發布")
        post.click()
        verify = time.monotonic() + 90
        while time.monotonic() < verify:
            self._stop()
            body = str(self.driver.execute_script(
                "return (document.body && document.body.innerText) || '';"
            ) or "").casefold()
            if not self._exact_post_buttons() or any(
                    x in body for x in (
                        # Facebook 目前不一定顯示「Reel published」，新版英文
                        # 會先顯示貼文已成功分享，接著顯示 Reel 正在處理。
                        "your reel was published",
                        "reel published",
                        "your post is successfully shared",
                        "your reel is being processed",
                        "your reel is ready to view",
                        # 中文
                        "已發布",
                        "貼文已成功分享",
                        "你的 reel 正在處理",
                        "你的連續短片正在處理",
                        # 法文
                        "votre publication a bien été partagée",
                        "votre reel est en cours de traitement",
                        "votre réel est en cours de traitement",
                        # 菲律賓／Tagalog
                        "matagumpay na naibahagi ang iyong post",
                        "pinoproseso ang reel mo",
                        # 泰文
                        "แชร์โพสต์ของคุณเรียบร้อยแล้ว",
                        "รีลของคุณกำลังประมวลผล",
                        # 阿拉伯文
                        "تمت مشاركة منشورك بنجاح",
                        "تتم معالجة ريلز الخاص بك",
                        "تم نشر الريل",
                        "تم نشر مقطع الريلز",
                    )):
                record_success(self.profile_id, self.profile_name, material)
                _log.info("[%s] Reels 發布成功。", self.profile_name)
                return "success"
            self._wait(0.5)
        raise RuntimeError("已點擊 Post，但無法確認發布成功")

    def _upload_without_file_dialog(self, add_video, material) -> None:
        """直接寫入 Add video 對應的 file input，不開啟系統選檔視窗。"""
        video_input = self.driver.execute_script(
            """
            const button = arguments[0];
            const dialog = button.closest('[role="dialog"]');
            if (!dialog) return null;
            const heading = dialog.querySelector('[role="heading"],h1,h2,h3');
            const title = ((heading && heading.innerText) || '')
                .trim().toLowerCase();
            const allowed = new Set([
              'create reel', 'create a reel',
              'إنشاء ريل', 'إنشاء مقطع ريلز',
              '建立連續短片', '建立 reel'
            ]);
            if (!allowed.has(title)) return null;

            // 由 Add video 按鈕逐層向上找共同容器。實際 Facebook
            // 結構通常是：input[type=file] 與按鈕為同一容器的兄弟節點。
            let node = button;
            while (node && node !== dialog.parentElement) {
              const inputs = node.querySelectorAll('input[type="file"]');
              for (const input of inputs) {
                const accept = (input.getAttribute('accept') || '').toLowerCase();
                if (!input.disabled && accept.includes('video')) return input;
              }
              if (node === dialog) break;
              node = node.parentElement;
            }
            return null;
            """,
            add_video,
        )
        if video_input is None:
            raise RuntimeError(
                "Create reel 的 Add video 區塊找不到對應影片欄位；"
                "為避免打開 Windows 選檔視窗，本次停止"
            )
        video_input.send_keys(str(material.video))
        _log.info(
            "[%s] 已直接寫入 Create reel 影片欄位（未開啟系統選檔視窗）。",
            self.profile_name,
        )
