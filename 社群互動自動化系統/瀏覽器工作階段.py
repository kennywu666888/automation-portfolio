import logging
import re
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys


PERSONAL_PROFILE_RE = re.compile(
    r"^https?://(?:www\.)?facebook\.com/profile\.php\?id=(\d+)",
    re.IGNORECASE,
)

FACEBOOK_NOTIFICATION_ORIGINS = (
    "https://www.facebook.com",
    "https://facebook.com",
    "https://m.facebook.com",
    "https://web.facebook.com",
)
_log = logging.getLogger(__name__)


def grant_facebook_notification_permission(driver):
    """Set Chrome's native Facebook notification permission before preflight."""
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


def dismiss_facebook_notification_overlay(driver):
    """Close only Facebook's in-page notification request modal."""
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
              if(!dialogTerms.some(term=>textOf(dialog).includes(term)))continue;
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
            driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            return True
    except Exception:
        pass
    return False


class BrowserSession:
    def __init__(self, info):
        self.info = info
        self.driver = None
        self.personal_profile_url = ""

    def connect(self):
        options = Options()
        options.add_experimental_option(
            "debuggerAddress", self.info.selenium_address
        )
        self.driver = webdriver.Chrome(
            service=Service(self.info.webdriver_path), options=options
        )
        self.driver.set_page_load_timeout(45)
        if grant_facebook_notification_permission(self.driver):
            _log.info("Selenium 接管後已立即預設允許 Facebook 通知。")
        else:
            _log.warning("無法立即設定 Facebook 通知權限；切換 Facebook 分頁後仍會清理遮罩。")
        return self.driver

    def switch_to_facebook(self):
        """Switch to the existing Facebook tab without changing its URL."""
        for handle in list(self.driver.window_handles):
            try:
                self.driver.switch_to.window(handle)
                if "facebook.com" in self.driver.current_url.lower():
                    dismiss_facebook_notification_overlay(self.driver)
                    return True
            except Exception:
                pass
        raise RuntimeError(
            "找不到 AdsPower 既有的 Facebook 分頁；"
            "已取消建立新分頁或導向 Facebook 首頁"
        )

    @staticmethod
    def _canonical_personal_profile_url(url):
        match = PERSONAL_PROFILE_RE.match((url or "").strip())
        if not match:
            return ""
        return f"https://www.facebook.com/profile.php?id={match.group(1)}"

    def cache_current_personal_profile_url(self, stop_event=None, timeout=6.0):
        """Cache the AdsPower start page when it is the owner's profile."""
        deadline = time.monotonic() + max(0.5, float(timeout))
        last_url = ""
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("操作已停止")
            last_url = self.driver.current_url or ""
            profile_url = self._canonical_personal_profile_url(last_url)
            if profile_url:
                try:
                    ready = self.driver.execute_script(
                        "return document.readyState !== 'loading' "
                        "&& !!document.querySelector('[role=\"main\"]');"
                    )
                except Exception:
                    ready = False
                if ready:
                    self.set_personal_profile_url(profile_url)
                    return profile_url
            time.sleep(0.25)
        raise RuntimeError(
            "AdsPower 的 Facebook 起始分頁不是可用的本人個人主頁："
            f"{last_url or '(空白網址)'}"
        )

    def ensure_startup_personal_profile_url(self, stop_event=None):
        """Preserve tab 1; recover the owner Timeline in the Facebook tab.

        If the Facebook tab starts on messages, notifications, reels, home, or
        another Facebook page, visit Facebook Home in that same tab, extract
        the single visible owner Timeline/top-avatar profile.php URL, navigate
        to it, and verify it before any RC18 task starts. Never use /me.
        """
        try:
            return self.cache_current_personal_profile_url(
                stop_event, timeout=1.2
            )
        except RuntimeError:
            if stop_event is not None and stop_event.is_set():
                raise

        try:
            self.driver.set_page_load_timeout(12)
            self.driver.get("https://www.facebook.com")
        except (TimeoutException, WebDriverException):
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass
        finally:
            try:
                self.driver.set_page_load_timeout(45)
            except Exception:
                pass

        words = (
            "timeline", "journal", "chronologie", "ไทม์ไลน์", "يوميات",
            "動態時報", "动态时报", "時間軸", "时间线",
        )
        for attempt in range(2):
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError("操作已停止")
                try:
                    rows = self.driver.execute_script(
                        r"""
                        const words=arguments[0];
                        const visible=e=>{
                          if(!e||!e.isConnected)return false;
                          const s=getComputedStyle(e),r=e.getBoundingClientRect();
                          return s.display!=='none'&&s.visibility!=='hidden'&&
                                 r.width>0&&r.height>0;
                        };
                        return [...document.querySelectorAll('a[href]')]
                          .filter(a=>visible(a)&&a.href.includes('profile.php?id='))
                          .map(a=>{
                            const r=a.getBoundingClientRect();
                            const label=(a.getAttribute('aria-label')||
                              a.getAttribute('title')||a.innerText||'')
                              .replace(/\s+/g,' ').trim();
                            const folded=label.toLocaleLowerCase();
                            return {href:a.href,label,
                              timeline:words.some(w=>folded.includes(w)),
                              topAvatar:r.top>=0&&r.top<=180&&r.width>=20&&
                                r.width<=90&&r.height>=20&&r.height<=90};
                          });
                        """,
                        words,
                    ) or []
                except (TimeoutException, WebDriverException):
                    rows = []

                labelled = {}
                top_avatars = {}
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    href = str(row.get("href") or "")
                    canonical = self._canonical_personal_profile_url(href)
                    if not canonical:
                        continue
                    if row.get("timeline"):
                        labelled.setdefault(canonical, row.get("label") or "")
                    if row.get("topAvatar"):
                        top_avatars.setdefault(canonical, row.get("label") or "")
                candidates = labelled if len(labelled) == 1 else top_avatars
                if len(candidates) == 1:
                    profile_url = next(iter(candidates))
                    self.set_personal_profile_url(profile_url)
                    if not self.go_personal_profile(
                        profile_url, stop_event, timeout=20.0
                    ):
                        raise RuntimeError(
                            f"已取得本人網址但無法進入個人主頁：{profile_url}"
                        )
                    return profile_url
                time.sleep(0.35)

            if attempt == 0:
                try:
                    self.driver.refresh()
                except (TimeoutException, WebDriverException):
                    try:
                        self.driver.execute_script("window.stop();")
                    except Exception:
                        pass
                time.sleep(0.6)

        raise RuntimeError(
            "Facebook首頁找不到唯一的本人Timeline／頂端頭像網址"
        )

    def set_personal_profile_url(self, profile_url):
        canonical = self._canonical_personal_profile_url(profile_url)
        if not canonical:
            raise RuntimeError(f"無效的本人個人主頁網址：{profile_url}")
        self.personal_profile_url = canonical
        if self.driver is not None:
            setattr(self.driver, "_facebook_personal_profile_url", canonical)
        return canonical

    def go_personal_profile(self, profile_url="", stop_event=None, timeout=20.0):
        """Return to the cached personal profile; never fall back to / or /me."""
        target = (
            profile_url
            or self.personal_profile_url
            or getattr(self.driver, "_facebook_personal_profile_url", "")
        )
        target = self.set_personal_profile_url(target)

        current = self._canonical_personal_profile_url(self.driver.current_url)
        if current == target:
            return True

        try:
            self.driver.get(target)
        except TimeoutException:
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass

        deadline = time.monotonic() + max(1.0, float(timeout))
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            current = self._canonical_personal_profile_url(
                self.driver.current_url
            )
            if current == target:
                try:
                    ready = self.driver.execute_script(
                        "return document.readyState !== 'loading' "
                        "&& !!document.querySelector('[role=\"main\"]');"
                    )
                except Exception:
                    ready = False
                if ready:
                    return True
            time.sleep(0.25)
        raise RuntimeError(f"無法返回已快取的本人個人主頁：{target}")

    def detach(self):
        self.driver = None
