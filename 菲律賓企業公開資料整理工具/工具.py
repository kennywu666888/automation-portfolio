import re
import random
import time
from urllib.parse import urlparse, urljoin

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,24})(?![\w.-])", re.I)
BAD_EMAILS = {"demo@example.com", "demo@example.com", "demo@example.com", "demo@example.com", "demo@example.com"}


def clean_email(value):
    value = value.strip().lower().replace("mailto:", "").split("?")[0]
    if value in BAD_EMAILS or not EMAIL_RE.fullmatch(value):
        return ""
    if any(value.endswith(x) for x in (".png", ".jpg", ".jpeg", ".gif", ".css", ".js")):
        return ""
    return value


def extract_emails(text):
    return sorted({v for x in EMAIL_RE.findall(text or "") if (v := clean_email(x))})


def normalize_name(value):
    value = value.lower().replace("&", " and ")
    value = re.sub(r"\b(corporation|corp|incorporated|inc|company|co|philippines|phils)\b\.?", " ", value)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


def normalize_phone(value):
    digits = re.sub(r"\D", "", value or "")
    return digits if 7 <= len(digits) <= 15 else ""


def valid_http_url(value):
    try:
        p = urlparse(value)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except ValueError:
        return False


def absolute_url(base, href):
    url = urljoin(base, href)
    return url if valid_http_url(url) else ""


def polite_delay(minimum, maximum, stop_event=None):
    end = time.monotonic() + random.uniform(minimum, maximum)
    while time.monotonic() < end:
        if stop_event and stop_event.is_set():
            return
        time.sleep(min(.2, end - time.monotonic()))

