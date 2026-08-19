from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait


@dataclass
class ReplyResult:
    success: bool
    method: str = ""
    detail: str = ""


_REPLY_WORDS = (
    "reply", "respond", "comment", "回覆", "回复", "留言", "tumugon", "komento",
    "balas", "ตอบ", "رد", "تعليق", "responder", "répondre", "rispondi",
)


def _set_windows_unicode_clipboard(text: str) -> None:
    if not hasattr(ctypes, "windll"):
        raise RuntimeError("目前作業系統不支援 Windows Unicode 剪貼簿")
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    encoded = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not handle:
        raise RuntimeError("GlobalAlloc 失敗")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise RuntimeError("GlobalLock 失敗")
    try:
        ctypes.memmove(pointer, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise RuntimeError("OpenClipboard 失敗")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise RuntimeError("SetClipboardData 失敗")
        handle = None
    finally:
        user32.CloseClipboard()
    if handle:
        kernel32.GlobalFree(handle)


def _click_reply_trigger(driver) -> bool:
    """Open a nested reply editor when Facebook only shows a Reply button."""
    script = r"""
    const visible = e => {
      const r=e.getBoundingClientRect(), s=getComputedStyle(e);
      return r.width>5 && r.height>5 && r.bottom>0 && r.top<innerHeight &&
             s.visibility!=='hidden' && s.display!=='none';
    };
    const words = ['reply','respond','回覆','回复','tumugon','balas','ตอบ','رد','responder','répondre','rispondi'];
    const nodes=[...document.querySelectorAll('[role="button"],button,a[role="link"],span[role="button"]')]
      .filter(visible)
      .map(e=>({e,t:((e.innerText||e.textContent||e.getAttribute('aria-label')||'').trim().toLowerCase()),r:e.getBoundingClientRect()}))
      .filter(x=>words.some(w=>x.t===w || x.t.startsWith(w+' ')))
      .sort((a,b)=>b.r.top-a.r.top);
    if(!nodes.length) return false;
    nodes[0].e.scrollIntoView({block:'center'});
    nodes[0].e.click();
    return true;
    """
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def _candidate_script() -> str:
    return r"""
    const visible = e => {
      const r=e.getBoundingClientRect(), s=getComputedStyle(e);
      return r.width>8 && r.height>8 && r.bottom>0 && r.top<innerHeight &&
             s.visibility!=='hidden' && s.display!=='none' && s.opacity!=='0';
    };
    const isEditor = e => e && e.matches && e.matches(
      'div[contenteditable="true"], [role="textbox"][contenteditable="true"], [data-lexical-editor="true"], textarea'
    );
    // A reply notification normally opens and focuses the exact nested editor.
    // Preserve that target instead of scanning down to the post comment box.
    const hint = e => ((e.getAttribute('aria-label')||'')+' '+
      (e.getAttribute('aria-placeholder')||'')+' '+
      (e.getAttribute('placeholder')||'')+' '+
      (e.getAttribute('data-placeholder')||'')).toLowerCase();
    const replyWords=/reply|respond|write an answer|回覆|回复|tumugon|sagot|balas|ตอบ|رد|responder|répondre|rispondi/;
    const publicCommentWords=/public comment|write a comment|發表留言|发表评论|komento/;
    const active=document.activeElement;
    if(isEditor(active) && visible(active)) {
      const activeHint=hint(active);
      if(replyWords.test(activeHint) || !publicCommentWords.test(activeHint)) return active;
    }

    const fields=[...document.querySelectorAll(
      'div[contenteditable="true"], [role="textbox"][contenteditable="true"], [data-lexical-editor="true"], textarea'
    )].filter(visible);
    const scored=fields.map((e,i)=>{
      const a=hint(e);
      const r=e.getBoundingClientRect();
      let score=0;
      if(e.getAttribute('data-lexical-editor')==='true') score+=8;
      if(e.getAttribute('role')==='textbox') score+=5;
      if(e.getAttribute('contenteditable')==='true') score+=4;
      const isReply=replyWords.test(a);
      const isTopLevelComment=publicCommentWords.test(a) && !isReply;
      if(isReply) score+=100;
      if(isTopLevelComment) score-=100;
      if(/search|搜尋|搜索/.test(a)) score-=30;
      const dialog=e.closest('[role="dialog"]');
      if(dialog) score+=4;
      const form=e.closest('form');
      if(form) score+=2;
      return {i,score,top:r.top,label:a,isReply};
    }).filter(x=>x.isReply).sort((a,b)=>b.score-a.score);
    return scored.length ? fields[scored[0].i] : null;
    """


def _find_editor(driver, timeout: float = 8):
    return WebDriverWait(driver, timeout, poll_frequency=0.30).until(
        lambda d: d.execute_script(_candidate_script())
    )


def _read_editor_text(driver, editor) -> str:
    try:
        return str(driver.execute_script(
            """
            const e=arguments[0];
            return ((typeof e.value==='string' ? e.value : '') || e.innerText || e.textContent || '').trim();
            """,
            editor,
        ) or "")
    except StaleElementReferenceException:
        return ""


def _matches(actual: str, expected: str) -> bool:
    expected_text = " ".join(expected.split()).casefold()
    actual_text = " ".join((actual or "").split()).casefold()
    return bool(expected_text) and actual_text == expected_text


def _clear_editor(driver, editor) -> bool:
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].focus();", editor)
    ActionChains(driver).move_to_element(editor).click().perform()
    for _ in range(3):
        try:
            ActionChains(driver).move_to_element(editor).click().key_down(
                Keys.CONTROL
            ).send_keys("a").key_up(Keys.CONTROL).send_keys(
                Keys.BACKSPACE
            ).perform()
            time.sleep(0.25)
            if not _read_editor_text(driver, editor):
                return True
        except Exception:
            break
    driver.execute_script(
        r"""
        const e=arguments[0];
        e.focus();
        const selection=window.getSelection(), range=document.createRange();
        range.selectNodeContents(e);
        selection.removeAllRanges(); selection.addRange(range);
        try{document.execCommand('delete',false);}catch(_){}
        if(typeof e.value==='string') e.value='';
        e.dispatchEvent(new InputEvent('input',{
          bubbles:true,inputType:'deleteContentBackward',data:null
        }));
        """,
        editor,
    )
    time.sleep(0.3)
    return not bool(_read_editor_text(driver, editor))


