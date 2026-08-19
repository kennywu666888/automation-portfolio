from dataclasses import dataclass, field, asdict


@dataclass
class Company:
    company_name: str
    category: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    mobiles: list[str] = field(default_factory=list)
    fax: list[str] = field(default_factory=list)
    address: str = ""
    city: str = ""
    province: str = ""
    website: str = ""
    facebook: str = ""
    contact_person: str = ""
    description: str = ""
    sources: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    email_source_url: str = ""
    email_source_type: str = ""
    license_number: str = ""
    status: str = "完成"

    def to_dict(self):
        return asdict(self)

    @property
    def completeness(self):
        present = [bool(self.emails), bool(self.phones or self.mobiles), bool(self.address),
                   bool(self.website), bool(self.city)]
        return int(sum(present) / len(present) * 100)

