from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256

from notification_patterns import classify_detail


@dataclass
class NotificationCandidate:
    text: str
    url: str
    unread: bool
    kind: str
    language: str = "unknown"
    matched_pattern: str = ""
    key: str = ""
    accepted: bool = False
    skip_reason: str = ""
    section: str = "unknown"
    occurrence: int = 1

    def to_dict(self):
        return asdict(self)


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def make_key(profile_id: str, text: str, url: str, section: str = "", occurrence: int = 1) -> str:
    payload = "\n".join([profile_id, url or "", _norm(text), section or "", str(occurrence)])
    return sha256(payload.encode("utf-8")).hexdigest()


def collect_all_candidates(driver, profile_id: str) -> list[NotificationCandidate]:
    js = r"""
    const visible = e => {
      const r = e.getBoundingClientRect();
      const s = getComputedStyle(e);
      return r.width > 8 && r.height > 8 && s.display !== 'none' && s.visibility !== 'hidden';
    };
    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
    const headingWords = {
      new: ['new','新的通知','新通知','baru','bago','nuevo','nuevas','nouveau','ใหม่','الجديدة','ใหม่ล่าสุด'],
      earlier: ['earlier','較早','更早','稍早','sebelumnya','nakaraan','anteriores','plus tôt','ก่อนหน้านี้','السابق']
    };
    const heads = [...document.querySelectorAll('h1,h2,h3,h4,[role="heading"],span,div')]
      .filter(visible)
      .map(e => ({e, t: clean(e.innerText).toLowerCase(), y: e.getBoundingClientRect().top}))
      .filter(x => x.t.length > 0 && x.t.length < 40 && (
        headingWords.new.includes(x.t) || headingWords.earlier.includes(x.t)
      ));
    const sectionFor = node => {
      const y = node.getBoundingClientRect().top;
      let best = null;
      for (const h of heads) if (h.y <= y + 2 && (!best || h.y > best.y)) best = h;
      if (!best) return 'unknown';
      if (headingWords.new.includes(best.t)) return 'new';
      if (headingWords.earlier.includes(best.t)) return 'earlier';
      return 'unknown';
    };
    const scoreLink = a => {
      const href = a.href || '';
      const txt = clean(a.innerText || a.getAttribute('aria-label') || '');
      let score = 0;
      if (/notif|notification|comment_id|reply_comment_id|story_fbid|permalink|posts\/|reel\//i.test(href)) score += 20;
      if (/comment|reply|mention/i.test(href)) score += 10;
      if (txt.length > 20) score += 5;
      if (/profile\.php|\/people\/|\/photo\/?|\/photos\//i.test(href)) score -= 15;
      const img = a.querySelector('img');
      if (img && txt.length < 8) score -= 10;
      return score;
    };
    const roots = [...document.querySelectorAll('[role="listitem"], [role="article"]')].filter(visible);
    const source = roots.length ? roots : [...document.querySelectorAll('a[href]')].filter(visible);
    const out = [];
    for (const node of source) {
      const anchors = node.matches('a[href]') ? [node] : [...node.querySelectorAll('a[href]')];
      if (!anchors.length) continue;
      const ranked = anchors
        .filter(a => (a.href || '').includes('facebook.com'))
        .map(a => ({a, score: scoreLink(a)}))
        .sort((x,y) => y.score - x.score);
      if (!ranked.length) continue;
      const link = ranked[0].a;
      const href = link.href || '';
      const text = clean(node.innerText || link.innerText || link.getAttribute('aria-label') || '');
      if (!text || text.length < 8) continue;
      const aria = clean(node.getAttribute('aria-label')).toLowerCase();
      const unreadTerms = [
        'unread', '未讀', '未读', 'hindi pa nababasa', 'belum dibaca',
        'ยังไม่ได้อ่าน', 'غير مقروءة'
      ];
      const markReadTerms = [
        'mark as read', '標示為已讀', '标记为已读', '設為已讀', '设为已读',
        'markahan bilang nabasa', 'minarkahan bilang nabasa',
        'tandai sebagai sudah dibaca',
        'ทำเครื่องหมายว่าอ่านแล้ว', 'وضع علامة كمقروءة'
      ];
      const markUnreadTerms = [
        'mark as unread', '標示為未讀', '标记为未读', '設為未讀', '设为未读',
        'markahan bilang hindi pa nababasa', 'minarkahan bilang hindi pa nababasa',
        'tandai sebagai belum dibaca',
        'ทำเครื่องหมายว่ายังไม่ได้อ่าน', 'وضع علامة كغير مقروءة'
      ];
      const labels = [node, ...node.querySelectorAll('[aria-label],[title],[role="button"]')]
        .map(el => clean(
          (el.getAttribute('aria-label') || '') + ' ' +
          (el.getAttribute('title') || '')
        ).toLowerCase())
        .filter(Boolean);
      const explicitlyRead = labels.some(label =>
        markUnreadTerms.some(term => label.includes(term))
      );
      const explicitlyUnread = !explicitlyRead && (
        labels.some(label =>
          markReadTerms.some(term => label.includes(term)) ||
          unreadTerms.some(term => label === term || label.startsWith(term + ' '))
        ) ||
        unreadTerms.some(term => aria === term || aria.startsWith(term + ' ')) ||
        /^unread\b/i.test(text)
      );
      const unread = explicitlyUnread;
      out.push({text, url: href, unread, section: sectionFor(node)});
    }
    return out;
    """
    raws = driver.execute_script(js) or []
    counts: dict[tuple[str, str], int] = {}
    result: list[NotificationCandidate] = []
    for raw in raws:
        text = raw.get("text", "")
        section = raw.get("section", "unknown") or "unknown"
        sig = (_norm(text), section)
        counts[sig] = counts.get(sig, 0) + 1
        occurrence = counts[sig]
        detail = classify_detail(text)
        c = NotificationCandidate(
            text=text,
            url=raw.get("url", ""),
            unread=bool(raw.get("unread")),
            kind=detail["kind"],
            language=detail["language"],
            matched_pattern=detail["matched_pattern"],
            section=section,
            occurrence=occurrence,
        )
        c.key = make_key(profile_id, c.text, c.url, c.section, c.occurrence)
        result.append(c)
    return result


