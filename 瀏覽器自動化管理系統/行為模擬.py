"""
behavior.py
===========
Facebook Auto Warm-up Lite — V8 Stable（安全 Like + Radio 0/9 貼文流程）
重點修正：
1. 不再重新導向 Facebook 首頁，避免網路慢時重複載入導致 Timeout。
2. 貼文偵測改成「可視區域 + DOM 特徵」判斷，不只依賴固定 CSS。
3. 瀏覽時間最高不超過 120 秒。
4. 每輪等待縮短，找不到貼文時快速滑動下一批。
5. 連續多輪找不到貼文會提前結束。
"""

import random
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webelement import WebElement

from 瀏覽器 import BrowserController
from 設定 import CONFIG, BrowseConfig, InteractionConfig, OpenAIConfig
from 日誌 import get_logger
from 媒體來源 import MediaPool, media_kind
from 多行文字 import copy_to_windows_clipboard, random_text_block
from 工具 import random_sleep, should_do, truncate, with_retry

_log = get_logger(__name__)

FACEBOOK_HOME_URL = "https://www.facebook.com"

# Labels used by Facebook's normal and professional-mode post composers.
# Keep these semantic instead of relying on the left/right position because
# Arabic Facebook uses a right-to-left layout.
POST_COMPOSER_TERMS = (
    "what's on your mind", "你在想些什麼", "你在想什麼", "在想些什麼",
    "ano ang nasa isip mo", "quoi de neuf", "คุณกำลังคิดอะไรอยู่",
    "بم تفكر",
)
POST_PUBLIC_TERMS = (
    "public", "公開", "pampubliko", "public", "สาธารณะ", "عام", "العامة",
)
POST_FRIENDS_TERMS = (
    "friends", "好友", "amis", "mga kaibigan", "เพื่อน", "الأصدقاء",
)
POST_ONLY_ME_TERMS = (
    "only me", "只限本人", "moi uniquement", "ako lang", "เฉพาะฉัน", "أنا فقط",
)
POST_DONE_TERMS = (
    "done", "完成", "terminé", "tapos", "เสร็จสิ้น", "تم",
)
POST_SAVE_TERMS = (
    "save", "儲存", "保存", "enregistrer", "i-save", "บันทึก", "حفظ",
)
POST_CONTINUE_TERMS = (
    "continue", "繼續", "continuer", "magpatuloy", "ดำเนินการต่อ", "متابعة",
)

FALLBACK_COMMENTS = [
    "Nice post!",
    "Great!",
    "Interesting!",
    "Thanks for sharing!",
    "Looks good!",
    "Awesome!",
    "Nice one!",
    "Good update!",
    "Salamat sa pag-share!",
    "Maganda ito!",
    "Nice kaayo!",
]



def _read_random_post_from_xlsx(filename: str = "文案.xlsx") -> Optional[str]:
    """
    從程式同資料夾的 Excel A 欄隨機讀取一筆非空白文案。
    只讀取、不刪除、不修改、不寫回 Excel。
    不需要 openpyxl，使用 Python 標準函式庫解析 xlsx。
    """
    candidates = [
        Path(__file__).resolve().parent / filename,
        Path.cwd() / filename,
    ]
    xlsx_path = next((p for p in candidates if p.is_file()), None)
    if xlsx_path is None:
        return None

    try:
        with zipfile.ZipFile(xlsx_path, "r") as archive:
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for item in shared_root.findall("m:si", ns):
                    parts = [node.text or "" for node in item.findall(".//m:t", ns)]
                    shared_strings.append("".join(parts))

            sheet_name = "xl/worksheets/sheet1.xml"
            if sheet_name not in archive.namelist():
                sheet_candidates = sorted(
                    name for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                )
                if not sheet_candidates:
                    return None
                sheet_name = sheet_candidates[0]

            sheet_root = ET.fromstring(archive.read(sheet_name))
            values: list[str] = []

            for cell in sheet_root.findall(".//m:c", ns):
                ref = (cell.get("r") or "").upper()
                if not ref.startswith("A"):
                    continue

                cell_type = cell.get("t") or ""
                value = ""

                if cell_type == "inlineStr":
                    value = "".join(
                        node.text or "" for node in cell.findall(".//m:is/m:t", ns)
                    )
                else:
                    value_node = cell.find("m:v", ns)
                    if value_node is None or value_node.text is None:
                        continue
                    raw = value_node.text
                    if cell_type == "s":
                        try:
                            value = shared_strings[int(raw)]
                        except (ValueError, IndexError):
                            value = ""
                    else:
                        value = raw

                value = value.strip()
                if value:
                    values.append(value)

            if not values:
                return None

            selected = random.choice(values)
            _log.info("[PostText] 已從文案.xlsx A欄隨機選取 1 筆文案（共 %d 筆，不修改檔案）。", len(values))
            return selected

    except Exception as exc:
        _log.warning("讀取文案.xlsx 失敗，改用 OpenAI／本地備援：%s", exc)
        return None

def get_fallback_comment() -> str:
    """OpenAI 無法使用時，使用本地固定留言備援。"""
    return random.choice(FALLBACK_COMMENTS)


# OpenAI 額度不足提醒節流設定
OPENAI_QUOTA_WARNING_INTERVAL_SEC = 300
_last_openai_quota_warning_ts = 0.0


def notify_openai_quota_warning(reason: str) -> None:
    """
    OpenAI 429 / insufficient_quota 重複提醒。
    第一次立即提醒，之後每 5 分鐘提醒一次，避免 Terminal 被洗版但又不會漏看。
    """
    global _last_openai_quota_warning_ts

    now = time.time()
    if now - _last_openai_quota_warning_ts < OPENAI_QUOTA_WARNING_INTERVAL_SEC:
        return

    _last_openai_quota_warning_ts = now

    msg_lines = [
        "",
        "=" * 70,
        "⚠️  OpenAI API 額度不足 / 呼叫失敗",
        "⚠️  已自動切換為【本地備援留言模式】",
        "⚠️  請儘快儲值 OpenAI API，或更換新的 API Key",
        f"原因：{reason}",
        "=" * 70,
        "",
    ]

    for line in msg_lines:
        print(line)

    _log.warning("=" * 70)
    _log.warning("OpenAI API 額度不足 / 呼叫失敗")
    _log.warning("已自動切換為本地備援留言模式")
    _log.warning("請儘快儲值 OpenAI API，或更換新的 API Key")
    _log.warning("原因：%s", reason)
    _log.warning("=" * 70)



# 強制限制瀏覽時間
BROWSE_MIN_SEC = 60
BROWSE_MAX_SEC = 120

# 找不到貼文最多輪數
MAX_NO_POST_ROUNDS = 3


@dataclass
class PostInfo:
    """單篇貼文資訊。"""
    element: WebElement
    text: str = ""
    has_comments: bool = False
    has_photo: bool = False
    already_liked: bool = False
    comment_generated: str = ""


