from bs4 import BeautifulSoup
from models import Company
from .base import BaseScraper, SiteStructureChangedError
from 瀏覽器 import rendered_html


class GovernmentScraper(BaseScraper):
    name="Government"; base_url="https://pcab.construction.gov.ph"
    # Public PCAB verification/search page; parser supports table-based current and legacy layouts.
    def search(self, keyword="", category="All", city="", max_results=0):
        url="https://pcab.construction.gov.ph/verify/"; count=0; found=False
        try:
            soup=BeautifulSoup(rendered_html(url,4500),"lxml")
        except Exception as exc:
            raise ConnectionError(f"PCAB 動態表格載入失敗：{exc}") from exc
        for row in soup.select("table tr"):
                cells=[x.get_text(" ",strip=True) for x in row.find_all(["th","td"],recursive=False)]
                if len(cells)==7: cells=cells[1:]  # phpGrid includes a hidden database id column.
                if len(cells)!=6 or cells[0].lower() in ("company name","contractor name","name of firm") or cells[0] in ("","×"):continue
                text=" ".join(cells)
                if keyword and keyword.lower() not in text.lower():continue
                c=Company(cells[0],category=cells[3],contact_person=cells[2],
                          license_number=cells[1],description=f"License valid to {cells[4]}; Government projects: {cells[5]}",
                          sources=[self.name],source_urls=[url],email_source_type="Government Directory")
                self.enrich_contacts(c,row,url); yield c; count+=1; found=True
                if max_results and count>=max_results:return
        if not found: raise SiteStructureChangedError("PCAB 公開頁目前未提供可直接解析的結果表；可於瀏覽器使用查驗頁")
