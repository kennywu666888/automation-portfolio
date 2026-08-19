from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

COMMENT_REPLY = "comment_reply"
COMMENT_MENTION = "comment_mention"
POST_MENTION = "post_mention"
REACTION = "reaction"
FRIEND = "friend"
SYSTEM = "system"
OTHER = "other"

@dataclass(frozen=True)
class PatternRule:
    kind: str
    language: str
    patterns: tuple[str, ...]

# Regex patterns are intentionally semantic and language-oriented. Dynamic CSS classes are not used.
RULES: tuple[PatternRule, ...] = (
    # Traditional / Simplified Chinese
    PatternRule(COMMENT_REPLY, "zh", (
        r"回覆了?你的留言", r"回覆了?你的評論", r"回复了?你的留言", r"回复了?你的评论",
        r"回應了?你的留言", r"回应了?你的评论",
    )),
    PatternRule(COMMENT_MENTION, "zh", (
        r"在留言中提及你", r"在評論中提及你", r"在评论中提到了你", r"在留言中標註你", r"在评论中@了你",
    )),
    PatternRule(POST_MENTION, "zh", (r"在貼文中提及你", r"在帖子中提到了你", r"在貼文中標註你")),

    # English
    PatternRule(COMMENT_REPLY, "en", (
        r"replied to your comment", r"replied to a comment", r"replied to your reply",
        r"responded to your comment", r"responded to a comment", r"answered your comment",
    )),
    PatternRule(COMMENT_MENTION, "en", (
        r"mentioned you in a comment", r"mentioned you in the comments", r"tagged you in a comment",
    )),
    PatternRule(POST_MENTION, "en", (r"mentioned you in a post", r"tagged you in a post")),

    # Filipino / Tagalog
    PatternRule(COMMENT_REPLY, "fil", (
        r"tumugon(?:\s+si\s+.+?)?\s+sa komento mo", r"nag-reply(?:\s+si\s+.+?)?\s+sa komento mo", r"sumagot(?:\s+si\s+.+?)?\s+sa komento mo",
        r"tumugon sa iyong komento", r"nagreply sa iyong komento",
    )),
    PatternRule(COMMENT_MENTION, "fil", (
        r"binanggit ka(?:\s+ni\s+.+?)?\s+sa isang komento", r"minention ka(?:\s+ni\s+.+?)?\s+sa isang komento", r"tinag ka(?:\s+ni\s+.+?)?\s+sa isang komento",
    )),
    PatternRule(POST_MENTION, "fil", (r"binanggit ka sa isang post", r"tinag ka sa isang post")),

    # Thai
    PatternRule(COMMENT_REPLY, "th", (
        r"ตอบกลับความคิดเห็นของคุณ", r"ตอบกลับคอมเมนต์ของคุณ", r"ตอบกลับความเห็นของคุณ",
    )),
    PatternRule(COMMENT_MENTION, "th", (
        r"กล่าวถึงคุณในความคิดเห็น", r"แท็กคุณในความคิดเห็น", r"พูดถึงคุณในคอมเมนต์",
    )),
    PatternRule(POST_MENTION, "th", (r"กล่าวถึงคุณในโพสต์", r"แท็กคุณในโพสต์")),

    # Arabic
    PatternRule(COMMENT_REPLY, "ar", (
        r"رد على تعليقك", r"قام بالرد على تعليقك", r"أجاب على تعليقك", r"ردّ على تعليقك",
    )),
    PatternRule(COMMENT_MENTION, "ar", (
        r"أشار إليك في تعليق", r"ذكرك في تعليق", r"قام بالإشارة إليك في تعليق",
    )),
    PatternRule(POST_MENTION, "ar", (r"أشار إليك في منشور", r"ذكرك في منشور")),

    # Indonesian / Malay
    PatternRule(COMMENT_REPLY, "id", (
        r"membalas komentar anda", r"membalas komentar kamu", r"membalas komentar Anda",
        r"menanggapi komentar anda", r"menjawab komentar anda",
    )),
    PatternRule(COMMENT_MENTION, "id", (
        r"menyebut anda dalam komentar", r"menandai anda dalam komentar", r"menyebut kamu dalam komentar",
    )),
    PatternRule(POST_MENTION, "id", (r"menyebut anda dalam postingan", r"menandai anda dalam postingan")),

    # Spanish
    PatternRule(COMMENT_REPLY, "es", (r"respondió a tu comentario", r"respondio a tu comentario", r"respondió un comentario tuyo")),
    PatternRule(COMMENT_MENTION, "es", (r"te mencionó en un comentario", r"te etiquetó en un comentario")),
    PatternRule(POST_MENTION, "es", (r"te mencionó en una publicación", r"te etiquetó en una publicación")),

    # Portuguese
    PatternRule(COMMENT_REPLY, "pt", (r"respondeu ao seu comentário", r"respondeu a seu comentário")),
    PatternRule(COMMENT_MENTION, "pt", (r"mencionou você em um comentário", r"marcou você em um comentário")),
    PatternRule(POST_MENTION, "pt", (r"mencionou você em uma publicação", r"marcou você em uma publicação")),

    # French
    PatternRule(COMMENT_REPLY, "fr", (r"a répondu à votre commentaire", r"a répondu à ton commentaire")),
    PatternRule(COMMENT_MENTION, "fr", (r"vous a mentionné dans un commentaire", r"t’a mentionné dans un commentaire", r"vous a identifié dans un commentaire")),
    PatternRule(POST_MENTION, "fr", (r"vous a mentionné dans une publication", r"vous a identifié dans une publication")),
)

SYSTEM_PATTERNS: tuple[str, ...] = (
    r"your comment is unavailable", r"comment is unavailable", r"留言無法顯示", r"留言不可用",
    r"评论无法显示", r"komentar anda tidak tersedia", r"komentar kamu tidak tersedia",
    r"facebook security", r"登入警示", r"安全性通知",
)
REACTION_PATTERNS: tuple[str, ...] = (
    r"liked your comment", r"reacted to your comment", r"按讚了你的留言", r"對你的留言表示",
    r"赞了你的评论", r"对你的评论作出了回应", r"nag-react sa komento mo", r"กดถูกใจความคิดเห็นของคุณ",
    r"أعجب بتعليقك", r"تفاعل مع تعليقك",
)
FRIEND_PATTERNS: tuple[str, ...] = (
    r"accepted your friend request", r"sent you a friend request", r"接受了你的交友邀請", r"好友邀請",
    r"接受了你的好友请求", r"friend request", r"tinanggap ang friend request mo",
)


def normalize(text: str) -> str:
    return " ".join((text or "").casefold().split())


def _first_match(text: str, patterns: Iterable[str]) -> str:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return ""


def classify_detail(text: str) -> dict[str, str]:
    normalized = normalize(text)
    for kind, patterns in ((SYSTEM, SYSTEM_PATTERNS), (REACTION, REACTION_PATTERNS), (FRIEND, FRIEND_PATTERNS)):
        matched = _first_match(normalized, patterns)
        if matched:
            return {"kind": kind, "language": "multi", "matched_pattern": matched}
    for rule in RULES:
        matched = _first_match(normalized, rule.patterns)
        if matched:
            return {"kind": rule.kind, "language": rule.language, "matched_pattern": matched}
    return {"kind": OTHER, "language": "unknown", "matched_pattern": ""}


def classify(text: str) -> str:
    return classify_detail(text)["kind"]
