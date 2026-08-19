"""Facebook/Messenger 五語文字、語意與 DOM 定位共用層。"""

from __future__ import annotations

from dataclasses import dataclass
import time
from urllib.parse import urlparse

from selenium.webdriver.common.by import By


LANGUAGE_WORDS = {
    "message": (
        "訊息", "發送訊息", "傳送訊息", "message", "send message", "messages",
        "messenger", "i-message", "magpadala ng mensahe", "mensahe", "ข้อความ", "ส่งข้อความ",
        "رسالة", "إرسال رسالة", "الرسائل",
    ),
    "search": ("搜尋", "search", "maghanap", "ค้นหา", "بحث"),
    "unread": ("未讀", "unread", "hindi pa nababasa", "ยังไม่ได้อ่าน", "غير مقروء"),
    "write": (
        "撰寫訊息", "輸入訊息", "寫訊息", "输入消息", "写消息",
        "write a message", "type a message", "message",
        "sumulat ng mensahe", "mag-type ng mensahe",
        "écrire un message", "envoyer un message",
        "escribe un mensaje", "escribir un mensaje",
        "escreva uma mensagem", "digite uma mensagem",
        "nhập tin nhắn", "viết tin nhắn",
        "เขียนข้อความ", "พิมพ์ข้อความ",
        "اكتب رسالة", "اكتب رسالةً",
        "tulis pesan", "ketik pesan", "tulis mesej", "taip mesej",
        "nachricht schreiben", "nachricht eingeben",
        "scrivi un messaggio", "digita un messaggio",
        "メッセージを入力", "メッセージを書く",
        "메시지 입력", "메시지 작성",
        "написать сообщение", "введите сообщение",
        "संदेश लिखें", "संदेश टाइप करें",
        "mesaj yaz", "bir mesaj yaz",
    ),
    "send": ("傳送", "發送", "send", "ipadala", "ส่ง", "إرسال"),
    "close": (
        "關閉", "关闭", "close", "dismiss", "isara", "fermer", "cerrar",
        "fechar", "đóng", "ปิด", "إغلاق", "tutup", "schließen",
        "chiudi", "閉じる", "닫기", "закрыть", "बंद करें", "kapat",
    ),
    "back": ("返回", "back", "bumalik", "กลับ", "رجوع"),
    "retry": ("重試", "retry", "subukan muli", "ลองอีกครั้ง", "إعادة المحاولة"),
}

ACCOUNT_RESTRICTIONS = (
    "確認身分", "confirm your identity", "kumpirmahin ang iyong pagkakakilanlan",
    "ยืนยันตัวตน", "تأكيد هويتك", "你暫時無法傳送訊息",
    "temporarily restricted", "hindi ka makapagpadala ng mensahe",
    "ไม่สามารถส่งข้อความ", "لا يمكنك إرسال رسائل",
    "due to suspicious activity", "restricted from sending messages",
    "vous ne pouvez pas envoyer de messages", "no puedes enviar mensajes",
    "não podes enviar mensagens", "không thể gửi tin nhắn",
    "tidak dapat mengirim pesan", "tidak dapat menghantar mesej",
    "du kannst keine nachrichten senden", "non puoi inviare messaggi",
    "メッセージを送信できません", "메시지를 보낼 수 없습니다",
    "не можете отправлять сообщения", "संदेश नहीं भेज सकते",
    "mesaj gönderemezsin",
)

CHAT_IDENTITY_RESTRICTIONS = (
    "confirm your identity to send messages",
    "確認身分才能傳送訊息", "確認你的身分才能傳送訊息",
    "确认身份才能发送消息", "确认你的身份才能发送消息",
    "kumpirmahin ang iyong pagkakakilanlan upang magpadala ng mga mensahe",
    "confirmez votre identité pour envoyer des messages",
    "ยืนยันตัวตนของคุณเพื่อส่งข้อความ",
    "قم بتأكيد هويتك لإرسال الرسائل",
)

CHAT_MUTED_PREFIX = "聊天室禁言"