class CommentGenerator:
    """OpenAI 留言產生器。"""

    def __init__(self, cfg: Optional[OpenAIConfig] = None) -> None:
        self._cfg = cfg or CONFIG.openai
        self._client: Optional[OpenAI] = None
        self._init_client()

    def _init_client(self) -> None:
        api_key = self._cfg.api_key or ""
        if not api_key:
            _log.warning("OpenAI API Key 未設定，留言功能將跳過。")
            return

        try:
            self._client = OpenAI(api_key=api_key, timeout=self._cfg.request_timeout)
        except Exception as exc:
            _log.error("OpenAI 客戶端初始化失敗：%s", exc)

    def generate(self, post_text: str) -> Optional[str]:
        """
        V4.2：OpenAI 產生留言。
        OpenAI 429 / insufficient_quota 時，自動使用本地備援留言並重複提醒。
        """
        if not post_text.strip():
            return get_fallback_comment()

        if not self._client:
            notify_openai_quota_warning("OpenAI Client 未初始化或 API Key 未設定")
            return get_fallback_comment()

        truncated = truncate(post_text, max_len=400)

        try:
            response = self._client.chat.completions.create(
                model=self._cfg.model,
                messages=[
                    {"role": "system", "content": self._cfg.system_prompt},
                    {"role": "user", "content": f"Post: {truncated}"},
                ],
                max_tokens=self._cfg.max_tokens,
                temperature=self._cfg.temperature,
            )
            comment = response.choices[0].message.content
            if comment:
                comment = comment.strip().strip('"').strip("'")
            if not comment:
                comment = get_fallback_comment()
            _log.debug("留言內容：「%s」", truncate(comment or "", 60))
            return comment

        except Exception as exc:
            exc_text = str(exc)
            if (
                "429" in exc_text
                or "insufficient_quota" in exc_text
                or "quota" in exc_text.lower()
                or "billing" in exc_text.lower()
            ):
                notify_openai_quota_warning(exc_text)
            else:
                _log.warning("OpenAI API 呼叫失敗，改用本地備援留言：%s", exc)
            return get_fallback_comment()


    def generate_filipino_post(self, text_file: str = "") -> str:
        """優先隨機選取 RC19 格式 TXT，再使用文案.xlsx／既有備援。"""
        if str(text_file or "").strip():
            try:
                text_post, total = random_text_block(text_file)
                _log.info(
                    "[PostText] 已從 PO 文 TXT 隨機選取 1 篇（共 %d 篇、%d 行、%d 字元）。",
                    total,
                    text_post.count("\n") + 1,
                    len(text_post),
                )
                return text_post
            except Exception as exc:
                _log.warning("[PostText] PO 文 TXT 無法使用，改用文案.xlsx／既有備援：%s", exc)

        excel_post = _read_random_post_from_xlsx("文案.xlsx")
        if excel_post:
            return excel_post

        fallback_posts = [
            "Minsan, ang simpleng pahinga at tahimik na sandali ang pinakamasarap na bahagi ng araw. Kumusta ang araw mo?",
            "Nakakatuwang isipin na kahit maliit na bagay—isang magandang kanta, masarap na kape, o maikling usapan—puwedeng magpagaan ng buong araw.",
            "Ang daming bagong bagay na puwedeng matutunan araw-araw. Ano ang pinaka-interesting na nalaman mo kamakailan?",
            "Hindi kailangang maging espesyal ang bawat araw. Minsan sapat na ang mabagal na umaga, malinaw na isip, at magandang pakiramdam.",
            "May mga simpleng sandali talagang biglang nagpapasaya sa atin. Sana may magandang mangyari sa araw mo ngayon.",
        ]
        if not self._client:
            return random.choice(fallback_posts)

        prompt = (
            "Sumulat ng isang natural at kawili-wiling Facebook post sa Filipino/Tagalog. "
            "Dapat 1 hanggang 3 pangungusap lamang, magaan at friendly. "
            "Paksa: araw-araw na buhay, teknolohiya, pagkain, musika, pahinga, motivation, "
            "o simpleng obserbasyon. Huwag mag-imbento ng balita, pangalan, petsa, o estadistika. "
            "Huwag gumamit ng hashtag."
        )
        try:
            response = self._client.chat.completions.create(
                model=self._cfg.model,
                messages=[
                    {"role": "system", "content": "Ikaw ay mahusay magsulat ng natural na Filipino Facebook posts."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max(100, self._cfg.max_tokens),
                temperature=0.9,
            )
            content = response.choices[0].message.content
            if content:
                cleaned = content.strip().strip('"').strip("'")
                if cleaned:
                    return cleaned
        except Exception as exc:
            _log.warning("OpenAI 產生菲律賓文貼文失敗，改用本地內容：%s", exc)

        return random.choice(fallback_posts)


class ScrollEngine:
    """自然滑動引擎。"""

    def __init__(self, ctrl: BrowserController, cfg: Optional[BrowseConfig] = None) -> None:
        self._ctrl = ctrl
        self._cfg = cfg or CONFIG.browse

    def natural_scroll(self, fast: bool = False) -> bool:
        """
        執行一次滑動，並確認 scrollY 是否改變。
        fast=True 時用於找不到貼文，等待較短。
        """
        try:
            before = int(self._ctrl.get_scroll_position())
        except Exception:
            before = 0

        max_position = max(0, int(getattr(self._cfg, "max_scroll_position", 3000)))
        remaining = max_position - before
        if remaining <= 0:
            _log.info("已到達滑動上限：%s px，停止繼續下滑。", max_position)
            return False

        distance = random.randint(480, 900) if fast else random.randint(320, 760)
        distance = min(distance, remaining)
        steps = random.randint(2, 3)
        base_step, extra = divmod(distance, steps)

        for index in range(steps):
            step_distance = base_step + (1 if index < extra else 0)
            if step_distance <= 0:
                break
            self._ctrl.scroll_down(step_distance)
            time.sleep(random.uniform(0.04, 0.12))

        wait_min, wait_max = (1.0, 2.0) if fast else (2.0, 4.0)
        random_sleep(wait_min, wait_max)

        try:
            after = int(self._ctrl.get_scroll_position())
        except Exception:
            after = before

        if after == before and before < max_position:
            # 補滑一次
            _log.warning("滑動後位置未改變，補滑一次。")
            retry_distance = min(random.randint(650, 1000), max_position - before)
            self._ctrl.run_js("window.scrollBy(0, arguments[0]);", retry_distance)
            random_sleep(1.0, 2.0)
            try:
                after = int(self._ctrl.get_scroll_position())
            except Exception:
                after = before

        # 動態版面載入有機會改變捲軸位置；最後再硬性限制於設定上限。
        if after > max_position:
            self._ctrl.run_js("window.scrollTo(0, arguments[0]);", max_position)
            after = max_position

        ok = after != before
        _log.info("滑動完成：%s → %s（%s）。", before, after, "成功" if ok else "未移動")
        return ok


class FeedInteractor:
    """動態牆互動引擎。"""

    # 你提供的絕對 XPath 只作為最後備用，mount_0_0 會變動，所以不能只靠它。
    _USER_POST_XPATH = (
        "//*[@id='mount_0_0_lz']/div/div[1]/div/div[3]/div/div/div[1]/div[1]/div/"
        "div[2]/div/div/div/div[2]/div/div[4]/div/div[2]/div[6]/div/span/div/"
        "div/div/div/div/div/div/div/div/div/div/div"
    )

    # 新版 Facebook 常見貼文外層
    _POST_CSS_SELECTORS = [
        "div[role='article']",
        "div[aria-posinset][data-virtualized='false']",
        "div[data-pagelet^='FeedUnit']",
        "div[data-pagelet*='FeedUnit']",
        "div.x1yztbdb",
    ]

    _LIKE_BTN_XPATHS = [
        ".//*[@aria-label='Like' and @role='button']",
        ".//*[@aria-label='讚' and @role='button']",
        ".//*[@aria-label='喜歡' and @role='button']",
        ".//*[contains(@aria-label,'Like') and @role='button']",
        ".//*[contains(@aria-label,'讚') and @role='button']",
        ".//*[contains(@aria-label,'Gusto') and @role='button']",
        ".//*[contains(@aria-label,'ถูกใจ') and @role='button']",
        ".//*[contains(@aria-label,'إعجاب') and @role='button']",
        ".//span[text()='Like']/ancestor::*[@role='button'][1]",
        ".//span[text()='讚']/ancestor::*[@role='button'][1]",
    ]

    _COMMENT_BTN_XPATHS = [
        ".//*[@aria-label='Comment' and @role='button']",
        ".//*[@aria-label='留言' and @role='button']",
        ".//*[contains(@aria-label,'Comment') and @role='button']",
        ".//*[contains(@aria-label,'留言') and @role='button']",
        ".//*[contains(@aria-label,'komento') and @role='button']",
        ".//*[contains(@aria-label,'ความคิดเห็น') and @role='button']",
        ".//*[contains(@aria-label,'تعليق') and @role='button']",
        ".//span[normalize-space()='Comment']/ancestor::*[@role='button'][1]",
        ".//span[normalize-space()='留言']/ancestor::*[@role='button'][1]",
        ".//span[normalize-space()='Komento']/ancestor::*[@role='button'][1]",
        ".//span[contains(text(),'Comment')]/ancestor::*[@role='button'][1]",
        ".//span[contains(text(),'留言')]/ancestor::*[@role='button'][1]",
        ".//span[contains(text(),'komento') or contains(text(),'Komento')]/ancestor::*[@role='button'][1]",
        ".//*[contains(text(),'Comment')]/ancestor::*[@role='button'][1]",
        ".//*[contains(text(),'留言')]/ancestor::*[@role='button'][1]",
    ]

    _COMMENT_BOX_XPATHS = [
        ".//*[@contenteditable='true' and @role='textbox']",
        ".//*[@role='textbox' and contains(@aria-label,'comment')]",
        ".//*[@role='textbox' and contains(@aria-label,'留言')]",
        ".//*[@role='textbox' and contains(@aria-label,'komento')]",
        ".//*[@role='textbox' and contains(@aria-label,'ความคิดเห็น')]",
        ".//*[@role='textbox' and contains(@aria-label,'تعليق')]",
        ".//*[@aria-label='Write a comment…']",
        ".//*[@aria-label='Write a comment']",
        ".//*[@aria-label='寫留言…']",
        ".//*[@aria-label='留言…']",
        ".//*[@data-lexical-editor='true']",
    ]

    _SHARE_BTN_XPATHS = [
        ".//*[@aria-label='Share' and @role='button']",
        ".//*[@aria-label='分享' and @role='button']",
        ".//*[contains(@aria-label,'Share') and @role='button']",
        ".//*[contains(@aria-label,'分享') and @role='button']",
        ".//*[contains(@aria-label,'Ibahagi') and @role='button']",
        ".//span[normalize-space()='Share']/ancestor::*[@role='button'][1]",
        ".//span[normalize-space()='分享']/ancestor::*[@role='button'][1]",
        ".//span[normalize-space()='Ibahagi']/ancestor::*[@role='button'][1]",
        ".//span[contains(text(),'Share')]/ancestor::*[@role='button'][1]",
        ".//span[contains(text(),'分享')]/ancestor::*[@role='button'][1]",
        ".//span[contains(text(),'Ibahagi')]/ancestor::*[@role='button'][1]",
        ".//*[contains(text(),'Share')]/ancestor::*[@role='button'][1]",
        ".//*[contains(text(),'分享')]/ancestor::*[@role='button'][1]",
        ".//*[contains(text(),'Ibahagi')]/ancestor::*[@role='button'][1]",
    ]

    def __init__(
        self,
        ctrl: BrowserController,
        comment_gen: CommentGenerator,
        interaction_cfg: Optional[InteractionConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._comment_gen = comment_gen
        self._cfg = interaction_cfg or CONFIG.interaction

    def get_visible_posts(self) -> list[PostInfo]:
        """
        V4.2 快速取得目前畫面可見貼文。

        重點：
        - 掃描最多 2 秒。
        - 不再對大量 WebElement 逐一取 el.text / find_elements。
        - 用 JavaScript 一次回傳候選貼文與文字，再轉成 PostInfo。
        """
        scan_start = time.time()
        candidates = self._find_posts_by_visual_js_fast()
        posts: list[PostInfo] = []

        for item in candidates[:6]:
            if time.time() - scan_start > 2.0:
                _log.warning("貼文掃描達 2 秒上限，停止本輪掃描。")
                break

            try:
                el = item.get("element")
                if not el:
                    continue

                posts.append(
                    PostInfo(
                        element=el,
                        text=(item.get("text") or "")[:700],
                        has_comments=bool(item.get("has_comments")),
                        has_photo=bool(item.get("has_photo")),
                        already_liked=bool(item.get("already_liked")),
                    )
                )
            except Exception:
                continue

        scan_cost = time.time() - scan_start
        _log.info("目前可見貼文：%d 篇（掃描 %.1f 秒）。", len(posts), scan_cost)
        return posts

    def _find_posts_by_visual_js(self) -> list[WebElement]:
        """保留舊介面相容性。"""
        items = self._find_posts_by_visual_js_fast()
        return [item["element"] for item in items if item.get("element")]

    def _find_posts_by_visual_js_fast(self) -> list[dict]:
        """
        V4.2 視覺模式貼文掃描。
        不依賴固定 class，不掃全頁大量 div。
        用中間欄位 + 高度 + 圖片/文字 + 互動列判斷貼文。
        """
        self._ctrl._ensure_driver()
        script = """
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const started = performance.now();
        const maxMs = 1500;
        const result = [];
        const seen = new Set();

        function safeText(el) {
            try { return (el.innerText || '').trim(); } catch (e) { return ''; }
        }

        function isVisibleRect(r) {
            return r && r.width > 0 && r.height > 0 && r.bottom > 70 && r.top < vh - 30;
        }

        function inFeedColumn(r) {
            const center = r.left + (r.width / 2);
            return center > vw * 0.20 && center < vw * 0.76 &&
                   r.width >= 300 && r.width <= 950;
        }

        function hasInteraction(el, text) {
            const words = [
                'Like','Comment','Share','讚','喜歡','留言','分享',
                'Gusto','Komento','Ibahagi','React',
                "J’aime",'Jaime','Aimer','Commenter','Partager',
                'ถูกใจ','แสดงความคิดเห็น','แชร์',
                'إعجاب','أعجبني','تعليق','مشاركة'
            ];
            for (const w of words) {
                if (text.includes(w)) return true;
            }
            const ariaNodes = el.querySelectorAll('[aria-label]');
            for (const node of Array.from(ariaNodes).slice(0, 50)) {
                const label = node.getAttribute('aria-label') || '';
                for (const w of words) {
                    if (label.includes(w)) return true;
                }
            }
            return false;
        }

        function addCandidate(el) {
            if (!el || result.length >= 10) return;
            if (performance.now() - started > maxMs) return;

            const r = el.getBoundingClientRect();
            if (!isVisibleRect(r) || !inFeedColumn(r) || r.height < 180) return;

            const key = Math.round(r.left) + '-' + Math.round(r.top) + '-' +
                        Math.round(r.width) + '-' + Math.round(r.height);
            if (seen.has(key)) return;
            seen.add(key);

            const text = safeText(el).slice(0, 900);
            const imgCount = el.querySelectorAll('img').length;
            const hasPhoto = imgCount > 0;
            const interaction = hasInteraction(el, text);

            if (text.length < 8 && !hasPhoto) return;
            if (!interaction && !(hasPhoto && r.height > 260)) return;

            const hasComments =
                /comment|comments|留言|回覆|komento|แสดงความคิดเห็น|تعليق/i.test(text) ||
                !!el.querySelector(
                    '[aria-label*="Comment"], [aria-label*="留言"], ' +
                    '[aria-label*="komento"], [aria-label*="แสดงความคิดเห็น"], ' +
                    '[aria-label*="تعليق"]'
                );

            const alreadyLiked =
                !!el.querySelector(
                    '[aria-pressed="true"][aria-label*="Like"], ' +
                    '[aria-pressed="true"][aria-label*="讚"], ' +
                    '[aria-pressed="true"][aria-label*="喜歡"], ' +
                    '[aria-pressed="true"][aria-label*="J’aime"], ' +
                    '[aria-pressed="true"][aria-label*="ถูกใจ"], ' +
                    '[aria-pressed="true"][aria-label*="إعجاب"], ' +
                    '[aria-pressed="true"][aria-label*="أعجبني"]'
                );

            result.push({
                element: el,
                text: text,
                has_comments: hasComments,
                has_photo: hasPhoto,
                already_liked: alreadyLiked
            });
        }

        const selectors = [
            '[role="article"]',
            '[aria-posinset]',
            '[data-virtualized="false"]',
            'div[data-pagelet*="FeedUnit"]'
        ];

        for (const selector of selectors) {
            const nodes = Array.from(document.querySelectorAll(selector)).slice(0, 35);
            for (const el of nodes) {
                addCandidate(el);
                if (result.length >= 4 || performance.now() - started > maxMs) break;
            }
            if (result.length >= 4 || performance.now() - started > maxMs) break;
        }

        if (result.length < 3 && performance.now() - started <= maxMs) {
            const imgs = Array.from(document.querySelectorAll('img')).slice(0, 40);
            for (const img of imgs) {
                let el = img;
                for (let i = 0; i < 9 && el; i++) {
                    const r = el.getBoundingClientRect();
                    if (r && isVisibleRect(r) && inFeedColumn(r) && r.height >= 220) {
                        addCandidate(el);
                        break;
                    }
                    el = el.parentElement;
                }
                if (result.length >= 6 || performance.now() - started > maxMs) break;
            }
        }

        if (result.length < 3 && performance.now() - started <= maxMs) {
            const buttons = Array.from(document.querySelectorAll('[aria-label]')).filter(n => {
                const label = n.getAttribute('aria-label') || '';
                return /Like|Comment|Share|讚|喜歡|留言|分享|Gusto|Komento|Ibahagi|J’aime|Jaime|Aimer|Commenter|Partager|ถูกใจ|แสดงความคิดเห็น|แชร์|إعجاب|أعجبني|تعليق|مشاركة/i.test(label);
            }).slice(0, 35);

            for (const btn of buttons) {
                let el = btn;
                for (let i = 0; i < 10 && el; i++) {
                    const r = el.getBoundingClientRect();
                    if (r && isVisibleRect(r) && inFeedColumn(r) && r.height >= 180) {
                        addCandidate(el);
                        break;
                    }
                    el = el.parentElement;
                }
                if (result.length >= 6 || performance.now() - started > maxMs) break;
            }
        }

        return result;
        """

        start = time.time()
        try:
            result = self._ctrl.driver.execute_script(script)  # type: ignore[union-attr]
            cost = time.time() - start
            if cost > 2:
                _log.warning("貼文視覺掃描超過 2 秒：%.1f 秒，跳過慢速結果。", cost)
                return []
            return result or []
        except WebDriverException as exc:
            _log.warning("貼文視覺掃描失敗，跳過本輪：%s", exc)
            return []

    def _looks_like_post(self, el: WebElement) -> bool:
        """判斷元素是否像貼文。"""
        try:
            rect = el.rect
            width = rect.get("width", 0)
            height = rect.get("height", 0)

            if width < 300 or height < 160:
                return False

            text = (el.text or "").strip()
            imgs = el.find_elements(By.TAG_NAME, "img")
            buttons = el.find_elements(By.XPATH, ".//*[@role='button']")

            # 避免抓到左側選單或右側聊天室
            x = rect.get("x", 0)
            if x < 250:
                return False

            if len(text) < 10 and not imgs:
                return False

            if not buttons and len(text) < 60:
                return False

            return True
        except Exception:
            return False

    @with_retry(max_retries=1, wait_sec=1.0, fallback=False)
    def _quick_find_in_post(self, post: PostInfo, xpaths: list[str], action_name: str) -> Optional[WebElement]:
        """快速在貼文內找元素，避免弱網路時卡住。"""
        start = time.time()
        for xpath in xpaths:
            if time.time() - start > 2.0:
                _log.warning("[%s] 找元素超過 2 秒，跳過。", action_name)
                return None
            try:
                elements = post.element.find_elements(By.XPATH, xpath)
                if elements:
                    _log.info("[%s] 找到元素：%s", action_name, xpath)
                    return elements[0]
            except Exception:
                continue
        _log.info("[%s] 找不到元素，跳過。", action_name)
        return None

    def _quick_find_global(self, xpaths: list[str], action_name: str) -> Optional[WebElement]:
        """快速在整頁找元素，只作為留言框/確認按鈕備用。"""
        # 單次 JS 掃描常見輸入框／按鈕，避免大型 DOM 逐條 XPath。
        try:
            words = {
                "CommentBox": ["comment", "留言", "komento"],
                "ShareConfirm": ["share now", "post", "分享", "立即分享"],
            }.get(action_name, [])
            element = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                """
                const words=arguments[0];
                const norm=v=>(v||'').replace(/\\s+/g,' ').trim().toLowerCase();
                const visible=el=>{
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>1&&r.height>1&&s.display!=='none'&&s.visibility!=='hidden';
                };
                const nodes=[...document.querySelectorAll(
                    '[contenteditable="true"],[role="textbox"],button,[role="button"]'
                )];
                return nodes.find(el=>{
                    if(!visible(el))return false;
                    if(el.matches('[contenteditable="true"],[role="textbox"]') &&
                       words.some(w=>norm(el.getAttribute('aria-label')).includes(w)))return true;
                    const t=norm(el.getAttribute('aria-label')||el.innerText||el.textContent);
                    return words.some(w=>t===w||t.includes(w));
                })||null;
                """,
                words,
            )
            if element:
                return element
        except Exception:
            pass
        start = time.time()
        for xpath in xpaths:
            if time.time() - start > 2.0:
                _log.warning("[%s] 全頁找元素超過 2 秒，跳過。", action_name)
                return None
            try:
                el = self._ctrl.find(By.XPATH, xpath)
                if el:
                    _log.info("[%s] 全頁找到元素：%s", action_name, xpath)
                    return el
            except Exception:
                continue
        _log.info("[%s] 全頁找不到元素，跳過。", action_name)
        return None

    def _quick_find_by_text_js(self, post: PostInfo, words: list[str], action_name: str) -> Optional[WebElement]:
        """
        V4.2：用 JS 從貼文內找含指定文字的可點擊元素。
        適用於新版 Facebook 的 Comment / Share / Like。
        """
        self._ctrl._ensure_driver()
        script = """
        const root = arguments[0];
        const words = arguments[1];
        const started = performance.now();
        const maxMs = 1200;

        function visible(el) {
            const r = el.getBoundingClientRect();
            return r && r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
        }

        function clickableAncestor(el) {
            let cur = el;
            for (let i = 0; i < 8 && cur; i++) {
                const role = cur.getAttribute && cur.getAttribute('role');
                const aria = cur.getAttribute && cur.getAttribute('aria-label');
                const tab = cur.getAttribute && cur.getAttribute('tabindex');

                if (
                    role === 'button' ||
                    tab === '0' ||
                    cur.tagName === 'A' ||
                    cur.tagName === 'BUTTON' ||
                    aria
                ) {
                    return cur;
                }

                cur = cur.parentElement;
            }
            return el;
        }

        const nodes = Array.from(
            root.querySelectorAll('[aria-label], [role="button"], span, div, a')
        ).slice(0, 180);

        for (const node of nodes) {
            if (performance.now() - started > maxMs) break;

            const text = (
                node.getAttribute('aria-label') ||
                node.innerText ||
                node.textContent ||
                ''
            ).trim();

            if (!text) continue;

            for (const word of words) {
                if (text.toLowerCase().includes(word.toLowerCase())) {
                    const target = clickableAncestor(node);
                    if (target && visible(target)) return target;
                }
            }
        }

        return null;
        """
        try:
            el = self._ctrl.driver.execute_script(script, post.element, words)  # type: ignore[union-attr]
            if el:
                _log.info("[%s] JS文字找到元素：%s", action_name, "/".join(words))
                return el
        except Exception as exc:
            _log.info("[%s] JS文字找元素失敗：%s", action_name, exc)
        try:
            buttons = post.element.find_elements(
                By.XPATH,
                ".//*[@role='button']"
            )

            _log.info("[%s] role=button 數量：%d", action_name, len(buttons))

            for i, btn in enumerate(buttons):

                try:
                    text = (btn.text or "").strip()
                except Exception:
                    text = ""

                try:
                    aria = btn.get_attribute("aria-label") or ""
                except Exception:
                    aria = ""

                _log.info(
                    "[%s] #%02d text='%s' aria='%s'",
                    action_name,
                    i,
                    text,
                    aria,
                )

                # 如果 text / aria 都沒有，再印 HTML 前 300 字
                if not text and not aria:
                    try:
                        html = btn.get_attribute("outerHTML") or ""
                        _log.info(
                            "[%s] #%02d html=%s",
                            action_name,
                            i,
                            html[:300]
                        )
                    except Exception:
                        pass

        except Exception as exc:

            _log.info(
                "[%s] Debug 失敗：%s",
                action_name,
                exc,
            )

        return None

    def _find_like_by_text_js(self, post: PostInfo) -> Optional[WebElement]:
        """
        V4.2：用 JS 從貼文內找 Like / 讚 / 喜歡 / Gusto。
        不只靠 aria-label，改用文字 + role + 可見位置判斷。
        """
        self._ctrl._ensure_driver()
        script = """
        const root = arguments[0];
        const words = [
            'Like', '讚', '喜歡', 'Gusto', 'React', "J’aime", 'Aimer',
            'ถูกใจ', 'إعجاب', 'أعجبني'
        ];
        const started = performance.now();
        const maxMs = 1200;

        function visible(el) {
            const r = el.getBoundingClientRect();
            return r && r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
        }

        function clickableAncestor(el) {
            let cur = el;
            for (let i = 0; i < 8 && cur; i++) {
                const role = cur.getAttribute && cur.getAttribute('role');
                const aria = cur.getAttribute && cur.getAttribute('aria-label');
                const tab = cur.getAttribute && cur.getAttribute('tabindex');
                if (
                    role === 'button' ||
                    tab === '0' ||
                    cur.tagName === 'BUTTON' ||
                    (aria && /Like|讚|喜歡|Gusto|React|J’aime|Aimer|ถูกใจ|إعجاب|أعجبني/i.test(aria))
                ) {
                    return cur;
                }
                cur = cur.parentElement;
            }
            return el;
        }

        const nodes = Array.from(
            root.querySelectorAll('[aria-label], [role="button"], span, div')
        ).slice(0, 180);

        for (const node of nodes) {
            if (performance.now() - started > maxMs) break;

            const text = (
                node.getAttribute('aria-label') ||
                node.innerText ||
                node.textContent ||
                ''
            ).trim();

            if (!text) continue;

            for (const word of words) {
                if (text.toLowerCase().includes(word.toLowerCase())) {
                    const target = clickableAncestor(node);
                    if (target && visible(target)) return target;
                }
            }
        }

        return null;
        """
        try:
            el = self._ctrl.driver.execute_script(script, post.element)  # type: ignore[union-attr]
            if el:
                _log.info("[Like] JS文字找到 Like 元素。")
                return el
        except Exception as exc:
            _log.info("[Like] JS文字找 Like 失敗：%s", exc)
        return None

    def try_like(self, post: PostInfo) -> bool:
        """V8：保留舊版 JS 找 Like 的能力，但排除反應統計、小藍讚、留言讚與已按讚。"""
        if post.already_liked:
            _log.info("[Like V8] 貼文已按讚，跳過。")
            return False

        _log.info("[Like V8] 開始尋找安全的主貼文按讚鍵。")

        script = r"""
        const originalRoot = arguments[0];
        const terms = [
            'like','讚','赞','喜歡','喜欢','gusto','react',
            "j’aime",'jaime','aimer','curtir','me gusta',
            'ถูกใจ','إعجاب','أعجبني'
        ];
        const commentTerms = [
            'comment','留言','komento','commenter','แสดงความคิดเห็น','تعليق'
        ];
        const shareTerms = ['share','分享','ibahagi','partager','แชร์','مشاركة'];

        function rect(el){ try{return el.getBoundingClientRect();}catch(e){return null;} }
        function visible(el) {
            if (!el) return false;
            const r = rect(el);
            if (!r) return false;
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight &&
                   s.display !== 'none' && s.visibility !== 'hidden' &&
                   parseFloat(s.opacity || '1') > 0;
        }
        function textOf(el){
            return ((el && (el.getAttribute('aria-label') || el.innerText || el.textContent)) || '')
                .trim().toLowerCase();
        }
        function colorLooksBlue(el){
            const list=[el,...(el.querySelectorAll?Array.from(el.querySelectorAll('*')).slice(0,30):[])];
            for(const n of list){
                let cs; try{cs=getComputedStyle(n);}catch(e){continue;}
                for(const c of [cs.color,cs.fill,cs.backgroundColor]){
                    const m=(c||'').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
                    if(!m) continue;
                    const r=+m[1],g=+m[2],b=+m[3];
                    if(b>120 && b>r+25 && b>g+10) return true;
                }
            }
            return false;
        }
        function isPressed(el){
            if(!el) return false;
            if(el.getAttribute('aria-pressed')==='true' || el.getAttribute('aria-checked')==='true') return true;
            const t=textOf(el);
            if(/liked|讚了|已說讚|取消讚|unlike|不喜歡了|เลิกถูกใจ|إلغاء الإعجاب/.test(t)) return true;
            return colorLooksBlue(el);
        }
        function inCommentArea(el){
            let cur=el;
            for(let i=0;i<8 && cur;i++,cur=cur.parentElement){
                const role=(cur.getAttribute&&cur.getAttribute('role')||'').toLowerCase();
                const aria=(cur.getAttribute&&cur.getAttribute('aria-label')||'').toLowerCase();
                const txt=textOf(cur).slice(0,180);
                if(role==='article' && i>0) break;
                if(/comment|留言|komento|reply|回覆|แสดงความคิดเห็น|ตอบกลับ|تعليق|رد/.test(aria)) return true;
                if(i<4 && /回覆|reply|傳達心情|ตอบกลับ|رد/.test(txt)) return true;
            }
            return false;
        }
        function isReactionSummary(el, root){
            const r=rect(el), rr=rect(root);
            if(!r || !rr) return true;
            const t=textOf(el);
            if(/讚[:：]?\s*[\d,.萬千]+|like[s]?[:：]?\s*[\d,.k]+|\d+\s*(人|likes?)/i.test(t)) return true;
            if(r.width < 52 && r.height < 52 && (r.left > rr.left + rr.width*0.72 || r.top < rr.top + rr.height*0.72)) return true;
            return false;
        }
        function sameActionRow(el, root){
            const er=rect(el), rr=rect(root);
            if(!er || !rr) return false;
            const candidates=[...root.querySelectorAll('[role="button"],[aria-label],button')].filter(visible);
            let hasComment=false,hasShare=false;
            for(const n of candidates){
                const nr=rect(n); if(!nr) continue;
                if(Math.abs(nr.top-er.top)>22) continue;
                const t=textOf(n);
                if(commentTerms.some(w=>t.includes(w))) hasComment=true;
                if(shareTerms.some(w=>t.includes(w))) hasShare=true;
            }
            // 主按讚通常在貼文下半部，且和留言或分享同排
            return er.top > rr.top + rr.height*0.45 && (hasComment || hasShare);
        }
        function looksLikeTerm(el){
            const t=textOf(el);
            if(!t) return false;
            return terms.some(x => t===x || t.startsWith(x+' ') || t.includes(x));
        }
        function candidateButtons(root){
            if(!root || !root.querySelectorAll) return [];
            const nodes=[...root.querySelectorAll('[role="button"],[aria-label],button,[tabindex="0"]')];
            const out=[];
            for(const el of nodes){
                if(!visible(el) || el.closest('[role="dialog"]')) continue;
                if(inCommentArea(el)) continue;
                if(isReactionSummary(el,root)) continue;
                if(isPressed(el)) continue;
                if(!sameActionRow(el,root)) continue;
                const t=textOf(el);
                // 有文字時用文字確認；無文字也允許，但必須位於操作列最左側
                if(t){
                    if(!looksLikeTerm(el)) continue;
                }else{
                    const er=rect(el), rr=rect(root);
                    if(!er || !rr || er.left > rr.left + rr.width*0.42) continue;
                }
                out.push(el);
            }
            out.sort((a,b)=>{
                const ar=rect(a), br=rect(b);
                return (ar.left-br.left) || (br.width*br.height-ar.width*ar.height);
            });
            return out;
        }
        function findSafe(root){
            const list=candidateButtons(root);
            return list.length?list[0]:null;
        }

        let root=originalRoot;
        let btn=null;
        try{btn=findSafe(root);}catch(e){}

        if(!btn){
            const feed=document.querySelector('[role="feed"]')||document.querySelector('[role="main"]')||document.body;
            const posts=[...feed.querySelectorAll('[role="article"],[aria-posinset],[data-virtualized="false"]')].filter(p=>{
                const r=rect(p);
                return r && r.width>280 && r.height>140 && r.bottom>80 && r.top<innerHeight-20 && !p.closest('[role="dialog"]');
            });
            for(const p of posts){
                btn=findSafe(p);
                if(btn){root=p;break;}
            }
        }

        if(!btn) return {ok:false,reason:'not_found'};
        btn.scrollIntoView({block:'center'});

        // 捲動後重新確認，避免 DOM 改變或誤點已按讚。
        if(!visible(btn) || isPressed(btn) || inCommentArea(btn) || isReactionSummary(btn,root) || !sameActionRow(btn,root)){
            return {ok:false,reason:'unsafe_after_scroll'};
        }

        const before={pressed:btn.getAttribute('aria-pressed'),label:textOf(btn)};
        btn.click();
        return {ok:true,before:before};
        """

        try:
            result = self._ctrl.driver.execute_script(script, post.element)  # type: ignore[union-attr]
            if isinstance(result, dict) and result.get('ok'):
                _log.info("[Like V8] 已安全點擊主貼文按讚鍵。")
                random_sleep(0.8, 1.3)
                return True
            reason = result.get('reason') if isinstance(result, dict) else 'unknown'
            _log.info("[Like V8] 找不到可安全點擊的主按讚鍵：%s。", reason)
            return False
        except Exception as exc:
            _log.info("[Like V8] JS 執行失敗：%s", exc)
            return False

    def _find_comment_box_after_click(self, post: PostInfo) -> Optional[WebElement]:
        """
        V4.2：點留言後找 contenteditable / textbox。
        先貼文內找，再全頁找。
        """
        box = self._quick_find_in_post(post, self._COMMENT_BOX_XPATHS, "CommentBox")
        if box:
            return box

        global_xpaths = [
            "//*[@contenteditable='true' and @role='textbox']",
            "//*[@contenteditable='true']",
            "//*[@role='textbox' and contains(@aria-label,'comment')]",
            "//*[@role='textbox' and contains(@aria-label,'留言')]",
            "//*[@role='textbox' and contains(@aria-label,'komento')]",
            "//*[@data-lexical-editor='true']",
        ]
        return self._quick_find_global(global_xpaths, "CommentBox")

    def _find_share_confirm(self) -> Optional[WebElement]:
        """
        V4.2：分享彈窗確認按鈕。
        """
        xpaths = [
            "//*[@aria-label='Share now' and @role='button']",
            "//*[@aria-label='Post' and @role='button']",
            "//*[@aria-label='分享' and @role='button']",
            "//*[contains(@aria-label,'Share now') and @role='button']",
            "//*[contains(@aria-label,'Post') and @role='button']",
            "//*[contains(@aria-label,'Share') and @role='button']",
            "//span[normalize-space()='Share now']/ancestor::*[@role='button'][1]",
            "//span[normalize-space()='Post']/ancestor::*[@role='button'][1]",
            "//span[normalize-space()='分享']/ancestor::*[@role='button'][1]",
            "//span[contains(text(),'Share now')]/ancestor::*[@role='button'][1]",
            "//span[contains(text(),'Post')]/ancestor::*[@role='button'][1]",
            "//span[contains(text(),'分享')]/ancestor::*[@role='button'][1]",
        ]
        return self._quick_find_global(xpaths, "ShareConfirm")

    def try_comment(self, post: PostInfo) -> bool:
        """V4.2：先確認可以留言，再呼叫 OpenAI。"""
        _log.info("[Comment] 開始嘗試留言。")

        comment_btn = self._quick_find_in_post(post, self._COMMENT_BTN_XPATHS, "CommentBtn")
        if comment_btn is None:

            _log.info("[Comment] 找不到留言按鈕。")

            try:

                html = post.element.get_attribute("outerHTML")

                with open(
                    "comment_debug.html",
                    "w",
                    encoding="utf-8"
                ) as f:
                    f.write(html)

                _log.info(
                    "[Comment] 已輸出 comment_debug.html (%d bytes)",
                    len(html)
                )

            except Exception as e:

                _log.info(
                    "[Comment] 匯出 HTML 失敗：%s",
                    e
                )

            return False

        if comment_btn is None:
            _log.info("[Comment] 找不到留言按鈕。")

            try:
                with open("comment_debug.html", "w", encoding="utf-8") as f:
                    f.write(post.element.get_attribute("outerHTML"))
                _log.info("[Comment] 已輸出 comment_debug.html")
            except Exception as e:
                _log.info("[Comment] 匯出 HTML 失敗：%s", e)

            return False    

        if comment_btn is None:
            _log.info("[Comment] 找不到留言按鈕，直接跳過留言。")
            return False

        try:
            self._ctrl.click(comment_btn)
            random_sleep(0.5,1.0)
        except Exception as exc:
            _log.info("[Comment] 點擊留言按鈕失敗：%s",exc)
            return False

        comment_box=self._find_comment_box_after_click(post)
        if comment_box is None:
            _log.info("[Comment] 找不到留言框，直接跳過。")
            return False

        if not post.comment_generated:
            generated=self._comment_gen.generate(post.text)
            if not generated:
                return False
            post.comment_generated=generated

        try:
            self._ctrl.click(comment_box)
            random_sleep(0.2,0.5)
            self._ctrl.type_text(comment_box,post.comment_generated)
            comment_box.send_keys(Keys.RETURN)
            _log.info("[Comment] 留言成功。")
            return True
        except Exception as exc:
            _log.info("[Comment] 留言失敗：%s",exc)
            return False

    def try_share(self, post: PostInfo) -> bool:
        """
        V4.2分享：
        用 XPath + JS 文字找 Share / 分享 / Ibahagi，點擊後找彈窗確認。
        """
        _log.info("[Share] 開始嘗試分享。")

        btn = self._quick_find_in_post(post, self._SHARE_BTN_XPATHS, "Share")
        if btn is None:
            btn = self._quick_find_by_text_js(post, ["Share", "分享", "Ibahagi"], "Share")

        if not btn:
            _log.info("[Share] 找不到分享按鈕，跳過。")
            return False

        try:
            if not self._ctrl.click(btn):
                _log.info("[Share] 分享按鈕點擊失敗。")
                return False

            random_sleep(1.2, 2.0)

            confirm = self._find_share_confirm()
            if not confirm:
                _log.info("[Share] 找不到確認分享按鈕，跳過。")
                return False

            if self._ctrl.click(confirm):
                random_sleep(1.5, 2.5)
                _log.info("[Share] 分享成功。")
                return True

            _log.info("[Share] 確認分享點擊失敗。")
            return False
        except Exception as exc:
            _log.info("[Share] 分享失敗，跳過：%s", exc)
            return False

    def _find_primary_blue_button(self, footer_only: bool = False) -> Optional[WebElement]:
        """
        找目前最上層可見 Dialog 中的主要藍色按鈕。

        footer_only=True 時，只找 Dialog 底部操作列中的按鈕，並排除：
        - role=switch
        - aria-checked 元件
        - checkbox / radio
        - 加強推廣貼文等切換控制
        """
        try:
            return self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                r"""
                const footerOnly = arguments[0] === true;
                const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(d=>{
                    const r=d.getBoundingClientRect(), s=getComputedStyle(d);
                    return r.width>250 && r.height>140 && r.bottom>0 && r.top<innerHeight &&
                           s.display!=='none' && s.visibility!=='hidden' &&
                           parseFloat(s.opacity||'1')>0;
                });
                if (!dialogs.length) return null;

                dialogs.sort((a,b)=>{
                    const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                    return br.width*br.height-ar.width*ar.height;
                });
                const d=dialogs[0];
                const dr=d.getBoundingClientRect();
                const bottomZone = dr.top + dr.height * 0.72;

                const buttons=[...d.querySelectorAll('[role="button"],button')].filter(b=>{
                    const r=b.getBoundingClientRect(), s=getComputedStyle(b);
                    if (r.width<90 || r.height<28 || r.bottom<=0 || r.top>=innerHeight) return false;
                    if (b.disabled || b.getAttribute('aria-disabled')==='true') return false;
                    if (s.display==='none' || s.visibility==='hidden' || parseFloat(s.opacity||'1')<=0) return false;

                    // 明確排除 Switch / Toggle / Checkbox / Radio。
                    const role=(b.getAttribute('role')||'').toLowerCase();
                    const type=(b.getAttribute('type')||'').toLowerCase();
                    if (role==='switch' || role==='checkbox' || role==='radio') return false;
                    if (type==='checkbox' || type==='radio') return false;
                    if (b.hasAttribute('aria-checked')) return false;
                    if (b.closest('[role="switch"],[role="checkbox"],[role="radio"]')) return false;

                    const t=(b.innerText||b.textContent||'').trim();
                    if (/加強推廣|boost post|promouvoir|booster|promotion|โปรโมทโพสต์|ترويج المنشور/i.test(t)) return false;

                    // 貼文設定頁只允許底部操作列。
                    if (footerOnly && r.top < bottomZone) return false;
                    return true;
                });
                if (!buttons.length) return null;

                function score(b){
                    const r=b.getBoundingClientRect();
                    const bg=getComputedStyle(b).backgroundColor||'';
                    const nums=bg.match(/\d+/g)||[];
                    const t=(b.innerText||b.textContent||'').trim();
                    const label=(b.getAttribute('aria-label')||t)
                        .replace(/\s+/g,' ').trim().toLowerCase();
                    let n=0;

                    // 底部、靠右、較寬優先。
                    n += r.top * 2;
                    n += r.left * 1.2;
                    n += r.width * 2;

                    if(nums.length>=3){
                        const rr=+nums[0],gg=+nums[1],bb=+nums[2];
                        if(bb>rr+30 && bb>gg+10) n+=10000;
                    }

                    // 發佈類文字優先；儲存類文字降權。
                    const publishTerms=[
                        'post','publish','publier','發佈','發布','发布','發表',
                        'i-post','โพสต์','نشر'
                    ];
                    const nextTerms=[
                        'next','continue','suivant','繼續','下一步','susunod',
                        'ถัดไป','ดำเนินการต่อ','التالي','متابعة'
                    ];
                    const saveTerms=[
                        'save','儲存','保存','enregistrer','i-save','บันทึก','حفظ'
                    ];
                    if(publishTerms.includes(label)) n+=12000;
                    if(nextTerms.includes(label)) n+=7000;
                    if(saveTerms.some(x=>label===x||label.startsWith(x+' '))) n-=5000;
                    return n;
                }

                buttons.sort((a,b)=>score(b)-score(a));
                return buttons[0]||null;
                """,
                footer_only,
            )
        except Exception:
            return None



    def _dismiss_notification_permission_before_scroll(self) -> bool:
        """
        V3.2：不再點擊頁面任何位置，只解除網頁元素焦點並送出 ESC。

        Chrome 原生通知權限泡泡不在網頁 DOM 中，無法用 XPath 或 JavaScript
        直接定位。左下方可能存在廣告，因此禁止以滑鼠點擊空白位置；
        改用 blur 與 ESC 關閉或失焦提示。
        """
        driver = self._ctrl.driver
        if driver is None:
            return False

        handled = False

        try:
            driver.switch_to.default_content()
        except Exception:
            pass

        # 只解除目前網頁元素焦點，不派送 click／MouseEvent。
        try:
            driver.execute_script(
                """
                const active = document.activeElement;
                if (active && active !== document.body &&
                    typeof active.blur === 'function') active.blur();
                """
            )
            handled = True
            _log.info("[Permission] 已安全解除頁面焦點（未點擊任何位置）。")
        except Exception as exc:
            _log.info("[Permission] 解除頁面焦點失敗，直接送出 ESC：%s", exc)

        # 再送 ESC，關閉或使 Chrome 權限泡泡失去焦點。
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            _log.info("[Permission] 滑動前已送出 ESC。")
            handled = True
            time.sleep(0.25)
        except Exception:
            try:
                body = driver.find_element(By.TAG_NAME, "body")
                body.send_keys(Keys.ESCAPE)
                handled = True
                time.sleep(0.2)
            except Exception:
                pass

        return handled

    def _dismiss_cookie_consent(self) -> bool:
        """在主頁及 iframe 中關閉 Facebook Cookie 視窗，成功關閉後才允許滑動。"""
        driver = self._ctrl.driver
        if driver is None:
            return False

        # 優先點「不同意選用 Cookie」，找不到才點「允許所有 Cookie」。
        reject_terms = [
            "不同意選用 cookie", "不同意選用cookie", "僅允許必要 cookie", "僅允許必要cookie",
            "拒絕選用 cookie", "拒絕選用cookie", "拒絕非必要 cookie", "拒絕非必要cookie",
            "decline optional cookies", "reject optional cookies", "only allow essential cookies",
            "necessary cookies only", "reject all", "decline all",
        ]
        allow_terms = [
            "允許所有 cookie", "允許所有cookie", "允許全部 cookie", "允許全部cookie",
            "allow all cookies", "accept all cookies", "allow all", "accept all",
            "payagan ang lahat ng cookies", "tanggapin lahat ng cookies",
        ]
        terms = reject_terms + allow_terms

        def normalize(value: str) -> str:
            return " ".join((value or "").split()).strip().lower()

        def click_in_current_frame() -> tuple[bool, str, bool]:
            """回傳：(是否點擊、按鈕文字、是否看見 Cookie 視窗)。"""
            try:
                result = driver.execute_script(
                    r"""
                    const terms = arguments[0];
                    function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                    function visible(el){
                        if(!el) return false;
                        const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                        return r.width>10 && r.height>10 && r.bottom>0 && r.top<innerHeight &&
                               s.display!=='none' && s.visibility!=='hidden' && parseFloat(s.opacity||'1')>0;
                    }
                    const pageText=norm(document.body ? document.body.innerText : '');
                    const hasCookie=/cookie|cookies|餅乾/.test(pageText);
                    if(!hasCookie) return {clicked:false,text:'',present:false};

                    // 只掃真正可點擊元素；避免抓到整個父層 div。
                    const nodes=[...document.querySelectorAll(
                        'button,[role="button"],input[type="button"],input[type="submit"],a[role="button"]'
                    )].filter(visible);

                    for(const term of terms){
                        for(const node of nodes){
                            const text=norm(
                                node.getAttribute('aria-label') ||
                                node.getAttribute('value') ||
                                node.innerText || node.textContent || ''
                            );
                            if(!(text===term || text.startsWith(term) || text.includes(term))) continue;
                            node.scrollIntoView({block:'center',inline:'center'});
                            try { node.click(); }
                            catch(e){
                                node.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                            }
                            return {clicked:true,text:text,present:true};
                        }
                    }
                    return {clicked:false,text:'',present:true};
                    """,
                    terms,
                ) or {}
                return bool(result.get("clicked")), str(result.get("text") or ""), bool(result.get("present"))
            except Exception:
                return False, "", False

        def scan_frames(depth: int = 0) -> tuple[bool, str, bool]:
            clicked, text, present = click_in_current_frame()
            if clicked:
                return True, text, True
            any_present = present
            if depth >= 3:
                return False, "", any_present

            try:
                frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
            except Exception:
                frames = []

            for frame in frames[:12]:
                try:
                    driver.switch_to.frame(frame)
                    child_clicked, child_text, child_present = scan_frames(depth + 1)
                    driver.switch_to.parent_frame()
                    any_present = any_present or child_present
                    if child_clicked:
                        return True, child_text, True
                except Exception:
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        try:
                            driver.switch_to.default_content()
                        except Exception:
                            pass
            return False, "", any_present

        try:
            driver.switch_to.default_content()
            clicked, text, present = scan_frames()
            driver.switch_to.default_content()

            if clicked:
                _log.info("[Cookie] 已點擊 Cookie 視窗按鈕：%s", text or "未知")
                # 等待遮罩真正消失，避免按完立即滑動仍被擋住。
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    time.sleep(0.35)
                    driver.switch_to.default_content()
                    _, _, still_present = scan_frames()
                    driver.switch_to.default_content()
                    if not still_present:
                        _log.info("[Cookie] Cookie 視窗已關閉，開始瀏覽。")
                        return True
                _log.warning("[Cookie] 已點擊按鈕，但 Cookie 視窗仍存在。")
                return True

            if present:
                _log.warning("[Cookie] 偵測到 Cookie 視窗，但找不到可點擊的允許／不同意按鈕。")
            return False
        except Exception as exc:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            _log.info("[Cookie] Cookie 視窗處理失敗，略過：%s", exc)
            return False

    def _click_update_settings_save(self) -> bool:
        """在受眾設定頁點擊專用 Save，並驗證該設定視窗已真正離開。"""
        save_terms = [
            '儲存', '保存', 'save', 'i-save', 'enregistrer',
            'บันทึก', 'حفظ',
        ]
        try:
            # 先把最上層設定視窗捲到最底部，讓真正的藍色儲存鍵出現。
            self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                r"""
                const dialogs=[...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].filter(d=>{
                    const r=d.getBoundingClientRect(),s=getComputedStyle(d);
                    return r.width>250&&r.height>140&&r.bottom>0&&r.top<innerHeight&&s.display!=='none'&&s.visibility!=='hidden';
                });
                dialogs.sort((a,b)=>{
                    const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                    return br.width*br.height-ar.width*ar.height;
                });
                const d=dialogs.length?dialogs[0]:document.scrollingElement;
                const scrollables=[d,...(d&&d.querySelectorAll?[...d.querySelectorAll('*')]:[])].filter(e=>{
                    try{const s=getComputedStyle(e);return e.scrollHeight>e.clientHeight+20 && /(auto|scroll)/.test(s.overflowY);}catch(x){return false;}
                });
                for(const e of scrollables){try{e.scrollTop=e.scrollHeight;}catch(x){}}
                """
            )
            random_sleep(0.5, 0.9)

            info = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                r"""
                const terms=arguments[0].map(x=>x.toLowerCase());
                function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                function visible(el){
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>=80&&r.height>=28&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0&&
                           !el.disabled&&el.getAttribute('aria-disabled')!=='true';
                }
                const dialogs=[...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].filter(d=>{
                    const r=d.getBoundingClientRect(),s=getComputedStyle(d);
                    return r.width>250&&r.height>140&&r.bottom>0&&r.top<innerHeight&&s.display!=='none'&&s.visibility!=='hidden';
                });
                dialogs.sort((a,b)=>{
                    const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                    return br.width*br.height-ar.width*ar.height;
                });
                const root=dialogs.length?dialogs[0]:document.body;
                const rr=root.getBoundingClientRect();
                const candidates=[...root.querySelectorAll('[role="button"],button')].filter(b=>{
                    if(!visible(b)) return false;
                    const role=(b.getAttribute('role')||'').toLowerCase();
                    if(['switch','checkbox','radio'].includes(role)||b.hasAttribute('aria-checked')) return false;
                    const t=norm(b.getAttribute('aria-label')||b.innerText||b.textContent||'');
                    return terms.some(x=>t===x||t.startsWith(x+' '));
                });
                if(!candidates.length) return {ok:false};
                // 這個 Facebook 頁面的 Save 按鈕有穩定的完整 aria-label，
                // 優先使用它，避免點到 Dialog 內其他含 Save 文字的元素。
                const exact=candidates.find(b=>
                    norm(b.getAttribute('aria-label'))===
                    'save privacy audience selection and close dialog'
                );
                if(exact){
                    const r=exact.getBoundingClientRect();
                    exact.scrollIntoView({block:'center'});
                    try{exact.click();}catch(e){
                        exact.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                    }
                    return {ok:true,text:norm(exact.getAttribute('aria-label')),top:Math.round(r.top),width:Math.round(r.width)};
                }
                function score(b){
                    const r=b.getBoundingClientRect(),bg=getComputedStyle(b).backgroundColor||'';
                    const nums=bg.match(/\d+/g)||[];
                    let n=r.top*5+r.width*2;
                    if(r.top>rr.top+rr.height*0.65)n+=12000;
                    if(nums.length>=3){const R=+nums[0],G=+nums[1],B=+nums[2];if(B>R+25&&B>G+8)n+=15000;}
                    return n;
                }
                candidates.sort((a,b)=>score(b)-score(a));
                const btn=candidates[0],r=btn.getBoundingClientRect();
                const text=norm(btn.getAttribute('aria-label')||btn.innerText||btn.textContent||'');
                btn.scrollIntoView({block:'center'});
                try{btn.click();}catch(e){btn.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));}
                return {ok:true,text:text,top:Math.round(r.top),width:Math.round(r.width)};
                """,
                save_terms,
            ) or {"ok": False}
            if not info.get("ok"):
                _log.info("[Post] 受眾設定頁找不到明確文字為儲存／Save 的底部按鈕。")
                return False

            _log.info(
                "[Post] 已點擊最底部儲存按鈕：text=%s, top=%s, width=%s。",
                info.get("text"), info.get("top"), info.get("width"),
            )

            # 必須驗證含 Public 與專用 Save 的受眾設定視窗真的離開。
            # 不可再用 Radio 數量判斷，因為 Facebook 目前可能顯示 8 或 9 個。
            deadline=time.time()+6.0
            while time.time()<deadline:
                try:
                    still_open=bool(self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                        r"""
                        function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                        const publicTerms=['public','公開','pampubliko','สาธารณะ','عام','العامة'];
                        const saveTerms=['save','儲存','保存','enregistrer','i-save','บันทึก','حفظ'];
                        const dialogs=[...document.querySelectorAll('[role="dialog"], [aria-modal="true"]')].filter(d=>{
                            const r=d.getBoundingClientRect(),s=getComputedStyle(d);
                            return r.width>250&&r.height>140&&r.bottom>0&&r.top<innerHeight&&s.display!=='none'&&s.visibility!=='hidden';
                        });
                        return dialogs.some(d=>{
                            const hasPublic=[...d.querySelectorAll('span,div')].some(x=>
                                x.children.length===0 && publicTerms.includes(norm(x.textContent))
                            );
                            const hasSave=[...d.querySelectorAll('[role="button"],button')].some(b=>
                                norm(b.getAttribute('aria-label'))===
                                'save privacy audience selection and close dialog' ||
                                saveTerms.includes(norm(
                                    b.getAttribute('aria-label')||b.innerText||b.textContent
                                ))
                            );
                            return hasPublic && hasSave;
                        });
                        """
                    ))
                    if not still_open:
                        _log.info("[Post] 儲存成功，已離開受眾設定頁。")
                        return True
                except Exception:
                    pass
                time.sleep(0.3)
            _log.info("[Post] 點擊儲存後受眾設定頁仍存在，判定沒有真正按成功。")
            return False
        except Exception as exc:
            _log.info("[Post] 點擊底部儲存按鈕失敗：%s", exc)
            return False

    def _ensure_post_composer_public(self) -> bool:
        """Ensure the Create post composer is Public in every supported locale."""
        driver = self._ctrl.driver
        if driver is None:
            return False

        public_terms = [x.casefold() for x in POST_PUBLIC_TERMS]
        restricted_terms = [
            x.casefold() for x in POST_FRIENDS_TERMS + POST_ONLY_ME_TERMS
        ]
        done_terms = [
            x.casefold() for x in POST_DONE_TERMS + POST_SAVE_TERMS
        ]
        try:
            state = driver.execute_script(
                r"""
                const publicTerms=arguments[0], restrictedTerms=arguments[1];
                function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                function visible(el){
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden'&&
                           parseFloat(s.opacity||'1')>0;
                }
                function matches(value, terms){
                    const t=norm(value);
                    return terms.some(x=>t===x||t.startsWith(x+' ')||t.endsWith(' '+x));
                }
                const dialogs=[...document.querySelectorAll('[role="dialog"]')]
                    .filter(visible);
                if(!dialogs.length)return {ok:false,reason:'create_post_dialog_not_found'};
                const d=dialogs.find(x=>x.querySelector(
                    '[contenteditable="true"][role="textbox"],textarea'
                ))||dialogs.sort((a,b)=>{
                    const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                    return br.width*br.height-ar.width*ar.height;
                })[0];
                const dr=d.getBoundingClientRect();
                const candidates=[...d.querySelectorAll('[role="button"],button')]
                    .filter(b=>{
                        if(!visible(b)||b.disabled||b.getAttribute('aria-disabled')==='true')return false;
                        const r=b.getBoundingClientRect();
                        if(r.top>dr.top+dr.height*.48)return false;
                        const value=(b.innerText||b.textContent||b.getAttribute('aria-label')||'');
                        return matches(value,publicTerms)||matches(value,restrictedTerms);
                    });
                const currentPublic=candidates.find(b=>
                    matches((b.innerText||b.textContent||b.getAttribute('aria-label')||''),publicTerms)
                );
                if(currentPublic)return {ok:true,already:true};
                const selector=candidates.find(b=>
                    matches((b.innerText||b.textContent||b.getAttribute('aria-label')||''),restrictedTerms)
                );
                if(!selector)return {ok:false,reason:'audience_selector_not_found'};
                selector.scrollIntoView({block:'center'});
                try{selector.click();}catch(e){
                    selector.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                }
                return {ok:true,opened:true};
                """,
                public_terms,
                restricted_terms,
            ) or {}
        except Exception as exc:
            _log.info("[Post] 檢查建立貼文分享對象失敗：%s", exc)
            return False

        if not state.get("ok"):
            _log.info("[Post] 找不到建立貼文分享對象：%s。", state.get("reason"))
            return False
        if state.get("already"):
            _log.info("[Post] 建立貼文分享對象已是 Public。")
            return True

        deadline = time.time() + 8.0
        selected = False
        while time.time() < deadline and not selected:
            try:
                result = driver.execute_script(
                    r"""
                    const publicTerms=arguments[0];
                    function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                    function visible(el){
                        const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                        return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&
                               s.display!=='none'&&s.visibility!=='hidden';
                    }
                    function matches(v){const t=norm(v);return publicTerms.some(x=>t===x||t.startsWith(x+' '));}
                    const dialogs=[...document.querySelectorAll('[role="dialog"]')]
                        .filter(visible).reverse();
                    for(const d of dialogs){
                        const labels=[...d.querySelectorAll('span,div,label')].filter(x=>
                            visible(x)&&x.children.length===0&&matches(x.textContent)
                        );
                        for(const label of labels){
                            let row=label,radio=null;
                            for(let i=0;row&&row!==d&&i<12;i++,row=row.parentElement){
                                radio=row.querySelector('[role="radio"],input[type="radio"]');
                                if(radio)break;
                            }
                            if(!radio)continue;
                            if(radio.getAttribute('aria-checked')==='true'||radio.checked===true)
                                return {ok:true,already:true};
                            const clickable=row.closest(
                                '[role="radio"],label,[role="button"],[role="none"][tabindex="-1"]'
                            )||row;
                            clickable.scrollIntoView({block:'center'});
                            try{clickable.click();}catch(e){
                                clickable.dispatchEvent(new MouseEvent('click',{
                                    bubbles:true,cancelable:true,view:window
                                }));
                            }
                            return {ok:true,clicked:true};
                        }
                    }
                    return {ok:false};
                    """,
                    public_terms,
                ) or {}
                selected = bool(result.get("ok"))
            except Exception:
                selected = False
            if not selected:
                time.sleep(0.25)
        if not selected:
            _log.info("[Post] 分享對象視窗找不到 Public。")
            return False

        time.sleep(0.6)
        try:
            clicked_done = bool(driver.execute_script(
                r"""
                const terms=arguments[0];
                function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                function visible(el){
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&
                           s.display!=='none'&&s.visibility!=='hidden';
                }
                const dialogs=[...document.querySelectorAll('[role="dialog"]')]
                    .filter(visible).reverse();
                for(const d of dialogs){
                    if(!d.querySelector('[role="radio"],input[type="radio"]'))continue;
                    const buttons=[...d.querySelectorAll('[role="button"],button')].filter(b=>{
                        if(!visible(b)||b.disabled||b.getAttribute('aria-disabled')==='true')return false;
                        const t=norm(b.getAttribute('aria-label')||b.innerText||b.textContent||'');
                        return terms.some(x=>t===x||t.startsWith(x+' '));
                    });
                    if(!buttons.length)continue;
                    buttons.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top);
                    const button=buttons[0];
                    try{button.click();}catch(e){
                        button.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                    }
                    return true;
                }
                return false;
                """,
                done_terms,
            ))
        except Exception:
            clicked_done = False
        if not clicked_done:
            _log.info("[Post] 已選取 Public，但找不到 Done／Save。")
            return False

        time.sleep(0.8)
        _log.info("[Post] 已將建立貼文分享對象改為 Public。")
        return True

    def _handle_post_audience_popup(self) -> None:
        """快速處理首次發文的受眾設定頁；最多連續點擊 5 個主要藍色按鈕。"""
        for step in range(1, 6):
            # 真正發文框已出現時立即停止，不再多等。
            try:
                ready = bool(self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    return [...document.querySelectorAll(
                        '[contenteditable="true"][role="textbox"][data-lexical-editor="true"]'
                    )].some(e=>{
                        const r=e.getBoundingClientRect();
                        const p=(e.getAttribute('aria-placeholder')||'').trim();
                        const a=(e.getAttribute('aria-label')||'').trim();
                        return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
                               p!=='Aa' && !/^Write to /i.test(a) && !/^Écrire à /i.test(a);
                    });
                    """
                ))
                if ready:
                    return
            except Exception:
                pass

            btn = None
            deadline = time.time() + 2.5
            while time.time() < deadline and btn is None:
                btn = self._find_primary_blue_button()
                if btn is None:
                    time.sleep(0.2)

            if btn is None:
                return

            try:
                self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    "arguments[0].scrollIntoView({block:'center'});arguments[0].click();",
                    btn,
                )
                _log.info("[PostPopup] 已點擊第 %d 個主要按鈕。", step)
                random_sleep(0.5, 0.9)
            except Exception:
                return

    def _attach_post_media(self, editor: WebElement, media_path: str | Path) -> str:
        """Attach one RC19-selected photo/video and wait until its draft is ready."""
        path = Path(media_path).expanduser().resolve()
        kind = media_kind(path)
        if kind not in {"photo", "video"}:
            raise RuntimeError(f"PO 文媒體格式不支援：{path.suffix}")
        if not path.is_file() or path.stat().st_size <= 0:
            raise FileNotFoundError(f"PO 文媒體檔不存在或為空：{path}")

        driver = self._ctrl.driver
        upload_input = None
        deadline = time.time() + 10.0
        clicked_media_button = False
        while time.time() < deadline and upload_input is None:
            upload_input = driver.execute_script(  # type: ignore[union-attr]
                r"""
                const editor=arguments[0], kind=arguments[1], extension=arguments[2];
                const dialog=editor.closest('[role="dialog"]') ||
                    [...document.querySelectorAll('[role="dialog"]')].filter(d=>{
                        const r=d.getBoundingClientRect(),s=getComputedStyle(d);
                        return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
                    }).pop();
                if(!dialog) return null;
                const inputs=[...dialog.querySelectorAll('input[type="file"]')];
                const scored=inputs.map((input,index)=>{
                    const accept=(input.getAttribute('accept')||'').toLowerCase();
                    let score=index;
                    if(!accept) score+=10;
                    if(extension&&accept.includes(extension)) score+=60;
                    if(kind==='photo'&&accept.includes('image')) score+=50;
                    if(kind==='video'&&accept.includes('video')) score+=50;
                    if(kind==='photo'&&accept.includes('video')&&!accept.includes('image')) score-=100;
                    if(kind==='video'&&accept.includes('image')&&!accept.includes('video')) score-=100;
                    if(input.multiple) score+=5;
                    return {input,score};
                }).sort((a,b)=>b.score-a.score);
                return scored.length&&scored[0].score>=0 ? scored[0].input : null;
                """,
                editor,
                kind,
                path.suffix.casefold(),
            )
            if upload_input is not None:
                break
            if not clicked_media_button:
                clicked_media_button = bool(driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    const editor=arguments[0];
                    const dialog=editor.closest('[role="dialog"]');
                    if(!dialog) return false;
                    const terms=[
                        'photo/video','photo or video','photos/videos','add photos/videos',
                        '相片／影片','相片/影片','新增相片／影片','新增相片/影片',
                        'larawan/video','photo/vidéo','photo ou vidéo',
                        'รูปภาพ/วิดีโอ','صور/فيديو','صورة/فيديو'
                    ];
                    const nodes=[...dialog.querySelectorAll('[role="button"],button,label')];
                    for(const node of nodes){
                        const text=((node.getAttribute('aria-label')||'')+' '+
                            (node.innerText||node.textContent||'')).trim().toLowerCase();
                        if(!terms.some(term=>text.includes(term))) continue;
                        const r=node.getBoundingClientRect();
                        if(r.width<=0||r.height<=0) continue;
                        node.click(); return true;
                    }
                    return false;
                    """,
                    editor,
                ))
            time.sleep(0.25)

        if upload_input is None:
            raise RuntimeError("建立貼文視窗內找不到相片／影片上傳欄位")
        upload_input.send_keys(str(path))
        _log.info("[PostMedia] 已送入%s：%s", "相片" if kind == "photo" else "影片", path)

        timeout = 180.0 if kind == "video" else 45.0
        deadline = time.time() + timeout
        stable_ready = 0
        last_state: dict = {}
        while time.time() < deadline:
            last_state = driver.execute_script(  # type: ignore[union-attr]
                r"""
                const editor=arguments[0];
                const dialog=editor.closest('[role="dialog"]');
                if(!dialog) return {dialog:false};
                const visible=el=>{
                    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                    return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
                };
                const selected=[...dialog.querySelectorAll('input[type="file"]')]
                    .some(input=>input.files&&input.files.length>0);
                const preview=[...dialog.querySelectorAll('img,video,[style*="blob:"]')]
                    .some(el=>visible(el)&&(
                        el.tagName==='VIDEO'||(el.currentSrc||el.src||'').startsWith('blob:')||
                        (el.getAttribute('style')||'').includes('blob:')
                    ));
                const removeTerms=['remove photo','remove video','remove media','移除相片','移除影片',
                    'supprimer la photo','supprimer la vidéo','alisin ang larawan','alisin ang video',
                    'ลบรูปภาพ','ลบวิดีโอ','إزالة الصورة','إزالة الفيديو'];
                const removable=[...dialog.querySelectorAll('[role="button"],button')].some(el=>{
                    const t=((el.getAttribute('aria-label')||'')+' '+(el.innerText||'')).toLowerCase();
                    return visible(el)&&removeTerms.some(term=>t.includes(term));
                });
                const progress=[...dialog.querySelectorAll('[role="progressbar"]')].some(visible);
                const text=(dialog.innerText||'').toLowerCase();
                const busyTerms=['uploading','processing','preparing','正在上傳','處理中','正在處理',
                    'téléchargement','traitement','ina-upload','pinoproseso','กำลังอัปโหลด',
                    'กำลังประมวลผล','جارٍ التحميل','قيد المعالجة'];
                const busy=progress||busyTerms.some(term=>text.includes(term));
                return {dialog:true,selected,preview,removable,progress,busy};
                """,
                editor,
            ) or {}
            ready = bool(last_state.get("dialog")) and bool(
                last_state.get("selected") or last_state.get("preview") or last_state.get("removable")
            ) and not bool(last_state.get("busy"))
            stable_ready = stable_ready + 1 if ready else 0
            if stable_ready >= 3:
                _log.info(
                    "[PostMedia] %s已完成載入%s。",
                    "相片" if kind == "photo" else "影片",
                    "與 Facebook 處理" if kind == "video" else "",
                )
                return kind
            time.sleep(0.5)
        raise TimeoutError(
            f"PO 文{('相片' if kind == 'photo' else '影片')}載入／處理逾時：{last_state}"
        )

    def post_to_own_timeline(self, content: str, media_path: str | Path | None = None) -> bool:
        """V7.1：快速開啟發文框、快速輸入，並處理 Continue 後的二次發佈。"""
        self._ctrl._ensure_driver()

        # 先用單次 JS 掃描，避免 implicit_wait 讓多個 XPath 累積到兩分鐘。
        composer = None
        deadline = time.time() + 5.0
        while time.time() < deadline and composer is None:
            try:
                composer = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    const main=document.querySelector('[role="main"]')||document.body;
                    const terms=[
                        "what's on your mind",'你在想些什麼','你在想什麼','在想些什麼',
                        'ano ang nasa isip mo','quoi de neuf',
                        'คุณกำลังคิดอะไรอยู่','بم تفكر'
                    ];
                    const nodes=[...main.querySelectorAll('[role="button"],span,div')];
                    for(const n of nodes){
                        const t=(n.innerText||n.textContent||'').trim().toLowerCase();
                        if(!t || !terms.some(x=>t.includes(x))) continue;
                        let cur=n;
                        for(let i=0;i<6 && cur;i++,cur=cur.parentElement){
                            if(cur.getAttribute && cur.getAttribute('role')==='button'){
                                const r=cur.getBoundingClientRect();
                                if(r.width>180 && r.height>30 && r.bottom>0 && r.top<innerHeight) return cur;
                            }
                        }
                    }
                    // 跨語言備援：首頁上方中央的大型可點擊卡片。
                    const vw=innerWidth;
                    const buttons=[...main.querySelectorAll('[role="button"]')];
                    for(const b of buttons){
                        const r=b.getBoundingClientRect();
                        if(r.width<220 || r.height<32 || r.height>150 || r.top<70 || r.top>430) continue;
                        const c=r.left+r.width/2;
                        if(c<vw*.18 || c>vw*.78) continue;
                        const t=(b.innerText||b.textContent||'').trim();
                        if(t.length>=3 && t.length<=180) return b;
                    }
                    return null;
                    """
                )
            except Exception:
                composer = None
            if composer is None:
                time.sleep(0.2)

        if composer is None:
            _log.info("[Post] 5 秒內找不到首頁發文入口。")
            return False

        try:
            self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                "arguments[0].scrollIntoView({block:'center'});arguments[0].click();",
                composer,
            )
            _log.info("[Composer] JS 快速找到並點擊發文入口。")
            random_sleep(0.5, 0.8)
            self._handle_post_audience_popup()
        except Exception as exc:
            _log.info("[Post] 開啟發文視窗失敗：%s", exc)
            return False

        editor = None
        deadline = time.time() + 10.0
        while time.time() < deadline and editor is None:
            try:
                editor = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    const list=[...document.querySelectorAll(
                        '[contenteditable="true"][role="textbox"][data-lexical-editor="true"]'
                    )].filter(e=>{
                        const r=e.getBoundingClientRect(), s=getComputedStyle(e);
                        const p=(e.getAttribute('aria-placeholder')||'').trim();
                        const a=(e.getAttribute('aria-label')||'').trim();
                        return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
                               s.display!=='none' && s.visibility!=='hidden' &&
                               p!=='Aa' && !/^Write to /i.test(a) && !/^Écrire à /i.test(a);
                    });
                    if(!list.length) return null;
                    list.sort((a,b)=>{
                        const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                        return br.width*br.height-ar.width*ar.height;
                    });
                    return list[0];
                    """
                )
            except Exception:
                editor = None
            if editor is None:
                time.sleep(0.2)

        if editor is None:
            _log.info("[Post] 10 秒內找不到發文輸入框。")
            return False

        if not self._ensure_post_composer_public():
            _log.info("[Post] 無法確認分享對象為 Public，停止本次發文。")
            return False

        if media_path:
            try:
                self._attach_post_media(editor, media_path)
            except Exception as exc:
                _log.warning("[PostMedia] 附加相片／影片失敗，停止本次發文：%s", exc)
                return False

        safe_content = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not safe_content:
            return False

        try:
            # Windows Unicode 剪貼簿可完整保留多行、空白行與非 BMP Emoji。
            self._ctrl.driver.execute_script("arguments[0].focus();", editor)  # type: ignore[union-attr]
            editor.send_keys(Keys.CONTROL, "a")
            editor.send_keys(Keys.BACKSPACE)
            copy_to_windows_clipboard(safe_content)
            editor.send_keys(Keys.CONTROL, "v")
            random_sleep(0.6, 1.0)
            actual = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                "return (arguments[0].innerText||arguments[0].textContent||'').replace(/\\r/g,'');",
                editor,
            ) or ""
            normalize = lambda value: " ".join(str(value).split())
            if normalize(actual) != normalize(safe_content):
                # 貼上未完整生效時先清空再用瀏覽器原生 insertText，避免文案重複。
                editor.send_keys(Keys.CONTROL, "a")
                editor.send_keys(Keys.BACKSPACE)
                inserted = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    const el=arguments[0], text=arguments[1];
                    el.focus();
                    const ok=document.execCommand('insertText', false, text);
                    el.dispatchEvent(new InputEvent('input', {
                        bubbles:true, inputType:'insertText', data:text
                    }));
                    return ok;
                    """,
                    editor,
                    safe_content,
                )
                random_sleep(0.3, 0.5)
                actual = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    "return (arguments[0].innerText||arguments[0].textContent||'').replace(/\\r/g,'');",
                    editor,
                ) or ""
                if not inserted or normalize(actual) != normalize(safe_content):
                    raise RuntimeError("多行文案輸入後驗證不一致")
            _log.info("[Post] 已輸入並驗證隨機文案（保留多行與 Emoji）。")
        except Exception as exc:
            _log.info("[Post] 輸入貼文失敗：%s", exc)
            return False

        # 第一階段：Create post 畫面的 Post / Continue。
        submit = None
        deadline = time.time() + 6.0
        while time.time() < deadline and submit is None:
            submit = self._find_primary_blue_button()
            if submit is None:
                time.sleep(0.2)

        if submit is None:
            _log.info("[Post] 找不到第一個主要發佈按鈕。")
            return False

        try:
            self._ctrl.driver.execute_script("arguments[0].click();", submit)  # type: ignore[union-attr]
            _log.info("[Post] 已點擊第一階段主要按鈕，等待下一層畫面完整載入。")
            # 第一次按鍵後放慢，避免下一層尚未完成就誤判或重複點擊。
            random_sleep(3.2, 4.2)
        except Exception as exc:
            _log.info("[Post] 第一階段發佈失敗：%s", exc)
            return False

        def get_post_dialog_state() -> dict:
            try:
                return self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    function visible(el){
                        const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                        return r.width>0 && r.height>0 && r.right>0 && r.left<innerWidth &&
                               r.bottom>0 && r.top<innerHeight && s.display!=='none' &&
                               s.visibility!=='hidden' && parseFloat(s.opacity||'1')>0;
                    }
                    function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                    const updateTerms=[
                        'update settings','更新設定','更新设置',
                        'i-update ang mga setting','อัปเดตการตั้งค่า','تحديث الإعدادات'
                    ];
                    const publicTerms=[
                        'public','公開','pampubliko','สาธารณะ','عام','العامة'
                    ];
                    const saveTerms=[
                        'save','儲存','保存','enregistrer','i-save','บันทึก','حفظ'
                    ];
                    const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(d=>{
                        const r=d.getBoundingClientRect(), s=getComputedStyle(d);
                        return r.width>250 && r.height>140 && r.bottom>0 && r.top<innerHeight &&
                               s.display!=='none' && s.visibility!=='hidden';
                    });
                    const body=(document.body.innerText||'').toLowerCase();
                    if(!dialogs.length){
                        return {open:false, radio_count:0, duplicate:/duplicate|重複|重复|相同內容|same content/.test(body)};
                    }
                    dialogs.sort((a,b)=>{
                        const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                        return br.width*br.height-ar.width*ar.height;
                    });
                    const d=dialogs[0];
                    const radios=[...d.querySelectorAll('[role="radio"],input[type="radio"]')].filter(x=>{
                        const r=x.getBoundingClientRect(), s=getComputedStyle(x);
                        return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
                               s.display!=='none' && s.visibility!=='hidden';
                    });
                    return {
                        open:true,
                        radio_count:radios.length,
                        // Facebook 會把 Review audience 與 Continue 後的 Update settings
                        // 同時保留在同一個 Dialog DOM，所以必須只計算目前可見的區塊。
                        audience_settings:[...d.querySelectorAll('h1,h2,span,div')].some(x=>
                            visible(x) && x.children.length===0 &&
                            updateTerms.includes(norm(x.textContent))
                        ) && [...d.querySelectorAll('span,div')].some(x=>
                            visible(x) && x.children.length===0 &&
                            publicTerms.includes(norm(x.textContent))
                        ) && [...d.querySelectorAll('[role="button"],button')].some(b=>
                            visible(b) && norm(b.getAttribute('aria-label'))===
                            'save privacy audience selection and close dialog' ||
                            visible(b) && saveTerms.includes(norm(
                                b.getAttribute('aria-label')||b.innerText||b.textContent
                            ))
                        ),
                        duplicate:/duplicate|重複|重复|相同內容|same content/.test(body)
                    };
                    """
                ) or {}
            except Exception:
                return {"open": True, "radio_count": 0, "duplicate": False}

        def click_first_radio() -> bool:
            """在 Radio 設定頁精準選取 Public。

            Facebook 的 Public input 本身沒有 aria-label，而且直接對 input
            執行 JavaScript click() 有時不會觸發 React 的選項列事件。因此改為
            依同一個 Dialog 內的 Public 文字找到整個選項列，點擊後
            再驗證該列 radio 的 aria-checked 已變為 true。
            """
            try:
                result = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    function visible(el){
                        const r=el.getBoundingClientRect(), s=getComputedStyle(el);
                        return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
                               s.display!=='none' && s.visibility!=='hidden' &&
                               parseFloat(s.opacity||'1')>0;
                    }
                    function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                    const updateTerms=[
                        'update settings','更新設定','更新设置',
                        'i-update ang mga setting','อัปเดตการตั้งค่า','تحديث الإعدادات'
                    ];
                    const publicTerms=[
                        'public','公開','pampubliko','สาธารณะ','عام','العامة'
                    ];
                    const saveTerms=[
                        'save','儲存','保存','enregistrer','i-save','บันทึก','حفظ'
                    ];
                    const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(d=>{
                        const r=d.getBoundingClientRect(), s=getComputedStyle(d);
                        return r.width>250 && r.height>140 && r.bottom>0 && r.top<innerHeight &&
                               s.display!=='none' && s.visibility!=='hidden';
                    });
                    if(!dialogs.length) return {ok:false,reason:'no_dialog'};
                    dialogs.sort((a,b)=>{
                        const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                        return br.width*br.height-ar.width*ar.height;
                    });
                    const d=dialogs[0];
                    const updateHeading=[...d.querySelectorAll('h1,h2,span,div')].find(x=>
                        visible(x) && x.children.length===0 &&
                        updateTerms.includes(norm(x.textContent))
                    );
                    if(!updateHeading) return {ok:false,reason:'visible_update_settings_not_found'};
                    const radios=[...d.querySelectorAll('[role="radio"],input[type="radio"]')].filter(x=>{
                        const r=x.getBoundingClientRect(), s=getComputedStyle(x);
                        return r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight &&
                               s.display!=='none' && s.visibility!=='hidden';
                    });
                    // Facebook 的受眾選項數會因帳號與版面變成 8 或 9；
                    // 以 Public 選項與專用 Save 按鈕辨識，不再鎖死數量。
                    const hasAudienceSave=[...d.querySelectorAll('[role="button"],button')].some(b=>
                        norm(b.getAttribute('aria-label'))===
                        'save privacy audience selection and close dialog' ||
                        saveTerms.includes(norm(
                            b.getAttribute('aria-label')||b.innerText||b.textContent
                        ))
                    );
                    if(!hasAudienceSave) return {ok:false,reason:'audience_save_not_found'};

                    const labels=[...d.querySelectorAll('span,div')].filter(el=>
                        visible(el) && publicTerms.includes(norm(el.textContent)) &&
                        el.children.length===0
                    );
                    if(!labels.length) return {ok:false,reason:'public_text_not_found'};

                    let row=null, radio=null;
                    for(const label of labels){
                        let p=label;
                        for(let depth=0; p && p!==d && depth<12; depth++,p=p.parentElement){
                            const found=p.querySelector('input[type="radio"],[role="radio"]');
                            if(found){
                                row=p;radio=found;break;
                            }
                        }
                        if(row&&radio) break;
                    }
                    if(!row||!radio) return {ok:false,reason:'public_radio_not_found'};
                    if(radio.getAttribute('aria-checked')==='true' || radio.checked===true)
                        return {ok:true,already:true};

                    // 此版 Public 的可點選項容器是 role="none" + tabindex="-1"，
                    // input 只用來驗證狀態，實際必須點整個選項列。
                    const clickable=row.closest('[role="radio"],label,[role="button"],[role="none"][tabindex="-1"]') || row;
                    clickable.scrollIntoView({block:'center'});
                    try{clickable.click();}catch(e){
                        clickable.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                    }
                    return {ok:true,already:false};
                    """
                ) or {"ok": False, "reason": "empty_result"}
                if not result.get("ok"):
                    _log.info("[Post] Public 選項定位失敗：%s。", result.get("reason"))
                    return False

                deadline = time.time() + 5.0
                while time.time() < deadline:
                    selected = bool(self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                        r"""
                        function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                        const publicTerms=[
                            'public','公開','pampubliko','สาธารณะ','عام','العامة'
                        ];
                        const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(d=>{
                            const r=d.getBoundingClientRect(),s=getComputedStyle(d);
                            return r.width>250&&r.height>140&&r.bottom>0&&r.top<innerHeight&&
                                   s.display!=='none'&&s.visibility!=='hidden';
                        });
                        if(!dialogs.length)return false;
                        dialogs.sort((a,b)=>{
                            const ar=a.getBoundingClientRect(),br=b.getBoundingClientRect();
                            return br.width*br.height-ar.width*ar.height;
                        });
                        const d=dialogs[0];
                        const labels=[...d.querySelectorAll('span,div')].filter(el=>
                            publicTerms.includes(norm(el.textContent)) &&
                            el.children.length===0
                        );
                        for(const label of labels){
                            let p=label;
                            for(let depth=0;p&&p!==d&&depth<12;depth++,p=p.parentElement){
                                const r=p.querySelector('input[type="radio"],[role="radio"]');
                                if(r)
                                    return r.getAttribute('aria-checked')==='true'||r.checked===true;
                            }
                        }
                        return false;
                        """
                    ))
                    if selected:
                        _log.info("[Post] Public 已真正選中（aria-checked=true）。")
                        return True
                    time.sleep(0.25)
                _log.info("[Post] 已點擊 Public，但驗證時仍未選中。")
                return False
            except Exception as exc:
                _log.info("[Post] 精準選取 Public 失敗：%s", exc)
                return False

        def handle_review_audience_page() -> bool:
            """僅在同一個 Review audience modal dialog 內點擊 Continue。"""
            try:
                result = self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                    r"""
                    function visible(el){
                        const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                        return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&
                               s.display!=='none'&&s.visibility!=='hidden'&&parseFloat(s.opacity||'1')>0;
                    }
                    function norm(v){return (v||'').replace(/\s+/g,' ').trim().toLowerCase();}
                    const reviewTerms=[
                        'review audience','檢查分享對象','review ng audience',
                        'ตรวจสอบกลุ่มเป้าหมาย','مراجعة الجمهور'
                    ];
                    const continueTerms=[
                        'continue','繼續','continuer','magpatuloy',
                        'ดำเนินการต่อ','متابعة'
                    ];
                    const dialogs=[...document.querySelectorAll(
                        '[role="dialog"][aria-modal="true"]'
                    )].filter(visible).reverse();
                    for(const root of dialogs){
                        const text=norm(root.innerText||root.textContent||'');
                        if(!reviewTerms.some(x=>text.includes(x))) continue;
                        const buttons=[...root.querySelectorAll(
                            '[role="button"],button'
                        )].filter(b=>{
                            if(!visible(b)||b.disabled||b.getAttribute('aria-disabled')==='true') return false;
                            const t=norm(
                                b.getAttribute('aria-label')||b.innerText||b.textContent
                            );
                            return continueTerms.includes(t);
                        });
                        if(!buttons.length) return {found:true,clicked:false};
                        buttons.sort((a,b)=>b.getBoundingClientRect().top-a.getBoundingClientRect().top);
                        const btn=buttons[0];
                        btn.scrollIntoView({block:'center'});
                        try{btn.click();}catch(e){
                            btn.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));
                        }
                        return {found:true,clicked:true};
                    }
                    return {found:false,clicked:false};
                    """,
                ) or {"found": False, "clicked": False}
            except Exception as exc:
                _log.info("[Post] Review audience 畫面偵測失敗：%s", exc)
                return False

            if not result.get("found"):
                return True
            if not result.get("clicked"):
                _log.info("[Post] 偵測到 Review audience，但找不到該視窗內的 Continue 按鈕。")
                return False

            _log.info("[Post] 偵測到 Review audience，已點擊底部 Continue。")
            deadline = time.time() + 8.0
            while time.time() < deadline:
                try:
                    still_open = bool(self._ctrl.driver.execute_script(  # type: ignore[union-attr]
                        r"""
                        function visible(el){
                            const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                            return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&
                                   s.display!=='none'&&s.visibility!=='hidden';
                        }
                        const continueTerms=[
                            'continue','繼續','continuer','magpatuloy',
                            'ดำเนินการต่อ','متابعة'
                        ];
                        // 同一 Dialog 內會同時留下兩個滑頁 DOM；不能以 Dialog
                        // 含 Review audience 文字來判斷，改驗證可見 Continue 是否消失。
                        return [...document.querySelectorAll(
                            '[role="dialog"][aria-modal="true"] [role="button"][aria-label],'+
                            '[role="dialog"][aria-modal="true"] button[aria-label]'
                        )].filter(visible).some(b=>
                            continueTerms.includes((
                                b.getAttribute('aria-label')||b.innerText||b.textContent||''
                            ).replace(/\s+/g,' ').trim().toLowerCase())
                        );
                        """
                    ))
                    if not still_open:
                        _log.info("[Post] Review audience 已關閉，重新偵測 Radio 設定頁。")
                        random_sleep(1.0, 1.6)
                        return True
                except Exception:
                    pass
                time.sleep(0.3)

            _log.info("[Post] 點擊 Continue 後 Review audience 仍未關閉。")
            return False

        # Facebook 可能在受眾設定頁前額外顯示 Review audience。
        # 確認標題存在才點 Continue，再進入受眾設定辨識。
        if not handle_review_audience_page():
            return False

        # 第一階段可能已直接完成；最多等待 4 秒確認。
        direct_deadline = time.time() + 4.0
        state = get_post_dialog_state()
        while time.time() < direct_deadline:
            state = get_post_dialog_state()
            if state.get("duplicate"):
                _log.info("[Post] Facebook 顯示重複內容提醒；停止操作，避免重複發文。")
                return False
            if not state.get("open"):
                _log.info("[Post] 發文視窗已關閉，確認發佈完成。")
                _log.info("[Post] 菲律賓文貼文已發佈：「%s」", truncate(safe_content, 80))
                return True
            time.sleep(0.35)

        # 有些帳號的 Review audience 會比第一階段按鍵慢幾秒才出現，
        # 因此在正式計算 Radio 前再檢查一次。
        if not handle_review_audience_page():
            return False
        state = get_post_dialog_state()
        if state.get("duplicate"):
            _log.info("[Post] Facebook 顯示重複內容提醒；停止操作，避免重複發文。")
            return False
        if not state.get("open"):
            _log.info("[Post] 發文視窗已關閉，確認發佈完成。")
            _log.info("[Post] 菲律賓文貼文已發佈：「%s」", truncate(safe_content, 80))
            return True

        radio_count = int(state.get("radio_count") or 0)
        _log.info("[Post] 下一層畫面 Radio 數量：%d。", radio_count)

        audience_settings = bool(state.get("audience_settings"))

        # 受眾設定頁以 Public + 專用 Save 的語意結構辨識。
        # Radio 數量只保留在 LOG，不再限定只能等於 9。
        if audience_settings:
            _log.info(
                "[Post] 已辨識受眾設定頁（Radio=%d）：準備選取 Public。",
                radio_count,
            )
            if not click_first_radio():
                _log.info("[Post] Public 選取失敗。")
                return False
            random_sleep(0.8, 1.3)

            if not self._click_update_settings_save():
                _log.info("[Post] 受眾設定頁未能真正按下最底部儲存鍵。")
                return False
            random_sleep(1.5, 2.5)

            # 儲存後回到貼文設定，第二層底部藍色鍵一律視為發佈。
            publish_btn = None
            deadline = time.time() + 8.0
            while time.time() < deadline and publish_btn is None:
                current = get_post_dialog_state()
                if not current.get("open"):
                    _log.info("[Post] 儲存後發文視窗已關閉，確認發佈完成。")
                    _log.info("[Post] 菲律賓文貼文已發佈：「%s」", truncate(safe_content, 80))
                    return True
                if int(current.get("radio_count") or 0) == 0:
                    publish_btn = self._find_primary_blue_button(footer_only=True)
                if publish_btn is None:
                    time.sleep(0.3)

            if publish_btn is None:
                _log.info("[Post] 儲存後找不到貼文設定底部發佈按鈕。")
                return False

        elif radio_count == 0:
            # Radio = 0 代表貼文設定，直接按底部發佈。
            _log.info("[Post] Radio 為 0，判定為貼文設定頁，直接按發佈。")
            publish_btn = self._find_primary_blue_button(footer_only=True)
            if publish_btn is None:
                _log.info("[Post] 找不到貼文設定底部發佈按鈕。")
                return False
        else:
            _log.info(
                "[Post] Radio 數量為 %d，且未辨識到 Public＋專用 Save；停止本次發文，避免誤點。",
                radio_count,
            )
            return False

        # 發佈只按一次，避免重複貼文。
        try:
            self._ctrl.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});arguments[0].click();",
                publish_btn,
            )  # type: ignore[union-attr]
            _log.info("[Post] 已點擊貼文設定底部發佈按鈕一次。")
        except Exception as exc:
            _log.info("[Post] 發佈按鈕點擊失敗：%s", exc)
            return False

        finish_deadline = time.time() + 12.0
        while time.time() < finish_deadline:
            final_state = get_post_dialog_state()
            if final_state.get("duplicate"):
                _log.info("[Post] Facebook 顯示重複內容提醒；停止操作。")
                return False
            if not final_state.get("open"):
                _log.info("[Post] 發文視窗已關閉，確認發佈完成。")
                _log.info("[Post] 菲律賓文貼文已發佈：「%s」", truncate(safe_content, 80))
                return True
            time.sleep(0.5)

        _log.info("[Post] 發佈後視窗仍存在；不再重複點擊發佈。")
        return False

    def try_like_comment_share(self, post: PostInfo) -> tuple[bool, bool, bool]:
        """
        V4.2：對同一篇貼文依序執行 Like → Comment → Share。
        每篇最多三個動作，符合「讚+留言+分享不能超過三次」。
        Like 失敗時不做 Comment / Share。
        """
        liked = False
        commented = False
        shared = False

        _log.info("[Combo] 開始同篇貼文 Like → Comment → Share。")

        liked = self.try_like(post)
        if not liked:
            _log.info("[Combo] Like 失敗，跳過本篇 Comment / Share。")
            return liked, commented, shared

        # Like 成功後才留言
        commented = self.try_comment(post)

        # Like 成功後才分享，即使留言失敗也可嘗試分享
        shared = self.try_share(post)

        _log.info(
            "[Combo] 本篇完成：Like=%s，Comment=%s，Share=%s。",
            liked,
            commented,
            shared,
        )
        return liked, commented, shared

    def _element_has_comments(self, el: WebElement) -> bool:
        try:
            text = el.text.lower()
            return any(k in text for k in ["comment", "留言", "komento", "reply", "回覆"])
        except Exception:
            return False

    def _element_has_photo(self, el: WebElement) -> bool:
        try:
            return len(el.find_elements(By.TAG_NAME, "img")) > 0
        except Exception:
            return False

    def _is_already_liked(self, el: WebElement) -> bool:
        try:
            xpaths = [
                ".//*[@aria-pressed='true' and contains(@aria-label,'Like')]",
                ".//*[@aria-pressed='true' and contains(@aria-label,'讚')]",
                ".//*[@aria-pressed='true' and contains(@aria-label,'喜歡')]",
            ]
            for xpath in xpaths:
                if el.find_elements(By.XPATH, xpath):
                    return True
        except Exception:
            pass
        return False


@dataclass
class BrowseResult:
    """瀏覽結果。"""
    liked_count: int = 0
    commented_count: int = 0
    shared_count: int = 0
    duration_sec: float = 0.0
    actions: list[str] = field(default_factory=list)


class FeedBrowser:
    """動態牆瀏覽主控制器。"""

    def __init__(
        self,
        ctrl: BrowserController,
        comment_gen: CommentGenerator,
        browse_cfg: Optional[BrowseConfig] = None,
        interaction_cfg: Optional[InteractionConfig] = None,
    ) -> None:
        self._ctrl = ctrl
        self._scroll_engine = ScrollEngine(ctrl, browse_cfg)
        self._interactor = FeedInteractor(ctrl, comment_gen, interaction_cfg)
        self._browse_cfg = browse_cfg or CONFIG.browse
        self._interaction_cfg = interaction_cfg or CONFIG.interaction

    def _ensure_facebook_home(self, task_name: str, timeout: float = 15.0) -> bool:
        """在 PO 文或瀏覽前固定進入 Facebook 首頁動態牆。"""
        driver = self._ctrl.driver
        if driver is None:
            return False

        try:
            current_url = (driver.current_url or "").lower()
        except Exception:
            current_url = ""

        try:
            path = current_url.split("facebook.com", 1)[1].split("?", 1)[0].split("#", 1)[0]
        except Exception:
            path = ""

        if path not in ("", "/"):
            _log.info("[%s] 正在前往 Facebook 首頁：%s", task_name, FACEBOOK_HOME_URL + "/")
            try:
                # 不使用 driver.get，避免 Facebook 圖片或背景請求造成長時間阻塞。
                driver.execute_script(
                    "window.location.replace(arguments[0]);",
                    FACEBOOK_HOME_URL + "/",
                )
            except Exception as exc:
                _log.warning("[%s] 導向 Facebook 首頁失敗：%s", task_name, exc)
                return False
        else:
            _log.info("[%s] 目前已在 Facebook 首頁。", task_name)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ready = driver.execute_script(
                    r"""
                    const path=location.pathname.replace(/\/+$/,'') || '/';
                    if(path!=='/') return false;
                    const main=document.querySelector('[role="main"]');
                    if(!main) return false;
                    const r=main.getBoundingClientRect();
                    return r.width>0 && r.height>0;
                    """
                )
                if ready:
                    _log.info("[%s] 已確認 Facebook 首頁動態牆。", task_name)
                    return True
            except Exception:
                pass
            time.sleep(0.25)

        _log.warning("[%s] %s 秒內無法確認 Facebook 首頁動態牆。", task_name, int(timeout))
        return False

    def run(
        self,
        enable_post: bool = True,
        enable_browse_like: bool = True,
        like_target: int = 1,
        post_text_file: str = "",
        post_media_enabled: bool = False,
        post_media_mode: str = "random",
        post_random_media_dir: str = "",
        post_fixed_media_file: str = "",
    ) -> BrowseResult:
        """PO 文與「瀏覽＋按讚」各自獨立；按讚數量可設定。"""
        result = BrowseResult()
        total_start_time = time.time()
        like_target = max(1, int(like_target))

        cfg_min = getattr(self._browse_cfg, "browse_duration_min", 60)
        cfg_max = getattr(self._browse_cfg, "browse_duration_max", 120)
        min_sec = max(30, min(float(cfg_min), 120))
        max_sec = max(min_sec, min(float(cfg_max), 120))
        target_duration = random.uniform(min_sec, max_sec)

        _log.info(
            "開始執行養號流程：PO文=%s、瀏覽／按讚=%s%s。",
            "啟用" if enable_post else "停用",
            "啟用" if enable_browse_like else "停用",
            f"（目標 {like_target} 次，瀏覽 {target_duration:.0f} 秒）"
            if enable_browse_like else "",
        )

        if enable_post:
            if not self._ensure_facebook_home("PO文"):
                _log.warning("[Post] 未能進入 Facebook 首頁，跳過本次發文。")
                result.duration_sec = time.time() - total_start_time
                return result
            try:
                content = self._interactor._comment_gen.generate_filipino_post(post_text_file)
                media_path = None
                if post_media_enabled:
                    media_pool = MediaPool.from_settings({
                        "post_media_mode": post_media_mode,
                        "post_random_media_dir": post_random_media_dir,
                        "post_fixed_media_file": post_fixed_media_file,
                    })
                    media_path = media_pool.claim()
                    if media_path is None:
                        raise RuntimeError("已勾選加相片／影片，但沒有取得素材")
                    _log.info(
                        "[PostMedia] %s模式已選取%s：%s",
                        "隨機" if post_media_mode == "random" else "固定",
                        "相片" if media_kind(media_path) == "photo" else "影片",
                        media_path,
                    )
                if self._interactor.post_to_own_timeline(content, media_path=media_path):
                    result.actions.append("post")
            except Exception as exc:
                _log.warning("[Post] 發文流程失敗，但繼續瀏覽：%s", exc)
        else:
            _log.info("[Post] PO 文功能已停用，跳過發文。")

        if not enable_browse_like:
            result.duration_sec = time.time() - total_start_time
            _log.info(
                "瀏覽／按讚功能已停用：總耗時 %.0f 秒，發文=%s。",
                result.duration_sec,
                "成功" if "post" in result.actions else "失敗或跳過",
            )
            return result

        if not self._ensure_facebook_home("瀏覽／按讚"):
            _log.warning("[Browse] 未能進入 Facebook 首頁，跳過本次瀏覽／按讚。")
            result.duration_sec = time.time() - total_start_time
            return result

        try:
            self._ctrl.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)  # type: ignore[union-attr]
            self._ctrl.run_js("window.scrollTo(0,0);")
            random_sleep(0.8, 1.2)
        except Exception:
            pass

        browse_start = time.time()
        no_post_rounds = 0
        no_move_rounds = 0

        # V3.2：只解除焦點並送 ESC，完全不點擊頁面任何位置。
        self._interactor._dismiss_notification_permission_before_scroll()

        while time.time() - browse_start < target_duration:
            try:
                current_scroll = int(self._ctrl.get_scroll_position())
            except Exception:
                current_scroll = 0
            max_scroll = max(
                0,
                int(getattr(self._browse_cfg, "max_scroll_position", 3000)),
            )
            if current_scroll >= max_scroll:
                _log.info(
                    "目前滑動位置已達 %s px 上限，停止本 Profile 的瀏覽／按讚。",
                    max_scroll,
                )
                break

            moved = self._scroll_engine.natural_scroll(
                fast=result.liked_count < like_target
            )
            if not moved:
                _log.info("[Permission] 滑動未移動，安全解除焦點並送 ESC 後重試。")
                self._interactor._dismiss_notification_permission_before_scroll()
                moved = self._scroll_engine.natural_scroll(
                    fast=result.liked_count < like_target
                )

            if moved:
                no_move_rounds = 0
            else:
                no_move_rounds += 1
                _log.info("頁面無法繼續下滑（%d/2）。", no_move_rounds)
                if no_move_rounds >= 2:
                    _log.info("已滑到底部，立即結束本 Profile。")
                    break

            posts = self._interactor.get_visible_posts()
            if not posts:
                no_post_rounds += 1
                if no_post_rounds >= 8:
                    _log.info("連續多輪找不到貼文，結束本 Profile。")
                    break
                continue

            no_post_rounds = 0

            if result.liked_count < like_target:
                # 每輪使用剛重新抓到的元素；最多試 3 篇，避免 stale 舊元素拖慢。
                for post in posts[:3]:
                    if self._interactor.try_like(post):
                        result.liked_count += 1
                        result.actions.append("like")
                        _log.info(
                            "按讚進度：%d/%d。",
                            result.liked_count,
                            like_target,
                        )
                        break

            if result.liked_count >= like_target:
                _log.info("按讚已達目標，立即結束本 Profile 的瀏覽流程。")
                break

            random_sleep(1.0, 1.8)

        result.duration_sec = time.time() - total_start_time
        _log.info(
            "養號流程完成：總耗時 %.0f 秒，發文=%s，按讚=%d/%d。",
            result.duration_sec,
            "成功" if "post" in result.actions else "失敗或跳過",
            result.liked_count,
            like_target,
        )
        return result
