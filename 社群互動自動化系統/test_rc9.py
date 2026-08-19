from pathlib import Path

from 留言回覆讀取 import _extract_message, _notification_actor
from text_sources import build_customer_reply, load_text_lines


assert _notification_actor("John Smith replied to your comment") == "John Smith"
assert _notification_actor("王小明回覆了你的留言") == "王小明"
assert _notification_actor("Ana mentioned you in a comment") == "Ana"
assert _extract_message(
    ["จารุกัญญ์ มูลทรัพย์ · 16h", "Fskb Fuc yes", "Reply", "Share"],
    "จารุกัญญ์ มูลทรัพย์",
) == "Fskb Fuc yes"
assert _extract_message(
    ["จารุกัญญ์ มูลทรัพย์", "·", "16h", "Fskb Fuc yes", "Reply", "Share"],
    "จารุกัญญ์ มูลทรัพย์",
) == "Fskb Fuc yes"
assert _extract_message(
    ["จารุกัญญ์ มูลทรัพย์", "·", "16h", "Reply", "Share"],
    "จารุกัญญ์ มูลทรัพย์",
) == ""

sample = Path(__file__).with_name("回覆文案.example.txt")
lines = load_text_lines(sample)
assert len(lines) == 3
reply = build_customer_reply(sample, "@example")
first, blank, account = reply.splitlines()
assert first in lines
assert blank == ""
assert account == "@example"

print("RC12 tests passed")