CHAT_RESTRICTIONS = (
    "message request limit", "you've reached the message request limit",
    "you have reached the message request limit",
    "there's a limit to how many requests you can send",
    "there is a limit to how many requests you can send",
    "number of message invitations you can send in 24 hours is limited",
    "24 小時內可傳送", "24小時內可傳送", "陌生訊息邀請上限",
    "陌生訊息數量已達到上限", "消息请求数量已达上限", "消息邀请上限",
    "limitasyon sa kahilingan sa mensahe", "limitasyon sa imbitasyon sa mensahe",
    "naabot mo na ang limitasyon", "คำขอข้อความ", "ถึงขีดจำกัดคำขอข้อความ",
    "حد طلبات الرسائل", "لقد وصلت إلى حد طلبات الرسائل",
    "you cannot message this account",
    "can't access this chat yet", "cannot access this chat yet",
    "無法傳送訊息給此帳號", "hindi ma-access ang chat",
    "ไม่สามารถส่งข้อความถึงบัญชีนี้", "لا يمكنك مراسلة هذا الحساب",
    "it may help to add them as a friend", "may help to add them as a friend",
    "可能有助於加為朋友", "maaaring makatulong na idagdag",
    "maaaring makatulong kung idagdag",
    "limite d’invitations par message", "limite d'invitations par message",
    "24 heures est limité", "límite de solicitudes de mensajes",
    "límite de invitaciones por mensaje", "limite de solicitações de mensagem",
    "limite de convites por mensagem", "giới hạn yêu cầu tin nhắn",
    "batas permintaan pesan", "had permintaan mesej",
    "limit für nachrichtenanfragen", "limite di richieste di messaggi",
    "メッセージリクエストの上限", "메시지 요청 한도",
    "лимит запросов на переписку", "संदेश अनुरोध की सीमा",
    "mesaj isteği sınırı",
)

COMMENT_INPUT_WORDS = (
    "留言", "發表留言", "发表评论", "写评论", "comment", "write a comment",
    "add a comment", "reply", "magkomento", "sumulat ng komento",
    "commenter", "écrire un commentaire", "comentario", "escribe un comentario",
    "comentário", "escreva um comentário", "bình luận", "viết bình luận",
    "ความคิดเห็น", "เขียนความคิดเห็น", "تعليق", "اكتب تعليقًا",
    "komentar", "tulis komentar", "komen", "tulis komen",
    "kommentar", "kommentar schreiben", "commento", "scrivi un commento",
    "コメント", "コメントを入力", "댓글", "댓글을 입력",
    "комментарий", "написать комментарий", "टिप्पणी", "टिप्पणी लिखें",
    "yorum", "yorum yaz",
)


def normalize(value: str) -> str:
    return " ".join((value or "").lower().split())


def contains_any(value: str, words) -> bool:
    text = normalize(value)
    return any(normalize(word) in text for word in words)


def has_chat_identity_restriction(page_text: str) -> bool:
    return contains_any(page_text, CHAT_IDENTITY_RESTRICTIONS)


def chat_muted_profile_name(profile_name: str, profile_id: str = "") -> str:
    original = (profile_name or "").strip() or (profile_id or "").strip()
    if not original:
        raise RuntimeError("無法建立聊天室禁言環境名稱：名稱與環境 ID 都是空白")
    return (
        original
        if original.startswith(CHAT_MUTED_PREFIX)
        else f"{CHAT_MUTED_PREFIX}{original}"
    )


def suppress_messenger_restore_prompts(driver) -> dict:
    """Hide Messenger restore/PIN dialogs and their white veil without clicks.

    This is intentionally page-local: it does not choose See options, Close,
    Don't restore messages, or any PIN action. Facebook may recreate the
    prompt after navigation, so callers may safely invoke it during wait loops.
    """
    try:
        payload = driver.execute_script(
            r"""
            const clean=v=>(v||'').replace(/\s+/g,' ').trim().toLowerCase();
            const isRestore=text=>(
              (text.includes('chat history can')&&
               text.includes('restored on this device')) ||
              text.includes('continue without restoring') ||
              text.includes('enter your pin to restore your chats') ||
              text.includes('restore your chat history') ||
              text.includes('restore your chat') ||
              text.includes('forgot pin')
            );
            let hiddenDialogs=0,hiddenVeils=0;
            const targets=[...document.querySelectorAll(
              '[data-codex-restore-hidden="1"],'
              +'[role="dialog"],[aria-modal="true"]'
            )].filter(el=>el.dataset.codexRestoreHidden==='1'||
              isRestore(clean(el.innerText||el.textContent)));
            for(const target of targets){
              target.dataset.codexRestoreHidden='1';
              const style=getComputedStyle(target);
              if(style.display!=='none'||style.pointerEvents!=='none'){
                hiddenDialogs++;
              }
              target.style.setProperty('display','none','important');
              target.style.setProperty('pointer-events','none','important');
            }
            const hadRestore=targets.length>0||
              !!document.querySelector('[data-codex-restore-hidden="1"]');
            if(hadRestore){
              for(const veil of document.querySelectorAll(
                '[data-codex-restore-veil="1"]'
              )){
                veil.style.setProperty('display','none','important');
                veil.style.setProperty('pointer-events','none','important');
              }
              const points=[
                [innerWidth/2,innerHeight/2],
                [innerWidth*.25,innerHeight*.25],
                [innerWidth*.75,innerHeight*.75]
              ];
              const candidates=new Set();
              for(const [x,y] of points){
                for(const node of document.elementsFromPoint(x,y)){
                  candidates.add(node);
                }
              }
              for(const node of candidates){
                if(node===document.body||node===document.documentElement)continue;
                const rect=node.getBoundingClientRect(),style=getComputedStyle(node);
                const text=clean(node.innerText||node.textContent);
                const coversViewport=rect.width>=innerWidth*.8&&
                  rect.height>=innerHeight*.8;
                const coloredVeil=style.backgroundColor!=='rgba(0, 0, 0, 0)'&&
                  style.backgroundColor!=='transparent';
                if(style.position==='fixed'&&coversViewport&&coloredVeil&&!text){
                  node.dataset.codexRestoreVeil='1';
                  node.style.setProperty('display','none','important');
                  node.style.setProperty('pointer-events','none','important');
                  hiddenVeils++;
                }
              }
              document.documentElement.style.setProperty(
                'overflow','auto','important'
              );
              document.body.style.setProperty('overflow','auto','important');
            }
            const visible=el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);
              return r.width>5&&r.height>5&&r.bottom>0&&r.top<innerHeight&&
                s.display!=='none'&&s.visibility!=='hidden'&&
                Number(s.opacity||1)>0;};
            const visibleRestore=[...document.querySelectorAll(
              '[role="dialog"],[aria-modal="true"]'
            )].filter(visible).filter(el=>
              isRestore(clean(el.innerText||el.textContent))
            ).length;
            return {hiddenDialogs,hiddenVeils,visibleRestore};
            """
        ) or {}
    except Exception:
        return {
            "hidden_dialogs": 0,
            "hidden_veils": 0,
            "visible_restore": 0,
        }
    return {
        "hidden_dialogs": int(payload.get("hiddenDialogs") or 0),
        "hidden_veils": int(payload.get("hiddenVeils") or 0),
        "visible_restore": int(payload.get("visibleRestore") or 0),
    }


