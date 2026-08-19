from pathlib import Path
import json
import os

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
load_dotenv(BASE_DIR / ".env", override=False)

DEFAULTS = {
    "adspower_base_url": "http://local.adspower.net:50325",
    "group_id": "0",
    "search_mode": "prefix",
    "delete_group_url_after_claim": True,
    "delete_group_url_after_success": True,
    "author_dedupe_scope": "group",
    "group_comment_media_mode": "random",
    "group_comment_random_media_dir": str(Path.home() / "Desktop" / "view"),
    "group_comment_fixed_media_file": "",
    "max_replies": 20,
    "max_scrolls": 5,
    "sort_order": "oldest",
    "only_unread": False,
    "new_section_only": True,
    "telegram_enabled": True,
    "auto_reply_enabled": True,
    "auto_reply_text": "thanks",
    "mark_read_after_success": False,
    "threads": 1,
    "cycles": 1,
    "cycle_wait_minutes": 30,
    "notification_wait_seconds": 2,
    "close_browser": False,
    "delete_verified_profile": False,
}
SENSITIVE_SETTING_KEYS = {"adspower_api_key"}


def settings_for_storage(data):
    return {
        key: value
        for key, value in dict(data or {}).items()
        if key not in SENSITIVE_SETTING_KEYS
    }


def load_settings():
    data = DEFAULTS.copy()
    try:
        stored = settings_for_storage(
            json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        )
        # RC18 called this option "after claim", although a claim used to
        # remove the URL before the group had produced a comment.  Preserve a
        # user's existing choice while migrating to RC19's success-only name.
        if (
            "delete_group_url_after_success" not in stored
            and "delete_group_url_after_claim" in stored
        ):
            stored["delete_group_url_after_success"] = bool(
                stored["delete_group_url_after_claim"]
            )
        old_media_mode = str(stored.get("group_comment_media_mode", "none")).lower()
        if "group_comment_random_media_dir" not in stored:
            if old_media_mode == "video":
                stored["group_comment_random_media_dir"] = stored.get(
                    "group_comment_video_dir",
                    str(Path.home() / "Desktop" / "reelsv"),
                )
            else:
                stored["group_comment_random_media_dir"] = stored.get(
                    "group_comment_photo_dir",
                    str(Path.home() / "Desktop" / "view"),
                )
        if old_media_mode in {"photo", "video"}:
            stored["group_comment_media_mode"] = "random"
        stored.pop("group_comment_photo_dir", None)
        stored.pop("group_comment_video_dir", None)
        data.update(stored)
    except FileNotFoundError:
        return data
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"無法讀取設定檔 {SETTINGS_PATH}：{exc}") from exc
    return data


def save_settings(data):
    SETTINGS_PATH.write_text(
        json.dumps(settings_for_storage(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def target_group():
    return (
        os.getenv("ADSPOWER_TARGET_GROUP_ID", "").strip(),
        os.getenv("ADSPOWER_TARGET_GROUP", "").strip(),
    )


def telegram_credentials():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    incoming = os.getenv("TELEGRAM_CHAT_ID1", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    replied = os.getenv("TELEGRAM_CHAT_ID2", os.getenv("TELEGRAM_REPLY_CHAT_ID", "")).strip()
    return token, incoming, replied
