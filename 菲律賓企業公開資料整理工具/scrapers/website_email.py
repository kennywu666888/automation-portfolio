from urllib.parse import urlparse
from models import Company
from .base import BaseScraper
from 工具 import absolute_url


class WebsiteEmailScraper(BaseScraper):
    name="Company Website"
    PATHS=("/","/contact","/contact-us","/about","/about-us","/company","/support")
    def enrich(self, company: Company):
        if not company.website:return company
        base=f"{urlparse(company.website).scheme or 'https'}://{urlparse(company.website).netloc}"
        for path in self.PATHS:
            try:
                url=absolute_url(base,path); soup=self.soup(self.get(url)); before=len(company.emails)
                self.enrich_contacts(company,soup,url)
                if len(company.emails)>before:
                    company.email_source_url=url; company.email_source_type="Company Website"
            except Exception as exc:self.log(f"官網補充 {base}{path} 失敗：{exc}")
        return company