def restriction_scope(page_text: str) -> str:
    if contains_any(page_text, ACCOUNT_RESTRICTIONS):
        return "account"
    if contains_any(page_text, CHAT_RESTRICTIONS):
        return "chat"
    return ""


def chat_id_from_url(url: str, fallback: str = "") -> str:
    try:
        parts = [part for part in urlparse(url).path.split("/") if part]
        if "t" in parts:
            return parts[parts.index("t") + 1]
        if parts and parts[0] in {"messages", "messaging"}:
            return parts[-1]
    except Exception:
        pass
    return fallback


def is_chat_url(url: str) -> bool:
    """只接受真正的 Messenger 對話網址，包含一般及 E2EE 聊天室。"""
    try:
        path = urlparse(url or "").path.rstrip("/")
    except Exception:
        return False
    return bool(
        path.startswith("/messages/t/")
        or path.startswith("/messages/e2ee/t/")
        or path.startswith("/messaging/thread/")
    )


def visible(elements):
    result = []
    for element in elements:
        try:
            rect = element.rect
            if element.is_displayed() and rect.get("width", 0) > 2 and rect.get("height", 0) > 2:
                result.append(element)
        except Exception:
            continue
    return result


def find_active_messenger_container(driver, target_name: str = ""):
    """找右側 Messenger 小視窗；不依賴單一介面語言。"""
    script = r"""
    const vw = window.innerWidth || document.documentElement.clientWidth;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const nodes = Array.from(document.querySelectorAll(
      '[role="dialog"], [aria-label*="Messenger" i], [aria-label*="Chat" i], ' +
      'div:has(> div div[contenteditable="true"][role="textbox"])'
    ));
    const target = String(arguments[0] || '').replace(/\s+/g,' ').trim().toLowerCase();
    const found = [];
    for (const el of nodes) {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) continue;
      if (r.width < 220 || r.height < 160 || r.right < vw * 0.60 || r.top >= vh) continue;
      if (el.closest('[role="banner"], header')) continue;
      const editors = el.querySelectorAll(
        'div[contenteditable="true"][role="textbox"], ' +
        'div[contenteditable="true"][data-lexical-editor="true"], textarea'
      ).length;
      const content = [
        el.innerText || '', el.getAttribute('aria-label') || '',
        ...Array.from(el.querySelectorAll('[aria-label],[aria-placeholder]'))
          .map(x => (x.getAttribute('aria-label') || '') + ' ' +
                    (x.getAttribute('aria-placeholder') || ''))
      ].join(' ').replace(/\s+/g,' ').toLowerCase();
      const targetMatch = target && content.includes(target);
      const score = (targetMatch ? 10000 : 0) +
                    (el.getAttribute('role') === 'dialog' ? 400 : 0) +
                    editors * 300 + r.left + Math.min(r.height, 700);
      found.push({el, score, area:r.width*r.height});
    }
    found.sort((a,b) => b.score-a.score || a.area-b.area);
    return found.length ? found[0].el : null;
    """
    try:
        return driver.execute_script(script, target_name)
    except Exception:
        return None


