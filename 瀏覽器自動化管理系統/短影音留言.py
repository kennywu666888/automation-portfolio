"""獨立 Reels 留言任務：回到個人主頁並在第一篇貼文留言。"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Iterable, Optional
from types import SimpleNamespace

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

from 行為模擬 import FeedInteractor, PostInfo

DEFAULT_REELS_COMMENT = """Register!👇Click
PHP LOTTO 
https://www.phplottos.com/invite/69ae8e1992362
💥PINAKA DAKOG WINNING RATE💥
3D WIN 1= 990
2D WIN 1= 950
1. Share Live
2. Mag-comment ng best mong 3D number 1 combination.
3. Mag-sign up ka bilang miyembro gamit ang link l. Para ma bigyan ng VIP# kapag nag deposit ka...
👇👇👇👇TONY VIP 

🎉 TONY VIP DAILY GIVEAWAY 🎉
https://t.me/num3dlotto
🎉 Sumali na sa aming Telegram Activity Channel!
🎁 Huwag palampasin ang aming mga random raffle draw na ginaganap sa iba't ibang oras."""

_log = logging.getLogger("Reels留言")

COMMENT_HINTS = (
    "write a public comment",
    "write a comment",
    "comment as",
    "comment",
    "留言",
    "發表留言",
    "寫留言",
    "sumulat ng pampublikong komento",
    "sumulat ng komento",
    "magkomento",
    "komento",
    "écrire un commentaire public",
    "écrire un commentaire",
    "commenter",
    "เขียนความคิดเห็นสาธารณะ",
    "เขียนความคิดเห็น",
    "ความคิดเห็น",
    "اكتب تعليقًا عامًا",
    "اكتب تعليقًا",
    "تعليق",
)


LIKE_HINTS = (
    "like",
    "讚",
    "赞",
    "gusto",
    "j’aime",
    "j'aime",
    "ถูกใจ",
    "إعجاب",
    "أعجبني",
)

COMMENT_BOX_XPATHS = (
    ".//*[@contenteditable='true' and @role='textbox' and @data-lexical-editor='true']",
    ".//*[@contenteditable='true' and @role='textbox' and (@aria-placeholder or @aria-label)]",
    ".//*[@contenteditable='true' and @role='textbox']",
    ".//*[@contenteditable='true' and @data-lexical-editor='true']",
    ".//*[@contenteditable='true']",
)


