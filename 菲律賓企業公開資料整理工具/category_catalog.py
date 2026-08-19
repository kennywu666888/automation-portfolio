import re
import json
from pathlib import Path
from collections import Counter
import requests
from bs4 import BeautifulSoup


HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
BUSINESSLIST_FALLBACK={"Tradesmen & Construction":["Construction","Construction Equipment","Construction Services","Chemicals","Building Materials","Architectural Services","Plumbing Services","Aluminium Openings","Decorators","Locksmiths","Metals","Roofing","Doors","Plumbers","Windows","Concrete","Excavators","Construction Training","Glass Manufacturing","Fencing and Fence Materials","Exterminating and Disinfecting","Remodeling","Gardeners","Drywall","Stone","Paving","Forestry","Landscaping","Handyman","Lumber"]}
FILBUILD_FALLBACK=["【完整歷史資料】 All Archive Entries","Construction - Commercial","Construction - Industrial","Construction - Residential","Construction Machinery & Equipment","Architects","Consulting - Engineering","Engineering - Civil","Engineering - Electrical","Engineering - Mechanical"]


def _clean(text):
    return re.sub(r"\s+\d[\d,]*\s*$","",text.strip())


def load_category_catalog():
    tree={"BusinessList Philippines":BUSINESSLIST_FALLBACK.copy(),"Filbuild":{"":FILBUILD_FALLBACK[:]}}
    urls={}
    cache=Path(__file__).parent/"data"/"category_catalog.json"
    try:
        saved=json.loads(cache.read_text(encoding="utf-8"));tree=saved["tree"];urls={tuple(key.split("\t",1)):value for key,value in saved["urls"].items()}
    except Exception:
        pass
    try:
        html=requests.get("https://www.businesslist.ph/browse-business-directory",headers=HEADERS,timeout=12).text
        # Use Python's bundled parser here so refreshing the category catalog
        # does not silently fall back to an old cache when lxml is unavailable.
        soup=BeautifulSoup(html,"html.parser");raw_groups={}
        for heading in soup.select("h2.cath2"):
            main=heading.select_one("a[href]");items=heading.find_next_sibling("ul")
            if not main or not items:continue
            major=_clean(main.get_text(" ",strip=True));values=[]
            for link in items.select("a[href]"):
                label=_clean(link.get_text(" ",strip=True))
                href=requests.compat.urljoin("https://www.businesslist.ph",link["href"])
                if label and (label,href) not in values:values.append((label,href))
            if values:raw_groups[major]=values
        if raw_groups:
            counts=Counter(label for values in raw_groups.values() for label,_ in values);groups={}
            for major,values in raw_groups.items():
                groups[major]=[]
                for label,href in values:
                    display=f"{label} 〔{major}〕" if counts[label]>1 else label
                    groups[major].append(display);urls[("BusinessList Philippines",display)]=href
            tree["BusinessList Philippines"]=groups
    except Exception:
        pass
    for major,values in tree.get("BusinessList Philippines",{}).items():
        tree["BusinessList Philippines"][major]=[value for value in values if not value.startswith("【整個大分類】")]
    urls={key:value for key,value in urls.items() if not (key[0]=="BusinessList Philippines" and key[1].startswith("【整個大分類】"))}
    try:
        html=requests.get("https://www.filbuild.com/",headers=HEADERS,timeout=12).text
        soup=BeautifulSoup(html,"html.parser");archive="【完整歷史資料】 All Archive Entries";values=[archive];urls[("Filbuild",archive)]="https://www.filbuild.com/a/0100.html"
        for link in soup.select("a.font19b[href*='/b/']"):
            label=link.get_text(" ",strip=True)
            if label and label not in values:
                values.append(label);urls[("Filbuild",label)]=requests.compat.urljoin("https://www.filbuild.com",link["href"])
        if values:tree["Filbuild"]={"":values}
    except Exception:
        pass
    try:
        cache.parent.mkdir(exist_ok=True);cache.write_text(json.dumps({"tree":tree,"urls":{"\t".join(key):value for key,value in urls.items()}},ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception:
        pass
    return tree,urls