def find_message_input(driver, container=None):
    """只回傳 Messenger 聊天輸入欄，排除搜尋、貼文留言與其他表單。"""
    container = container or find_active_messenger_container(driver)
    selectors = [
        (By.CSS_SELECTOR, "div[contenteditable='true'][role='textbox']"),
        (By.CSS_SELECTOR, "div[contenteditable='true'][data-lexical-editor='true']"),
        (By.CSS_SELECTOR, "textarea[aria-label]"),
    ]

    def collect(root):
        found = []
        for by, selector in selectors:
            try:
                found.extend(visible(root.find_elements(by, selector)))
            except Exception:
                continue
        return found

    def acceptable(element, *, spatial_fallback: bool) -> bool:
        try:
            label = " ".join([
                element.get_attribute("aria-label") or "",
                element.get_attribute("placeholder") or "",
                element.get_attribute("aria-placeholder") or "",
            ])
            if contains_any(label, LANGUAGE_WORDS["search"]):
                return False
            if contains_any(label, COMMENT_INPUT_WORDS):
                return False
            # 多行粉專文案會讓 Messenger composer 自動長高。已經位於
            # 明確 Messenger 容器內時不可用 160px 上限排除；只有全頁
            # 空間備援仍限制極端高度，避免誤抓大型貼文／留言編輯器。
            if spatial_fallback and element.rect.get("height", 0) > 320:
                return False
            if spatial_fallback:
                # 容器辨識偶爾會因 Facebook 改版漏掉。備援時只接受畫面
                # 右下方、具 Messenger Lexical editor 結構的輸入欄。
                # 貼文留言框即使文字是未知語言，也不會同時滿足這些條件。
                rect = element.rect
                viewport = driver.execute_script(
                    "return {w: window.innerWidth, h: window.innerHeight};"
                )
                aria_placeholder = normalize(
                    element.get_attribute("aria-placeholder") or ""
                )
                aria_label = normalize(element.get_attribute("aria-label") or "")
                lexical = normalize(
                    element.get_attribute("data-lexical-editor") or ""
                ) == "true"
                messenger_label = (
                    aria_placeholder == "aa"
                    or aria_label.startswith("write to ")
                    or contains_any(aria_label, LANGUAGE_WORDS["write"])
                )
                if not lexical or not messenger_label:
                    return False
                if rect.get("x", 0) + rect.get("width", 0) < viewport["w"] * 0.62:
                    return False
                if rect.get("y", 0) < viewport["h"] * 0.50:
                    return False
            return True
        except Exception:
            return False

    # 第一層：沿用聊天容器內定位，未知語言仍可靠 DOM 結構通過。
    if container is not None:
        for element in reversed(collect(container)):
            if acceptable(element, spatial_fallback=False):
                return element

    # 第二層：診斷 698／624 顯示真正的 Write to…／Aa 輸入欄存在，
    # 但 Facebook 新版外層不一定被辨識為 Messenger 容器。
    for element in reversed(collect(driver)):
        if acceptable(element, spatial_fallback=True):
            return element
    return None


def find_chat_items(driver):
    """從 Messenger 左側導覽區取得真正的聊天室項目。"""
    selectors = [
        "div[role='navigation'] a[href*='/messages/e2ee/t/']",
        "div[role='navigation'] a[href*='/messages/t/']",
        "div[role='navigation'] a[href*='/messaging/thread/']",
        "a[href*='/messages/e2ee/t/']",
        "a[href*='/messages/t/']",
        "a[href*='/messaging/thread/']",
    ]
    found = []
    seen = set()
    for selector in selectors:
        for element in visible(driver.find_elements(By.CSS_SELECTOR, selector)):
            href = element.get_attribute("href") or ""
            if not is_chat_url(href):
                continue
            key = chat_id_from_url(href) or href or element.id
            if key not in seen:
                seen.add(key)
                found.append(element)
    return found


def wait_for_chat_items(driver, timeout: float = 20.0):
    """等待 Facebook 動態建立左側聊天室清單。"""
    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        try:
            suppress_messenger_restore_prompts(driver)
            items = find_chat_items(driver)
            if items:
                return items
        except Exception:
            pass
        time.sleep(0.4)
    return []


