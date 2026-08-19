import logging
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from 工具 import extract_emails, normalize_phone, polite_delay, absolute_url


class SiteStructureChangedError(RuntimeError):
    pass


class BaseScraper:
    name = "Base"
    base_url = ""

    def __init__(self, minimum=1.5, maximum=3.0, pause_event=None, stop_event=None, log=None):
        self.minimum, self.maximum = minimum, maximum
        self.pause_event = pause_event or threading.Event(); self.pause_event.set()
        self.stop_event = stop_event or threading.Event()
        self.log = log or logging.getLogger(self.name).info
        self.session = requests.Session()
        self.session.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36 PhilippinesCompanyCollector/1.0"})

    def wait(self):
        while not self.pause_event.wait(.2):
            if self.stop_event.is_set(): return False
        polite_delay(self.minimum, self.maximum, self.stop_event)
        return not self.stop_event.is_set()

    def get(self, url, **kwargs):
        last = None
        for attempt in range(4):
            if not self.wait(): raise InterruptedError("使用者停止")
            try:
                response = self.session.get(url, timeout=(10,30), **kwargs)
                if response.status_code in (403,429,503):
                    raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
                response.raise_for_status(); return response
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last=exc
                if attempt == 3: break
                delay=5*(2**attempt); self.log(f"{self.name} 暫時失敗：{exc}，{delay} 秒後重試")
                if self.stop_event.wait(delay): raise InterruptedError("使用者停止")
        raise last

    @staticmethod
    def soup(response):
        try:
            return BeautifulSoup(response.text, "lxml")
        except Exception:
            return BeautifulSoup(response.text, "html.parser")

    @staticmethod
    def first_text(node, selectors):
        for selector in selectors:
            found=node.select_one(selector)
            if found and found.get_text(" ",strip=True): return found.get_text(" ",strip=True)
        return ""

    def enrich_contacts(self, company, soup, url):
        company.emails = sorted(set(company.emails + extract_emails(soup.get_text(" ") + " " + str(soup))))
        for a in soup.select("a[href]"):
            href=a.get("href","")
            if href.startswith("tel:"):
                raw=href[4:].strip()
                if normalize_phone(raw): company.phones.append(raw)
            elif href.startswith("http") and not company.website:
                host=urlparse(href).netloc.lower()
                excluded=("facebook.com","twitter.com","instagram.com","linkedin.com","google.com","googleapis.com",
                          "youtube.com","wa.me","whatsapp.com","t.me",urlparse(url).netloc)
                if not any(x in host for x in excluded):
                    company.website=href
        company.phones=list(dict.fromkeys(company.phones))
        if company.emails and not company.email_source_url: company.email_source_url=url
        return company