class ReelsCommentTask:
    def __init__(self, driver, profile_name: str, text_file: str, stop_event=None, mode: str = "default"):
        self.driver = driver
        self.profile_name = profile_name
        self.text_file = text_file
        self.stop_event = stop_event
        self.mode = (mode or "default").strip().lower()

    def _stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    def _load_complete_custom_comment(self) -> str:
        """讀取自選 TXT 的完整內容，保留換行、空白行與 Emoji。"""
        path = Path(self.text_file).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"找不到 Reels 留言文案檔案：{path}")

        raw = path.read_bytes()
        if not raw:
            raise ValueError(f"Reels 留言文案沒有可用內容：{path}")

        comment: Optional[str] = None
        decode_error: Optional[UnicodeDecodeError] = None
        # UTF-8／UTF-8 BOM 可完整保留 Emoji；UTF-16 用於相容 Windows
        # 記事本另存的 Unicode 文字檔。只在 UTF-8 解碼失敗時才後備。
        for encoding in ("utf-8-sig", "utf-16"):
            try:
                comment = raw.decode(encoding)
                break
            except UnicodeDecodeError as exc:
                decode_error = exc

        if comment is None:
            raise ValueError(
                f"Reels 留言文案不是有效的 UTF-8 或 UTF-16 文字檔：{path}"
            ) from decode_error

        # Facebook Lexical 使用 LF；只統一換行，不拆行、不隨機抽取，
        # 也不移除文件中間的空白行、空格、網址或 Emoji。
        comment = comment.replace("\r\n", "\n").replace("\r", "\n")
        comment = comment.strip("\ufeff\n")
        if not comment.strip():
            raise ValueError(f"Reels 留言文案沒有可用內容：{path}")
        _log.info(
            "[%s] 已讀取完整自選 Reels 留言文案：%d 字元、%d 行",
            self.profile_name,
            len(comment),
            comment.count("\n") + 1,
        )
        return comment

    def _ensure_cached_personal_profile(self) -> None:
        """只允許在 Health Check 後快取的本人 Timeline 上執行。"""
        timeline = str(
            getattr(self.driver, "_facebook_personal_profile_url", "") or ""
        ).split("&", 1)[0]
        if not (
            timeline.startswith("https://www.facebook.com/profile.php?id=")
            or timeline.startswith("https://facebook.com/profile.php?id=")
        ):
            raise RuntimeError(
                "Reels 留言缺少已驗證的本人 Timeline URL，拒絕在目前頁面執行"
            )
        current = str(self.driver.current_url or "").split("&", 1)[0]
        if current != timeline:
            _log.info(
                "[%s] Reels 留言開始前切換至已快取的本人主頁：%s",
                self.profile_name,
                timeline,
            )
            self.driver.get(timeline)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                current = str(self.driver.current_url or "").split("&", 1)[0]
                if current == timeline and self.driver.execute_script(
                    "return !!document.querySelector('[role=main]');"
                ):
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError("12 秒內無法確認進入已快取的本人 Timeline")

    @staticmethod
    def _visible(elements: Iterable[WebElement]) -> list[WebElement]:
        result: list[WebElement] = []
        for element in elements:
            try:
                if element.is_displayed() and element.is_enabled():
                    result.append(element)
            except StaleElementReferenceException:
                continue
        return result

    @staticmethod
    def _element_text(element: WebElement) -> str:
        values: list[str] = []
        for attr in ("aria-label", "aria-placeholder", "placeholder", "title"):
            try:
                value = element.get_attribute(attr)
                if value:
                    values.append(value)
            except Exception:
                pass
        try:
            if element.text:
                values.append(element.text)
        except Exception:
            pass
        return " ".join(values).strip().lower()

    def _is_comment_box(self, element: WebElement) -> bool:
        try:
            if not element.is_displayed() or not element.is_enabled():
                return False
            if element.get_attribute("contenteditable") != "true":
                return False
            hint = self._element_text(element)
            lexical = (element.get_attribute("data-lexical-editor") or "").lower() == "true"
            role = (element.get_attribute("role") or "").lower() == "textbox"
            # Lexical 編輯器或具備留言提示文字的可編輯 textbox 均接受。
            return lexical or (role and any(word in hint for word in COMMENT_HINTS))
        except StaleElementReferenceException:
            return False

    def _find_box_in(self, root: WebElement) -> Optional[WebElement]:
        fallback: Optional[WebElement] = None
        for xpath in COMMENT_BOX_XPATHS:
            try:
                elements = self._visible(root.find_elements(By.XPATH, xpath))
            except StaleElementReferenceException:
                return None
            for element in elements:
                if self._is_comment_box(element):
                    return element
                if fallback is None:
                    fallback = element
        return fallback

    def _find_active_reel_dialog(self, wait_seconds: float = 0.0) -> Optional[WebElement]:
        """取得目前 Reels 彈窗中包含留言框的最內層 dialog。

        Facebook 專業模式展開已有留言時，留言編輯器不是貼文
        ``role=article`` 的子節點，而是 Reels dialog 底部的兄弟節點。舊流程
        固定在背景 article 內搜尋，因此畫面明明有留言框仍會判定找不到。
        """
        deadline = time.monotonic() + max(0.0, wait_seconds)
        while True:
            try:
                on_reel = "/reel/" in str(self.driver.current_url or "").casefold()
            except Exception:
                on_reel = False
            if on_reel:
                try:
                    dialogs = self.driver.find_elements(By.CSS_SELECTOR, "[role='dialog']")
                except Exception:
                    dialogs = []
                # Facebook 會以巢狀 role=dialog 包住同一個 Reels；DOM 後方通常
                # 是較內層容器，倒序可避免選到包含其他彈窗的外層遮罩。
                for dialog in reversed(dialogs):
                    try:
                        if not dialog.is_displayed():
                            continue
                        if self._find_box_in(dialog) is not None:
                            return dialog
                    except StaleElementReferenceException:
                        continue
                    except Exception:
                        continue
            if time.monotonic() >= deadline or self._stopped():
                return None
            time.sleep(0.2)

    def _expand_comment_area(self, article: WebElement) -> None:
        candidates: list[WebElement] = []
        for xpath in (
            ".//*[@role='button']",
            ".//span[@role='button']",
            ".//div[@role='button']",
            ".//a[@role='link']",
        ):
            try:
                candidates.extend(self._visible(article.find_elements(By.XPATH, xpath)))
            except StaleElementReferenceException:
                return

        for element in candidates:
            label = self._element_text(element)
            if not any(word in label for word in COMMENT_HINTS):
                continue
            try:
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                self.driver.execute_script("arguments[0].click();", element)
                _log.info("[%s] 已點擊留言按鈕展開留言區：%s", self.profile_name, label[:80])
                time.sleep(1.5)
                return
            except Exception:
                continue



    def _prepare_profile_timeline(self) -> None:
        """只在目前個人主頁定位貼文時間軸，不切換 Reels 分頁。"""
        result = self.driver.execute_script(
            r"""
            const visible = el => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 40 && r.height > 15 && s.display !== 'none' && s.visibility !== 'hidden';
            };
            const words = ['posts','貼文','帖子','mga post','โพสต์','المنشورات'];
            const nodes = [...document.querySelectorAll('[role=tab],a,[role=button],h1,h2,h3,span,div')];
            for (const el of nodes) {
                if (!visible(el)) continue;
                const text = ((el.getAttribute('aria-label') || el.innerText || el.textContent || '') + '').trim().toLowerCase();
                if (!words.some(w => text === w)) continue;
                el.scrollIntoView({block:'center', inline:'nearest'});
                return {ok:true, text};
            }
            // 找不到明確 Posts 標籤時，只在個人主頁向下移動一個視窗高度的 60%，促使首篇貼文渲染。
            window.scrollBy({top: Math.max(280, Math.floor(window.innerHeight * 0.60)), behavior:'instant'});
            return {ok:false, fallback:true};
            """
        )
        if isinstance(result, dict) and result.get('ok'):
            _log.info('[%s] 已在個人主頁定位貼文時間軸：%s', self.profile_name, result.get('text', 'posts'))
        else:
            _log.info('[%s] 個人主頁未找到明確 Posts 標籤，已小幅移動以載入第一篇貼文', self.profile_name)
        time.sleep(2.0)

    def _find_first_article(self) -> WebElement:
        """定位個人主頁第一篇可操作貼文容器。

        Facebook 有時會先渲染只有 ``Loading...`` 的 ``role=article`` 骨架，
        真正貼文則未必保留 ``role=article``。因此先找可用 article；找不到時，
        以可見的主 Like 鍵、Comment／Share 與 Lexical 留言框反向推導貼文容器。
        """
        wait = WebDriverWait(self.driver, 10)

        def locate(driver):
            try:
                return driver.execute_script(
                    r"""
                    const visible = el => {
                        if (!el || el.closest('[role="dialog"]')) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width >= 300 && r.height >= 80 &&
                               s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const label = el => ((el.getAttribute('aria-label') ||
                                          el.getAttribute('aria-placeholder') ||
                                          el.innerText || el.textContent || '') + '').trim().toLowerCase();
                    const likeWords = [
                        'like','讚','赞',"j’aime","j'aime",'gusto','ถูกใจ',
                        'إعجاب','أعجبني'
                    ];
                    const commentWords = [
                        'comment','留言','commenter','komento','ความคิดเห็น','تعليق'
                    ];
                    const shareWords = [
                        'share','分享','partager','ibahagi','แชร์','مشاركة'
                    ];
                    const hasWord = (text, words) => words.some(w => text === w || text.includes(w));

                    // 0) 任務目標是「個人主頁第一篇 Reels」。先依 Facebook
                    //    貼文 DOM 順序鎖定第一個含 /reel/ID 的頂層 article，
                    //    不要求 Like／Comment 已完成渲染；否則第一篇控制列
                    //    載入較慢時，舊流程會跳去第二篇。
                    const articles = [...document.querySelectorAll('[role="article"]')];
                    for (const el of articles) {
                        if (!visible(el)) continue;
                        let p = el.parentElement, nested = false;
                        while (p) {
                            if (p.getAttribute && p.getAttribute('role') === 'article') { nested = true; break; }
                            p = p.parentElement;
                        }
                        if (nested) continue;
                        const text = (el.innerText || '').trim();
                        if (!text || /^loading[.…]*$/i.test(text)) continue;
                        const reelLink = [...el.querySelectorAll('a[href]')].find(a =>
                            /\/reel\/\d+/i.test(a.href || a.getAttribute('href') || '')
                        );
                        if (reelLink) return el;
                    }

                    // 1) 沒有結構化 Reel URL 時才使用舊版操作列後備。
                    for (const el of articles) {
                        if (!visible(el)) continue;
                        let p = el.parentElement, nested = false;
                        while (p) {
                            if (p.getAttribute && p.getAttribute('role') === 'article') { nested = true; break; }
                            p = p.parentElement;
                        }
                        if (nested) continue;
                        const text = (el.innerText || '').trim();
                        if (!text || /^loading[.…]*$/i.test(text)) continue;
                        const controls = [...el.querySelectorAll('[role="button"],[aria-label],[contenteditable="true"]')];
                        const hasLike = controls.some(n => hasWord(label(n), likeWords));
                        const hasComment = controls.some(n => hasWord(label(n), commentWords) || n.getAttribute('contenteditable') === 'true');
                        if (hasLike && hasComment) return el;
                    }

                    // 2) Facebook 新版有時真正貼文沒有 role=article。
                    //    從可見 Like 主按鈕往上找同時包含留言框／Comment 與 Share 的最小合理容器。
                    const likes = [...document.querySelectorAll('[role="button"][aria-label],button[aria-label]')]
                        .filter(el => visible(el) && hasWord(label(el), likeWords));
                    let best = null;
                    let bestScore = -1e9;
                    for (const like of likes) {
                        let cur = like;
                        for (let depth = 0; depth < 32 && cur; depth++, cur = cur.parentElement) {
                            if (!visible(cur)) continue;
                            const r = cur.getBoundingClientRect();
                            if (r.width < 240 || r.height < 50 || r.height > 12000) continue;
                            const controls = [...cur.querySelectorAll('[role="button"],[aria-label],[contenteditable="true"]')];
                            const likeCount = controls.filter(n => hasWord(label(n), likeWords)).length;
                            const hasComment = controls.some(n => hasWord(label(n), commentWords));
                            const hasShare = controls.some(n => hasWord(label(n), shareWords));
                            const hasEditor = controls.some(n => n.getAttribute('contenteditable') === 'true' &&
                                                               (n.getAttribute('role') === 'textbox' ||
                                                                n.getAttribute('data-lexical-editor') === 'true'));
                            if (!hasComment && !hasEditor) continue;
                            let score = 0;
                            score += hasComment ? 8 : 0;
                            score += hasShare ? 5 : 0;
                            score += hasEditor ? 10 : 0;
                            score += likeCount === 1 ? 6 : Math.max(0, 4 - likeCount);
                            score -= depth * 0.25;
                            score -= Math.max(0, r.height - 1800) / 400;
                            if (score > bestScore) { bestScore = score; best = cur; }
                        }
                    }
                    if (best) return best;

                    // 3) 若主 Like 尚未可用，從第一個 Lexical 留言框反向找同一篇貼文互動容器。
                    const editors = [...document.querySelectorAll('[contenteditable="true"]')].filter(el => {
                        if (el.closest('[role="dialog"]')) return false;
                        const s = getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden' &&
                               (el.getAttribute('role') === 'textbox' ||
                                el.getAttribute('data-lexical-editor') === 'true' ||
                                hasWord(label(el), commentWords));
                    });
                    for (const editor of editors) {
                        let cur = editor;
                        for (let depth = 0; depth < 32 && cur; depth++, cur = cur.parentElement) {
                            const r = cur.getBoundingClientRect();
                            if (r.width < 240 || r.height < 50 || r.height > 12000) continue;
                            const controls = [...cur.querySelectorAll('[role="button"],[aria-label],[contenteditable="true"]')];
                            const hasLike = controls.some(n => hasWord(label(n), likeWords));
                            if (hasLike) return cur;
                        }
                    }
                    return null;
                    """
                ) or False
            except Exception:
                return False

        container = wait.until(locate)
        if not container:
            raise RuntimeError("個人主頁找不到第一篇可操作貼文容器（article 與 Like/Comment DOM 備援均失敗）")
        try:
            role = container.get_attribute("role") or ""
        except Exception:
            role = ""
        if role == "article":
            _log.info("[%s] 找到第一篇 article", self.profile_name)
        else:
            _log.info("[%s] 第一篇貼文未使用 role=article，已由 Like／Comment／Lexical DOM 反向定位容器", self.profile_name)
        return container

    @staticmethod
    def _reel_id(url: str) -> str:
        match = re.search(r"/reel/(\d+)", str(url or ""), flags=re.IGNORECASE)
        return match.group(1) if match else ""

    def _article_reel_url(self, article: WebElement) -> str:
        """取得已鎖定文章本身的第一個 canonical Reel URL。"""
        try:
            value = self.driver.execute_script(
                r"""
                const root=arguments[0];
                const links=[...root.querySelectorAll('a[href]')];
                const hit=links.find(a=>/\/reel\/\d+/i.test(a.href||a.getAttribute('href')||''));
                if(!hit) return '';
                const match=String(hit.href||hit.getAttribute('href')||'').match(/^(https?:\/\/[^/]+)?\/reel\/(\d+)/i);
                return match ? (location.origin + '/reel/' + match[2] + '/') : '';
                """,
                article,
            )
            return str(value or "")
        except Exception:
            return ""

    def _open_exact_reel_dialog(self, target_url: str) -> WebElement:
        """直接開啟已鎖定的第一篇 Reel，並核對 URL ID 後取得 dialog。"""
        target_id = self._reel_id(target_url)
        if not target_id:
            raise RuntimeError("第一篇 Reels URL 缺少可驗證的 ID")
        self.driver.get(target_url)
        dialog = self._find_active_reel_dialog(wait_seconds=12.0)
        current_id = self._reel_id(str(self.driver.current_url or ""))
        if current_id != target_id:
            raise RuntimeError(
                f"開啟第一篇 Reels 後 ID 不一致：target={target_id}, current={current_id or 'none'}"
            )
        if dialog is None:
            raise RuntimeError("已開啟第一篇 Reels 精確網址，但 12 秒內找不到留言彈窗")
        return dialog

    def _position_action_row(self, article: WebElement) -> None:
        """依 DOM 找第一篇貼文的主操作列，不使用固定像素或猜測滑動距離。"""
        result = self.driver.execute_script(
            r"""
            const root = arguments[0];
            const commentWords = ['comment','留言','komento','ความคิดเห็น','تعليق'];
            const shareWords = ['share','分享','ibahagi','partager','مشاركة'];
            const nodes = [...root.querySelectorAll('[role="button"],[aria-label],button')];
            function text(el){return ((el.getAttribute('aria-label')||el.innerText||el.textContent)||'').trim().toLowerCase();}
            function rect(el){try{return el.getBoundingClientRect();}catch(e){return null;}}
            const candidates = nodes.filter(el => {
                const t=text(el);
                return commentWords.some(w=>t.includes(w)) || shareWords.some(w=>t.includes(w));
            });
            if (!candidates.length) return {ok:false, reason:'action_anchor_not_found'};
            // 主操作列通常同時含 Comment 與 Share；找兩者垂直位置最接近的一組。
            let anchor = candidates[0], best = 99999;
            for (const a of candidates) {
                const ar=rect(a); if(!ar) continue;
                for (const b of candidates) {
                    if(a===b) continue;
                    const at=text(a), bt=text(b), br=rect(b); if(!br) continue;
                    const mixed=(commentWords.some(w=>at.includes(w))&&shareWords.some(w=>bt.includes(w))) ||
                                (shareWords.some(w=>at.includes(w))&&commentWords.some(w=>bt.includes(w)));
                    if(!mixed) continue;
                    const d=Math.abs(ar.top-br.top);
                    if(d<best){best=d;anchor=a;}
                }
            }
            anchor.scrollIntoView({block:'center', inline:'nearest'});
            return {ok:true, label:text(anchor), distance:best};
            """,
            article,
        )
        if isinstance(result, dict) and result.get("ok"):
            time.sleep(1.0)
            _log.info("[%s] 已依 DOM 將第一篇 article 主操作列移至可視範圍", self.profile_name)
        else:
            _log.warning("[%s] 第一篇 article 找不到 Comment／Share 操作列定位點", self.profile_name)

    def _like_first_reel(self, article: WebElement) -> None:
        """直接共用瀏覽／按讚模組 FeedInteractor.try_like，不再維護第二套 Like 判斷。"""
        try:
            self._position_action_row(article)
            _log.info("[%s] 開始 Reels Like（共用瀏覽／按讚 Like V8）", self.profile_name)

            # try_like 目前只需要 ctrl.driver；使用輕量代理避免初始化其他養號功能。
            interactor = FeedInteractor.__new__(FeedInteractor)
            interactor._ctrl = SimpleNamespace(driver=self.driver)
            interactor._comment_gen = None
            interactor._cfg = None

            # 已按讚狀態交由 Like V8 在點擊前再次安全判斷。
            clicked = interactor.try_like(PostInfo(element=article, already_liked=False))
            if clicked:
                _log.info("[%s] Reels Like 成功", self.profile_name)
            else:
                _log.info("[%s] Reels Like 未點擊：可能已按讚或找不到安全主按讚鍵", self.profile_name)
        except Exception as exc:
            _log.warning("[%s] Reels Like 失敗但不中斷留言：%s", self.profile_name, exc)

    def _find_own_comment_container(self, article: WebElement, comment: str) -> Optional[WebElement]:
        # 使用第一行及前 80 個字辨識剛送出的留言，再往上找最近的留言 article。
        marker = next((line.strip() for line in comment.splitlines() if line.strip()), comment.strip())[:80]
        if not marker:
            return None
        deadline = time.time() + 12
        while time.time() < deadline and not self._stopped():
            try:
                current_root = self._find_active_reel_dialog() or article
                container = self.driver.execute_script(
                    """
                    const root = arguments[0];
                    const marker = arguments[1];
                    const nodes = [...root.querySelectorAll('div,span')];
                    const visible = el => {
                      const r = el.getBoundingClientRect();
                      const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const hits = nodes.filter(el =>
                      visible(el) &&
                      !el.closest('[contenteditable="true"]') &&
                      (el.innerText || '').includes(marker)
                    );
                    hits.sort((a,b) => (a.innerText || '').length - (b.innerText || '').length);
                    for (const hit of hits) {
                      const row = hit.closest('[role="article"]');
                      if (!row || row === root || !root.contains(row)) continue;
                      const rowText = (row.innerText || '').replace(/\\s+/g,' ').trim().toLowerCase();
                      const pending = [
                        'posting','publishing','正在發佈','正在发布','發佈中','发布中',
                        'ipino-post','publication en cours','กำลังโพสต์',
                        'جارٍ النشر','جاري النشر','يتم النشر'
                      ];
                      if (pending.some(word => rowText.includes(word))) continue;
                      const buttons = [...row.querySelectorAll('[role="button"],button,[tabindex="0"]')];
                      if (buttons.some(b => /(^|\\s)(Like|讚|赞|J’aime|J'aime|Gusto|ถูกใจ|إعجاب|أعجبني)(\\s|$)/i.test((b.getAttribute('aria-label') || b.innerText || '').trim()))) {
                        return row;
                      }
                    }
                    return null;
                    """,
                    current_root,
                    marker,
                )
                if container:
                    return container
            except Exception:
                pass
            time.sleep(1.0)
        return None

    def _find_like_button(self, container: WebElement) -> Optional[WebElement]:
        """只在指定留言 article 內尋找留言本身的 Like 控制。"""
        try:
            return self.driver.execute_script(
                r"""
                const root=arguments[0];
                const likeWords=arguments[1].map(x=>x.toLocaleLowerCase());
                const pressedWords=[
                  'unlike','remove like','收回讚','取消讚','取消赞',
                  "je n’aime plus","je n'aime plus",'alisin ang gusto',
                  'เลิกถูกใจ','إلغاء الإعجاب'
                ];
                const visible=el=>{
                  const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                  return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
                };
                const norm=v=>(v||'').replace(/\s+/g,' ').trim().toLocaleLowerCase();
                const candidates=[...root.querySelectorAll('[role="button"],button,[tabindex="0"]')]
                  .filter(visible).map(el=>({
                    el,label:norm(el.getAttribute('aria-label')||el.innerText||el.textContent)
                  })).filter(row=>row.label&&(
                    likeWords.some(w=>row.label===w||row.label.startsWith(w+' '))||
                    pressedWords.some(w=>row.label===w||row.label.startsWith(w+' '))
                  ));
                candidates.sort((a,b)=>a.label.length-b.label.length);
                return candidates.length?candidates[0].el:null;
                """,
                container,
                list(LIKE_HINTS),
            )
        except Exception:
            return None

    @staticmethod
    def _is_pressed(button: WebElement) -> bool:
        try:
            if (button.get_attribute("aria-pressed") or "").lower() == "true":
                return True
            label = " ".join(filter(None, (
                button.get_attribute("aria-label"),
                button.get_attribute("title"),
                button.text,
            ))).strip().lower()
            return any(word in label for word in (
                "unlike", "remove like", "收回讚", "取消讚", "取消赞",
                "je n’aime plus", "je n'aime plus", "alisin ang gusto",
                "เลิกถูกใจ", "إلغاء الإعجاب",
            ))
        except Exception:
            return False

    def _like_own_comment(
        self,
        article: WebElement,
        comment: str,
        container: Optional[WebElement] = None,
    ) -> None:
        container = container or self._find_own_comment_container(article, comment)
        if container is None:
            _log.warning("[%s] 找不到剛送出的自己的留言，無法按讚", self.profile_name)
            return
        button = self._find_like_button(container)
        if button is None:
            _log.warning("[%s] 自己的留言找不到按讚按鈕，略過", self.profile_name)
            return
        if self._is_pressed(button):
            _log.info("[%s] 自己的留言已按讚，略過", self.profile_name)
            return
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
            self.driver.execute_script("arguments[0].click();", button)
            time.sleep(1.0)
            _log.info("[%s] 已替自己的留言按讚", self.profile_name)
        except Exception as exc:
            _log.warning("[%s] 自己的留言按讚失敗：%s", self.profile_name, exc)

    def _copy_to_windows_clipboard(self, text: str) -> None:
        """快速寫入 Windows Unicode 剪貼簿，不建立 tkinter 視窗。"""
        import ctypes
        from ctypes import wintypes

        if not text:
            raise RuntimeError("剪貼簿文字為空")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        GMEM_MOVEABLE = 0x0002
        CF_UNICODETEXT = 13

        # ctypes 未宣告 WinAPI 簽章時，64 位元 Python 會把 HANDLE／指標
        # 當成 32 位元整數，GlobalLock 因此可能收到被截斷的 HGLOBAL。
        kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
        user32.OpenClipboard.argtypes = (wintypes.HWND,)
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = ()
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.argtypes = ()
        user32.CloseClipboard.restype = wintypes.BOOL

        data = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
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
        for _ in range(8):
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
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise OSError("SetClipboardData 失敗")
            handle = None  # 成功後由系統管理記憶體
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)

    def _live_editor(
        self,
        box: WebElement,
        article: WebElement,
    ) -> Optional[WebElement]:
        """取得同一篇貼文內目前仍存活的 Lexical 編輯器。

        Facebook 在 focus、貼上或輸入後可能重建 contenteditable 節點；此時
        原本的 WebElement 可能已失效，而 document.activeElement 甚至會退回
        body。只接受 article 內的可見留言框，避免讀到另一篇貼文或整個頁面。
        """
        # Reels dialog 可能在 focus／送出後由 React 重建；每次優先重新取得
        # 目前的 dialog root，不能一直沿用背景貼文或舊 dialog WebElement。
        article = self._find_active_reel_dialog() or article
        try:
            editor = self.driver.execute_script(
                r"""
                const original = arguments[0];
                const article = arguments[1];
                const selector = '[contenteditable="true"][role="textbox"],'
                               + '[contenteditable="true"][data-lexical-editor="true"]';
                const visible = el => {
                    if (!el || !el.isConnected || !article.contains(el)) return false;
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 20 && r.height > 10 &&
                           s.display !== 'none' && s.visibility !== 'hidden';
                };
                let active = document.activeElement;
                if (active && active.closest) active = active.closest(selector);
                if (active && visible(active)) return active;
                if (original && original.matches && original.matches(selector) && visible(original)) {
                    return original;
                }
                const candidates = [...article.querySelectorAll(selector)].filter(visible);
                return candidates.length ? candidates[0] : null;
                """,
                box,
                article,
            )
            if editor:
                return editor
        except Exception:
            pass
        return self._find_box_in(article)

    def _editor_text(self, box: WebElement, article: WebElement) -> str:
        """只讀取同一篇貼文內 Lexical 編輯器的實際內容。"""
        try:
            editor = self._live_editor(box, article)
            if editor is None:
                return ""
            value = self.driver.execute_script(
                r"""
                const editor = arguments[0];
                const value = editor.innerText || editor.textContent || editor.value || '';
                return String(value).replace(/\u00a0/g, ' ').trim();
                """,
                editor,
            )
            return str(value or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _comment_marker(text: str) -> str:
        marker = next((line.strip() for line in text.splitlines() if line.strip()), text.strip())
        return marker[:40]

    def _wait_editor_contains(
        self,
        box: WebElement,
        article: WebElement,
        marker: str,
        timeout: float = 1.6,
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stopped():
            if marker and marker in self._editor_text(box, article):
                return True
            time.sleep(0.08)
        return False

    @staticmethod
    def _normalized_editor_value(value: str) -> str:
        return " ".join((value or "").split())

    def _wait_editor_equals(
        self,
        box: WebElement,
        article: WebElement,
        text: str,
        timeout: float,
    ) -> bool:
        wanted = self._normalized_editor_value(text)
        deadline = time.time() + timeout
        while time.time() < deadline and not self._stopped():
            if self._normalized_editor_value(self._editor_text(box, article)) == wanted:
                return True
            time.sleep(0.08)
        return False

    def _clear_editor(self, box: WebElement, article: WebElement) -> WebElement:
        editor = self._live_editor(box, article)
        if editor is None:
            raise RuntimeError("找不到目前可操作的 Reels 留言框")
        self.driver.execute_script("arguments[0].focus();", editor)
        editor.send_keys(Keys.CONTROL, "a")
        editor.send_keys(Keys.BACKSPACE)
        time.sleep(0.1)
        return editor

    def _insert_text_with_javascript(self, box: WebElement, text: str) -> None:
        """使用單一次 execCommand 輸入，避免多種事件重複插入。"""
        ok = self.driver.execute_script(
            r"""
            const el = arguments[0];
            const text = arguments[1];
            el.focus();
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            range.collapse(false);
            selection.removeAllRanges();
            selection.addRange(range);

            let inserted = false;
            let usedFallback = false;
            try { inserted = document.execCommand('insertText', false, text); } catch (e) {}
            if (!inserted) {
                try {
                    const node = document.createTextNode(text);
                    range.insertNode(node);
                    range.setStartAfter(node);
                    range.collapse(true);
                    selection.removeAllRanges();
                    selection.addRange(range);
                    inserted = true;
                    usedFallback = true;
                } catch (e) {}
            }
            if (usedFallback) try {
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true, cancelable: false,
                    inputType: 'insertText', data: text
                }));
            } catch (e) {
                el.dispatchEvent(new Event('input', {bubbles: true}));
            }
            return inserted;
            """,
            box,
            text,
        )
        if not ok:
            raise RuntimeError("JavaScript 備援輸入失敗")

    def _safe_multiline_input(
        self,
        box: WebElement,
        text: str,
        article: WebElement,
    ) -> Optional[WebElement]:
        """快速貼上並驗證 Lexical 真的有內容，確認後才送出。"""
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
        self.driver.execute_script("arguments[0].focus(); arguments[0].click();", box)
        time.sleep(0.12)
        box = self._clear_editor(box, article)

        if self._stopped():
            raise InterruptedError("Reels 留言輸入已停止")

        marker = self._comment_marker(text)
        _log.info("[%s] 開始快速貼上 Reels 留言", self.profile_name)

        pasted = False
        clipboard_error = None
        try:
            self._copy_to_windows_clipboard(text)
            box = self._live_editor(box, article) or box
            box.send_keys(Keys.CONTROL, "v")
            pasted = self._wait_editor_equals(box, article, text, timeout=1.8)
        except Exception as exc:
            clipboard_error = exc

        if not pasted:
            _log.warning(
                "[%s] 剪貼簿貼上未驗證成功，立即改用 JavaScript 備援%s",
                self.profile_name,
                f"：{clipboard_error}" if clipboard_error else "",
            )
            box = self._clear_editor(box, article)
            self._insert_text_with_javascript(box, text)
            pasted = self._wait_editor_equals(box, article, text, timeout=1.2)

        if not pasted:
            raise RuntimeError("Reels 留言文字未實際進入輸入框，已取消送出")

        _log.info("[%s] 已確認留言框內只有一份文案，準備送出", self.profile_name)
        if self._stopped():
            raise InterruptedError("Reels 留言送出前已停止")

        # 只在已確認文字存在後送出，且直接對同一篇貼文的存活編輯器按 Enter。
        box = self._live_editor(box, article)
        if box is None:
            raise RuntimeError("留言已輸入，但送出前找不到目前的 Reels 留言框")
        self.driver.execute_script("arguments[0].focus();", box)
        box.send_keys(Keys.ENTER)
        _log.info("[%s] 已按 Enter 送出 Reels 留言", self.profile_name)

        # Enter 只是送出動作，不代表 Facebook 已接受。先確認編輯器清空，
        # 再確認同一篇貼文出現剛送出的留言，避免畫面沒送出卻記成成功。
        clear_deadline = time.time() + 6.0
        editor_cleared = False
        while time.time() < clear_deadline and not self._stopped():
            try:
                if marker not in self._editor_text(box, article):
                    editor_cleared = True
                    break
            except StaleElementReferenceException:
                box = self._find_box_in(article) or box
            time.sleep(0.15)
        if not editor_cleared:
            # 若第一次 Enter 後完整原文仍留在同一留言框，表示 Facebook 沒有
            # 接受按鍵。重新鎖定欄位後只重試一次；若內容已改變則不冒險重送。
            current = self._normalized_editor_value(self._editor_text(box, article))
            wanted = self._normalized_editor_value(text)
            if current != wanted:
                raise RuntimeError("按 Enter 後留言框內容異常，為避免重複留言已停止")
            box = self._live_editor(box, article)
            if box is None:
                raise RuntimeError("按 Enter 後找不到留言框，無法確認留言已送出")
            self.driver.execute_script("arguments[0].focus(); arguments[0].click();", box)
            box.send_keys(Keys.ENTER)
            _log.warning("[%s] 第一次 Enter 未送出，已對同一留言框安全重試一次", self.profile_name)
            retry_deadline = time.time() + 6.0
            while time.time() < retry_deadline and not self._stopped():
                if marker not in self._editor_text(box, article):
                    editor_cleared = True
                    break
                time.sleep(0.15)
            if not editor_cleared:
                raise RuntimeError("重試 Enter 後留言框仍未清空，留言沒有送出")

        own_comment = self._find_own_comment_container(article, text)
        if own_comment is None:
            # Facebook 新版有時送出後會立即清空 Lexical 編輯器，但留言列表
            # 不會同步展開／重繪，因此此時找不到留言 DOM 並不代表送出失敗。
            # 只要 Enter 後編輯器已確認清空，就將留言視為已成功送出；
            # 自己留言的按讚則交由後續流程再次搜尋，找不到時安全略過。
            _log.warning(
                "[%s] Enter 後留言框已清空，判定留言送出成功；"
                "目前尚未在 DOM 中定位到剛送出的留言",
                self.profile_name,
            )
            return None

        _log.info("[%s] 已確認剛送出的留言出現在同一篇貼文", self.profile_name)
        return own_comment

    def run(self) -> str:
        self._ensure_cached_personal_profile()
        if self.mode == "custom":
            comment = self._load_complete_custom_comment()
        else:
            comment = DEFAULT_REELS_COMMENT.strip()
        if not comment:
            raise RuntimeError("Reels 留言文案為空")
        if self._stopped():
            return "stopped"

        try:
            article = self._find_first_article()
        except Exception:
            _log.info('[%s] 個人主頁尚未載入第一篇貼文，維持個人主頁並定位貼文時間軸後重試', self.profile_name)
            self._prepare_profile_timeline()
            try:
                article = self._find_first_article()
            except Exception as exc:
                _log.warning('[%s] 個人主頁仍找不到第一篇可操作貼文，安全略過 Reels 留言：%s', self.profile_name, exc)
                return "skipped"

        target_reel_url = self._article_reel_url(article)
        target_reel_id = self._reel_id(target_reel_url)
        if not target_reel_id:
            raise RuntimeError("已定位第一篇貼文，但找不到第一篇 Reels 的結構化 URL，拒絕猜測第二篇")
        _log.info(
            "[%s] 已鎖定個人主頁第一篇 Reels：ID=%s",
            self.profile_name,
            target_reel_id,
        )

        # 先鎖定同一篇 article 的主操作列並按讚；完成後 Facebook 可能已
        # 將 Reels 展開成 dialog，留言框會移到 dialog 底部、位於留言
        # article 之外，因此後續必須重新判斷實際互動 root。
        self._like_first_reel(article)

        reel_dialog = self._find_active_reel_dialog()
        if reel_dialog is not None:
            current_reel_id = self._reel_id(str(self.driver.current_url or ""))
            if current_reel_id != target_reel_id:
                _log.warning(
                    "[%s] Facebook 開啟了非目標 Reels（target=%s, current=%s），"
                    "改用第一篇精確網址。",
                    self.profile_name,
                    target_reel_id,
                    current_reel_id or "none",
                )
                reel_dialog = self._open_exact_reel_dialog(target_reel_url)
        comment_root = reel_dialog or article
        if comment_root is not article:
            _log.info("[%s] 已切換至 Reels 彈窗根節點搜尋留言框", self.profile_name)
        else:
            _log.info("[%s] 開始搜尋第一篇 article 內的 Lexical 留言框", self.profile_name)
        box = self._find_box_in(comment_root)
        if box is None:
            self._expand_comment_area(article)
            reel_dialog = self._find_active_reel_dialog(wait_seconds=6.0)
            if reel_dialog is not None:
                current_reel_id = self._reel_id(str(self.driver.current_url or ""))
                if current_reel_id != target_reel_id:
                    _log.warning(
                        "[%s] 展開留言後 Reels ID 不一致（target=%s, current=%s），"
                        "改用第一篇精確網址。",
                        self.profile_name,
                        target_reel_id,
                        current_reel_id or "none",
                    )
                    reel_dialog = self._open_exact_reel_dialog(target_reel_url)
            comment_root = reel_dialog or article
            box = self._find_box_in(comment_root)

        # 第一篇控制列尚未渲染時，不能改抓第二篇；直接使用一開始記下的
        # 第一篇 Reel URL 開啟留言彈窗。
        if box is None:
            comment_root = self._open_exact_reel_dialog(target_reel_url)
            box = self._find_box_in(comment_root)

        if box is None:
            raise RuntimeError("第一篇 Reels 找不到留言輸入框（已檢查 article、Reels dialog 與展開留言區）")

        log_value = lambda value: str(value or "").encode(
            "unicode_escape"
        ).decode("ascii")
        _log.info(
            "[%s] 找到 Lexical 留言框：aria-label=%s aria-placeholder=%s lexical=%s",
            self.profile_name,
            log_value(box.get_attribute("aria-label")),
            log_value(box.get_attribute("aria-placeholder")),
            log_value(box.get_attribute("data-lexical-editor")),
        )
        own_comment = self._safe_multiline_input(box, comment, comment_root)
        _log.info("[%s] Reels 留言送出成功：%s", self.profile_name, " ".join(comment.split())[:180])

        _log.info("[%s] 開始在同一篇 article 內搜尋自己的留言", self.profile_name)
        self._like_own_comment(comment_root, comment, own_comment)
        return "success"