def click_chat_item(driver, chat_url: str, timeout: float = 8.0) -> bool:
    """從左側清單點入指定聊天室，避免 driver.get 造成整頁重新載入。"""
    expected_id = chat_id_from_url(chat_url)
    deadline = time.monotonic() + max(1.0, timeout)
    while time.monotonic() < deadline:
        try:
            suppress_messenger_restore_prompts(driver)
            for element in find_chat_items(driver):
                href = element.get_attribute("href") or ""
                item_id = chat_id_from_url(href)
                if not (
                    expected_id and item_id == expected_id
                    or href and href == chat_url
                ):
                    continue
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'nearest'});", element
                )
                try:
                    element.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", element)
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def chat_item_name(element, href: str = "") -> str:
    """安全取得聊天室名稱；空白圖示連結不會再觸發 IndexError。"""
    candidates = []
    try:
        candidates.extend((element.text or "").splitlines())
    except Exception:
        pass
    for attr in ("aria-label", "title"):
        try:
            candidates.extend((element.get_attribute(attr) or "").splitlines())
        except Exception:
            pass
    ignored = {
        "messenger", "messages", "message", "chat", "chats",
        "訊息", "消息", "聊天室", "mensahe",
    }
    for value in candidates:
        value = " ".join((value or "").split()).strip()
        if value and normalize(value) not in ignored:
            return value[:120]
    chat_id = chat_id_from_url(href)
    return f"聊天室 {chat_id}" if chat_id else "未命名聊天室"


def wait_for_conversation(driver, expected_chat_id: str = "", timeout: float = 20.0):
    """等待指定中央聊天室真正完成切換並穩定載入。

    Messenger 是 SPA：點左側第二個聊天室時，網址通常會先改變，中央訊息
    DOM 則仍短暫保留上一個聊天室。舊版只要看到任何訊息或輸入框就返回，
    因而可能把第一個聊天室的最後訊息誤當成第二個聊天室。現在要求：

    1. 網址已是 expected_chat_id；
    2. 網址切換後至少留出短暫的 DOM 更新時間；
    3. 同一份中央訊息快照連續穩定兩次，才視為載入完成。
    """
    deadline = time.monotonic() + max(1.0, timeout)
    last_snapshot = ("", "unknown", False)
    matched_since = None
    stable_snapshot = None
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            suppress_messenger_restore_prompts(driver)
            current_id = chat_id_from_url(driver.current_url)
            if expected_chat_id and current_id and current_id != expected_chat_id:
                matched_since = None
                stable_snapshot = None
                stable_count = 0
                time.sleep(0.25)
                continue
            if expected_chat_id and not current_id:
                matched_since = None
                stable_snapshot = None
                stable_count = 0
                time.sleep(0.25)
                continue
            if matched_since is None:
                matched_since = time.monotonic()
            # Facebook 常先更新 URL，再於下一個 render tick 替換訊息列。
            # 這段緩衝可避免第二筆仍讀到第一筆的殘留 DOM。
            if time.monotonic() - matched_since < 0.8:
                time.sleep(0.2)
                continue
            input_box = find_message_input(driver)
            last_snapshot = read_conversation(driver)
            snapshot_key = (
                normalize(last_snapshot[0]),
                last_snapshot[1],
                bool(last_snapshot[2]),
            )
            if snapshot_key == stable_snapshot:
                stable_count += 1
            else:
                stable_snapshot = snapshot_key
                stable_count = 1
            if stable_count >= 2 and (input_box is not None or last_snapshot[0]):
                return last_snapshot, input_box
        except Exception:
            pass
        time.sleep(0.35)
    return last_snapshot, None