def _insert_with_send_keys(driver, editor, text: str) -> bool:
    """Clear once, type a one-line reply in one call, then read it back."""
    if not _clear_editor(driver, editor):
        raise RuntimeError("無法清空回覆框，停止輸入以避免內容重複")
    editor.send_keys(text)
    time.sleep(0.7)
    return _matches(_read_editor_text(driver, editor), text)


def _insert_multiline_once(driver, editor, text: str) -> bool:
    """Type message, line breaks and account once without re-clicking the caret.

    Facebook submits on Enter, so every requested newline is Shift+Enter.
    Re-clicking the editor between lines can move the caret into the first line;
    keeping the same focused editor prevents the account from landing inside the
    message.
    """
    if not _clear_editor(driver, editor):
        raise RuntimeError("無法清空回覆框，停止輸入以避免內容重複")
    lines = text.split("\n")
    if lines[0]:
        editor.send_keys(lines[0])
    for line in lines[1:]:
        ActionChains(driver).key_down(Keys.SHIFT).send_keys(
            Keys.ENTER
        ).key_up(Keys.SHIFT).perform()
        if line:
            editor.send_keys(line)
        time.sleep(0.08)
    time.sleep(0.8)
    return _matches(_read_editor_text(driver, editor), text)


def _detail_text(text: str, limit: int = 180) -> str:
    compact = (text or "").replace("\r", "").replace("\n", "\\n")
    return compact[:limit] if compact else "<空白>"


def _paste_with_clipboard(driver, editor, text: str) -> bool:
    _set_windows_unicode_clipboard(text)
    if not _clear_editor(driver, editor):
        raise RuntimeError("無法清空回覆框，停止貼上以避免內容重複")
    ActionChains(driver).move_to_element(editor).click().key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
    time.sleep(1.0)
    return _matches(_read_editor_text(driver, editor), text)


