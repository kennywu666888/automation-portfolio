from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ReplyData:
    reply_user: str
    reply_text: str
    original_comment: str
    post_author: str
    notification_time: str
    facebook_url: str
    reader_detail: str = ""


_ACTION_PATTERNS = (
    r"\s+(?:replied|responded|mentioned)\b",
    r"(?:回覆了?|回复了?|回應了?|回应了?|提及了?|標註了?|标注了?)",
    r"\s+(?:tumugon|binanggit|menanggapi|membalas|mencionó|mencionou)\b",
)
_UI_WORDS = {
    "reply",
    "respond",
    "share",
    "like",
    "edited",
    "回覆",
    "回复",
    "分享",
    "讚",
    "赞",
    "tumugon",
    "ibahagi",
}


def _notification_actor(text: str) -> str:
    cleaned = " ".join((text or "").split())
    for pattern in _ACTION_PATTERNS:
        match = re.search(pattern, cleaned, flags=re.I)
        if match:
            return cleaned[: match.start()].strip(" ·:-")
    return ""


def _useful_lines(text: str) -> list[str]:
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    return [line for line in lines if line]


def _looks_like_time_or_ui(line: str) -> bool:
    normalized = line.strip().casefold()
    if not normalized or re.fullmatch(r"[·•‧|:：\-–—…\s]+", normalized):
        return True
    if normalized in _UI_WORDS:
        return True
    if re.fullmatch(
        r"[·•‧|:：\-–—…\s]*(?:\d+\s*)?"
        r"(?:s|m|h|d|w|秒|分鐘|分|小時|天|週)"
        r"[·•‧|:：\-–—…\s]*",
        normalized,
    ):
        return True
    if re.fullmatch(r"\d+\s*(?:s|m|h|d|w)", normalized):
        return True
    return False


def _extract_message(lines: list[str], actor: str) -> str:
    if not lines:
        return ""
    start = 0
    if actor:
        actor_key = actor.casefold()
        for index, line in enumerate(lines):
            if actor_key in line.casefold():
                start = index + 1
                break
    message = []
    for line in lines[start:]:
        if _looks_like_time_or_ui(line):
            if message:
                break
            continue
        if actor and line.casefold() == actor.casefold():
            continue
        message.append(line)
        if len(message) >= 4:
            break
    return "\n".join(message).strip()