def read_conversation(driver) -> tuple[str, str, bool]:
    """只回傳中央聊天室最後一個可確認方向的有效訊息泡泡。

    方向判定採安全三態：incoming／outgoing／unknown。沒有明確 DOM、
    顏色或左右位置證據時絕不預設為對方傳入。
    """
    # 2026-07-25 Ctrl+S 實頁證明，新版 E2EE Messenger 已提供穩定的
    # data-scope=messages_table、aria-roledescription=message 與
    # data-message-id。優先直接讀取「訊息列」，不要先從內層 dir=auto
    # 反推祖先；後者會因 Facebook 改變訊息列寬度而把真實泡泡濾掉。
    try:
        semantic_row = driver.execute_script(
            r"""
        const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
        const low = s => norm(s).toLowerCase();
        const visible = e => {
          if (!e) return false;
          const r=e.getBoundingClientRect(), s=getComputedStyle(e);
          return r.width>1 && r.height>1 && r.bottom>0 && r.right>0 &&
                 r.top<(window.innerHeight||document.documentElement.clientHeight) &&
                 r.left<(window.innerWidth||document.documentElement.clientWidth) &&
                 s.display!=='none' && s.visibility!=='hidden' &&
                 Number(s.opacity||1)>0;
        };
        let rows = [...document.querySelectorAll(
          '[data-scope="messages_table"][aria-roledescription="message"],' +
          '[data-scope="messages_table"][data-message-id],' +
          '[aria-roledescription="message"][data-message-id]'
        )].filter(visible);
        if (!rows.length) return null;

        // SPA 切換聊天室期間，上一個聊天室的訊息列可能仍留在 DOM。
        // 只採用目前畫面主要中央對話區中的訊息列，排除左側清單、右下
        // 小視窗，以及已移出中央區但仍被 Facebook 保留的舊節點。
        const vw = window.innerWidth || document.documentElement.clientWidth;
        const vh = window.innerHeight || document.documentElement.clientHeight;
        const centralRows = rows.filter(e => {
          const r=e.getBoundingClientRect();
          const center=r.left+r.width/2;
          return r.width >= Math.min(360, vw*.30) &&
                 center >= vw*.38 &&
                 r.top >= 35 && r.bottom <= vh+8;
        });
        if (centralRows.length) rows = centralRows;

        const outgoingWords = [
          'message sent by you','sent by you','you sent','you:',
          'message envoyé par vous','envoyé par vous',
          'mensaje enviado por ti','enviado por ti',
          'mensagem enviada por você','enviado por você',
          'ipinadala mo','ikaw ang nagpadala','คุณส่ง','أرسلت أنت','أنت:','أنتِ:',
          '你傳送','你发送','你：','由你傳送','由你发送',
          'von dir gesendet','inviato da te','あなたが送信',
          '회원님이 보냄','отправлено вами','आपने भेजा','sen gönderdin'
        ];
        const systemWords = [
          'end-to-end encrypted','messages and calls are secured',
          'messages and calls are end-to-end encrypted','端對端加密','端到端加密',
          'encrypted from one device to another','secured with end-to-end'
        ];

        const row = rows[rows.length-1];
        const labels = [
          row.getAttribute('aria-label') || '',
          ...[...row.querySelectorAll('[aria-label]')]
            .map(e => e.getAttribute('aria-label') || '')
        ];
        const meta = low(labels.join(' | '));

        // 訊息本體在實頁中可能是 div/span/p[dir=auto]。先收集最內層
        // 可見文字並去重；若 Facebook 再改內層標籤，退回整列 innerText。
        const candidates = [];
        for (const e of row.querySelectorAll('[dir="auto"],[data-ad-comet-preview="message"]')) {
          if (!visible(e)) continue;
          const t = norm(e.innerText || e.textContent);
          if (!t || t.length > 5000 || !/[\p{L}\p{N}@]/u.test(t)) continue;
          const childSame = [...e.querySelectorAll('[dir="auto"]')].some(
            c => visible(c) && norm(c.innerText || c.textContent) === t
          );
          if (childSame) continue;
          if (!candidates.includes(t)) candidates.push(t);
        }
        candidates.sort((a,b) => b.length-a.length);
        let text = candidates.length ? candidates[0] :
                   norm(row.innerText || row.textContent);
        if (!text || systemWords.some(x => low(text).includes(x))) {
          return {text:'', direction:'unknown', unread:false, reason:'system-or-empty'};
        }

        let direction = 'unknown', reason = 'semantic-row';
        if (outgoingWords.some(x => meta.includes(x))) {
          direction = 'outgoing';
          reason = 'semantic-outgoing-label';
        } else {
          // 所有泡泡（包含自己送出）都可能帶有 "Message sent ... by
          // <姓名>"，所以不能只憑 sent 字樣判定為對方。先以訊息列內
          // 的實際左右位置判斷，避免再次把右側藍色泡泡誤加入待回覆。
          const rr=row.getBoundingClientRect();
          const textNode=[...row.querySelectorAll('[dir="auto"]')]
            .filter(e => visible(e) && norm(e.innerText || e.textContent) === text)
            .pop() || row;
          const tr=textNode.getBoundingClientRect();
          const center=tr.left+tr.width/2;
          if (center > rr.left+rr.width*.58) {
            direction='outgoing'; reason='semantic-right-aligned';
          } else if (center < rr.left+rr.width*.50) {
            direction='incoming'; reason='semantic-left-aligned';
          }
        }
        const unread = /unread|未讀|hindi pa nababasa|ยังไม่ได้อ่าน|غير مقروء/i.test(meta);
        return {text, direction, unread, reason};
            """
        )
    except Exception:
        semantic_row = None
    if semantic_row:
        semantic_text = (semantic_row.get("text") or "").strip()
        semantic_direction = semantic_row.get("direction", "unknown")
        if semantic_text:
            return (
                semantic_text,
                semantic_direction,
                bool(semantic_row.get("unread", False)),
            )

    # Messenger 的 E2EE／群組聊天室不一定有 role=main，訊息文字也可能是
    # span 或 p。先廣泛取得文字葉節點，再由下方 JS 以中央輸入框、面板位置、
    # 訊息列與泡泡證據嚴格過濾，避免把左側清單或標題誤當成訊息。
    selectors = (
        "div[role='main'] [dir='auto'], "
        "[data-scope='messages_table'] [dir='auto'], "
        "[aria-label*='Messages' i] [dir='auto'], "
        "[aria-label*='訊息'] [dir='auto'], "
        "div[dir='auto'], span[dir='auto'], p[dir='auto']"
    )
    message_nodes = visible(driver.find_elements(By.CSS_SELECTOR, selectors))
    system_words = (
        "end-to-end encrypted", "messages and calls are secured",
        "messages and calls are end-to-end encrypted",
        "端對端加密", "端到端加密", "e2ee", "loading", "載入中",
        "seen", "已讀", "delivered", "已送達", "active", "在線上",
        "type a message", "write a message", "輸入訊息", "撰寫訊息",
        "encrypted from one device to another", "secured with end-to-end",
        "mga mensahe at tawag ay secure", "การเข้ารหัสจากต้นทางถึงปลายทาง",
        "مشفرة تمامًا بين الطرفين",
    )
    for node in reversed(message_nodes):
        try:
            text = (node.text or "").strip()
            if not text or len(text) > 5000:
                continue
            if contains_any(text, system_words) and len(text) < 160:
                continue
            # 排除標題、導覽、搜尋結果及輸入區；只保留中央訊息列。
            if driver.execute_script(
                """
                const e=arguments[0];
                return !!e.closest(
                  'header,[role="banner"],[role="navigation"],[role="search"],' +
                  '[contenteditable="true"],form'
                );
                """,
                node,
            ):
                continue
            evidence = driver.execute_script(
                r"""
                const leaf = arguments[0];
                const norm = s => String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
                const visible = e => {
                  if (!e) return false;
                  const r=e.getBoundingClientRect(), s=getComputedStyle(e);
                  return r.width>1 && r.height>1 && s.display!=='none' &&
                         s.visibility!=='hidden' && Number(s.opacity||1)>0;
                };
                const outgoingWords = [
                  'sent by you','you sent','you:','你傳送','你发送','你：',
                  'ipinadala mo','ikaw ang nagpadala','คุณส่ง','أرسلت أنت','أنت:','أنتِ:',
                  'envoyé par vous','enviado por ti','enviado por você',
                  'von dir gesendet','inviato da te','あなたが送信',
                  '회원님이 보냄','отправлено вами','आपने भेजा','sen gönderdin'
                ];
                const incomingWords = [
                  'sent by ','傳送給你','发送给你','ipinadala sa iyo',
                  'ส่งถึงคุณ','أرسل إليك','envoyé par','enviado por',
                  'gesendet von','inviato da','から送信','님이 보냄',
                  'отправлено пользователем','ने भेजा'
                ];
                // 找到目前中央聊天室的輸入框，再由其祖先推定聊天面板範圍。
                const editors = [...document.querySelectorAll(
                  '[contenteditable="true"][role="textbox"],' +
                  '[contenteditable="true"][data-lexical-editor="true"],' +
                  'textarea'
                )].filter(visible);
                let composer = editors.find(e => {
                  const a=norm(
                    (e.getAttribute('aria-label')||'')+' '+
                    (e.getAttribute('data-placeholder')||'')+' '+
                    (e.getAttribute('placeholder')||'')
                  );
                  return /message|訊息|消息|mensahe|chat|reply|回覆/.test(a);
                }) || editors[editors.length-1] || null;
                let panel = null, cur = composer;
                for (let i=0; cur && i<12; i++,cur=cur.parentElement) {
                  const r=cur.getBoundingClientRect();
                  if (r.width >= window.innerWidth*.45 &&
                      r.height >= window.innerHeight*.48) {
                    panel=cur;
                    break;
                  }
                }
                const pr = panel ? panel.getBoundingClientRect() : {
                  left: Math.max(260, window.innerWidth*.18),
                  right: window.innerWidth,
                  top: 45,
                  bottom: window.innerHeight,
                  width: window.innerWidth-Math.max(260, window.innerWidth*.18),
                  height: window.innerHeight-45
                };
                const lr=leaf.getBoundingClientRect();
                if (!visible(leaf) || lr.right < pr.left || lr.left > pr.right ||
                    lr.bottom < pr.top || lr.top > pr.bottom) {
                  return {valid:false,direction:'unknown',reason:'outside-chat-panel'};
                }
                if (composer) {
                  const cr=composer.getBoundingClientRect();
                  if (lr.top >= cr.top-4) {
                    return {valid:false,direction:'unknown',reason:'composer-area'};
                  }
                }

                // 避免父子 [dir=auto] 同時代表同一則訊息；只保留最內層文字節點。
                const nested=[...leaf.querySelectorAll('[dir="auto"]')].find(e =>
                  visible(e) && norm(e.innerText||e.textContent)===norm(leaf.innerText||leaf.textContent)
                );
                if (nested) {
                  return {valid:false,direction:'unknown',reason:'duplicate-parent-node'};
                }

                let chain = [], row = null, bubble = leaf;
                cur = leaf;
                for (let i=0; cur && i<12; i++, cur=cur.parentElement) {
                  chain.push(
                    (cur.getAttribute('aria-label') || '') + ' ' +
                    (cur.getAttribute('data-testid') || '') + ' ' +
                    (cur.getAttribute('data-scope') || '')
                  );
                  const r = cur.getBoundingClientRect();
                  const style = getComputedStyle(cur);
                  const bg = style.backgroundColor || '';
                  const colored = bg && bg !== 'rgba(0, 0, 0, 0)' &&
                                  bg !== 'transparent' && bg !== 'rgb(255, 255, 255)';
                  if (colored && r.width < (pr.right-pr.left)*.78) bubble = cur;
                  if (r.width > Math.max(360, (pr.right-pr.left)*.68) &&
                      r.height < 520 && r.left >= pr.left-12 && r.right <= pr.right+12) {
                    row = cur;
                    break;
                  }
                }
                const meta = norm(chain.join(' '));
                if (outgoingWords.some(x => meta.includes(norm(x)))) {
                  return {valid:true,direction:'outgoing', reason:'aria'};
                }
                if (incomingWords.some(x => meta.includes(norm(x))) {
                  return {valid:true,direction:'incoming', reason:'aria'};
                }

                // Messenger 自己送出的泡泡通常使用 Facebook 藍色。
                cur = leaf;
                for (let i=0; cur && i<7; i++, cur=cur.parentElement) {
                  const bg = getComputedStyle(cur).backgroundColor || '';
                  const nums = (bg.match(/\d+/g) || []).map(Number);
                  if (nums.length >= 3) {
                    const [r,g,b] = nums;
                    if (b >= 175 && b > r * 1.35 && b > g * 1.15) {
                      return {valid:true,direction:'outgoing', reason:'blue-bubble'};
                    }
                  }
                }

                // 最後才採用訊息列內的左右位置；靠中或列結構不明即 unknown。
                const br = bubble.getBoundingClientRect();
                const rr = row ? row.getBoundingClientRect() : pr;
                if (rr && rr.width >= 360 && br.width > 8) {
                  const bubbleCenter = br.left + br.width / 2;
                  const rowCenter = rr.left + rr.width / 2;
                  const margin = Math.max(30, rr.width * .055);
                  if (bubbleCenter >= rowCenter + margin) {
                    return {valid:true,direction:'outgoing', reason:'right-aligned'};
                  }
                  if (bubbleCenter <= rowCenter - margin) {
                    return {valid:true,direction:'incoming', reason:'left-aligned'};
                  }
                }
                return {valid:true,direction:'unknown', reason:'insufficient-evidence'};
                """,
                node,
            ) or {}
            if not evidence.get("valid", True):
                continue
            direction = evidence.get("direction", "unknown")
            unread = contains_any(
                " ".join([
                    node.get_attribute("aria-label") or "",
                    node.get_attribute("data-testid") or "",
                ]),
                LANGUAGE_WORDS["unread"],
            )
            return text, direction, unread
        except Exception:
            continue
    return "", "unknown", False


