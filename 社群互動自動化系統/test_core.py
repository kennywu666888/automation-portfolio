import json
import tempfile
from pathlib import Path

from notification_parser import NotificationCandidate, make_key, select_candidates
from notification_patterns import classify, classify_detail
from notification_repository import NotificationRepository
from Telegram回報 import TelegramReporter, TelegramTargets

cases = {
    "John replied to your comment": "comment_reply",
    "Najib Koko mentioned you in a comment.": "comment_mention",
    "王小明回覆了你的留言": "comment_reply",
    "王小明在留言中提及你": "comment_mention",
    "Tumugon si Ana sa komento mo": "comment_reply",
    "Binanggit ka ni Ana sa isang komento": "comment_mention",
    "ตอบกลับความคิดเห็นของคุณ": "comment_reply",
    "กล่าวถึงคุณในความคิดเห็น": "comment_mention",
    "قام بالرد على تعليقك": "comment_reply",
    "أشار إليك في تعليق": "comment_mention",
    "Your comment is unavailable in Indonesia. See why.": "system",
    "John accepted your friend request.": "friend",
    "John liked your comment": "reaction",
}
for text, expected in cases.items():
    actual = classify(text)
    assert actual == expected, (text, actual, expected, classify_detail(text))

assert make_key("p", "x", "u") == make_key("p", "x", "u")
items = [
    NotificationCandidate("a", "u1", True, "comment_reply"),
    NotificationCandidate("b", "u2", True, "comment_mention"),
    NotificationCandidate("c", "u3", True, "friend"),
]
selected = select_candidates(
    items,
    process_replies=True,
    process_mentions=True,
    only_unread=False,
)
assert [item.kind for item in selected] == ["comment_reply", "comment_mention"]

with tempfile.TemporaryDirectory() as directory:
    repository = NotificationRepository(Path(directory) / "test.db")
    assert not repository.is_reported("x")

json.loads((Path(__file__).parent / "settings.json").read_text(encoding="utf-8"))

class Reply:
    reply_user = "A"
    reply_text = "B"
    original_comment = "C"
    post_author = "D"
    notification_time = "E"
    facebook_url = "https://facebook.com/x"

reporter = TelegramReporter("", TelegramTargets("1", "2"), False)
assert "Facebook 留言收到" in reporter.format_incoming("P", Reply())
assert "thanks" in reporter.format_replied("P", Reply(), "thanks")
print("ALL OFFLINE CORE TESTS PASSED")
