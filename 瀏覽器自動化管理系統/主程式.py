"""
main.py
=======
養號＋換頭像＋建立 Messenger PIN V1.0 Stable
整合 V8.2.4 養號流程與 V2.4.3 Stable 換頭像／建立 PIN 流程。

使用方式：
    python main.py                   # 啟動後詢問是否啟用加好友功能
    python main.py --profile jd4abc  # 只執行指定的單一 Profile
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from 環境管理介面 import AdsPowerClient, ProfileInfo
from 行為模擬 import CommentGenerator, FeedBrowser
from 瀏覽器 import BrowserController
from 設定 import CONFIG
from 臉書操作 import (
    FacebookFriendAdder,
    FacebookFriendConfirmer,
    HealthChecker,
    HealthStatus,
)
from 日誌 import ProfileSummary, get_logger, start_profile_log, stop_profile_log
from 工具 import random_sleep, shuffled
from 個人資料工具 import sort_profiles_by_number
from 個人資料設定 import (
    find_matching_banner,
    change_facebook_banner,
    read_profile_name,
    change_facebook_name,
    set_facebook_language,
)
import 頭像固定
from 頭像固定 import (
    allow_facebook_notifications,
    change_facebook_avatar,
    find_matching_image,
)
from 專業模式 import ProfessionalModeWorker, Profile as ProfessionalProfile
from 短影音 import ReelsPublisher
from 短影音留言 import ReelsCommentTask
from 任務診斷 import save_task_diagnostic
from 任務結果 import TaskStatsRegistry
from 聊天室資料庫 import ChatRepository
from 粉絲專頁訊息任務 import FanpageMessageTask
from 聊天室查詢任務 import ChatQueryTask
from 聊天室回覆任務 import ChatReplyTask
from 訊息選擇器 import has_chat_identity_restriction
from Telegram回報 import TelegramReporter
from 臉書帳號狀態 import (
    detect_facebook_account_status,
    ip_expired_profile_name,
    sleep_mode_profile_name,
    suspended_profile_name,
)
from 人工驗證 import (
    detect_human_verification_page as detect_human_verification_result,
    verification_profile_name,
)
_log = get_logger("main")

# Facebook 首頁
FACEBOOK_HOME_URL = "https://www.facebook.com"

# Chrome 明確指出代理設定／驗證失效時可直接視為 IP 到期。
# ERR_TIMED_OUT 可能只是短暫網路延遲，因此必須重新載入確認一次。
IP_EXPIRED_DIRECT_ERROR_CODES = (
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_REQUESTED",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_SOCKS_CONNECTION_FAILED",
    "ERR_NO_SUPPORTED_PROXIES",
    "ERR_MANDATORY_PROXY_CONFIGURATION_FAILED",
    "ERR_PAC_SCRIPT_FAILED",
)
IP_EXPIRED_RETRY_ERROR_CODES = (
    "ERR_TIMED_OUT",
    "ERR_CONNECTION_TIMED_OUT",
)


def _read_chrome_network_error_code(driver) -> str:
    """讀取 Chrome 錯誤頁的穩定 ERR_* 代碼，不依賴顯示語言。"""
    chunks = []
    for reader in (
        lambda: driver.page_source,
        lambda: driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        ),
    ):
        try:
            chunks.append(str(reader() or ""))
        except Exception:
            continue
    text = "\n".join(chunks).upper()
    for code in IP_EXPIRED_DIRECT_ERROR_CODES + IP_EXPIRED_RETRY_ERROR_CODES:
        if code in text:
            return code
    return ""


def _confirmed_ip_expired_error_code(driver) -> str:
    """回傳確認後的代理／IP錯誤碼；單次一般逾時會先重載複查。"""
    initial = _read_chrome_network_error_code(driver)
    if initial in IP_EXPIRED_DIRECT_ERROR_CODES or not initial:
        return initial

    # Chrome 錯誤頁已完成一次連線逾時。用 CDP 非阻塞重新載入，避免
    # driver.refresh() 等到完整 page-load timeout；無 CDP 才用 refresh。
    reload_started = False
    try:
        driver.execute_cdp_cmd("Page.reload", {"ignoreCache": True})
        reload_started = True
    except Exception:
        try:
            driver.refresh()
            reload_started = True
        except Exception:
            pass
    if not reload_started:
        return initial

    time.sleep(0.8)
    deadline = time.monotonic() + 7.0
    last_error = initial
    clear_observations = 0
    while time.monotonic() < deadline:
        current = _read_chrome_network_error_code(driver)
        if current in IP_EXPIRED_DIRECT_ERROR_CODES:
            return current
        if current in IP_EXPIRED_RETRY_ERROR_CODES:
            last_error = current
            clear_observations = 0
        else:
            try:
                recovered = bool(driver.execute_script(
                    """
                    return location.hostname.endsWith('facebook.com') &&
                           ['interactive','complete'].includes(document.readyState) &&
                           !document.documentElement.innerHTML.includes('chrome-error://');
                    """
                ))
            except Exception:
                recovered = False
            clear_observations = clear_observations + 1 if recovered else 0
            if clear_observations >= 2:
                return ""
        time.sleep(0.35)
    return last_error


def configure_chrome_cookie_access(
    ctrl: BrowserController,
    profile_name: str,
) -> None:
    """啟動每個 AdsPower 環境後，允許 Facebook 使用 Chrome 儲存／Cookie 存取。

    說明：
    - Chrome CDP 沒有一個可在執行中直接把「所有 Cookie」總開關改成 Allow
      的單一指令。
    - 這裡使用 Chrome 支援的 Storage Access 權限，針對 Facebook 與
      Accounts Center 授權，降低第三方 Cookie／Storage Access 被阻擋的情況。
    - 若目前 Chromium 版本不支援其中某個 permission 名稱，會安全略過，
      不影響後續養號流程。
    """
    driver = getattr(ctrl, "driver", None)
    if driver is None:
        return

    origins = (
        "https://www.facebook.com",
        "https://facebook.com",
        "https://accountscenter.facebook.com",
        "https://www.messenger.com",
    )
    permission_names = (
        "storage-access",
        "top-level-storage-access",
    )

    granted = 0
    for origin in origins:
        for permission_name in permission_names:
            try:
                driver.execute_cdp_cmd(
                    "Browser.setPermission",
                    {
                        "permission": {"name": permission_name},
                        "setting": "granted",
                        "origin": origin,
                    },
                )
                granted += 1
            except Exception:
                continue

    if granted:
        _log.info(
            "[%s] 已套用 Chrome Facebook Cookie／Storage Access 權限（%d 項）。",
            profile_name,
            granted,
        )
    else:
        _log.info(
            "[%s] 目前 Chromium 不支援動態 Cookie／Storage Access 權限設定，安全略過。",
            profile_name,
        )


def _task_failed(ctrl: BrowserController, profile: ProfileInfo, task: str, reason: str) -> None:
    """八項任務統一錯誤入口；Reels 仍保留自身更細緻的階段診斷。"""
    driver = getattr(ctrl, "driver", None)
    save_task_diagnostic(driver, profile.name, task, reason)


def _professional_worker(
    stop_event: threading.Event | None = None,
) -> ProfessionalModeWorker:
    """建立共用的專業模式／個人主頁操作器，不另外啟停 AdsPower。"""
    return ProfessionalModeWorker(
        api=None,
        min_delay=1.0,
        max_delay=2.0,
        close_after=False,
        emit=lambda message: _log.info(message),
        stop_event=stop_event or threading.Event(),
    )


def return_to_personal_profile(
    ctrl: BrowserController,
    profile_name: str,
    stage: str,
    stop_event: threading.Event | None = None,
) -> bool:
    """使用 AdsPower 啟動頁快取的本人網址回到個人主頁。"""
    return return_to_personal_profile_via_timeline(
        ctrl,
        profile_name,
        stage,
        stop_event,
    )


def cache_personal_timeline_url(
    ctrl: BrowserController,
    profile_name: str,
    stop_event: threading.Event | None = None,
) -> str:
    """從目前 Facebook 首頁讀取並快取本人 Timeline 的固定 Profile URL。"""
    driver = ctrl.driver
    if driver is None:
        raise RuntimeError("Driver 不存在")
    # AdsPower 剛啟動時 renderer 可能能顯示首頁，卻無法在時限內穩定回應
    # 數十次 Selenium WebElement 查詢。改成一次 JavaScript 讀取所有連結，
    # 並以頂端本人頭像位置作為 Timeline 標籤尚未完成時的後備判斷。
    # 首次只等待 5 秒。若首頁骨架已顯示但本人 Timeline 連結尚未建立，
    # 按一次 F5 重新整理，再重新等待 5 秒；最多只重整一次，避免無限循環。
    deadline = time.monotonic() + 5
    refreshed_once = False
    last_error = ""
    while True:
        if time.monotonic() >= deadline:
            if refreshed_once:
                break
            _log.warning(
                "[%s] 5 秒內未取得本人個人主頁網址，按 F5 重新整理後重試。",
                profile_name,
            )
            try:
                body = driver.find_element(By.TAG_NAME, "body")
                body.send_keys(Keys.F5)
                _log.info("[%s] 已按 F5 重新整理 Facebook 首頁。", profile_name)
            except Exception as exc:
                _log.warning(
                    "[%s] F5 重新整理失敗，改用 driver.refresh()：%s",
                    profile_name,
                    exc,
                )
                try:
                    driver.refresh()
                except Exception as refresh_exc:
                    raise RuntimeError(
                        f"無法重新整理 Facebook 首頁：{refresh_exc}"
                    ) from refresh_exc
            refreshed_once = True
            last_error = ""
            deadline = time.monotonic() + 5
            time.sleep(0.6)
            continue
        if stop_event and stop_event.is_set():
            raise InterruptedError("使用者停止執行")
        try:
            rows = driver.execute_script(
                """
                const words = arguments[0];
                const visible = (element) => {
                    if (!element || !element.isConnected) return false;
                    const style = getComputedStyle(element);
                    if (
                        style.display === 'none' ||
                        style.visibility === 'hidden' ||
                        style.visibility === 'collapse'
                    ) return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                return Array.from(document.querySelectorAll('a[href]'))
                    .filter((anchor) => (
                        visible(anchor) &&
                        anchor.href.includes('profile.php?id=')
                    ))
                    .map((anchor) => {
                        const rect = anchor.getBoundingClientRect();
                        const label = (
                            anchor.getAttribute('aria-label') ||
                            anchor.getAttribute('title') ||
                            anchor.innerText ||
                            ''
                        ).replace(/\\s+/g, ' ').trim();
                        const folded = label.toLocaleLowerCase();
                        return {
                            href: anchor.href,
                            label,
                            timeline: words.some((word) => folded.includes(word)),
                            topAvatar: (
                                rect.top >= 0 &&
                                rect.top <= 180 &&
                                rect.width >= 20 &&
                                rect.width <= 90 &&
                                rect.height >= 20 &&
                                rect.height <= 90
                            ),
                        };
                    });
                """,
                (
                    "timeline",
                    "journal",
                    "chronologie",
                    "ไทม์ไลน์",
                    "يوميات",
                    "動態時報",
                    "动态时报",
                    "時間軸",
                    "时间线",
                ),
            ) or []
            last_error = ""
        except (TimeoutException, WebDriverException) as exc:
            last_error = (str(exc).splitlines() or [type(exc).__name__])[0].strip()
            try:
                ctrl.stop_loading()
            except Exception:
                pass
            time.sleep(0.5)
            continue

        labelled: dict[str, str] = {}
        top_avatars: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            href = str(row.get("href") or "")
            if "profile.php?id=" not in href:
                continue
            clean_url = href.split("&", 1)[0]
            if row.get("timeline"):
                labelled.setdefault(clean_url, str(row.get("label") or ""))
            if row.get("topAvatar"):
                top_avatars.setdefault(clean_url, str(row.get("label") or ""))
        candidates = labelled if len(labelled) == 1 else top_avatars
        if len(candidates) == 1:
            timeline_url = next(iter(candidates))
            driver._facebook_personal_profile_url = timeline_url
            if refreshed_once:
                _log.info(
                    "[%s] 重新整理後已取得本人個人主頁網址：%s",
                    profile_name,
                    timeline_url,
                )
            else:
                _log.info(
                    "[%s] 已從 Facebook 首頁讀取本人個人主頁網址：%s",
                    profile_name,
                    timeline_url,
                )
            return timeline_url
        time.sleep(0.4)
    detail = f"（最後一次 renderer 錯誤：{last_error}）" if last_error else ""
    raise RuntimeError(f"Facebook 首頁找不到唯一的本人 Timeline 連結{detail}")


def cache_current_personal_profile_url(
    ctrl: BrowserController,
    profile_name: str,
    stop_event: threading.Event | None = None,
    timeout: float = 6.0,
) -> str:
    """直接快取 AdsPower Facebook 分頁目前已開啟的本人 Profile URL。

    新流程假設 Facebook 第二分頁固定由本人個人主頁開始，不再導向
    ``www.facebook.com``，也不再從首頁頭像或 Timeline 連結反查。
    """
    driver = ctrl.driver
    if driver is None:
        raise RuntimeError("Driver 不存在")
    deadline = time.monotonic() + max(0.5, float(timeout))
    last_url = ""
    while time.monotonic() < deadline:
        if stop_event and stop_event.is_set():
            raise InterruptedError("使用者停止執行")
        try:
            last_url = str(driver.current_url or "").strip()
            match = re.match(
                r"^https?://(?:www\.)?facebook\.com/profile\.php\?id=(\d+)",
                last_url,
                flags=re.IGNORECASE,
            )
            page_ready = bool(driver.execute_script(
                "return document.readyState !== 'loading' && "
                "!!document.querySelector('[role=main]');"
            ))
        except (TimeoutException, WebDriverException):
            time.sleep(0.25)
            continue
        if match and page_ready:
            timeline_url = (
                "https://www.facebook.com/profile.php?id=" + match.group(1)
            )
            driver._facebook_personal_profile_url = timeline_url
            _log.info(
                "[%s] 已直接使用 AdsPower 啟動頁作為本人個人主頁：%s",
                profile_name,
                timeline_url,
            )
            return timeline_url
        time.sleep(0.25)
    raise RuntimeError(
        "AdsPower Facebook 啟動頁不是可確認的本人個人主頁"
        f"（目前網址：{last_url or '無法讀取'}）"
    )


def ensure_startup_personal_profile_url(
    ctrl: BrowserController,
    profile_name: str,
    stop_event: threading.Event | None = None,
) -> str:
    """確保Facebook工作分頁位於本人個人主頁並快取固定網址。

    AdsPower第一分頁是環境資訊，必須完整保留。本函式只操作目前已
    切入的Facebook分頁：若起始頁已是 ``profile.php?id=...`` 直接
    快取；否則開Facebook首頁，由頂端本人Timeline／頭像連結取得
    唯一個人網址，再導向並驗證。絕不使用不穩定的 ``/me``。
    """
    try:
        return cache_current_personal_profile_url(
            ctrl, profile_name, stop_event, timeout=1.2
        )
    except InterruptedError:
        raise
    except Exception as exc:
        _log.info(
            "[%s] Facebook起始頁不是本人個人主頁，改由首頁取得本人網址：%s",
            profile_name, exc,
        )

    if stop_event and stop_event.is_set():
        raise InterruptedError("使用者停止執行")

    _log.info(
        "[%s] 保留AdsPower環境資訊分頁，只在Facebook分頁開啟首頁讀取Timeline。",
        profile_name,
    )
    try:
        ctrl.navigate(FACEBOOK_HOME_URL)
    except (TimeoutException, WebDriverException) as exc:
        summary = (str(exc).splitlines() or [type(exc).__name__])[0].strip()
        _log.warning(
            "[%s] Facebook首頁載入較慢，停止等待並檢查已顯示內容：%s",
            profile_name, summary or type(exc).__name__,
        )
        try:
            ctrl.stop_loading()
        except Exception:
            pass

    timeline_url = cache_personal_timeline_url(ctrl, profile_name, stop_event)
    if not return_to_personal_profile_via_timeline(
        ctrl,
        profile_name,
        "啟動頁修正",
        stop_event,
    ):
        raise RuntimeError(f"已取得本人個人主頁網址但無法進入：{timeline_url}")
    _log.info(
        "[%s] 已由Facebook首頁取得本人網址並進入個人主頁：%s",
        profile_name, timeline_url,
    )
    return timeline_url


def return_to_personal_profile_via_timeline(
    ctrl: BrowserController,
    profile_name: str,
    stage: str,
    stop_event: threading.Event | None = None,
) -> bool:
    """使用 AdsPower 啟動頁直接快取的本人 Profile URL 返回個人主頁。"""
    driver = ctrl.driver
    if driver is None:
        _log.error("[%s] %s：Driver 不存在，無法回到個人主頁。", profile_name, stage)
        return False
    try:
        timeline_url = getattr(driver, "_facebook_personal_profile_url", "")
        if not timeline_url:
            _log.info(
                "[%s] %s：尚未快取個人主頁網址，嘗試使用目前啟動頁。",
                profile_name,
                stage,
            )
            timeline_url = cache_current_personal_profile_url(
                ctrl,
                profile_name,
                stop_event,
            )
        _log.info(
            "[%s] %s：使用已讀取的個人主頁網址：%s",
            profile_name,
            stage,
            timeline_url,
        )
        current_url = driver.current_url or ""
        if current_url.startswith(timeline_url):
            _log.info(
                "[%s] %s：目前已在本人個人主頁，不重複載入。",
                profile_name,
                stage,
            )
            return True
        try:
            driver.get(timeline_url)
        except (TimeoutException, WebDriverException) as exc:
            summary = (str(exc).splitlines() or [type(exc).__name__])[0].strip()
            _log.warning(
                "[%s] %s：個人主頁導向較慢，停止等待並檢查實際頁面：%s",
                profile_name,
                stage,
                summary or type(exc).__name__,
            )
            try:
                ctrl.stop_loading()
            except Exception:
                pass
        verify_deadline = time.monotonic() + 20
        while time.monotonic() < verify_deadline:
            if stop_event and stop_event.is_set():
                raise InterruptedError("使用者停止執行")
            try:
                current_url = driver.current_url or ""
            except (TimeoutException, WebDriverException):
                time.sleep(0.4)
                continue
            if "profile.php?id=" in current_url and current_url.startswith(timeline_url):
                _log.info(
                    "[%s] %s：已使用個人主頁網址進入 Profile：%s",
                    profile_name,
                    stage,
                    timeline_url,
                )
                return True
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text.casefold()
            except Exception:
                body_text = ""
            if "temporarily blocked" in body_text:
                raise RuntimeError("Facebook 顯示 Temporarily Blocked")
            time.sleep(0.4)
        raise RuntimeError("點擊本人 Timeline 後未進入個人主頁")
    except Exception as exc:
        summary = (str(exc).splitlines() or [type(exc).__name__])[0].strip()
        _log.warning(
            "[%s] %s：使用啟動頁快取網址回個人主頁失敗：%s",
            profile_name,
            stage,
            summary or type(exc).__name__,
        )
        return False


def run_professional_mode_task(
    ctrl: BrowserController,
    profile: ProfileInfo,
    stop_event: threading.Event | None = None,
) -> str:
    """使用既有瀏覽器連線執行專業模式，不重複啟停 AdsPower。"""
    if ctrl.driver is None:
        raise RuntimeError("Selenium Driver 不存在")
    worker = _professional_worker(stop_event)
    professional_profile = ProfessionalProfile(user_id=profile.profile_id, name=profile.name)
    _log.info("[%s] 開始執行「成為專業模式」。", profile.name)
    if worker.open_professional_mode_dialog(ctrl.driver):
        _log.info(
            "[%s] 三點選單出現「關閉專業模式」，確認已經是專業模式，本任務自動跳過。",
            profile.name,
        )
        return "skipped"
    worker.complete_wizard(ctrl.driver, professional_profile)
    _log.info("[%s] 成為專業模式設定完成。", profile.name)
    return "success"




def detect_human_verification_page(ctrl: BrowserController) -> bool:
    """
    偵測 Facebook「確認你是真人」驗證頁。

    只修改真人驗證偵測邏輯，其他登入、Dismiss、首頁、發文、Like、
    滑動、好友與循環流程全部不變。

    判斷方式：
    1. 支援原本中／英／法／菲律賓文完整句子。
    2. 英文新增支援「confirm that you're human」等變體。
    3. Checkpoint 網址搭配 human／真人／humain／tao 等關鍵字時判定。
    4. 畫面同時出現「確認類關鍵字」與「人類類關鍵字」時判定。
    5. 仍檢查 Continue／繼續／Continuer／Magpatuloy 按鈕，降低誤判。
    """
    driver = ctrl.driver
    if driver is None:
        return False

    try:
        return bool(driver.execute_script(
            r"""
            const bodyText=((document.body && document.body.innerText) || '')
                .replace(/\s+/g,' ').trim().toLowerCase();
            const title=(document.title || '').replace(/\s+/g,' ').trim().toLowerCase();
            const url=(location.href || '').toLowerCase();
            const combined=`${title} ${bodyText}`;

            const exactPhrases=[
                // English
                "confirm you're human to use your account",
                "confirm you are human to use your account",
                "confirm that you're human to use your account",
                "confirm that you are human to use your account",
                "confirm you're human",
                "confirm you are human",
                "confirm that you're human",
                "confirm that you are human",
                "please confirm you're human",
                "please confirm you are human",
                "please confirm that you're human",
                "please confirm that you are human",

                // 中文
                "確認你是真人", "确认你是真人",
                "請確認你是真人", "请确认你是真人",
                "確認你是本人", "确认你是本人",
                "請確認你是本人", "请确认你是本人",

                // Français
                "confirmez que vous êtes humain",
                "confirmer que vous êtes humain",
                "prouvez que vous êtes humain",
                "vérifiez que vous êtes humain",

                // Filipino / Tagalog
                "kumpirmahing tao ka",
                "kumpirmahin na tao ka",
                "patunayang tao ka",
                "kumpirmahin mong tao ka"
            ];

            const exactPhraseFound=exactPhrases.some(p => combined.includes(p));

            const confirmWords=[
                'confirm','verify','verification',
                '確認','确认','驗證','验证','證明','证明',
                'confirmez','confirmer','vérifiez','verifiez','prouvez',
                'kumpirmahin','kumpirmahing','patunayan','patunayang'
            ];

            const humanWords=[
                'human',
                '真人','本人','人類','人类',
                'humain',
                'tao'
            ];

            const hasConfirmWord=confirmWords.some(w => combined.includes(w));
            const hasHumanWord=humanWords.some(w => combined.includes(w));
            const keywordMatch=hasConfirmWord && hasHumanWord;

            const continueTerms=[
                'continue','繼續','继续','下一步',
                'continuer',
                'magpatuloy'
            ];

            const buttons=[...document.querySelectorAll(
                'button,[role="button"],input[type="submit"],input[type="button"],a[role="button"]'
            )];

            const hasContinue=buttons.some(b => {
                const t=((b.getAttribute('aria-label') || b.value || b.innerText || b.textContent || ''))
                    .replace(/\s+/g,' ').trim().toLowerCase();
                return continueTerms.some(x => t===x || t.includes(x));
            });

            const isCheckpoint=
                url.includes('/checkpoint/') ||
                url.includes('/checkpoint?') ||
                url.endsWith('/checkpoint');

            return (
                (exactPhraseFound && hasContinue) ||
                (keywordMatch && hasContinue) ||
                (isCheckpoint && hasHumanWord)
            );
            """
        ))
    except Exception as exc:
        _log.info("[HumanVerify] 驗證頁偵測失敗，略過：%s", exc)
        return False


def detect_account_removal_status(ctrl: BrowserController, profile: ProfileInfo) -> tuple[str, str]:
    """偵測需要停止目前流程的 Facebook／瀏覽器狀態。

    回傳 (狀態, 新環境名稱)。狀態為空字串代表正常。
    此功能固定啟用，不受 GUI 或設定檔控制。

    目前固定刪除：
    - Facebook 真人驗證
    - Facebook 帳號停權
    - Facebook 睡眠模式

    Chrome 代理驗證／連線錯誤，以及重試後仍存在的連線逾時，只更名
    為「IP到期＋原名稱」並關閉環境，絕不刪除。
    """
    driver = getattr(ctrl, "driver", None)
    if driver is None:
        return "", ""

    # 錯誤碼不受英文／中文／阿拉伯文等介面語言影響。代理專屬錯誤
    # 直接判定；一般逾時由 helper 重新載入一次後才判定。
    ip_error_code = _confirmed_ip_expired_error_code(driver)
    if ip_error_code:
        _log.warning(
            "[%s] 已確認代理／IP連線失效：%s",
            profile.name, ip_error_code,
        )
        return "tunnel_connection_failed", ip_expired_profile_name(
            profile.name, profile.profile_id
        )

    verification = detect_human_verification_result(driver)
    if verification.detected:
        return "verification", verification_profile_name(
            profile.name, profile.profile_id
        )

    account_status = detect_facebook_account_status(driver)
    if not account_status.detected:
        return "", ""
    if account_status.kind == "suspended":
        return "suspended", suspended_profile_name(
            profile.name, profile.profile_id
        )
    if account_status.kind == "sleep_mode":
        return "sleep_mode", sleep_mode_profile_name(
            profile.name, profile.profile_id
        )
    return "", ""


def prepare_profile_removal(
    adspower: AdsPowerClient,
    profile: ProfileInfo,
    status_kind: str,
    new_name: str,
) -> bool:
    """先更名；真正關閉與刪除由 run_profile 的 finally 安全完成。"""
    renamed = adspower.rename_profile(profile.profile_id, new_name)
    if renamed:
        _log.warning(
            "[%s] 偵測到 %s，環境已更名為「%s」，準備關閉並刪除。",
            profile.name, status_kind, new_name,
        )
    else:
        _log.warning(
            "[%s] 偵測到 %s，但環境更名失敗；為避免誤刪，本次不刪除。",
            profile.name, status_kind,
        )
    return bool(renamed)


def prepare_ip_expired_profile(
    adspower: AdsPowerClient,
    profile: ProfileInfo,
    new_name: str,
) -> bool:
    """Rename an expired proxy/Tunnel profile without ever authorizing deletion."""
    renamed = adspower.rename_profile(profile.profile_id, new_name)
    if renamed:
        _log.warning(
            "[%s] 偵測到代理／IP連線失效，環境已更名為「%s」；將關閉但不刪除。",
            profile.name,
            new_name,
        )
    else:
        _log.error(
            "[%s] 偵測到代理／IP連線失效，但更名為「%s」失敗；仍會關閉且不刪除。",
            profile.name,
            new_name,
        )
    return bool(renamed)


def mark_profile_as_verification(adspower: AdsPowerClient, profile: ProfileInfo) -> str:
    """將環境名稱改成「驗証+原環境名」，避免重複加前綴。"""
    original=(profile.name or '').strip() or profile.profile_id
    new_name=original if original.startswith('驗証') else f'驗証{original}'
    adspower.rename_profile(profile.profile_id, new_name)
    return new_name

def handle_login_and_dismiss(ctrl: BrowserController, profile_name: str) -> bool:
    """
    V8.2.3：依畫面元素判斷 Facebook 登入頁，並處理登入前／後可能直接出現的 Dismiss。

    支援中文、英文、法文、菲律賓文（Tagalog）。
    不只依賴網址；即使網址是 https://www.facebook.com/，只要同時看到帳號框、
    密碼框與登入按鈕，就判定為登入頁。

    此函式不輸入或修改帳號密碼，只使用瀏覽器中已經存在的欄位內容。
    """
    driver = ctrl.driver
    if driver is None:
        return False

    acted = False

    def wait_document_ready(timeout_sec: float = 12.0) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                if driver.execute_script("return document.readyState") in ("interactive", "complete"):
                    return
            except Exception:
                pass
            time.sleep(0.25)

    def get_login_state() -> dict:
        try:
            return driver.execute_script(
                r"""
                function visible(el){
                    if(!el)return false;
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>20&&r.height>20&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0;
                }
                function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                const email=[...document.querySelectorAll(
                    'input[name="email"],input[type="email"],input[autocomplete="username"],input[id*="email" i]'
                )].find(visible)||null;
                const pass=[...document.querySelectorAll(
                    'input[name="pass"],input[type="password"],input[autocomplete="current-password"]'
                )].find(visible)||null;
                const loginTerms=[
                    '登入','登录','登錄',
                    'log in','login','sign in',
                    'se connecter','connexion',
                    'mag-log in','mag-login','pumasok'
                ];
                const buttons=[...document.querySelectorAll(
                    'button,[role="button"],input[type="submit"],input[type="button"]'
                )].filter(visible);
                const loginButton=buttons.find(b=>{
                    const text=norm(b.getAttribute('aria-label')||b.getAttribute('value')||b.innerText||b.textContent||'');
                    return b.getAttribute('name')==='login'||loginTerms.some(t=>text===t);
                })||buttons.find(b=>norm(b.getAttribute('type'))==='submit')||null;
                return {
                    is_login:!!email&&!!pass&&!!loginButton,
                    has_email:!!email,
                    has_password:!!pass,
                    has_login_button:!!loginButton,
                    email_value:email?(email.value||'').trim():'',
                    password_length:pass?(pass.value||'').length:0
                };
                """
            ) or {}
        except Exception:
            return {}

    def click_login_button() -> bool:
        try:
            result = driver.execute_script(
                r"""
                function visible(el){
                    if(!el)return false;
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>20&&r.height>20&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0&&
                           !el.disabled&&el.getAttribute('aria-disabled')!=='true';
                }
                function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                const terms=[
                    '登入','登录','登錄',
                    'log in','login','sign in',
                    'se connecter','connexion',
                    'mag-log in','mag-login','pumasok'
                ];
                const nodes=[...document.querySelectorAll(
                    'button,[role="button"],input[type="submit"],input[type="button"]'
                )].filter(visible);
                const candidates=[];
                for(const node of nodes){
                    const text=norm(node.getAttribute('aria-label')||node.getAttribute('value')||node.innerText||node.textContent||'');
                    let score=0;
                    if(node.getAttribute('name')==='login')score+=20000;
                    if(terms.includes(text))score+=15000;
                    if(norm(node.getAttribute('type'))==='submit')score+=5000;
                    if(score>0)candidates.push({node,score,text});
                }
                if(!candidates.length)return {ok:false,text:''};
                candidates.sort((a,b)=>b.score-a.score);
                const target=candidates[0];
                target.node.scrollIntoView({block:'center',inline:'center'});
                try{target.node.click();}
                catch(e){target.node.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));}
                return {ok:true,text:target.text};
                """
            ) or {}
            if result.get("ok"):
                _log.info("[%s] 已按下 Facebook 登入按鈕：%s", profile_name, result.get("text") or "Login")
                return True
        except Exception as exc:
            _log.info("[%s] 登入按鈕偵測失敗：%s", profile_name, exc)
        return False

    def click_dismiss_button() -> tuple[bool, str]:
        """只在最上層可見彈窗／中繼頁按精確的 Dismiss 類按鈕，避免誤點 Facebook menu。"""
        try:
            result = driver.execute_script(
                r"""
                function visible(el){
                    if(!el)return false;
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>20&&r.height>20&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0&&
                           !el.disabled&&el.getAttribute('aria-disabled')!=='true';
                }
                function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                const terms=[
                    '關閉','关闭','稍後再說','稍后再说','略過','略过','跳過','跳过','取消',
                    'dismiss','close','not now','skip','cancel',
                    'fermer','ignorer','plus tard','pas maintenant','annuler',
                    'isara','laktawan','hindi ngayon','mamaya na','kanselahin'
                ];
                const dialogs=[...document.querySelectorAll('[role="dialog"],[aria-modal="true"]')].filter(visible);
                let root=dialogs.length?dialogs[dialogs.length-1]:null;

                // 一開啟就直接顯示 Dismiss 中繼頁時可能沒有 role=dialog。
                // 此時只允許精確文字，且整頁可見操作按鈕數不能太多，避免誤點首頁選單。
                if(!root){
                    const pageButtons=[...document.querySelectorAll(
                        'button,[role="button"],input[type="button"],input[type="submit"],a[role="button"]'
                    )].filter(visible);
                    const exact=pageButtons.filter(n=>{
                        const t=norm(n.getAttribute('aria-label')||n.getAttribute('value')||n.innerText||n.textContent||'');
                        return terms.includes(t);
                    });
                    if(exact.length===0||pageButtons.length>18)return {ok:false,text:''};
                    root=document.body;
                }

                const nodes=[...root.querySelectorAll(
                    'button,[role="button"],input[type="button"],input[type="submit"],a[role="button"]'
                )].filter(visible);
                const candidates=[];
                for(const node of nodes){
                    const text=norm(node.getAttribute('aria-label')||node.getAttribute('value')||node.innerText||node.textContent||'');
                    if(terms.includes(text))candidates.push({node,text});
                }
                if(!candidates.length)return {ok:false,text:''};
                const target=candidates[0];
                target.node.scrollIntoView({block:'center',inline:'center'});
                try{target.node.click();}
                catch(e){target.node.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));}
                return {ok:true,text:target.text};
                """
            ) or {}
            return bool(result.get("ok")), str(result.get("text") or "")
        except Exception:
            return False, ""

    wait_document_ready(12.0)

    # 可能一開啟就直接出現 Dismiss，中／英／法／菲律賓文都支援。
    clicked, text = click_dismiss_button()
    if clicked:
        acted = True
        _log.info("[%s] 已按下登入前／直接出現的頁面按鈕：%s", profile_name, text or "Dismiss")
        time.sleep(1.2)
        wait_document_ready(8.0)

    # 不靠網址，只有帳號框＋密碼框＋登入鍵同時存在才判定為登入頁。
    state = get_login_state()
    if state.get("is_login"):
        email_value = str(state.get("email_value") or "")
        password_length = int(state.get("password_length") or 0)
        if email_value and password_length > 0:
            _log.info("[%s] 依畫面元素確認為 Facebook 登入頁，等待載入完成後按登入。", profile_name)
            if click_login_button():
                acted = True
                deadline = time.time() + 30.0
                while time.time() < deadline:
                    time.sleep(0.6)
                    if not get_login_state().get("is_login"):
                        break
        else:
            _log.warning("[%s] 偵測到登入頁，但帳號或密碼欄位沒有完整資料，不自動送出。", profile_name)

    # 登入後可能再出現一次 Dismiss；最多處理 2 次精確匹配按鈕。
    for _ in range(2):
        clicked, text = click_dismiss_button()
        if not clicked:
            break
        acted = True
        _log.info("[%s] 已按下登入後頁面按鈕：%s", profile_name, text or "Dismiss")
        time.sleep(1.2)

    return acted

def is_facebook_url_safe(ctrl: BrowserController) -> bool:
    """弱網路模式：只要目前網址是 facebook.com，就視為已在 Facebook，不強制重新整理。"""
    try:
        url = ctrl.current_url().lower()
        return "facebook.com" in url
    except Exception:
        return False


def ensure_facebook_ready(ctrl: BrowserController, profile_name: str) -> bool:
    """
    V1.3.5 弱網路首頁策略：
    - 先切 Facebook 分頁。
    - 如果已經在 facebook.com，絕不 driver.get，不 F5。
    - 只有完全不在 Facebook 時，才嘗試導向首頁。
    """
    _log.info("[%s] 切換至 Facebook 分頁。", profile_name)

    try:
        ctrl.switch_to_facebook_tab()
    except Exception as exc:
        _log.warning("[%s] 切換 Facebook 分頁失敗：%s", profile_name, exc)

    if is_facebook_url_safe(ctrl):
        try:
            _log.info("[%s] 已在 Facebook，不重新整理。目前URL：%s", profile_name, ctrl.current_url())
        except Exception:
            _log.info("[%s] 已在 Facebook，不重新整理。", profile_name)
        random_sleep(1.0, 2.0)
        return True

    _log.info("[%s] 目前不在 Facebook，嘗試開啟首頁。", profile_name)
    try:
        ctrl.navigate(FACEBOOK_HOME_URL)
        random_sleep(2.0, 3.0)
        _log.info("[%s] 已開啟 Facebook 首頁。", profile_name)
        return True
    except Exception as exc:
        _log.warning("[%s] Facebook 首頁載入失敗，直接跳過：%s", profile_name, exc)
        return False



# AdsPower Profile 系列順序改為動態產生：只顯示「第一個字是中文」且「名稱包含數字」的系列。

# 首頁載入與網路錯誤快速判斷設定
HOME_LOAD_TIMEOUT_SEC = 6
HOME_WAIT_MAX_SEC = 5

NETWORK_ERROR_SIGNALS: list[str] = [
    # 英文
    "err_timed_out",
    "err_connection_reset",
    "err_connection_timed_out",
    "err_name_not_resolved",
    "this site can't be reached",
    "this page isn't available",
    "took too long to respond",
    "unable to connect",
    # 中文
    "無法連線",
    "網站無法使用",
    "回應時間過長",
    "找不到伺服器",
    "連線已重設",
    "連線逾時",
    # 菲律賓文 / Chrome 菲律賓語系
    "hindi makakonekta sa site na ito",
    "masyadong matagal bago nakatugon",
    "hindi available ang page na ito",
    "hindi matukoy ang dns address",
    # 法文
    "ce site est inaccessible",
    "cette page n’est pas disponible",
    "cette page n'est pas disponible",
    # 泰文
    "ไม่สามารถเข้าถึงเว็บไซต์นี้",
    "หน้านี้ไม่พร้อมใช้งาน",
    # 阿拉伯文
    "يتعذر الوصول إلى موقع الويب هذا",
    "هذه الصفحة غير متوفرة",
]

HOME_READY_SIGNALS: list[str] = [
    "what's on your mind",
    "create story",
    "contacts",
    "news feed",
    "home",
    "facebook",
    "你在想什麼",
    "建立限時動態",
    "聯絡人",
    "qu’avez-vous en tête",
    "qu'avez-vous en tête",
    "créer une story",
    "accueil",
    "คุณกำลังคิดอะไรอยู่",
    "สร้างสตอรี่",
    "หน้าหลัก",
    "بم تفكر",
    "إنشاء قصة",
    "الصفحة الرئيسية",
]


def page_has_network_error(ctrl: BrowserController) -> tuple[bool, str]:
    """快速判斷目前頁面是否為 Chrome / Facebook 網路錯誤頁。"""
    try:
        url = ctrl.current_url().lower()
        source = ctrl.page_source().lower()
    except Exception as exc:
        return True, f"讀取頁面失敗：{exc}"

    for signal in NETWORK_ERROR_SIGNALS:
        if signal in url or signal in source:
            return True, signal

    return False, ""


def wait_home_ready_or_network_error(ctrl: BrowserController, max_wait: int) -> tuple[bool, str]:
    """
    等待 Facebook 首頁載入。

    Returns:
        (True, "") 表示首頁已載入或可繼續 Health Check。
        (False, reason) 表示偵測到網路錯誤。
    """
    for _ in range(max_wait):
        has_error, reason = page_has_network_error(ctrl)
        if has_error:
            return False, reason

        try:
            source = ctrl.page_source().lower()
            url = ctrl.current_url().lower()
        except Exception:
            random_sleep(0.8, 1.2)
            continue

        if "facebook.com" in url and any(signal in source for signal in HOME_READY_SIGNALS):
            return True, ""

        random_sleep(0.8, 1.2)

    # 超過等待時間仍未明確載入，交給 Health Check 做最後判斷。
    return True, ""


def go_home_fast_or_skip(ctrl: BrowserController, profile_name: str) -> tuple[bool, str]:
    """
    V7.3 首頁策略：
    - 只有「網址確實是 Facebook 首頁」且首頁 DOM 已出現才算成功。
    - Messenger、個人頁、通知頁等 facebook.com 子頁面都會強制返回首頁。
    - 弱網路最多重試 2 次；即使 navigate 逾時，也會檢查實際頁面。
    """
    _log.info("[%s] 返回 Facebook 首頁。", profile_name)

    try:
        ctrl.switch_to_facebook_tab()
    except Exception:
        pass

    def home_ready() -> bool:
        try:
            url = ctrl.current_url().lower()
            if "facebook.com" not in url:
                return False

            # 必須是首頁路徑，不接受 messages、profile、notifications 等子頁。
            path = url.split("facebook.com", 1)[1].split("?", 1)[0].split("#", 1)[0]
            if path not in ("", "/"):
                return False

            return bool(ctrl.driver.execute_script(  # type: ignore[union-attr]
                r"""
                return !!(
                    document.querySelector('[role="feed"]') ||
                    document.querySelector('[data-pagelet*="FeedUnit"]') ||
                    document.querySelector('[role="main"] [aria-posinset]') ||
                    document.querySelector('[role="main"]')
                );
                """
            ))
        except Exception:
            return False

    if home_ready():
        _log.info("[%s] 已確認目前位於 Facebook 首頁。", profile_name)
        return True, ""

    old_timeout = None
    try:
        if ctrl.driver:
            old_timeout = CONFIG.browser.page_load_timeout
            ctrl.driver.set_page_load_timeout(HOME_LOAD_TIMEOUT_SEC)
    except Exception:
        old_timeout = None

    try:
        last_reason = ""
        for attempt in range(1, 3):
            _log.info("[%s] 開啟 Facebook 首頁（第 %d/2 次）。", profile_name, attempt)
            try:
                ctrl.navigate(FACEBOOK_HOME_URL)
            except Exception as exc:
                last_reason = str(exc)
                _log.warning("[%s] 首頁導向較慢，停止等待並檢查實際頁面：%s", profile_name, exc)
                try:
                    ctrl.stop_loading()
                except Exception:
                    pass

            deadline = time.time() + 10.0
            while time.time() < deadline:
                has_error, reason = page_has_network_error(ctrl)
                if has_error:
                    last_reason = reason
                    break

                if home_ready():
                    _log.info("[%s] 已確認進入 Facebook 首頁。", profile_name)
                    return True, ""

                random_sleep(0.5, 0.8)

            if attempt < 2:
                random_sleep(1.5, 2.5)

        return False, f"無法進入 Facebook 首頁：{last_reason or '首頁元素未出現'}"

    finally:
        try:
            if ctrl.driver and old_timeout:
                ctrl.driver.set_page_load_timeout(old_timeout)
        except Exception:
            pass


def is_chinese_char(char: str) -> bool:
    """判斷單一字元是否為中文。"""
    return bool(char) and "\u4e00" <= char <= "\u9fff"


def profile_has_digit(profile_name: str) -> bool:
    """Profile 名稱內必須包含數字，沒有數字不執行。"""
    return any(ch.isdigit() for ch in profile_name or "")


def classify_profile_series(profile_name: str) -> str:
    """
    依 Profile 名稱第一個字分類。

    規則：
    - 只看名稱第一個字。
    - 第一個字必須是中文，才會建立系列。
    - 名稱內必須包含數字，否則不執行。
    - 不符合者回傳空字串，後續不列入可選系列。
    """
    name = (profile_name or "").strip()
    if not name:
        return ""

    first_char = name[:1]
    if not is_chinese_char(first_char):
        return ""

    if not profile_has_digit(name):
        return ""

    return first_char


def extract_profile_number(profile_name: str) -> int | None:
    """取得 Profile 名稱最後一段數字，例如「訊息0501」→ 501。"""
    name = (profile_name or "").strip()
    match = re.search(r"(\d+)\s*$", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def ask_single_series_range(profiles: list[ProfileInfo]) -> list[ProfileInfo]:
    """
    單一系列才詢問起始與結束號碼。

    Enter 規則：
    - 開始號碼按 Enter：從此系列第一個號碼開始。
    - 結束號碼按 Enter：跑到此系列最後一個號碼。
    """
    numbered: list[tuple[int, ProfileInfo]] = []

    for profile in profiles:
        number = extract_profile_number(profile.name)
        if number is not None:
            numbered.append((number, profile))

    if not numbered:
        _log.warning("此系列找不到名稱尾端號碼，跳過範圍詢問並執行全部。")
        return profiles

    numbered.sort(key=lambda item: (item[0], item[1].name))
    available_min = numbered[0][0]
    available_max = numbered[-1][0]

    print("\n" + "=" * 60)
    print("單一系列號碼範圍設定")
    print(f"目前可用號碼：{available_min} ～ {available_max}")
    print(f"開始號碼按 Enter = {available_min}")
    print(f"結束號碼按 Enter = {available_max}")
    print("=" * 60)

    while True:
        start_raw = input(f"1. 從幾號開始跑（Enter={available_min}）：").strip()
        end_raw = input(f"2. 跑到幾號（Enter={available_max}）：").strip()

        if start_raw and not start_raw.isdigit():
            print("開始號碼請輸入純數字，或直接按 Enter。")
            continue
        if end_raw and not end_raw.isdigit():
            print("結束號碼請輸入純數字，或直接按 Enter。")
            continue

        start_no = int(start_raw) if start_raw else available_min
        end_no = int(end_raw) if end_raw else available_max

        if start_no > end_no:
            print("起始號碼不能大於結束號碼，請重新輸入。")
            continue

        selected = [profile for number, profile in numbered if start_no <= number <= end_no]
        if not selected:
            print(f"找不到號碼 {start_no}～{end_no} 的 Profile，請重新輸入。")
            continue

        selected.sort(key=lambda profile: (extract_profile_number(profile.name) or 0, profile.name))
        selected_numbers = {
            number for profile in selected
            if (number := extract_profile_number(profile.name)) is not None
        }
        expected_count = end_no - start_no + 1
        missing_count = max(0, expected_count - len(selected_numbers))

        print("\n已建立本輪 Profile 清單：")
        print(f"  範圍：{start_no} ～ {end_no}")
        print(f"  實際找到：{len(selected)} 個 Profile")
        print(f"  第一個：{selected[0].name}")
        print(f"  最後一個：{selected[-1].name}")
        if missing_count:
            print(f"  缺少：{missing_count} 個號碼（自動略過不存在的 Profile）")

        _log.info(
            "單一系列執行範圍：%d～%d，實際 %d 個 Profile，缺少 %d 個號碼。",
            start_no,
            end_no,
            len(selected),
            missing_count,
        )
        return selected


def group_profiles_by_series(
    profiles: list[ProfileInfo],
) -> tuple[dict[str, list[ProfileInfo]], list[tuple[ProfileInfo, str]]]:
    """
    依第一個中文字分組 Profile。

    Returns:
        grouped: 可執行 Profile 分組。
        skipped: 被跳過的 Profile 與原因。
    """
    grouped: dict[str, list[ProfileInfo]] = {}
    skipped: list[tuple[ProfileInfo, str]] = []

    for profile in profiles:
        name = (profile.name or "").strip()
        if not name:
            skipped.append((profile, "名稱空白"))
            continue

        first_char = name[:1]
        if not is_chinese_char(first_char):
            skipped.append((profile, "第一個字不是中文"))
            continue

        if not profile_has_digit(name):
            skipped.append((profile, "名稱未包含數字"))
            continue

        grouped.setdefault(first_char, []).append(profile)

    # 依中文系列名稱排序，讓選單固定穩定。
    grouped = dict(sorted(grouped.items(), key=lambda item: item[0]))
    return grouped, skipped


def choose_profile_series(
    profiles: list[ProfileInfo],
) -> list[ProfileInfo]:
    """
    讓使用者選擇要執行的 Profile 系列。

    支援：
    - 單選：1
    - 多選：2+3、1+2+4
    - 全部：全部 / all / a
    - 離開：q

    注意：
    - 選單只顯示第一個字是中文，且名稱內包含數字的 Profile。
    - 名稱沒有數字不執行。
    - 第一個字不是中文不執行。
    """
    grouped, skipped = group_profiles_by_series(profiles)
    series_order = list(grouped.keys())

    if skipped:
        _log.info("已排除 %d 個不符合命名規則的 Profile。", len(skipped))
        for profile, reason in skipped[:30]:
            _log.info("跳過 Profile：%s（id=%s，原因：%s）", profile.name, profile.profile_id, reason)
        if len(skipped) > 30:
            _log.info("另有 %d 個被跳過的 Profile 未逐筆顯示。", len(skipped) - 30)

    if not series_order:
        _log.warning("沒有任何可執行系列：Profile 第一個字需為中文，且名稱內需包含數字。")
        sys.exit(0)

    while True:
        print("\n" + "=" * 60)
        print("可用系列：")
        for idx, series in enumerate(series_order, start=1):
            print(f"{idx}. {series} ({len(grouped[series])})")
        print("=" * 60)
        print("請輸入要執行的系列，例如：1、2+3、1+2+4、全部、Q")

        user_input = input("請輸入：").strip()

        if not user_input:
            print("沒有輸入，請重新輸入。")
            continue

        if user_input.lower() == "q":
            _log.info("使用者取消執行。")
            sys.exit(0)

        if user_input in {"全部", "全", "all", "ALL", "a", "A"}:
            selected_names = series_order
        else:
            raw_parts = user_input.replace("，", "+").replace(",", "+").split("+")
            selected_indexes: list[int] = []

            for raw in raw_parts:
                raw = raw.strip()
                if not raw.isdigit():
                    print(f"輸入格式錯誤：{raw}，請重新輸入。")
                    selected_indexes = []
                    break

                idx = int(raw)
                if idx < 1 or idx > len(series_order):
                    print(f"系列編號超出範圍：{idx}，請重新輸入。")
                    selected_indexes = []
                    break

                if idx not in selected_indexes:
                    selected_indexes.append(idx)

            if not selected_indexes:
                continue

            selected_names = [series_order[idx - 1] for idx in selected_indexes]

        selected_profiles: list[ProfileInfo] = []
        for series in selected_names:
            selected_profiles.extend(grouped[series])

        if not selected_profiles:
            print("你選擇的系列沒有任何 Profile，請重新選擇。")
            continue

        print("\n你選擇：" + "、".join(selected_names))
        print(f"共 {len(selected_profiles)} 個 Profile")
        _log.info(
            "使用者選擇系列：%s，共 %d 個 Profile。",
            "+".join(selected_names),
            len(selected_profiles),
        )
        return selected_profiles


# ─────────────────────────────────────────────
# 單一 Profile 執行流程
# ─────────────────────────────────────────────

def run_profile(
    adspower: AdsPowerClient,
    profile: ProfileInfo,
    comment_gen: CommentGenerator,
    enable_add_friend: bool,
    enable_confirm_friend: bool,
    enable_post: bool,
    post_text_file: str,
    post_media_enabled: bool,
    post_media_mode: str,
    post_random_media_dir: str,
    post_fixed_media_file: str,
    enable_browse_like: bool,
    like_count: int,
    enable_avatar: bool,
    enable_banner: bool,
    enable_profile_name: bool,
    enable_facebook_language: bool,
    avatar_dir: str,
    banner_dir: str,
    name_text_file: str,
    facebook_language_target: str,
    enable_pin: bool,
    enable_professional_mode: bool,
    enable_reels: bool = False,
    reels_dry_run: bool = False,
    enable_reels_comment: bool = False,
    reels_comment_mode: str = "default",
    reels_comment_text_file: str = "reels_comment.txt",
    reels_video_dir: str = r"C:\Users\USER\Desktop\reelsv",
    reels_text_file: str = r"C:\Users\USER\Desktop\reelsw.txt",
    close_after: bool = False,
    bring_to_front: bool = True,
    stop_event: threading.Event | None = None,
    enable_fanpage_message: bool = False,
    enable_query_chats: bool = False,
    enable_reply_chats: bool = False,
    fanpage_url_file: str = "kolurl.txt",
    fanpage_text_file: str = "文二.txt",
    fanpage_mode: str = "txt",
    fanpage_max_urls: int = 1,
    chat_database: str = "chat_tasks.db",
    query_max_chats: int = 5,
    query_unread_only: bool = False,
    reply_text_file: str = "文一.txt",
    reply_mode: str = "txt",
    reply_max_count: int = 3,
    reply_max_retries: int = 3,
    enable_telegram_report: bool = False,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    telegram_reply_chat_id: str = "",
    task_stats: TaskStatsRegistry | None = None,
) -> None:
    """
    對單一 Profile 執行完整的養號流程。
    任何非致命錯誤均記錄後繼續，不中斷整體執行。

    Args:
        adspower:          AdsPower API 用戶端。
        profile:           目標 Profile 資訊。
        comment_gen:       OpenAI 留言產生器（多個 Profile 共用）。
        enable_add_friend: 是否主動送出好友邀請。
        enable_confirm_friend: 是否接受收到的好友邀請。
        enable_post:       是否在自己的動態時報 PO 文。
        post_text_file:    RC19 格式的隨機 PO 文文案 TXT；空白時使用文案.xlsx。
        post_media_enabled: 是否在 PO 文附加相片／影片。
        post_media_mode:   random 隨機資料夾或 fixed 固定檔案。
        enable_browse_like: 是否瀏覽首頁並按讚。
        like_count:        每個 Profile 預計按讚數量。
        enable_avatar:     是否自動更換 Facebook 頭像。
        enable_banner:     是否更換 Facebook Banner。
        enable_facebook_language: 是否統一為 Filipino。
        banner_dir:        Banner 圖片資料夾。
        enable_pin:        是否處理 Messenger PIN。
        enable_professional_mode: 是否開啟 Facebook 專業模式。
        enable_reels:      是否發布環境編號對應的 Reels。
        reels_dry_run:     是否跑完整 Reels 流程但停在最終發布前。
        enable_reels_comment: 是否在個人主頁第一篇貼文留言。
    """
    summary = ProfileSummary(
        profile_id=profile.profile_id,
        profile_name=profile.name,
    )
    profile_log_path = start_profile_log(profile.name)
    _log.info("[%s] 本 Profile 完整 LOG：%s", profile.name, profile_log_path)
    _log.info("=" * 60)
    _log.info("開始處理 Profile：%s（id=%s）", profile.name, profile.profile_id)

    # ── 步驟 1：已開啟則直接接管，未開啟則自動開啟 ──
    try:
        session = adspower.get_or_open_browser(profile.profile_id)
    except RuntimeError as exc:
        _log.error("無法開啟 Browser（Profile=%s）：%s", profile.name, exc)
        summary.finish(success=False, reason=f"Browser 開啟失敗：{exc}")
        stop_profile_log()
        return

    # ── 步驟 2：Selenium 連線 ───────────────────
    ctrl = BrowserController(session)
    delete_after_detection = False
    detected_removal_name = ""
    detected_removal_kind = ""
    try:
        ctrl.connect()
    except Exception as first_exc:
        ctrl.detach_keep_browser()
        error_text = str(first_exc).casefold()
        recoverable = any(marker in error_text for marker in (
            "read timed out",
            "connection refused",
            "cannot connect to chrome",
            "chrome not reachable",
            "disconnected",
        ))
        if recoverable and not (stop_event and stop_event.is_set()):
            _log.warning(
                "[%s] AdsPower Selenium 偵錯通道失效，將只重啟本環境後重試一次：%s",
                profile.name,
                first_exc,
            )
            try:
                adspower.close_browser(profile.profile_id)
                inactive_deadline = time.monotonic() + 12
                while (
                    time.monotonic() < inactive_deadline
                    and adspower.check_status(profile.profile_id)
                ):
                    if stop_event and stop_event.wait(0.4):
                        raise InterruptedError("使用者停止執行")
                    time.sleep(0.1)
                session = adspower.get_or_open_browser(profile.profile_id)
                ctrl = BrowserController(session)
                ctrl.connect()
                _log.info("[%s] 重啟本環境後 Selenium 已恢復連線。", profile.name)
            except Exception as retry_exc:
                ctrl.detach_keep_browser()
                _log.error(
                    "Selenium 重啟重試仍失敗（Profile=%s）：%s",
                    profile.name,
                    retry_exc,
                )
                summary.finish(
                    success=False,
                    reason=f"Selenium 連線失敗：{retry_exc}",
                )
                stop_profile_log()
                return
        else:
            _log.error("Selenium 連線失敗（Profile=%s）：%s", profile.name, first_exc)
            _log.info("[%s] Selenium 連線失敗，但 AdsPower 環境保持開啟。", profile.name)
            summary.finish(success=False, reason=f"Selenium 連線失敗：{first_exc}")
            stop_profile_log()
            return

    try:
        # 每個環境接管成功後先套用 Chrome Cookie／Storage Access 權限。
        configure_chrome_cookie_access(ctrl, profile.name)

        # ── 步驟 3：切換 Facebook 分頁，直接使用既有個人主頁 ──
        _log.info("[%s] 切換至 Facebook 分頁。", profile.name)
        ctrl.switch_to_facebook_tab()
        if bring_to_front:
            if not ctrl.bring_window_to_front():
                _log.warning(
                    "[%s] 已執行 AdsPower 視窗置前，但未能驗證為 Windows "
                    "目前前景視窗。",
                    profile.name,
                )
        if stop_event and stop_event.wait(0.5):
            _log.info("[%s] 已收到停止要求，結束目前環境。", profile.name)
            return

        # 固定啟用：真人驗證／停權／睡眠模式會更名、關閉並刪除；
        # 代理／IP失效只會更名為 IP到期、關閉，絕不刪除。
        detected_removal_kind, detected_removal_name = detect_account_removal_status(
            ctrl, profile
        )
        if detected_removal_kind:
            if detected_removal_kind == "tunnel_connection_failed":
                prepare_ip_expired_profile(
                    adspower, profile, detected_removal_name
                )
            else:
                delete_after_detection = prepare_profile_removal(
                    adspower, profile, detected_removal_kind, detected_removal_name
                )
            summary.finish(
                success=False,
                reason=f"偵測到帳號狀態：{detected_removal_kind}",
            )
            return

        # 可能情況：
        # 1. 直接是登入頁 → 等待載入 → 按登入 → 按 Dismiss。
        # 2. 一開始就是 Dismiss 頁 → 直接按下。
        handle_login_and_dismiss(ctrl, profile.name)

        # 先確認Facebook工作分頁是否為本人個人主頁。若AdsPower起始頁
        # 停在首頁、Messenger、通知、Reels或其他Facebook頁面，保留
        # 第一個環境資訊分頁，只在Facebook分頁由首頁取得本人Timeline
        # 網址、導向並驗證；成功後才繼續任務。
        try:
            ensure_startup_personal_profile_url(ctrl, profile.name, stop_event)
        except Exception as exc:
            _log.warning(
                "[%s] 無法取得或進入本人個人主頁，跳過此環境：%s",
                profile.name, exc,
            )
            summary.finish(success=False, reason=f"無法進入本人個人主頁：{exc}")
            return

        # 登入／導頁後狀態可能才出現，因此在 Health Check 前再檢查一次。
        detected_removal_kind, detected_removal_name = detect_account_removal_status(
            ctrl, profile
        )
        if detected_removal_kind:
            if detected_removal_kind == "tunnel_connection_failed":
                prepare_ip_expired_profile(
                    adspower, profile, detected_removal_name
                )
            else:
                delete_after_detection = prepare_profile_removal(
                    adspower, profile, detected_removal_kind, detected_removal_name
                )
            summary.finish(
                success=False,
                reason=f"偵測到帳號狀態：{detected_removal_kind}",
            )
            return

        # ── 步驟 4：Health Check ──────────────────
        _log.info("[%s] 等待目前個人主頁必要元素載入（最長 5 秒）。", profile.name)
        ready_deadline = time.monotonic() + 5
        while time.monotonic() < ready_deadline:
            if stop_event and stop_event.is_set():
                _log.info("[%s] 已收到停止要求，結束目前環境。", profile.name)
                return
            try:
                if ctrl.driver.execute_script(
                    """
                    return document.readyState === 'complete' &&
                        !!document.querySelector('[role=main]');
                    """
                ):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        _log.info("[%s] 執行 Health Check。", profile.name)
        checker = HealthChecker(ctrl)
        status, detail = checker.check()
        _log.info("[%s] Health Check 結果：%s（%s）", profile.name, status.value, detail)

        if status != HealthStatus.HEALTHY:
            # Health Check 若明確判定為 Facebook 登入頁面，
            # 視為此環境帳號已失效，沿用異常帳號流程：
            # 先更名，再於 finally 關閉 AdsPower 並永久刪除。
            detail_text = str(detail or "").strip()
            detail_folded = detail_text.casefold()
            login_page_detected = (
                "偵測到登入頁面" in detail_text
                or "检测到登录页面" in detail_text
                or "登入頁面" in detail_text
                or "登录页面" in detail_text
                or "login page" in detail_folded
                or "log in page" in detail_folded
            )

            if login_page_detected:
                original = (profile.name or "").strip() or profile.profile_id
                detected_removal_kind = "login_page"
                detected_removal_name = (
                    original if original.startswith("登入") else f"登入{original}"
                )
                delete_after_detection = prepare_profile_removal(
                    adspower,
                    profile,
                    detected_removal_kind,
                    detected_removal_name,
                )
                summary.finish(
                    success=False,
                    reason=f"Health Check 失敗：{detail_text}",
                )
                return

            _log.warning("[%s] 帳號不健康，跳過此 Profile。", profile.name)
            summary.finish(success=False, reason=f"Health Check 失敗：{detail}")
            return

        # 所有已勾選任務只在第一項開始前處理一次 Chrome 原生通知權限。
        # Allow／Block 屬於瀏覽器介面，不在 Facebook DOM 中；使用 CDP
        # 等同按下 Allow，並保留約 3 秒讓原生提示與頁面遮罩完全消失。
        _log.info("[%s] 第一項任務開始前，先允許 Facebook 通知權限。", profile.name)
        allow_facebook_notifications(ctrl.driver, profile.name)
        if stop_event and stop_event.wait(0.6):
            _log.info("[%s] 已收到停止要求，結束目前環境。", profile.name)
            return

        # ── 最高優先任務：Facebook 介面語言 ─────────
        # 語言必須先統一，後續所有任務才能使用一致的文字與版面辨識。
        # 此流程可直接由任意 Facebook 頁面進入語言設定，不必先繞回
        # 個人主頁；完成後仍回到本人個人主頁再開始其他任務。
        if enable_facebook_language:
            _log.info(
                "[%s] 已啟用語言切換，優先於所有其他任務執行：%s。",
                profile.name, facebook_language_target,
            )
            try:
                if set_facebook_language(
                    ctrl.driver, profile.name, facebook_language_target
                ):
                    summary.add_action(f"語言切換 {facebook_language_target}")
            except Exception as exc:
                _log.exception(
                    "[%s] Facebook 語言切換異常，繼續其他任務：%s",
                    profile.name, exc,
                )
                summary.add_issue(f"語言切換異常：{exc}")
                _task_failed(ctrl, profile, "facebook_language", str(exc))
            finally:
                if not return_to_personal_profile(
                    ctrl, profile.name, "優先語言切換完成後", stop_event
                ):
                    summary.add_issue("語言切換後無法回個人主頁")
        else:
            _log.info("[%s] 統一 Facebook 語言功能已停用，跳過。", profile.name)

        # Reels 現在可由健康的 Facebook 頁面直接開啟 Create reel。
        # 若 Reels 是本環境第一個啟用的任務，就不再為了找 Reels 先繞回
        # 個人主頁；其他較早執行的功能仍保留原本的個人主頁起點。
        profile_task_before_reels = any((
            enable_professional_mode,
            enable_avatar,
            enable_pin,
            enable_confirm_friend,
            enable_post,
        ))
        if enable_reels and not profile_task_before_reels:
            _log.info(
                "[%s] Health Check 正常，Reels 為第一項任務；略過個人主頁，將直接開啟 Create reel。",
                profile.name,
            )
        elif not return_to_personal_profile(
            ctrl, profile.name, "任務開始前", stop_event
        ):
            summary.finish(success=False, reason="任務開始前無法回到個人主頁")
            return

        # 浮動 Messenger 對話視窗會跨頁保留，可能遮住個人頁／粉專頁
        # 右側的 Message 按鈕。每個環境只在第一項任務開始前清理一次。
        ctrl.dismiss_floating_chats(profile.name)
        ctrl.wake_rendering_surface(profile.name)

        # ── 獨立任務：成為專業模式 ─────────────────
        if enable_professional_mode:
            try:
                result = run_professional_mode_task(ctrl, profile, stop_event)
                summary.add_action("成為專業模式" if result == "success" else "已是專業模式")
            except InterruptedError:
                _log.info("[%s] 專業模式任務已停止。", profile.name)
                return
            except Exception as exc:
                _log.exception("[%s] 成為專業模式失敗，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"專業模式失敗：{exc}")
                _task_failed(ctrl, profile, "professional_mode", str(exc))
            finally:
                if not (stop_event and stop_event.is_set()):
                    if not return_to_personal_profile(
                        ctrl, profile.name, "專業模式任務完成後", stop_event
                    ):
                        summary.add_issue("專業模式後無法回個人主頁")
        else:
            _log.info("[%s] 成為專業模式功能已停用，跳過。", profile.name)

        # ── 獨立任務：換頭像 ───────────────────────
        if enable_avatar:
            try:
                avatar_pin.IMAGE_FOLDER = Path(avatar_dir).expanduser()
                image_path = find_matching_image(profile.name)
                if image_path is None:
                    _log.warning("[%s] 找不到環境名稱對應的頭像圖片，跳過換頭像。", profile.name)
                    summary.add_issue("換頭像：找不到對應圖片")
                    _task_failed(ctrl, profile, "avatar", "找不到環境名稱對應的頭像圖片")
                else:
                    avatar_ok, _ = change_facebook_avatar(
                        ctrl.driver, profile, image_path,
                        enable_avatar=True, enable_pin=False,
                    )
                    if avatar_ok is True:
                        summary.add_action("更換頭像")
                    else:
                        _log.warning("[%s] 換頭像失敗，繼續其他任務。", profile.name)
                        summary.add_issue("換頭像失敗")
                        _task_failed(ctrl, profile, "avatar", "換頭像流程回傳失敗")
            except Exception as exc:
                _log.exception("[%s] 換頭像異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"換頭像異常：{exc}")
                _task_failed(ctrl, profile, "avatar", str(exc))
            finally:
                if not return_to_personal_profile(
                    ctrl, profile.name, "換頭像任務完成後", stop_event
                ):
                    summary.add_issue("換頭像後無法回個人主頁")
        else:
            _log.info("[%s] 自動換頭像功能已停用，跳過。", profile.name)

        # ── 個人資料設定：Banner ─────────────────────
        if enable_banner:
            try:
                banner_path = find_matching_banner(profile.name, banner_dir)
                if banner_path is None:
                    _log.warning("[%s] 找不到環境名稱對應的 Banner 圖片，已略過。", profile.name)
                    summary.add_issue("Banner：找不到對應圖片")
                    _task_failed(ctrl, profile, "banner", "找不到環境名稱對應的 Banner 圖片")
                elif change_facebook_banner(ctrl.driver, profile.name, banner_path):
                    summary.add_action("更換 Banner")
            except Exception as exc:
                _log.exception("[%s] 更換 Banner 異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"Banner 異常：{exc}")
                _task_failed(ctrl, profile, "banner", str(exc))
            finally:
                if not return_to_personal_profile(ctrl, profile.name, "Banner 任務完成後", stop_event):
                    summary.add_issue("Banner 後無法回個人主頁")
        else:
            _log.info("[%s] 更換 Banner 功能已停用，跳過。", profile.name)

        # ── 個人資料設定：名字 ───────────────────────
        if enable_profile_name:
            try:
                new_name = read_profile_name(name_text_file, profile.name)
                if change_facebook_name(
                    ctrl.driver,
                    profile.name,
                    new_name,
                    personal_profile_url=getattr(
                        ctrl.driver, "_facebook_personal_profile_url", ""
                    ),
                ):
                    summary.add_action("更換名字")
            except Exception as exc:
                _log.exception("[%s] 更換名字異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"名字異常：{exc}")
                _task_failed(ctrl, profile, "profile_name", str(exc))
            finally:
                if not return_to_personal_profile(ctrl, profile.name, "名字任務完成後", stop_event):
                    summary.add_issue("名字後無法回個人主頁")
        else:
            _log.info("[%s] 更換名字功能已停用，跳過。", profile.name)

        # ── 獨立任務：Messenger PIN ─────────────────
        if enable_pin:
            try:
                _, pin_ok = change_facebook_avatar(
                    ctrl.driver, profile, None,
                    enable_avatar=False, enable_pin=True,
                )
                if pin_ok is True:
                    summary.add_action("建立／確認 PIN")
                else:
                    _log.warning("[%s] PIN 處理失敗，繼續其他任務。", profile.name)
                    summary.add_issue("Messenger PIN 失敗")
                    _task_failed(ctrl, profile, "messenger_pin", "PIN 流程回傳失敗")
            except Exception as exc:
                _log.exception("[%s] PIN 處理異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"Messenger PIN 異常：{exc}")
                _task_failed(ctrl, profile, "messenger_pin", str(exc))
            finally:
                if not return_to_personal_profile(
                    ctrl, profile.name, "Messenger PIN 任務完成後", stop_event
                ):
                    summary.add_issue("PIN 後無法回個人主頁")
        else:
            _log.info("[%s] Messenger PIN 功能已停用，跳過。", profile.name)

        # ── 步驟 6：確認收到的好友邀請（獨立開關＋獨立數量） ───
        if enable_confirm_friend:
            try:
                _log.info(
                    "[%s] 開始同意好友邀請流程，設定數量=%d。",
                    profile.name, CONFIG.friend.confirm_friend_count,
                )
                confirmed = FacebookFriendConfirmer(ctrl).run()
                if confirmed > 0:
                    summary.add_action(f"確認好友 {confirmed} 人")
            except Exception as exc:
                _log.exception("[%s] 同意好友異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"同意好友異常：{exc}")
                _task_failed(ctrl, profile, "confirm_friend", str(exc))
            finally:
                if not return_to_personal_profile(
                    ctrl, profile.name, "同意好友任務完成後", stop_event
                ):
                    summary.add_issue("同意好友後無法回個人主頁")
        else:
            _log.info("[%s] 同意好友功能已停用，跳過。", profile.name)

        # ── 步驟 7：PO 文與「瀏覽＋按讚」各自獨立 ───────────
        _log.info(
            "[%s] 養號功能：PO文=%s、瀏覽／按讚=%s%s。",
            profile.name,
            "啟用" if enable_post else "停用",
            "啟用" if enable_browse_like else "停用",
            f"（目標 {like_count} 次）" if enable_browse_like else "",
        )
        if enable_post:
            try:
                browse_result = FeedBrowser(ctrl, comment_gen).run(
                    enable_post=True, enable_browse_like=False, like_target=0,
                    post_text_file=post_text_file,
                    post_media_enabled=post_media_enabled,
                    post_media_mode=post_media_mode,
                    post_random_media_dir=post_random_media_dir,
                    post_fixed_media_file=post_fixed_media_file,
                )
                if "post" in browse_result.actions:
                    summary.add_action("發貼文 1 次")
                else:
                    summary.add_issue("PO 文失敗或未送出")
                    _task_failed(ctrl, profile, "post", "PO 文失敗或未確認送出")
            except Exception as exc:
                _log.exception("[%s] PO 文異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"PO 文異常：{exc}")
                _task_failed(ctrl, profile, "post", str(exc))
            finally:
                if not return_to_personal_profile(
                    ctrl, profile.name, "PO 文任務完成後", stop_event
                ):
                    summary.add_issue("PO 文後無法回個人主頁")

        # ── 獨立任務：發布 Reels（PO 文後、瀏覽按讚前） ──
        if enable_reels:
            try:
                result = ReelsPublisher(
                    driver=ctrl.driver,
                    profile_id=profile.profile_id,
                    profile_name=profile.name,
                    video_dir=reels_video_dir,
                    text_file=reels_text_file,
                    stop_event=stop_event,
                    dry_run=reels_dry_run,
                ).run()
                if result == "success":
                    summary.add_action("發布 Reels 1 次")
                elif result == "ready":
                    summary.add_action("Reels 測試發送完成（未發布）")
                else:
                    summary.add_action("Reels 已發布過，跳過")
            except InterruptedError:
                _log.info("[%s] Reels 任務已停止。", profile.name)
                return
            except (FileNotFoundError, ValueError, IndexError) as exc:
                _log.warning("[%s] Reels 素材不完整，本項跳過：%s", profile.name, exc)
                summary.add_issue(f"Reels 跳過：{exc}")
                _task_failed(ctrl, profile, "reels", str(exc))
            except Exception as exc:
                _log.exception("[%s] Reels 發布異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"Reels 發布異常：{exc}")
                if not getattr(locals().get("result", None), "last_diagnostic", None):
                    _task_failed(ctrl, profile, "reels", str(exc))
            finally:
                if not (stop_event and stop_event.is_set()):
                    if not return_to_personal_profile_via_timeline(
                        ctrl, profile.name, "Reels 任務完成後", stop_event
                    ):
                        summary.add_issue("Reels 後無法回個人主頁")
        else:
            _log.info("[%s] Reels 功能已停用，跳過。", profile.name)

        # ── 獨立任務：Reels 留言（回到個人主頁，第一篇貼文留言） ──
        if enable_reels_comment:
            try:
                if not return_to_personal_profile_via_timeline(
                    ctrl, profile.name, "Reels 留言任務開始前", stop_event
                ):
                    raise RuntimeError("Reels 留言開始前無法確認位於個人主頁")
                reels_comment_result = ReelsCommentTask(
                    ctrl.driver, profile.name, reels_comment_text_file, stop_event, reels_comment_mode
                ).run()
                if reels_comment_result == "success":
                    summary.add_action("Reels 留言 1 次")
                elif reels_comment_result == "stopped":
                    _log.info("[%s] Reels 留言任務已停止。", profile.name)
                    return
                else:
                    _log.info(
                        "[%s] Reels 留言沒有可操作的個人主頁貼文，本次安全跳過。",
                        profile.name,
                    )
            except InterruptedError:
                _log.info("[%s] Reels 留言任務已停止。", profile.name)
                return
            except Exception as exc:
                _log.exception("[%s] Reels 留言異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"Reels 留言異常：{exc}")
                _task_failed(ctrl, profile, "reels_comment", str(exc))
            finally:
                if not (stop_event and stop_event.is_set()):
                    return_to_personal_profile_via_timeline(
                        ctrl, profile.name, "Reels 留言任務完成後", stop_event
                    )
        else:
            _log.info("[%s] Reels 留言功能已停用，跳過。", profile.name)

        if enable_browse_like:
            try:
                browse_result = FeedBrowser(ctrl, comment_gen).run(
                    enable_post=False, enable_browse_like=True, like_target=like_count,
                )
                if browse_result.liked_count > 0:
                    summary.add_action(f"按讚 {browse_result.liked_count} 次")
                if browse_result.liked_count < like_count:
                    summary.add_issue(
                        f"按讚未達目標：{browse_result.liked_count}/{like_count}"
                    )
                    _task_failed(
                        ctrl, profile, "browse_like",
                        f"按讚未達目標：{browse_result.liked_count}/{like_count}",
                    )
            except Exception as exc:
                _log.exception("[%s] 瀏覽／按讚異常，繼續其他任務：%s", profile.name, exc)
                summary.add_issue(f"瀏覽／按讚異常：{exc}")
                _task_failed(ctrl, profile, "browse_like", str(exc))
            finally:
                if not return_to_personal_profile(
                    ctrl, profile.name, "瀏覽／按讚任務完成後", stop_event
                ):
                    summary.add_issue("瀏覽／按讚後無法回個人主頁")

        if not enable_post and not enable_reels and not enable_browse_like:
            _log.info("[%s] PO 文、Reels 與瀏覽／按讚均已停用，完整跳過動態流程。", profile.name)

        # ── 步驟 8：主動加入好友（可選） ─────────
        if enable_add_friend:
            try:
                _log.info(
                    "[%s] 開始主動加好友流程，設定數量=%d。",
                    profile.name, CONFIG.friend.add_friend_count,
                )
                added = FacebookFriendAdder(ctrl).run()
                if added > 0:
                    summary.add_action(f"加好友 {added} 人")
                if added < CONFIG.friend.add_friend_count:
                    summary.add_issue(
                        f"加好友未達目標：{added}/{CONFIG.friend.add_friend_count}"
                    )
                    _task_failed(
                        ctrl, profile, "add_friend",
                        f"加好友未達目標：{added}/{CONFIG.friend.add_friend_count}",
                    )
            except Exception as exc:
                _log.exception("[%s] 主動加好友異常：%s", profile.name, exc)
                summary.add_issue(f"主動加好友異常：{exc}")
                _task_failed(ctrl, profile, "add_friend", str(exc))
            finally:
                if not return_to_personal_profile(
                    ctrl, profile.name, "主動加好友任務完成後", stop_event
                ):
                    summary.add_issue("加好友後無法回個人主頁")
        else:
            _log.info("[%s] 加好友功能已停用，跳過。", profile.name)

        # ── 新增獨立任務：粉專私訊 ─────────────────
        def private_diagnostic(*, task_name, stage, reason, job_id=""):
            return save_task_diagnostic(
                ctrl.driver, profile.name, task_name, reason,
                profile_id=profile.profile_id, stage=stage, job_id=job_id,
                settings={
                    "fanpage_url_file": fanpage_url_file,
                    "fanpage_text_file": fanpage_text_file,
                    "fanpage_mode": fanpage_mode,
                    "chat_database": chat_database,
                    "query_max_chats": query_max_chats,
                    "query_unread_only": query_unread_only,
                    "reply_text_file": reply_text_file,
                    "reply_mode": reply_mode,
                    "reply_max_count": reply_max_count,
                    "reply_max_retries": reply_max_retries,
                },
            )

        repository = None
        if enable_query_chats or enable_reply_chats:
            repository = ChatRepository(chat_database)

        fanpage_task_failed = False
        fanpage_failure_reason = ""
        if enable_fanpage_message:
            try:
                task_result = FanpageMessageTask(
                    ctrl.driver, profile_name=profile.name,
                    url_file=fanpage_url_file, text_file=fanpage_text_file,
                    reply_mode=fanpage_mode, max_urls=fanpage_max_urls,
                    stop_event=stop_event, diagnostic_callback=private_diagnostic,
                    rename_callback=lambda new_name: adspower.rename_profile(
                        profile.profile_id, new_name
                    ),
                ).run()
                if task_stats:
                    task_stats.add(task_result)
                _log.info("[%s] [粉專私訊] 結果=%s 成功=%d 失敗=%d 跳過=%d",
                          profile.name, task_result.status, task_result.success_count,
                          task_result.failed_count, task_result.skipped_count)
                if task_result.status in {"failed", "restricted"}:
                    fanpage_task_failed = True
                    fanpage_failure_reason = task_result.detail or task_result.status
                    summary.add_issue(f"粉專私訊{task_result.status}：{fanpage_failure_reason}")
                elif task_result.success_count:
                    summary.add_action("粉專私訊 1 次")
                elif "找不到 Message" in (task_result.detail or ""):
                    summary.add_issue(f"粉專私訊跳過：{task_result.detail}")
            except Exception as exc:
                fanpage_task_failed = True
                fanpage_failure_reason = str(exc)
                _log.exception("[%s] [粉專私訊] 任務失敗：%s", profile.name, exc)
                summary.add_issue(f"粉專私訊異常：{exc}")
                private_diagnostic(task_name="粉專私訊", stage="startup", reason=str(exc))

        if enable_query_chats:
            try:
                telegram_reporter = None
                if enable_telegram_report:
                    telegram_reporter = TelegramReporter(
                        repository,
                        bot_token=telegram_bot_token,
                        chat_id=telegram_chat_id,
                    )
                task_result = ChatQueryTask(
                    ctrl.driver, repository, profile_id=profile.profile_id,
                    profile_name=profile.name, max_chats=query_max_chats,
                    unread_only=query_unread_only, max_retries=reply_max_retries,
                    stop_event=stop_event,
                    telegram_reporter=telegram_reporter,
                    diagnostic_callback=private_diagnostic,
                    rename_callback=lambda new_name: adspower.rename_profile(
                        profile.profile_id, new_name
                    ),
                ).run()
                if task_stats:
                    task_stats.add(task_result)
                _log.info("[%s] [查詢聊天室] 結果=%s 入庫=%d 失敗=%d 跳過=%d",
                          profile.name, task_result.status, task_result.success_count,
                          task_result.failed_count, task_result.skipped_count)
                if has_chat_identity_restriction(task_result.detail):
                    reason = (
                        "偵測到 Confirm your identity；AdsPower 更名失敗"
                        if "更名失敗" in task_result.detail
                        else "偵測到 Confirm your identity；環境已標記聊天室禁言"
                    )
                    summary.add_issue(reason)
                    if not return_to_personal_profile(
                        ctrl, profile.name, "聊天室禁言更名後", stop_event
                    ):
                        summary.add_issue("聊天室禁言更名後無法回個人主頁")
                    summary.finish(success=False, reason=reason)
                    return
                if task_result.failed_count or task_result.restricted_count:
                    summary.add_issue(
                        "查詢聊天室異常："
                        f"失敗={task_result.failed_count}、"
                        f"受限={task_result.restricted_count}"
                    )
                if task_result.success_count:
                    summary.add_action(
                        f"查詢聊天室入庫 {task_result.success_count} 筆"
                    )
            except Exception as exc:
                _log.exception("[%s] [查詢聊天室] 任務失敗：%s", profile.name, exc)
                summary.add_issue(f"查詢聊天室異常：{exc}")
                private_diagnostic(task_name="查詢聊天室", stage="query", reason=str(exc))

        if enable_reply_chats:
            try:
                telegram_reporter = None
                if enable_telegram_report:
                    telegram_reporter = TelegramReporter(
                        repository,
                        bot_token=telegram_bot_token,
                        chat_id=telegram_reply_chat_id or telegram_chat_id,
                    )
                task_result = ChatReplyTask(
                    ctrl.driver, repository, profile_id=profile.profile_id,
                    profile_name=profile.name, text_file=reply_text_file,
                    reply_mode=reply_mode, max_replies=reply_max_count,
                    stop_event=stop_event, diagnostic_callback=private_diagnostic,
                    telegram_reporter=telegram_reporter,
                    rename_callback=lambda new_name: adspower.rename_profile(
                        profile.profile_id, new_name
                    ),
                ).run()
                if task_stats:
                    task_stats.add(task_result)
                _log.info("[%s] [回覆聊天室] 結果=%s 成功=%d 失敗=%d 跳過=%d",
                          profile.name, task_result.status, task_result.success_count,
                          task_result.failed_count, task_result.skipped_count)
                if has_chat_identity_restriction(task_result.detail):
                    reason = (
                        "偵測到 Confirm your identity；AdsPower 更名失敗"
                        if "更名失敗" in task_result.detail
                        else "偵測到 Confirm your identity；環境已標記聊天室禁言"
                    )
                    summary.add_issue(reason)
                    if not return_to_personal_profile(
                        ctrl, profile.name, "聊天室禁言更名後", stop_event
                    ):
                        summary.add_issue("聊天室禁言更名後無法回個人主頁")
                    summary.finish(success=False, reason=reason)
                    return
                if task_result.failed_count or task_result.restricted_count:
                    summary.add_issue(
                        "回覆聊天室異常："
                        f"失敗={task_result.failed_count}、"
                        f"受限={task_result.restricted_count}"
                    )
                if task_result.success_count:
                    summary.add_action(
                        f"回覆聊天室 {task_result.success_count} 筆"
                    )
            except Exception as exc:
                _log.exception("[%s] [回覆聊天室] 任務失敗：%s", profile.name, exc)
                summary.add_issue(f"回覆聊天室異常：{exc}")
                private_diagnostic(task_name="回覆聊天室", stage="reply", reason=str(exc))

        # ── 完成 ──────────────────────────────────
        if not return_to_personal_profile(
            ctrl, profile.name, "全部任務完成後", stop_event
        ):
            summary.add_issue("全部任務完成後無法回個人主頁")
        summary.finish(
            success=not fanpage_task_failed,
            reason=(f"粉專私訊失敗：{fanpage_failure_reason}" if fanpage_task_failed else ""),
        )

    except Exception as exc:
        _log.exception("[%s] 執行過程中發生未預期錯誤：%s", profile.name, exc)
        summary.finish(success=False, reason=f"未預期錯誤：{exc}")

    finally:
        # ── 步驟 9：解除接管，但不關閉 AdsPower 環境 ──
        _log.info("[%s] 操作結束，解除 Selenium 接管。", profile.name)
        ctrl.detach_keep_browser()
        if delete_after_detection:
            adspower.close_browser(profile.profile_id)
            inactive_deadline = time.monotonic() + 12
            while (
                time.monotonic() < inactive_deadline
                and adspower.check_status(profile.profile_id)
            ):
                time.sleep(0.4)
            if adspower.delete_profile(profile.profile_id):
                _log.warning(
                    "[%s] %s 環境已永久刪除（更名後：%s）。",
                    profile.name, detected_removal_kind, detected_removal_name,
                )
            else:
                _log.error(
                    "[%s] %s 環境刪除失敗，請手動確認（目前名稱：%s）。",
                    profile.name, detected_removal_kind, detected_removal_name,
                )
        elif detected_removal_kind == "tunnel_connection_failed":
            adspower.close_browser(profile.profile_id)
            _log.warning(
                "[%s] 代理／IP連線失效環境已關閉並保留（未刪除；目前名稱：%s）。",
                profile.name,
                detected_removal_name,
            )
        elif close_after:
            adspower.close_browser(profile.profile_id)
            _log.info("[%s] 已依 GUI 設定關閉 AdsPower 環境。", profile.name)
        else:
            _log.info("[%s] 已依 GUI 設定保持 AdsPower 環境開啟。", profile.name)
        stop_profile_log()


# ─────────────────────────────────────────────
# 啟動選項
# ─────────────────────────────────────────────

def ask_enable_add_friend() -> bool:
    """啟動時詢問是否啟用加好友功能。"""
    print("\n" + "=" * 60)
    print("是否啟用加好友功能？")
    print("1 = 啟用")
    print("2 = 不啟用")
    print("=" * 60)

    while True:
        choice = input("請輸入：").strip()

        if choice == "1":
            return True

        if choice == "2":
            return False

        print("輸入錯誤，請輸入 1 或 2。")


def ask_enable_feature(feature_name: str) -> bool:
    """啟動時詢問是否啟用指定功能。"""
    print("\n" + "=" * 60)
    print(f"是否啟用【{feature_name}】？")
    print("1 = 啟用")
    print("2 = 不啟用")
    print("=" * 60)

    while True:
        choice = input("請輸入：").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("輸入錯誤，請輸入 1 或 2。")


def ask_feature_count(
    feature_name: str,
    default: int,
    maximum: int = 100,
    unit: str = "人",
) -> int:
    """只在功能啟用時詢問執行數量，Enter 使用預設值。"""
    print("\n" + "=" * 60)
    print(f"請設定【{feature_name}】數量")
    print(f"直接按 Enter = {default} {unit}；可輸入 1～{maximum}")
    print("=" * 60)
    while True:
        raw = input("請輸入數量：").strip()
        if raw == "":
            return default
        if raw.isdigit() and 1 <= int(raw) <= maximum:
            return int(raw)
        print(f"輸入錯誤，請輸入 1～{maximum} 的整數。")




# ─────────────────────────────────────────────
# 命令列參數解析
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """解析命令列參數。"""
    parser = argparse.ArgumentParser(
        description="Facebook Auto Warm-up Lite V8.2.4 Stable",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  python main.py                      啟動後詢問是否啟用加好友
  python main.py --profile abc123     只執行指定 Profile ID
  python main.py --openai-key sk-...  設定 OpenAI API Key
        """,
    )
    parser.add_argument(
        "--no-friend",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="",
        metavar="PROFILE_ID",
        help="只執行指定的單一 Profile ID（省略則執行全部）",
    )
    parser.add_argument(
        "--openai-key",
        type=str,
        default="",
        metavar="API_KEY",
        help="設定 OpenAI API Key（也可透過環境變數 OPENAI_API_KEY 設定）",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        default=False,
        help="隨機打亂 Profile 執行順序（預設：依序執行）",
    )
    return parser.parse_args()