def wait_for_sent_message(
    driver, reply_text: str, expected_chat_id: str = "", timeout: float = 20.0
) -> bool:
    """送出後只在同一中央聊天室確認；等待期間絕不再次輸入或按 Enter。"""
    deadline = time.monotonic() + max(2.0, timeout)
    wanted = normalize(reply_text)
    empty_since = None
    while time.monotonic() < deadline:
        try:
            suppress_messenger_restore_prompts(driver)
            current_id = chat_id_from_url(driver.current_url)
            if expected_chat_id and current_id and current_id != expected_chat_id:
                time.sleep(0.3)
                continue
            scope = restriction_scope(driver.find_element(By.TAG_NAME, "body").text)
            if scope:
                return False
            text, direction, _ = read_conversation(driver)
            if direction == "outgoing" and wanted[:120] in normalize(text):
                return True
            input_box = find_message_input(driver)
            if input_box is not None:
                value = normalize(
                    input_box.get_attribute("textContent")
                    or input_box.get_attribute("innerText")
                    or ""
                )
                if not value:
                    empty_since = empty_since or time.monotonic()
                    if time.monotonic() - empty_since >= 2.0:
                        return True
                else:
                    empty_since = None
        except Exception:
            pass
        time.sleep(0.4)
    return False


@dataclass(frozen=True)
class ChatSnapshot:
    chat_id: str
    chat_name: str
    chat_url: str
    message_text: str
    direction: str
    is_unread: bool
