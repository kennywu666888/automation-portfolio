from __future__ import annotations

import re
from typing import Any


def natural_sort_key(text: str) -> tuple[tuple[int, Any], ...]:
    parts = re.split(r"(\d+)", str(text or ""))
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in parts
    )


def profile_number_sort_key(profile: object) -> tuple:
    name = str(getattr(profile, "name", profile) or "").strip()
    match = re.search(r"(\d+)\s*$", name)
    if match:
        return (0, int(match.group(1)), natural_sort_key(name))
    return (1, 0, natural_sort_key(name))


def sort_profiles_by_number(profiles) -> list:
    return sorted(list(profiles), key=profile_number_sort_key)


def profile_matches_search(profile: object, keyword: str) -> bool:
    query = str(keyword or "").strip().casefold()
    if not query:
        return True
    fields = (
        "name",
        "group_name",
        "profile_id",
        "serial_number",
        "proxy_ip",
        "remark",
    )
    return any(query in str(getattr(profile, field, "") or "").casefold() for field in fields)
