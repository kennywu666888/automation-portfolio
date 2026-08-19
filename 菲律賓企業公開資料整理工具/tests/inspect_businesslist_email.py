import json
import re
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    page = browser.contexts[0].pages[0]
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(1000)
    body = page.locator("body").inner_text(timeout=10000)
    html = page.content()
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}", body + " " + html)))
    data = page.locator("body").evaluate("""el => Array.from(el.querySelectorAll('a,button,[role=button],[class*=email],[id*=email]'))
      .filter(x => /@|email|show/i.test((x.innerText||'')+' '+(x.getAttribute('href')||'')+' '+(x.getAttribute('class')||'')+' '+(x.getAttribute('id')||'')))
      .slice(0,100)
      .map(x => ({tag:x.tagName, text:(x.innerText||'').trim().slice(0,300),
                  cls:String(x.className||''), id:x.id,
                  href:x.getAttribute('href'), src:x.getAttribute('src'),
                  data:Array.from(x.attributes).filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value])}))""")
    print(json.dumps({"url": page.url, "emails": emails, "candidates": data}, ensure_ascii=True))