# ─────────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────────

def main() -> None:
    """
    程式主入口。
    1. 解析命令列參數
    2. 初始化設定與 OpenAI
    3. 讀取全部 AdsPower Profiles
    4. 依序（或隨機）對每個 Profile 執行養號流程
    """
    args = parse_args()

    # V2.4：所有選項與執行 LOG 集中在同一個 GUI。
    from 圖形介面 import launch_configuration_gui

    adspower = AdsPowerClient()
    def gui_runner(gui_settings, stop_event: threading.Event) -> None:
        CONFIG.enable_add_friend = gui_settings.add_friend
        CONFIG.enable_confirm_friend = gui_settings.confirm_friend
        CONFIG.friend.add_friend_count = gui_settings.add_friend_count
        CONFIG.friend.confirm_friend_count = gui_settings.confirm_friend_count

        target_profiles = sort_profiles_by_number(gui_settings.profiles)
        current_round = 1
        total_start = time.time()
        _log.info("=" * 60)
        _log.info("Facebook 養號十一項任務 GUI V4.1.0 Multi-Thread Stable 開始執行")
        task_stats = TaskStatsRegistry()
        _log.info(
            "選擇環境：%d 個；循環：%s；執行線程：%d",
            len(target_profiles),
            "無限" if gui_settings.loop_count == 0 else gui_settings.loop_count,
            min(gui_settings.worker_count, len(target_profiles)),
        )
        _log.info("=" * 60)

        while (
            not stop_event.is_set()
            and (gui_settings.loop_count == 0 or current_round <= gui_settings.loop_count)
        ):
            round_profiles = sort_profiles_by_number(target_profiles)
            _log.info(
                "目前第 %d%s 輪", current_round,
                "（∞）" if gui_settings.loop_count == 0 else f"/{gui_settings.loop_count}",
            )
            worker_count = min(gui_settings.worker_count, len(round_profiles))
            groups = [round_profiles[index::worker_count] for index in range(worker_count)]
            progress_lock = threading.Lock()
            completed_count = 0

            def run_group(worker_index: int, profiles: list[ProfileInfo]) -> None:
                nonlocal completed_count
                worker_adspower = AdsPowerClient()
                worker_comment_gen = CommentGenerator()
                _log.info(
                    "[線程%d] 已分配 %d 個環境：%s",
                    worker_index, len(profiles), "、".join(p.name for p in profiles),
                )
                for group_index, profile in enumerate(profiles, start=1):
                    if stop_event.is_set():
                        break
                    with progress_lock:
                        next_progress = completed_count + 1
                    _log.info(
                        "[線程%d] 進度：總計待完成第 %d/%d；本線程 %d/%d；環境：%s",
                        worker_index, next_progress, len(round_profiles),
                        group_index, len(profiles), profile.name,
                    )
                    try:
                        run_profile(
                            adspower=worker_adspower,
                            profile=profile,
                            comment_gen=worker_comment_gen,
                            enable_add_friend=gui_settings.add_friend,
                            enable_confirm_friend=gui_settings.confirm_friend,
                            enable_post=gui_settings.post,
                            post_text_file=gui_settings.post_text_file,
                            post_media_enabled=gui_settings.post_media_enabled,
                            post_media_mode=gui_settings.post_media_mode,
                            post_random_media_dir=gui_settings.post_random_media_dir,
                            post_fixed_media_file=gui_settings.post_fixed_media_file,
                            enable_browse_like=gui_settings.browse_like,
                            like_count=gui_settings.like_count,
                            enable_avatar=gui_settings.avatar,
                            enable_banner=gui_settings.banner,
                            enable_profile_name=gui_settings.profile_name,
                            enable_facebook_language=gui_settings.facebook_language,
                            avatar_dir=gui_settings.avatar_dir,
                            banner_dir=gui_settings.banner_dir,
                            name_text_file=gui_settings.name_text_file,
                            facebook_language_target=gui_settings.facebook_language_target,
                            enable_pin=gui_settings.pin,
                            enable_professional_mode=gui_settings.professional_mode,
                            enable_reels=gui_settings.reels,
                            reels_dry_run=gui_settings.reels_dry_run,
                            enable_reels_comment=gui_settings.reels_comment,
                            reels_comment_mode=gui_settings.reels_comment_mode,
                            reels_comment_text_file=gui_settings.reels_comment_text_file,
                            reels_video_dir=gui_settings.reels_video_dir,
                            reels_text_file=gui_settings.reels_text_file,
                            close_after=gui_settings.close_after,
                            bring_to_front=gui_settings.bring_to_front,
                            stop_event=stop_event,
                            enable_fanpage_message=gui_settings.fanpage_message,
                            enable_query_chats=gui_settings.query_chats,
                            enable_reply_chats=gui_settings.reply_chats,
                            fanpage_url_file=gui_settings.fanpage_url_file,
                            fanpage_text_file=gui_settings.fanpage_text_file,
                            fanpage_mode=gui_settings.fanpage_mode,
                            fanpage_max_urls=gui_settings.fanpage_max_urls,
                            chat_database=gui_settings.chat_database,
                            query_max_chats=gui_settings.query_max_chats,
                            query_unread_only=gui_settings.query_unread_only,
                            reply_text_file=gui_settings.reply_text_file,
                            reply_mode=gui_settings.reply_mode,
                            reply_max_count=gui_settings.reply_max_count,
                            reply_max_retries=gui_settings.reply_max_retries,
                            enable_telegram_report=gui_settings.telegram_report,
                            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
                            telegram_reply_chat_id=os.environ.get(
                                "TELEGRAM_REPLY_CHAT_ID",
                                os.environ.get("TELEGRAM_CHAT_ID", ""),
                            ),
                            task_stats=task_stats,
                        )
                    except Exception:
                        _log.exception(
                            "[線程%d] 環境 %s 發生未預期錯誤；本線程繼續下一個環境。",
                            worker_index, profile.name,
                        )
                    with progress_lock:
                        completed_count += 1
                        finished = completed_count
                    _log.info(
                        "[線程%d] 環境完成：%s；總進度 [%d/%d]",
                        worker_index, profile.name, finished, len(round_profiles),
                    )
                    if group_index < len(profiles) and not stop_event.is_set():
                        random_sleep(CONFIG.profile_gap_min, CONFIG.profile_gap_max)

            with ThreadPoolExecutor(
                max_workers=worker_count, thread_name_prefix="ProfileWorker"
            ) as executor:
                futures = [
                    executor.submit(run_group, index + 1, group)
                    for index, group in enumerate(groups) if group
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        _log.exception("環境線程發生未預期錯誤；其他線程繼續執行。")
            current_round += 1
            if gui_settings.loop_count != 0 and current_round > gui_settings.loop_count:
                _log.info(
                    "已完成設定的 %d 輪；執行一次時不會建立第二輪。",
                    gui_settings.loop_count,
                )

        elapsed = time.time() - total_start
        if stop_event.is_set():
            _log.info("使用者已停止執行，總耗時 %.1f 分鐘。", elapsed / 60)
        else:
            _log.info("全部環境執行完畢，總耗時 %.1f 分鐘。", elapsed / 60)
        _log.info("三項私訊任務統計：%s", task_stats.snapshot())

    launch_configuration_gui(adspower, gui_runner)
    _log.info("GUI 已關閉，程式結束。")
    return

    # ── 設定 OpenAI API Key ───────────────────
    # 優先順序：命令列 > 環境變數 > config.py
    openai_key = (
        args.openai_key
        or os.environ.get("OPENAI_API_KEY", "")
        or CONFIG.openai.api_key
    )
    if not openai_key:
        _log.warning(
            "未設定 OpenAI API Key，留言功能將自動跳過。\n"
            "設定方式：\n"
            "  方式一：python main.py --openai-key sk-xxxx\n"
            "  方式二：set OPENAI_API_KEY=sk-xxxx（Windows）\n"
            "  方式三：在 config.py 的 OpenAIConfig.api_key 填入"
        )
    CONFIG.openai.api_key = openai_key

    # ── 七項功能全部獨立設定 ───────────────────
    enable_professional_mode = ask_enable_feature("成為 Facebook 專業模式")
    _log.info(
        "成為 Facebook 專業模式：%s",
        "啟用" if enable_professional_mode else "停用",
    )

    enable_avatar = ask_enable_feature("自動更換 Facebook 頭像")
    _log.info("自動更換 Facebook 頭像：%s", "啟用" if enable_avatar else "停用")

    enable_pin = ask_enable_feature("Messenger PIN")
    _log.info("Messenger PIN：%s", "啟用" if enable_pin else "停用")

    enable_add_friend = ask_enable_feature("主動加好友")
    CONFIG.enable_add_friend = enable_add_friend
    if enable_add_friend:
        CONFIG.friend.add_friend_count = ask_feature_count(
            "主動加好友",
            default=CONFIG.friend.add_friend_count,
        )
    _log.info("主動加好友功能：%s", "啟用" if enable_add_friend else "停用")

    enable_confirm_friend = ask_enable_feature("同意好友邀請")
    CONFIG.enable_confirm_friend = enable_confirm_friend
    if enable_confirm_friend:
        CONFIG.friend.confirm_friend_count = ask_feature_count(
            "同意好友邀請",
            default=CONFIG.friend.confirm_friend_count,
        )
    _log.info("同意好友功能：%s", "啟用" if enable_confirm_friend else "停用")

    enable_post = ask_enable_feature("PO 文")
    _log.info("PO 文功能：%s", "啟用" if enable_post else "停用")

    enable_browse_like = ask_enable_feature("瀏覽／按讚（同一功能）")
    like_count = 1
    if enable_browse_like:
        like_count = ask_feature_count(
            "按讚",
            default=1,
            maximum=100,
            unit="次",
        )
    _log.info(
        "瀏覽／按讚功能：%s%s",
        "啟用" if enable_browse_like else "停用",
        f"（目標 {like_count} 次）" if enable_browse_like else "",
    )

    # ── 初始化共用元件 ────────────────────────
    adspower = AdsPowerClient()
    comment_gen = CommentGenerator()

    # ── 讀取 Profiles ─────────────────────────
    _log.info("讀取 AdsPower Profile 清單...")
    try:
        all_profiles = adspower.list_all_profiles()
    except RuntimeError as exc:
        _log.error("無法讀取 Profile 清單：%s", exc)
        _log.error("請確認 AdsPower 已啟動，且 Local API 功能已開啟。")
        sys.exit(1)

    if not all_profiles:
        _log.warning("找不到任何 Profile，程式結束。")
        sys.exit(0)

    # ── 篩選指定 Profile（若有） ──────────────
    if args.profile:
        target_profiles: list[ProfileInfo] = [
            p for p in all_profiles if p.profile_id == args.profile
        ]
        if not target_profiles:
            _log.error("找不到指定的 Profile ID：%s", args.profile)
            sys.exit(1)
    else:
        target_profiles = choose_profile_series(all_profiles)

        # V8：只有選到單一系列時，詢問起始與結束號碼。
        selected_series = {classify_profile_series(p.name) for p in target_profiles}
        selected_series.discard("")
        if len(selected_series) == 1:
            target_profiles = ask_single_series_range(target_profiles)
        else:
            _log.info("目前選擇多個系列，不詢問號碼範圍，執行所選全部 Profile。")

    # ==========================
    # V8：詢問循環次數
    # Enter = 1，0 = 無限循環
    # ==========================
    while True:
        loop_raw = input("\n請輸入循環次數（Enter=1，0=無限）：").strip()
        if loop_raw == "":
            loop_count = 1
            break
        if loop_raw.isdigit():
            loop_count = int(loop_raw)
            break
        print("請輸入 0 或正整數；直接按 Enter 代表執行 1 次。")

    # ── 執行前最後確認 ──────────────────────────
    series_names = sorted({classify_profile_series(p.name) for p in target_profiles if classify_profile_series(p.name)})
    first_no = extract_profile_number(target_profiles[0].name) if target_profiles else None
    last_no = extract_profile_number(target_profiles[-1].name) if target_profiles else None
    loop_text = "∞（無限循環）" if loop_count == 0 else str(loop_count)

    print("\n" + "─" * 60)
    print(f"系列        ：{'、'.join(series_names) or '指定 Profile'}")
    if len(series_names) == 1 and first_no is not None and last_no is not None:
        print(f"範圍        ：{first_no} ～ {last_no}")
    else:
        print("範圍        ：所選系列全部 Profile")
    print(f"Profile 數  ：{len(target_profiles)}")
    print(f"第一個      ：{target_profiles[0].name}")
    print(f"最後一個    ：{target_profiles[-1].name}")
    print(f"循環次數    ：{loop_text}")
    print(f"專業模式    ：{'啟用' if enable_professional_mode else '停用'}")
    print(f"換頭像      ：{'啟用' if enable_avatar else '停用'}")
    print(f"Messenger PIN：{'啟用' if enable_pin else '停用'}")
    print(
        f"主動加好友  ："
        f"{'啟用，' + str(CONFIG.friend.add_friend_count) + ' 人' if enable_add_friend else '停用'}"
    )
    print(
        f"同意好友    ："
        f"{'啟用，' + str(CONFIG.friend.confirm_friend_count) + ' 人' if enable_confirm_friend else '停用'}"
    )
    print(f"PO 文       ：{'啟用' if enable_post else '停用'}")
    print(
        f"瀏覽／按讚  ："
        f"{'啟用，按讚 ' + str(like_count) + ' 次' if enable_browse_like else '停用'}"
    )
    print("─" * 60)

    while True:
        confirm = input("是否開始執行？（Y/N）：").strip().lower()
        if confirm in {"y", "yes", "是", "1"}:
            break
        if confirm in {"n", "no", "否", "2"}:
            _log.info("使用者在最後確認畫面取消執行。")
            print("已取消執行。")
            return
        print("請輸入 Y 或 N。")

    # ── 隨機打亂順序（若啟用） ────────────────
    if args.shuffle:
        target_profiles = shuffled(target_profiles)
        _log.info("Profile 執行順序已隨機打亂。")

    # ── 列印執行摘要 ──────────────────────────
    _log.info("=" * 60)
    _log.info("養號＋換頭像＋建立 Messenger PIN V1.6 KeepBrowser Stable 開始執行")
    _log.info("Browser 模式：已開啟直接接管；未開啟自動開啟；完成後全部保持開啟")
    _log.info("Profile 總數：%d", len(target_profiles))
    _log.info(
        "成為 Facebook 專業模式：%s",
        "啟用" if enable_professional_mode else "停用",
    )
    _log.info(
        "同意好友功能：%s%s",
        "啟用" if enable_confirm_friend else "停用",
        f"（{CONFIG.friend.confirm_friend_count} 人）" if enable_confirm_friend else "",
    )
    _log.info("自動更換 Facebook 頭像：%s", "啟用" if enable_avatar else "停用")
    _log.info("Messenger PIN：%s", "啟用" if enable_pin else "停用")
    _log.info(
        "主動加好友功能：%s%s",
        "啟用" if enable_add_friend else "停用",
        f"（{CONFIG.friend.add_friend_count} 人）" if enable_add_friend else "",
    )
    _log.info("PO 文功能：%s", "啟用" if enable_post else "停用")
    _log.info(
        "瀏覽／按讚功能：%s%s",
        "啟用" if enable_browse_like else "停用",
        f"（目標 {like_count} 次）" if enable_browse_like else "",
    )
    _log.info("=" * 60)

    total_start = time.time()
    success_count = 0
    fail_count = 0

    # ── 主迴圈：逐一執行 Profile ──────────────
    current_round = 1

    while True:

        if loop_count != 0 and current_round > loop_count:
            break

        if loop_count == 0:
            round_title = f"目前第 {current_round} 輪（∞）"
        else:
            round_title = f"目前第 {current_round}/{loop_count} 輪"

        print("\n" + "=" * 60)
        print(round_title)
        print("=" * 60)

        _log.info("=" * 60)
        _log.info(round_title)
        _log.info("=" * 60)

        for idx, profile in enumerate(target_profiles, start=1):
            _log.info(
                "進度：[%d/%d] 處理 Profile：%s",
                idx,
                len(target_profiles),
                profile.name,
            )

            run_profile(
                adspower=adspower,
                profile=profile,
                comment_gen=comment_gen,
                enable_add_friend=enable_add_friend,
                enable_confirm_friend=enable_confirm_friend,
                enable_post=enable_post,
                post_text_file="",
                post_media_enabled=False,
                post_media_mode="random",
                post_random_media_dir=str(Path.home() / "Desktop" / "view"),
                post_fixed_media_file="",
                enable_browse_like=enable_browse_like,
                like_count=like_count,
                enable_avatar=enable_avatar,
                enable_banner=False,
                enable_profile_name=False,
                enable_facebook_language=False,
                avatar_dir=str(Path.home() / "Desktop" / "頭像圖片"),
                banner_dir=str(Path.home() / "Desktop" / "Banner"),
                name_text_file=str(Path.home() / "Desktop" / "名字.txt"),
                facebook_language_target="Filipino",
                enable_pin=enable_pin,
                enable_professional_mode=enable_professional_mode,
            )

            if idx < len(target_profiles):
                random_sleep(
                    CONFIG.profile_gap_min,
                    CONFIG.profile_gap_max,
                )

        current_round += 1

    # ── 最終摘要 ──────────────────────────────
    total_elapsed = time.time() - total_start
    _log.info("=" * 60)
    _log.info("全部 Profile 執行完畢。")
    _log.info("總耗時：%.0f 秒（%.1f 分鐘）", total_elapsed, total_elapsed / 60)
    _log.info("=" * 60)


if __name__ == "__main__":
    main()
