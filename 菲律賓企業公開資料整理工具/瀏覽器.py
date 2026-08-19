"""Browser helpers for sites that require real JavaScript rendering.

BusinessList uses a user-controlled Chrome profile exposed over local CDP.
No password, cookie, or token is read or logged by this module.
"""
import subprocess
import time
import urllib.request
import html as html_module
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CDP_URL = "http://127.0.0.1:9222"


def find_chrome():
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    return next((p for p in candidates if p.exists()), None)


def cdp_available():
    try:
        with urllib.request.urlopen(CDP_URL + "/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def open_businesslist_login():
    """Open a normal visible Chrome profile and return immediately for manual login."""
    chrome = find_chrome()
    if not chrome:
        return False, "找不到 Google Chrome 或 Microsoft Edge"
    if not cdp_available():
        profile = ROOT / "data" / "businesslist_chrome_profile"
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [str(chrome), "--remote-debugging-port=9222", f"--user-data-dir={profile}",
             "--no-first-run", "--no-default-browser-check", "https://www.businesslist.ph/sign-in"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(20):
            if cdp_available():
                break
            time.sleep(.25)
    return (True, "登入瀏覽器已開啟，請自行完成登入後按『我已登入，接管』") if cdp_available() else (False, "Chrome 已啟動，但無法建立本機接管連線")


def check_businesslist_login():
    if not cdp_available():
        return False, "尚未開啟 BusinessList 登入瀏覽器"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            # Authenticated sessions are redirected from /sign-in to /user/<id>.
            # The homepage may still render a generic "Sign in" button, so it is
            # not a reliable authentication signal.
            page.goto("https://www.businesslist.ph/sign-in", wait_until="domcontentloaded", timeout=45000)
            current_url = page.url.lower()
            signed_in = (
                "/user/" in current_url
                or page.locator("a[href*='sign-out'], a[href*='logout']").count() > 0
                or page.get_by_text("Sign out", exact=False).count() > 0
                or page.get_by_text("My Account", exact=False).count() > 0
            )
            if signed_in:
                context.storage_state(path=str(ROOT / "data" / "businesslist_session.json"))
            return signed_in, "登入成功，後續搜尋會接管此瀏覽器" if signed_in else "目前仍顯示未登入；請在開啟的瀏覽器完成登入"
    except Exception as exc:
        return False, f"接管檢查失敗：{type(exc).__name__}: {exc}"


def _reveal_businesslist_emails(page):
    """Click the logged-in SHOW EMAIL link and read its accessible modal text.

    BusinessList renders this modal outside the ordinary queryable DOM. CDP's
    Accessibility tree is used because it represents the same visible text the
    user can see; no cookies, tokens, or hidden API calls are inspected.
    """
    email_re = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,24}", re.I)
    session = page.context.new_cdp_session(page)
    tree = session.send("Accessibility.getFullAXTree")
    # Exclude ordinary page/header emails. The hidden modal can already exist in
    # the AX tree before clicking, so comparing two AX snapshots would wrongly
    # discard the company's revealed address.
    before_emails={value.lower() for value in email_re.findall(page.content())}
    show_node = next((node for node in tree.get("nodes", [])
                      if (node.get("role") or {}).get("value") == "link"
                      and (node.get("name") or {}).get("value", "").strip().upper() == "SHOW EMAIL"
                      and node.get("backendDOMNodeId")), None)
    if not show_node:
        return []
    resolved = session.send("DOM.resolveNode", {"backendNodeId": show_node["backendDOMNodeId"]})
    object_id = (resolved.get("object") or {}).get("objectId")
    if not object_id:
        return []
    session.send("Runtime.callFunctionOn", {
        "objectId": object_id,
        "functionDeclaration": "function(){ this.click(); }",
        "returnByValue": True,
    })
    # The site's modal often keeps its spinner visible for several seconds.
    # Poll the visible accessibility tree instead of assuming 800 ms is enough.
    for _ in range(10):
        page.wait_for_timeout(500)
        revealed = session.send("Accessibility.getFullAXTree")
        values = []
        for node in revealed.get("nodes", []):
            for field in ("name", "value"):
                text = str((node.get(field) or {}).get("value", ""))
                values.extend(email_re.findall(text))
        new_emails={value.lower() for value in values}-before_emails
        if new_emails:
            return sorted(new_emails)
    return []


def rendered_html(url, wait_ms=2500, cdp=False, reveal_email=False):
    """Return rendered HTML from CDP Chrome or an isolated Playwright Chromium."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        if cdp:
            if not cdp_available():
                raise RuntimeError("BusinessList 登入瀏覽器尚未開啟或已關閉")
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(wait_ms)
                revealed_emails = _reveal_businesslist_emails(page) if reveal_email else []
                html = page.content()
                if revealed_emails:
                    marker = '<div id="collector-revealed-emails">' + " ".join(
                        html_module.escape(value) for value in revealed_emails) + "</div>"
                    html = html.replace("</body>", marker + "</body>")
                return html
            finally:
                page.close()
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(wait_ms)
        html = page.content()
        browser.close()
        return html