def _insert_with_javascript(driver, editor, text: str) -> bool:
    if not _clear_editor(driver, editor):
        raise RuntimeError("無法清空回覆框，停止 JavaScript 備援")
    actual = driver.execute_script(
        r"""
        const e=arguments[0], text=arguments[1];
        e.scrollIntoView({block:'center'}); e.focus();
        if(e.tagName==='TEXTAREA' || e.tagName==='INPUT'){
          const setter=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e),'value')?.set;
          if(setter) setter.call(e,text); else e.value=text;
          e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:text}));
          e.dispatchEvent(new Event('change',{bubbles:true}));
          return e.value || '';
        }
        const sel=window.getSelection(), range=document.createRange();
        range.selectNodeContents(e); range.deleteContents(); range.collapse(true);
        sel.removeAllRanges(); sel.addRange(range);
        let success=false;
        try{success=document.execCommand('insertText',false,text);}catch(_){}
        if(!success || !(e.innerText||e.textContent||'').trim()){
          e.textContent='';
          const p=document.createElement('p'); p.textContent=text; e.appendChild(p);
          e.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,inputType:'insertText',data:text}));
          e.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:text}));
          e.dispatchEvent(new Event('change',{bubbles:true}));
        }
        return (e.innerText||e.textContent||'').trim();
        """,
        editor,
        text,
    )
    return _matches(str(actual or ""), text)


def _page_contains_reply(driver, text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    try:
        body = " ".join((driver.find_element(By.TAG_NAME, "body").text or "").split()).lower()
        return normalized in body
    except Exception:
        return False


def reply_to_customer(driver, text: str, timeout: float = 12) -> ReplyResult:
    clean = (text or "").strip()
    if not clean:
        return ReplyResult(False, detail="回覆文字為空白")

    try:
        editor = _find_editor(driver, timeout)
    except Exception as exc:
        return ReplyResult(False, detail=f"找不到可用留言／回覆框：{exc}")

    attempts = []
    method = ""
    inserted = False

    # A multiline customer reply gets exactly one keyboard attempt. It never
    # falls through to clipboard or JavaScript because a second insertion path
    # can append duplicate text after Facebook re-renders its Lexical editor.
    if "\n" in clean:
        try:
            inserted = _insert_multiline_once(driver, editor, clean)
            attempts.append(
                "keyboard_multiline_once"
                if inserted
                else "keyboard_multiline_once_failed"
            )
            method = "keyboard_multiline_once"
        except Exception as exc:
            attempts.append(
                f"keyboard_multiline_once_error:{type(exc).__name__}:{exc}"
            )
            inserted = False
        if not inserted:
            actual = _read_editor_text(driver, editor)
            return ReplyResult(
                False,
                method or ",".join(attempts),
                "單次鍵盤輸入後驗證失敗，已停止且不再重試；"
                f"欄位內容={_detail_text(actual)}；嘗試={','.join(attempts)}",
            )
    # V6.4 sequential typing remains the preferred path for one-line replies.
    elif all(ord(ch) <= 0xFFFF for ch in clean):
        try:
            inserted = _insert_with_send_keys(driver, editor, clean)
            attempts.append("send_keys" if inserted else "send_keys_failed")
            method = "send_keys"
        except Exception as exc:
            attempts.append(f"send_keys_error:{type(exc).__name__}")
            inserted = False

    if not inserted and "\n" not in clean:
        try:
            inserted = _paste_with_clipboard(driver, editor, clean)
            attempts.append("clipboard" if inserted else "clipboard_failed")
            method = "clipboard"
        except Exception as exc:
            attempts.append(f"clipboard_error:{type(exc).__name__}")
            inserted = False

    if not inserted:
        try:
            inserted = _insert_with_javascript(driver, editor, clean)
            attempts.append("javascript" if inserted else "javascript_failed")
            method = "javascript"
        except Exception as exc:
            attempts.append(f"javascript_error:{type(exc).__name__}")
            inserted = False

    if not inserted:
        return ReplyResult(False, ",".join(attempts), "留言框內未驗證到回覆文字")

    # V6.4 submission rule: keep the same editor, press Enter, and confirm that
    # its content was cleared or changed. Never search for another page field.
    try:
        before_text = _read_editor_text(driver, editor)
        if not _matches(before_text, clean):
            return ReplyResult(False, method, "送出前最終驗證失敗，未按 Enter")
        driver.execute_script("arguments[0].focus();", editor)
        ActionChains(driver).send_keys(Keys.ENTER).perform()
    except Exception as exc:
        return ReplyResult(False, method, f"按 Enter 送出失敗：{exc}")

    time.sleep(1.2)
    try:
        after_text = _read_editor_text(driver, editor)
        submitted = not after_text or after_text != before_text
    except StaleElementReferenceException:
        submitted = True
    except Exception:
        submitted = False
    if submitted:
        return ReplyResult(True, method, f"已送出並完成同欄位驗證；嘗試={','.join(attempts)}")
    return ReplyResult(False, method, "已按 Enter，但同一回覆框內容未改變")
