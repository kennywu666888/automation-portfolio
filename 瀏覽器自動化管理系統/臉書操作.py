"""
facebook.py
===========
Facebook Auto Warm-up Lite — Facebook 操作模組
負責健康檢查、網路錯誤偵測、搜尋功能與加好友流程。
所有 Facebook 業務邏輯集中於此。
"""

import random
from enum import Enum
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from 瀏覽器 import BrowserController
from 設定 import CONFIG, FriendConfig
from 日誌 import get_logger
from 工具 import random_sleep, with_retry

_log = get_logger(__name__)


# ─────────────────────────────────────────────
# 健康狀態列舉
# ─────────────────────────────────────────────

class HealthStatus(Enum):
    """Facebook 帳號健康狀態。"""
    HEALTHY = "healthy"
    LOGIN_PAGE = "login_page"
    CHECKPOINT = "checkpoint"
    DISABLED = "disabled"
    SECURITY_VERIFY = "security_verify"
    ERROR_PAGE = "error_page"
    NETWORK_ERROR = "network_error"
    UNKNOWN = "unknown"


# ─────────────────────────────────────────────
# 頁面關鍵字常數（多語言）
# ─────────────────────────────────────────────

# 登入頁特徵
_LOGIN_SIGNALS: list[str] = [
    "log in to facebook",
    "sign in",
    "log into facebook",
    "登入 facebook",
    "登入facebook",
    "mag-log in sa facebook",
    "se connecter à facebook",
    "adresse e-mail ou numéro de téléphone",
    "เข้าสู่ระบบ facebook",
    "อีเมลหรือหมายเลขโทรศัพท์",
    "تسجيل الدخول إلى فيسبوك",
    "البريد الإلكتروني أو رقم الهاتف",
    "id or phone number",
    "email or phone number",
    "email address or phone",
]

# Checkpoint / 身份驗證特徵
# V1.3：不要把單純的 checkpoint 字串當成 Checkpoint。
# 很多正常首頁 URL 會帶 checkpoint_src=any，容易誤判。
_CHECKPOINT_SIGNALS: list[str] = [
    "verify your identity",
    "confirm your identity",
    "security check",
    "help us confirm it's you",
    "we need to verify that",
    "confirm it's you",
    "account security",
    "確認你的身份",
    "確認你的身分",
    "安全檢查",
    "驗證你的帳號",
    "i-verify ang iyong pagkakakilanlan",
    "vérifiez votre identité",
    "confirmez votre identité",
    "ยืนยันตัวตนของคุณ",
    "ตรวจสอบความปลอดภัย",
    "تحقق من هويتك",
    "تأكيد هويتك",
]

# 正常 Facebook 首頁 / 可操作頁面特徵
_HOME_SIGNALS: list[str] = [
    "what's on your mind",
    "what’s on your mind",
    "create story",
    "contacts",
    "home",
    "friends",
    "marketplace",
    "watch",
    "reels",
    "你在想什麼",
    "建立限時動態",
    "首頁",
    "朋友",
    "聯絡人",
    "ano ang nasa isip mo",
    "gumawa ng story",
    "qu’avez-vous en tête",
    "qu'avez-vous en tête",
    "créer une story",
    "accueil",
    "amis",
    "คุณกำลังคิดอะไรอยู่",
    "สร้างสตอรี่",
    "หน้าหลัก",
    "เพื่อน",
    "بم تفكر",
    "إنشاء قصة",
    "الصفحة الرئيسية",
    "الأصدقاء",
]

# 帳號停用特徵
_DISABLED_SIGNALS: list[str] = [
    "your account has been disabled",
    "this account has been disabled",
    "account disabled",
    "已停用你的帳號",
    "na-disable ang iyong account",
    "votre compte a été désactivé",
    "บัญชีของคุณถูกปิดใช้งาน",
    "تم تعطيل حسابك",
]

