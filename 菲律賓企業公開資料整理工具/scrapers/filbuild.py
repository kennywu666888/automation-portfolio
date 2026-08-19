import re
from category_catalog import load_category_catalog
from models import Company
from 工具 import absolute_url
from .base import BaseScraper, SiteStructureChangedError


class FilbuildScraper(BaseScraper):
    name="Filbuild"; base_url="https://www.filbuild.com"
    def search(self, keyword="", category="All", city="", max_results=0):
        _,category_urls=load_category_catalog()
        url=category_urls.get(("Filbuild",category),self.base_url+"/b/construction_services.html")
        archive="/a/" in url.lower();visited_pages=set();seen_details=set();count=0
        while url and url not in visited_pages:
            visited_pages.add(url);soup=self.soup(self.get(url));links=[]
            for a in soup.select("a[href]"):
                label=a.get_text(" ",strip=True);href=a.get("href","")
                if label and "/b2b/" in href.lower() and not href.rstrip("/").endswith("/b2b"):
                    full=absolute_url(url,href);name=label.split("~",1)[0].strip()
                    if full and full not in seen_details and (not keyword or keyword.lower() in label.lower()):links.append((name,full));seen_details.add(full)
            if not links and len(visited_pages)==1:raise SiteStructureChangedError("Filbuild 網頁結構可能已變更")
            for name,detail in links:
                if max_results and count>=max_results:return
                try:
                    ds=self.soup(self.get(detail));c=Company(name,category=category,sources=[self.name],source_urls=[detail],email_source_type="Business Directory");c.company_name=name
                    text=ds.get_text(" ",strip=True);address=re.search(r"Address\s*:\s*(.+?)(?=Phone\s*:|Fax\s*:|E-?mail\s*:|$)",text,re.I);phone=re.search(r"Phone\s*:\s*(.+?)(?=Fax\s*:|E-?mail\s*:|$)",text,re.I);fax=re.search(r"Fax\s*:\s*(.+?)(?=E-?mail\s*:|$)",text,re.I)
                    if address:c.address=address.group(1).strip(" |")
                    if phone:c.phones=[x.strip() for x in re.split(r"\s*\|\s*",phone.group(1)) if x.strip()]
                    if fax:c.fax=[fax.group(1).strip(" |")]
                    self.enrich_contacts(c,ds,detail);yield c;count+=1
                except Exception as exc:self.log(f"{name} 失敗：{exc}")
            older=next((a for a in soup.select("a[href]") if "older" in a.get_text(" ",strip=True).lower()),None)
            url=absolute_url(url,older["href"]) if archive and older else None
