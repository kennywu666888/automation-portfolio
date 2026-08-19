import re
import time
from bs4 import BeautifulSoup
from models import Company
from 工具 import absolute_url, extract_emails
from 瀏覽器 import check_businesslist_login, cdp_available, rendered_html
from .base import BaseScraper, SiteStructureChangedError
from category_catalog import load_category_catalog


class BusinessListScraper(BaseScraper):
    name="BusinessList"; base_url="https://www.businesslist.ph"

    def login(self, visible=True):
        return check_businesslist_login()

    def _page_soup(self, url, reveal_email=False):
        last=None
        for attempt in range(3):
            try:
                if cdp_available():return BeautifulSoup(rendered_html(url,1200,cdp=True,reveal_email=reveal_email),"lxml")
                return self.soup(self.get(url))
            except Exception as exc:
                last=exc;self.log(f"BusinessList 頁面失敗（{attempt+1}/3）：{url}｜{exc}")
                if attempt<2:time.sleep(2*(attempt+1))
        raise last

    def search(self, keyword="", category="All", city="", max_results=0):
        _,category_urls=load_category_catalog()
        slug=re.sub(r"[^a-z0-9]+","-",(category if category!="All" else keyword or "construction").lower()).strip("-")
        url=category_urls.get(("BusinessList Philippines",category),f"{self.base_url}/category/{slug}") if category!="All" else f"{self.base_url}/companies/{slug}"
        yielded=0;seen_details=set();visited_pages=set();current=url
        while current and current not in visited_pages and not self.stop_event.is_set() and (not max_results or yielded<max_results):
            visited_pages.add(current)
            soup=self._page_soup(current)
            cards=soup.select(".company, .company-item, article, [class*='company']")
            links=[]
            for card in cards:
                a=card.select_one("a[href*='/company/'], a[href*='/biz/'], h2 a, h3 a")
                if a and a.get("href"): links.append((a.get_text(" ",strip=True),absolute_url(current,a["href"])))
            if not links:
                links=[(a.get_text(" ",strip=True),absolute_url(current,a["href"])) for a in soup.select("a[href*='/company/']")]
            # A company link can occur in both desktop/mobile or sponsored markup.
            # Deduplicate by canonical detail URL, not by (label, URL) tuple.
            unique_links={}
            for label, detail_url in links:
                if label and detail_url and detail_url not in unique_links:
                    unique_links[detail_url]=label
            links=[(label,detail_url) for detail_url,label in unique_links.items()]
            links=[(label,detail_url) for label,detail_url in links if detail_url not in seen_details]
            if not links:
                if len(visited_pages)==1: raise SiteStructureChangedError("BusinessList 網頁結構可能已變更")
                break
            seen_details.update(detail_url for _,detail_url in links)
            for name, detail in links:
                if city and city.lower() not in name.lower(): pass
                try:
                    ds=self._page_soup(detail,reveal_email=True); c=Company(name,category=category,sources=[self.name],source_urls=[detail],email_source_type="Business Directory")
                    c.company_name=self.first_text(ds,["h1",".company-name","[itemprop=name]"]) or name
                    c.address=self.first_text(ds,["[itemprop=address]",".address","[class*=address]"])
                    c.description=self.first_text(ds,["[itemprop=description]",".description",".company-description"])
                    marker=ds.select_one("#collector-revealed-emails");revealed=extract_emails(marker.get_text(" ")) if marker else []
                    self.enrich_contacts(c,ds,detail);c.emails=sorted(set(revealed));c.email_source_url=detail if c.emails else "";yield c;yielded+=1
                except Exception as exc: self.log(f"{name} 失敗：{exc}")
                if max_results and yielded>=max_results: return
            next_link=soup.select_one("a[rel='next'][href]")
            if not next_link:
                next_link=next((a for a in soup.select("a[href]") if a.get_text(" ",strip=True).lower() in ("→","next","next page")),None)
            current=absolute_url(current,next_link["href"]) if next_link else None