# 安全驗證特徵
_SECURITY_VERIFY_SIGNALS: list[str] = [
    "security verification",
    "unusual login activity",
    "confirm it's you",
    "異常登入",
    "安全驗證",
    "unusual activity",
    "activité de connexion inhabituelle",
    "การเข้าสู่ระบบที่ผิดปกติ",
    "نشاط تسجيل دخول غير معتاد",
]

# 網路錯誤頁面特徵（多語言）
_NETWORK_ERROR_SIGNALS: list[str] = [
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
    # 菲律賓文
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

# Facebook 首頁 URL
FACEBOOK_HOME_URL = "https://www.facebook.com"
FACEBOOK_SEARCH_URL = "https://www.facebook.com/search/people/?q={query}"


# ─────────────────────────────────────────────
# 健康檢查器
# ─────────────────────────────────────────────

class HealthChecker:
    """
    檢查目前 Facebook 頁面的帳號健康狀態。
    必須在登入後、執行任何操作前呼叫。
    """

    def __init__(self, ctrl: BrowserController) -> None:
        self._ctrl = ctrl

    def check(self) -> tuple[HealthStatus, str]:
        """
        執行健康檢查並回傳狀態。

        V1.3 調整重點：
        - 不再只因 URL 或 HTML 出現 checkpoint 就判定失敗。
        - 優先判斷「Facebook 是否可正常使用」。
        - 只有真的在驗證頁，才回傳 CHECKPOINT。

        Returns:
            (HealthStatus, detail_message) 元組。
            detail_message 描述具體偵測到的問題。
        """
        url = self._ctrl.current_url().lower()
        source = self._ctrl.page_source().lower()

        _log.debug("Health Check URL：%s", url[:120])

        # 1. 網路錯誤（優先判斷）
        for signal in _NETWORK_ERROR_SIGNALS:
            if signal in source or signal in url:
                return HealthStatus.NETWORK_ERROR, f"偵測到網路錯誤訊號：{signal}"

        # 2. 登入頁
        if self._is_login_page(url, source):
            return HealthStatus.LOGIN_PAGE, "偵測到登入頁面"

        # 3. 帳號停用
        if self._contains_any(source, _DISABLED_SIGNALS):
            return HealthStatus.DISABLED, "帳號已停用"

        # 4. 如果首頁/動態牆可操作，就直接判定健康。
        #    這一步放在 Checkpoint 前面，避免 checkpoint_src=any 誤判。
        if self._looks_like_usable_facebook(url, source):
            if "checkpoint" in url or "checkpoint" in source:
                _log.info("偵測到 checkpoint 字串，但頁面可正常使用，略過 Checkpoint 誤判。")
            return HealthStatus.HEALTHY, "頁面可正常使用，帳號健康"

        # 5. 安全驗證
        if self._contains_any(source, _SECURITY_VERIFY_SIGNALS):
            return HealthStatus.SECURITY_VERIFY, "需要安全驗證"

        # 6. 真正 Checkpoint：必須是 checkpoint 路徑 + 驗證關鍵字。
        if self._is_real_checkpoint(url, source):
            return HealthStatus.CHECKPOINT, "偵測到真正 Checkpoint 驗證頁面"

        # 7. 一般錯誤頁（HTTP 錯誤碼頁或空白頁）
        if self._is_error_page(url, source):
            return HealthStatus.ERROR_PAGE, "偵測到錯誤頁面"

        # 通過所有檢查
        return HealthStatus.HEALTHY, "帳號健康，可繼續操作"

    def _is_login_page(self, url: str, source: str) -> bool:
        """判斷是否為 Facebook 登入頁面。"""
        # URL 特徵
        if "/login" in url or "login.facebook.com" in url:
            return True
        # 頁面內容特徵
        return self._contains_any(source, _LOGIN_SIGNALS)

    def _is_error_page(self, url: str, source: str) -> bool:
        """判斷是否為一般錯誤頁面。"""
        # 若 URL 仍是 facebook.com 且 source 極短，可能是空白頁
        if "facebook.com" in url and len(source) < 500:
            return True
        return False

    def _looks_like_usable_facebook(self, url: str, source: str) -> bool:
        """
        判斷目前頁面是否看起來可正常使用。
        只要能看到首頁元素、貼文、搜尋框或可操作按鈕，就視為健康。
        """
        if "facebook.com" not in url:
            return False

        # 文字特徵：首頁、動態牆、建立貼文、聯絡人等。
        if self._contains_any(source, _HOME_SIGNALS):
            return True

        # DOM 特徵：貼文、搜尋框、按讚/分享按鈕等。
        try:
            if self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                """
                return Boolean(document.querySelector(
                    "div[role='article'],div[data-pagelet^='FeedUnit']," +
                    "input[aria-label='Search Facebook'],input[placeholder='Search Facebook']," +
                    "input[aria-label='搜尋 Facebook'],input[aria-label='Hanapin sa Facebook']," +
                    "[aria-label='Like'],[aria-label='讚'],[aria-label='Share']," +
                    "a[href*='/friends'],a[href*='/marketplace']"
                ));
                """
            ):
                return True
        except Exception:
            pass

        return False

    def _is_real_checkpoint(self, url: str, source: str) -> bool:
        """
        判斷是否為真正的 Checkpoint。
        注意：checkpoint_src=any 不算真正 Checkpoint。
        """
        checkpoint_url = (
            "/checkpoint" in url
            or "facebook.com/checkpoint" in url
        )

        # checkpoint_src=any 很常出現在正常首頁，不當作 checkpoint。
        if "checkpoint_src=any" in url:
            checkpoint_url = False

        return checkpoint_url and self._contains_any(source, _CHECKPOINT_SIGNALS)

    @staticmethod
    def _contains_any(text: str, signals: list[str]) -> bool:
        """檢查文字是否包含任一關鍵字訊號。"""
        return any(signal in text for signal in signals)


