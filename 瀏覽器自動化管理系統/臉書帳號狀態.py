from __future__ import annotations

from dataclasses import dataclass


SUSPENDED_PREFIX = "停權"
SLEEP_MODE_PREFIX = "睡眠"
IP_EXPIRED_PREFIX = "IP到期"
LEGACY_TUNNEL_PREFIX = "隧道"


@dataclass(frozen=True)
class FacebookAccountStatusResult:
    detected: bool
    kind: str = "normal"
    reason: str = ""
    url: str = ""
    title: str = ""


def _prefixed_profile_name(prefix: str, profile_name: str, profile_id: str = "") -> str:
    original = (profile_name or "").strip() or (profile_id or "").strip()
    if not original:
        raise RuntimeError("無法建立 AdsPower 環境名稱：名稱與環境 ID 都是空白")
    return original if original.startswith(prefix) else f"{prefix}{original}"


def suspended_profile_name(profile_name: str, profile_id: str = "") -> str:
    return _prefixed_profile_name(SUSPENDED_PREFIX, profile_name, profile_id)


def sleep_mode_profile_name(profile_name: str, profile_id: str = "") -> str:
    return _prefixed_profile_name(SLEEP_MODE_PREFIX, profile_name, profile_id)


def ip_expired_profile_name(profile_name: str, profile_id: str = "") -> str:
    """Return the non-deleting label used for an expired proxy/Tunnel.

    Older builds used the ``隧道`` prefix.  Remove that legacy prefix when
    upgrading the label so repeated checks produce ``IP到期＋原名稱`` rather
    than stacking two status prefixes.
    """
    original = (profile_name or "").strip() or (profile_id or "").strip()
    if not original:
        raise RuntimeError("無法建立 AdsPower 環境名稱：名稱與環境 ID 都是空白")
    if original.startswith(IP_EXPIRED_PREFIX):
        return original
    if original.startswith(LEGACY_TUNNEL_PREFIX):
        original = original[len(LEGACY_TUNNEL_PREFIX):].strip() or original
    return f"{IP_EXPIRED_PREFIX}{original}"


def detect_facebook_account_status(driver) -> FacebookAccountStatusResult:
    """Detect explicit suspended-account and sleep-mode Facebook pages."""
    try:
        payload = driver.execute_script(
            r"""
            const norm = value => (value || '')
                .replace(/[’‘`]/g, "'")
                .replace(/\s+/g, ' ')
                .trim()
                .toLowerCase();
            const bodyText = norm(document.body && document.body.innerText);
            const title = norm(document.title);
            const url = (location.href || '').toLowerCase();
            const combined = `${title} ${bodyText}`;

            const suspendedStrong = [
                "we've suspended your account",
                "we have suspended your account",
                "your account has been suspended",
                "we suspended your account",
                "我們已停用你的帳號",
                "我们已停用你的帐号",
                "你的帳號已被停權",
                "你的帐号已被停权"
            ];
            const suspendedSupport = [
                "days left to appeal",
                "permanently disable your account",
                "your account is not visible to people on facebook",
                "you cannot use it",
                "appeal our decision"
            ];
            const suspended = suspendedStrong.some(p => combined.includes(p)) ||
                (combined.includes('suspended') &&
                 suspendedSupport.filter(p => combined.includes(p)).length >= 2);

            const sleepStrong = [
                "you're in sleep mode",
                "you are in sleep mode",
                "你目前處於睡眠模式",
                "你目前处于睡眠模式",
                "你已進入睡眠模式",
                "你已进入睡眠模式"
            ];
            const sleepSupport = [
                "your notifications will be muted until",
                "now's a good time to close facebook",
                "now is a good time to close facebook"
            ];
            const sleepMode = sleepStrong.some(p => combined.includes(p)) ||
                (combined.includes('sleep mode') &&
                 sleepSupport.some(p => combined.includes(p)));

            let kind = 'normal';
            let reason = '';
            if (suspended) {
                kind = 'suspended';
                reason = 'suspended_account_text';
            } else if (sleepMode) {
                kind = 'sleep_mode';
                reason = 'sleep_mode_dialog_text';
            }
            return {detected: kind !== 'normal', kind, reason, url, title};
            """
        ) or {}
    except Exception as exc:
        return FacebookAccountStatusResult(
            False,
            reason=f"detection_error:{type(exc).__name__}:{exc}",
        )

    return FacebookAccountStatusResult(
        bool(payload.get("detected")),
        kind=str(payload.get("kind") or "normal"),
        reason=str(payload.get("reason") or ""),
        url=str(payload.get("url") or ""),
        title=str(payload.get("title") or ""),
    )