def read_reply(driver, notification_text: str) -> ReplyData:
    actor = _notification_actor(notification_text)
    script = r"""
    const actor=(arguments[0]||'').trim().toLowerCase();
    const visible=e=>{
      const r=e.getBoundingClientRect(),s=getComputedStyle(e);
      return r.width>8&&r.height>8&&s.display!=='none'&&
             s.visibility!=='hidden'&&s.opacity!=='0';
    };
    const clean=s=>(s||'').replace(/\s+/g,' ').trim();
    const isNoise=line=>{
      const value=clean(line), lower=value.toLowerCase();
      if(!value||/^[·•‧|:：\-–—…\s]+$/.test(value))return true;
      if(/^[·•‧|:：\-–—…\s]*\d+\s*(s|m|h|d|w)[·•‧|:：\-–—…\s]*$/.test(lower))return true;
      if(/^(reply|respond|share|like|edited|回覆|回复|分享|讚|赞|tumugon|ibahagi)$/.test(lower))return true;
      if(actor){
        if(lower===actor)return true;
        if(lower.startsWith(actor)){
          const rest=clean(lower.slice(actor.length));
          if(!rest||/^[·•‧|:：\-–—…\s]*\d*\s*(s|m|h|d|w)?$/.test(rest))return true;
        }
      }
      return false;
    };
    const editorSelector='div[contenteditable="true"],[role="textbox"][contenteditable="true"],[data-lexical-editor="true"],textarea';
    const hint=e=>clean(
      (e.getAttribute('aria-label')||'')+' '+
      (e.getAttribute('aria-placeholder')||'')+' '+
      (e.getAttribute('placeholder')||'')+' '+
      (e.getAttribute('data-placeholder')||'')
    ).toLowerCase();
    const replyWords=/reply|respond|write an answer|回覆|回复|tumugon|sagot|balas|ตอบ|رد|responder|répondre|rispondi/;
    let editor=document.activeElement;
    if(!(editor&&editor.matches&&editor.matches(editorSelector)&&visible(editor))){
      editor=[...document.querySelectorAll(editorSelector)]
        .filter(e=>visible(e)&&replyWords.test(hint(e)))[0]||null;
    }
    const er=editor?editor.getBoundingClientRect():null;
    const out=[];

    // Strongest path: find the visible node containing the notification actor,
    // then climb only until the smallest ancestor also contains message text.
    if(actor){
      const actorNodes=[...document.querySelectorAll('div[dir="auto"],span,a')]
        .filter(e=>visible(e)&&clean(e.innerText||e.textContent||'').toLowerCase().includes(actor));
      for(const node of actorNodes){
        let current=node;
        for(let depth=0;current&&depth<9;depth++,current=current.parentElement){
          const raw=(current.innerText||current.textContent||'').trim();
          const text=clean(raw);
          const lines=raw.split(/\n/).map(clean).filter(Boolean);
          const usable=lines.filter(line=>!isNoise(line)&&
            !(actor&&line.toLowerCase().includes(actor)));
          if(text.length>1200)break;
          if(lines.length>=2&&usable.length&&text.toLowerCase().includes(actor)){
            const r=current.getBoundingClientRect();
            if(!er||r.top<=er.bottom+80){
              out.push({
                raw,text,
                score:500-Math.min(text.length,1000)/3-depth*2+
                  Math.min(usable.length,3)*5,
                top:r.top,bottom:r.bottom,containsActor:true,
                source:'actor_ancestor',lineCount:lines.length,
                usableCount:usable.length
              });
              break;
            }
          }
        }
      }
    }

    const nodes=[...document.querySelectorAll(
      '[role="article"],[role="listitem"],li,div[dir="auto"]'
    )].filter(visible);
    for(const e of nodes){
      const raw=(e.innerText||e.textContent||'').trim();
      const text=clean(raw);
      if(!text||text.length<2||text.length>1200)continue;
      const r=e.getBoundingClientRect();
      if(er&&r.top>er.bottom+80)continue;
      const containsActor=!!actor&&text.toLowerCase().includes(actor);
      const lineCount=raw.split(/\n/).map(clean).filter(Boolean).length;
      let score=0;
      if(containsActor)score+=120;
      if(containsActor&&lineCount>=2)score+=80;
      if(containsActor&&lineCount<2)score-=120;
      if(e.getAttribute('role')==='article'||e.getAttribute('role')==='listitem')score+=35;
      if(er){
        const distance=Math.abs(er.top-r.bottom);
        score+=Math.max(0,80-Math.min(80,distance/5));
        if(r.bottom<=er.top+80)score+=25;
      }
      score+=Math.max(0,25-text.length/50);
      if(/write a public comment|write a public reply/i.test(text))score-=80;
      out.push({raw,text,score,top:r.top,bottom:r.bottom,containsActor,source:'global',lineCount});
    }
    out.sort((a,b)=>b.score-a.score||a.text.length-b.text.length);
    return {
      actor:arguments[0]||'',
      editorHint:editor?hint(editor):'',
      blocks:out.slice(0,30)
    };
    """
    payload = driver.execute_script(script, actor) or {}
    blocks = payload.get("blocks") or []

    best_lines: list[str] = []
    reply_text = ""
    selected_source = "none"
    if actor:
        for block in blocks:
            if not block.get("containsActor"):
                continue
            candidate_lines = _useful_lines(block.get("raw", ""))
            candidate_text = _extract_message(candidate_lines, actor)
            if candidate_text and candidate_text != notification_text:
                best_lines = candidate_lines
                reply_text = candidate_text
                selected_source = str(block.get("source") or "unknown")
                break
    if not reply_text:
        for block in blocks:
            candidate_lines = _useful_lines(block.get("raw", ""))
            candidate_text = _extract_message(candidate_lines, actor)
            if candidate_text and candidate_text != notification_text:
                best_lines = candidate_lines
                reply_text = candidate_text
                selected_source = str(block.get("source") or "fallback")
                break

    if not reply_text:
        reply_text = "無法讀取"
    reply_user = actor or (best_lines[0] if best_lines else "無法讀取")

    original_comment = "無法讀取"
    if actor and best_lines:
        for line in best_lines:
            if actor.casefold() in line.casefold():
                continue
            if line not in reply_text.splitlines() and not _looks_like_time_or_ui(line):
                original_comment = line
                break

    return ReplyData(
        reply_user=reply_user,
        reply_text=reply_text,
        original_comment=original_comment,
        post_author="無法讀取",
        notification_time="無法讀取",
        facebook_url=driver.current_url,
        reader_detail=(
            f"actor={actor or '-'};source={selected_source};"
            f"editor={payload.get('editorHint') or '-'};"
            f"blocks={len(blocks)}"
        ),
    )