def select_candidates(
    candidates: list[NotificationCandidate],
    *,
    process_replies: bool,
    process_mentions: bool,
    only_unread: bool,
    new_section_only: bool = True,
) -> list[NotificationCandidate]:
    accepted_kinds = set()
    if process_replies:
        accepted_kinds.add("comment_reply")
    if process_mentions:
        accepted_kinds.add("comment_mention")
    selected: list[NotificationCandidate] = []
    for c in candidates:
        if new_section_only and c.section == "earlier":
            c.skip_reason = "earlier_section"
        elif only_unread and not c.unread:
            c.skip_reason = "not_unread"
        elif c.kind not in accepted_kinds:
            c.skip_reason = f"kind_not_enabled:{c.kind}"
        else:
            c.accepted = True
            selected.append(c)
    return selected


def collect_candidates(driver, profile_id, only_unread=False, process_replies=True, process_mentions=True, new_section_only=True):
    all_candidates = collect_all_candidates(driver, profile_id)
    return select_candidates(
        all_candidates,
        process_replies=process_replies,
        process_mentions=process_mentions,
        only_unread=only_unread,
        new_section_only=new_section_only,
    )


def click_candidate(
    driver,
    candidate: NotificationCandidate,
    require_unread: bool = False,
) -> tuple[bool, str]:
    """重新定位通知；嚴格模式會在實際點擊前再次確認仍為未讀。"""
    js = r"""
    const wantedText = arguments[0], wantedSection = arguments[1], wantedOccurrence = arguments[2], requireUnread = arguments[3];
    const clean = s => (s || '').replace(/\s+/g, ' ').trim();
    const visible = e => { const r=e.getBoundingClientRect(), s=getComputedStyle(e); return r.width>8&&r.height>8&&s.display!=='none'&&s.visibility!=='hidden'; };
    const unreadTerms = [
      'unread', '未讀', '未读', 'hindi pa nababasa', 'belum dibaca',
      'ยังไม่ได้อ่าน', 'غير مقروءة'
    ];
    const markReadTerms = [
      'mark as read', '標示為已讀', '标记为已读', '設為已讀', '设为已读',
      'markahan bilang nabasa', 'minarkahan bilang nabasa',
      'tandai sebagai sudah dibaca',
      'ทำเครื่องหมายว่าอ่านแล้ว', 'وضع علامة كمقروءة'
    ];
    const markUnreadTerms = [
      'mark as unread', '標示為未讀', '标记为未读', '設為未讀', '设为未读',
      'markahan bilang hindi pa nababasa', 'minarkahan bilang hindi pa nababasa',
      'tandai sebagai belum dibaca',
      'ทำเครื่องหมายว่ายังไม่ได้อ่าน', 'وضع علامة كغير مقروءة'
    ];
    const isUnread = node => {
      const aria = clean(node.getAttribute('aria-label')).toLowerCase();
      const labels = [node, ...node.querySelectorAll('[aria-label],[title],[role="button"]')]
        .map(el => clean(
          (el.getAttribute('aria-label') || '') + ' ' +
          (el.getAttribute('title') || '')
        ).toLowerCase())
        .filter(Boolean);
      if (labels.some(label => markUnreadTerms.some(term => label.includes(term)))) return false;
      if (labels.some(label => markReadTerms.some(term => label.includes(term)))) return true;
      if (labels.some(label => unreadTerms.some(term => label === term || label.startsWith(term + ' ')))) return true;
      if (unreadTerms.some(term => aria === term || aria.startsWith(term + ' '))) return true;
      return /^unread\b/i.test(clean(node.innerText || ''));
    };
    const headingWords = {
      new: ['new','新的通知','新通知','baru','bago','nuevo','nuevas','nouveau','ใหม่','الجديدة','ใหม่ล่าสุด'],
      earlier: ['earlier','較早','更早','稍早','sebelumnya','nakaraan','anteriores','plus tôt','ก่อนหน้านี้','السابق']
    };
    const heads=[...document.querySelectorAll('h1,h2,h3,h4,[role="heading"],span,div')].filter(visible)
      .map(e=>({t:clean(e.innerText).toLowerCase(),y:e.getBoundingClientRect().top}))
      .filter(x=>x.t.length>0&&x.t.length<40&&(headingWords.new.includes(x.t)||headingWords.earlier.includes(x.t)));
    const sectionFor=node=>{const y=node.getBoundingClientRect().top;let best=null;for(const h of heads)if(h.y<=y+2&&(!best||h.y>best.y))best=h;if(!best)return'unknown';if(headingWords.new.includes(best.t))return'new';if(headingWords.earlier.includes(best.t))return'earlier';return'unknown';};
    const scoreLink = a => {
      const href=a.href||'', txt=clean(a.innerText||a.getAttribute('aria-label')||''); let score=0;
      if(/notif|notification|comment_id|reply_comment_id|story_fbid|permalink|posts\/|reel\//i.test(href))score+=20;
      if(/comment|reply|mention/i.test(href))score+=10;
      if(txt.length>20)score+=5;
      if(/profile\.php|\/people\/|\/photo\/?|\/photos\//i.test(href))score-=15;
      if(a.querySelector('img')&&txt.length<8)score-=10;
      return score;
    };
    const roots=[...document.querySelectorAll('[role="listitem"], [role="article"]')].filter(visible);
    let count=0;
    for(const node of roots){
      const text=clean(node.innerText||'');
      const section=sectionFor(node);
      if(text!==wantedText || (wantedSection!=='unknown' && section!==wantedSection)) continue;
      count++;
      if(count!==wantedOccurrence) continue;
      if(requireUnread && !isUnread(node)) return {clicked:false, reason:'not_unread_at_click'};
      const anchors=[...node.querySelectorAll('a[href]')].filter(a=>(a.href||'').includes('facebook.com'))
        .map(a=>({a,score:scoreLink(a)})).sort((x,y)=>y.score-x.score);
      const target=anchors.length?anchors[0].a:node;
      target.scrollIntoView({block:'center',inline:'nearest'});
      target.click();
      return {clicked:true, reason:'clicked', href:target.href||'', text, section};
    }
    return {clicked:false, reason:'candidate_not_found'};
    """
    result = driver.execute_script(
        js,
        candidate.text,
        candidate.section,
        int(candidate.occurrence),
        bool(require_unread),
    ) or {}
    return bool(result.get("clicked")), str(result.get("reason") or "unknown")
