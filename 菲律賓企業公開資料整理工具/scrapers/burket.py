from models import Company
from bs4 import BeautifulSoup
from .base import BaseScraper, SiteStructureChangedError
from 工具 import absolute_url
from 瀏覽器 import rendered_html


class BurketScraper(BaseScraper):
    name="Burket"; base_url="https://burket.ph"
    def search(self, keyword="", category="All", city="", max_results=0):
        url=self.base_url+"/category/construction-industrial/suppliers"
        try:
            soup=BeautifulSoup(rendered_html(url,2500),"lxml")
        except Exception as exc:
            raise ConnectionError(f"Burket 瀏覽器載入失敗：{exc}") from exc
        items=[]
        for a in soup.select("a[href]"):
            heading=a.select_one("h1,h2,h3,[class*='company-name']")
            label=(heading.get_text(" ",strip=True) if heading else a.get_text(" ",strip=True)); href=a.get("href","")
            if label and "/company/" in href.lower():
                full=absolute_url(url,href)
                if full: items.append((label,full))
        if not items:
            # Current industry page also exposes directory names as headings/list items without links.
            for node in soup.select("h2, h3, li"):
                label=node.get_text(" ",strip=True)
                if 2<len(label)<120 and not label.lower().startswith(("construction","sign up","request")):
                    items.append((label,url))
        items=list(dict.fromkeys(items))
        if not items: raise SiteStructureChangedError("Burket 網頁結構可能已變更")
        count=0
        for name,detail in items:
            if max_results and count>=max_results:return
            try:
                ds=BeautifulSoup(rendered_html(detail,1200),"lxml") if detail!=url else soup
                c=Company(name,category=category,sources=[self.name],source_urls=[detail],email_source_type="Business Directory")
                if detail!=url:
                    heading=self.first_text(ds,["h1","[itemprop=name]",".company-name"])
                    c.company_name=name if heading.lower() in ("server error","not found") else (heading or name)
                self.enrich_contacts(c,ds,detail)
                # Footer/support addresses belong to the marketplace, not the supplier.
                c.emails=[email for email in c.emails if not email.endswith("@burket.ph")]
                yield c; count+=1
            except Exception as exc:self.log(f"{name} 失敗：{exc}")