# ─────────────────────────────────────────────
# Facebook 搜尋與加好友
# ─────────────────────────────────────────────

class FacebookFriendAdder:
    """
    在 Facebook 搜尋菲律賓彩票關鍵字並加入固定人數的好友。
    加入人數由 FriendConfig.add_friend_count 控制（預設 2 人）。
    """

    def __init__(
        self,
        ctrl: BrowserController,
        cfg: Optional[FriendConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._cfg = cfg or CONFIG.friend

    def run(self) -> int:
        """
        執行完整的搜尋與加好友流程。

        Returns:
            成功送出好友邀請的人數。
        """
        keyword = random.choice(self._cfg.search_keywords)
        _log.info("開始加好友流程，搜尋關鍵字：「%s」", keyword)

        # 導航至搜尋人物頁面
        search_url = FACEBOOK_SEARCH_URL.format(query=keyword.replace(" ", "%20"))
        self._ctrl.navigate(search_url)
        random_sleep(
            self._cfg.search_wait_min,
            self._cfg.search_wait_max,
        )

        added = self._add_friends_from_results()
        _log.info("加好友流程完成，共成功加入 %d 人。", added)
        return added

    @with_retry(max_retries=2, wait_sec=3.0, fallback=0)
    def _add_friends_from_results(self) -> int:
        """V7.6：多語言、可見元素優先的好友邀請流程，最多只送出設定數量。"""
        target = max(0, int(self._cfg.add_friend_count))
        if target == 0:
            return 0

        self._ctrl._ensure_driver()
        script = r"""
        const limit = arguments[0];
        const addTerms = [
          'add friend','加朋友','新增朋友','添加好友',
          'magdagdag ng kaibigan','idagdag bilang kaibigan','add as friend',
          'ajouter','ajouter comme ami','ajouter aux amis',
          'เพิ่มเพื่อน','เพิ่มเป็นเพื่อน','ส่งคำขอเป็นเพื่อน',
          'إضافة صديق','إضافة إلى الأصدقاء','إرسال طلب صداقة'
        ];
        const sentTerms = [
          'cancel request','friend request sent','取消邀請','已送出邀請',
          'kanselahin ang request','nakapadala na ng friend request',
          "annuler l’invitation","annuler l'invitation",'invitation envoyée',
          'ยกเลิกคำขอ','ส่งคำขอเป็นเพื่อนแล้ว',
          'إلغاء الطلب','تم إرسال طلب الصداقة'
        ];
        function visible(el){
          if(!el) return false;
          const r=el.getBoundingClientRect(), s=getComputedStyle(el);
          return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
                 s.display!=='none' && s.visibility!=='hidden' && !el.disabled;
        }
        function label(el){
          return ((el.getAttribute('aria-label')||el.innerText||el.textContent||'')+'').trim().toLowerCase();
        }
        const nodes=[...document.querySelectorAll('[role="button"],button,[aria-label]')];
        const result=[];
        const seen=new Set();
        for(const el of nodes){
          if(result.length>=limit) break;
          if(!visible(el) || el.closest('[role="dialog"]')) continue;
          const text=label(el);
          if(!text || sentTerms.some(t=>text.includes(t))) continue;
          if(!addTerms.some(t=>text===t || text.startsWith(t+' ') || text.includes(t))) continue;
          const key=(el.getAttribute('aria-label')||text)+'|'+Math.round(el.getBoundingClientRect().top);
          if(seen.has(key)) continue;
          seen.add(key);
          try{
            el.scrollIntoView({block:'center'});
            el.click();
            result.push(text);
          }catch(e){}
        }
        return result;
        """

        try:
            clicked = self._ctrl.driver.execute_script(script, target)  # type: ignore[union-attr]
        except Exception as exc:
            _log.warning("執行好友邀請腳本失敗：%s", exc)
            return 0

        added = len(clicked or [])
        if added:
            for index in range(1, added + 1):
                _log.info("已送出好友邀請（第 %d/%d 人）。", index, target)
                if index < added:
                    random_sleep(
                        self._cfg.after_add_pause_min,
                        self._cfg.after_add_pause_max,
                    )
        else:
            _log.warning("在搜尋結果中找不到可用的加好友按鈕。")
        return added



# ─────────────────────────────────────────────
# Facebook 確認收到的好友邀請
# ─────────────────────────────────────────────

class FacebookFriendConfirmer:
    """接受別人傳來的好友邀請，與主動加好友功能完全分開。"""

    REQUESTS_URL = "https://www.facebook.com/friends/requests"

    def __init__(
        self,
        ctrl: BrowserController,
        cfg: Optional[FriendConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._cfg = cfg or CONFIG.friend

    def run(self) -> int:
        """進入好友邀請頁，確認指定數量的邀請。"""
        target = max(0, int(self._cfg.confirm_friend_count))
        if target == 0:
            _log.info("確認好友數量設定為 0，跳過。")
            return 0

        _log.info("開始確認好友邀請流程，最多確認 %d 人。", target)
        self._ctrl.navigate(self.REQUESTS_URL)
        random_sleep(self._cfg.search_wait_min, self._cfg.search_wait_max)
        confirmed = self._confirm_requests(target)
        _log.info("確認好友邀請流程完成，共確認 %d 人。", confirmed)
        return confirmed

    @with_retry(max_retries=2, wait_sec=3.0, fallback=0)
    def _confirm_requests(self, target: int) -> int:
        """支援中文、英文與菲律賓文，逐一確認並保留自然間隔。"""
        self._ctrl._ensure_driver()
        find_script = r"""
        const confirmTerms = [
          'confirm','確認','确认',
          'kumpirmahin','tanggapin','accept',
          'confirmer','accepter',
          'ยืนยัน','ตอบรับ','قبول','تأكيد'
        ];
        const rejectTerms = [
          'delete','remove','刪除','删除','拒絕','拒绝',
          'tanggalin','delete request','supprimer','refuser',
          'ลบ','ปฏิเสธ','حذف','رفض'
        ];
        function visible(el){
          if(!el) return false;
          const r=el.getBoundingClientRect(), s=getComputedStyle(el);
          return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
                 s.display!=='none' && s.visibility!=='hidden' && !el.disabled;
        }
        function label(el){
          return ((el.getAttribute('aria-label')||el.innerText||el.textContent||'')+'').trim().toLowerCase();
        }
        const nodes=[...document.querySelectorAll('[role="button"],button,[aria-label]')];
        for(const el of nodes){
          if(!visible(el)) continue;
          const text=label(el);
          if(!text || rejectTerms.some(t=>text.includes(t))) continue;
          if(!confirmTerms.some(t=>text===t || text.startsWith(t+' ') || text.includes(t))) continue;
          return el;
        }
        return null;
        """

        confirmed = 0
        while confirmed < target:
            try:
                button = self._ctrl.driver.execute_script(find_script)  # type: ignore[union-attr]
            except Exception as exc:
                _log.warning("搜尋確認好友按鈕失敗：%s", exc)
                break

            if button is None:
                break

            try:
                self._ctrl.driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", button
                )  # type: ignore[union-attr]
                random_sleep(0.3, 0.8)
                self._ctrl.click(button)
                confirmed += 1
                _log.info("已確認好友邀請（第 %d/%d 人）。", confirmed, target)
                if confirmed < target:
                    random_sleep(
                        self._cfg.after_confirm_pause_min,
                        self._cfg.after_confirm_pause_max,
                    )
            except Exception as exc:
                _log.warning("點擊確認好友按鈕失敗：%s", exc)
                break

        if confirmed == 0:
            _log.info("目前沒有找到可確認的好友邀請。")
            return 0

        finish_min = getattr(self._cfg, "after_confirm_finish_min", 3.0)
        finish_max = getattr(self._cfg, "after_confirm_finish_max", 8.0)
        _log.info("確認好友流程完成，等待 %.0f～%.0f 秒。", finish_min, finish_max)
        random_sleep(finish_min, finish_max)
        return confirmed


# ─────────────────────────────────────────────
# Facebook 搜尋工具（供 behavior 模組使用）
# ─────────────────────────────────────────────

class FacebookSearcher:
    """
    在 Facebook 搜尋列執行關鍵字搜尋。
    用於養號流程中的「搜尋指定關鍵字」步驟。
    """

    def __init__(self, ctrl: BrowserController) -> None:
        self._ctrl = ctrl

    @with_retry(max_retries=2, wait_sec=3.0, fallback=False)
    def search(self, keyword: str) -> bool:
        """
        在 Facebook 搜尋列輸入關鍵字並執行搜尋。

        Args:
            keyword: 要搜尋的關鍵字。

        Returns:
            True 表示搜尋成功，False 表示失敗。
        """
        _log.info("執行 Facebook 搜尋：「%s」", keyword)

        # 方式一：使用搜尋列輸入框（aria-label）
        search_input = self._ctrl.find(
            By.XPATH,
            "//input[@aria-label='Search Facebook' or @placeholder='Search Facebook'"
            " or @aria-label='搜尋 Facebook' or @aria-label='Hanapin sa Facebook']",
        )

        if search_input is None:
            # 方式二：使用頂部搜尋圖示（有些版本需先點擊才展開輸入框）
            search_icon = self._ctrl.find(
                By.XPATH, "//div[@aria-label='Facebook' or @data-testid='search-button']"
            )
            if search_icon:
                self._ctrl.click(search_icon)
                random_sleep(0.5, 1.2)
                search_input = self._ctrl.find(
                    By.XPATH,
                    "//input[@type='search' or @role='searchbox']",
                )

        if search_input is None:
            _log.warning("找不到搜尋輸入框。")
            return False

        # 清空並輸入關鍵字
        search_input.clear()
        random_sleep(0.3, 0.7)
        self._ctrl.type_text(search_input, keyword)
        random_sleep(0.5, 1.0)
        search_input.send_keys(Keys.RETURN)

        # 等待搜尋結果載入
        random_sleep(2.0, 4.0)
        _log.info("搜尋「%s」完成。", keyword)
        return True
