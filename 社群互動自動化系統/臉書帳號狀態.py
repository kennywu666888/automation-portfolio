from __future__ import annotations

from dataclasses import dataclass
import time


SUSPENDED_PREFIX = "停權"
SLEEP_MODE_PREFIX = "睡眠"
TUNNEL_PREFIX = "IP到期"
LEGACY_TUNNEL_PREFIX = "隧道"
LOGIN_PREFIX = "登入"

IP_EXPIRED_DIRECT_ERROR_CODES = (
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_REQUESTED",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_SOCKS_CONNECTION_FAILED",
    "ERR_NO_SUPPORTED_PROXIES",
    "ERR_MANDATORY_PROXY_CONFIGURATION_FAILED",
    "ERR_PAC_SCRIPT_FAILED",
)
IP_EXPIRED_RETRY_ERROR_CODES = (
    "ERR_TIMED_OUT",
    "ERR_CONNECTION_TIMED_OUT",
)


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


def tunnel_profile_name(profile_name: str, profile_id: str = "") -> str:
    original = (profile_name or "").strip() or (profile_id or "").strip()
    if not original:
        raise RuntimeError("無法建立 AdsPower 環境名稱：名稱與環境 ID 都是空白")
    if original.startswith(TUNNEL_PREFIX):
        return original
    if original.startswith(LEGACY_TUNNEL_PREFIX):
        original = original[len(LEGACY_TUNNEL_PREFIX):].strip() or original
    return f"{TUNNEL_PREFIX}{original}"


def login_profile_name(profile_name: str, profile_id: str = "") -> str:
    return _prefixed_profile_name(LOGIN_PREFIX, profile_name, profile_id)


def _read_chrome_network_error_code(driver) -> str:
    chunks = []
    for reader in (
        lambda: driver.page_source,
        lambda: driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        ),
    ):
        try:
            chunks.append(str(reader() or ""))
        except Exception:
            continue
    text = "\n".join(chunks).upper()
    for code in IP_EXPIRED_DIRECT_ERROR_CODES + IP_EXPIRED_RETRY_ERROR_CODES:
        if code in text:
            return code
    return ""


def _confirmed_ip_expired_error_code(driver) -> str:
    """Direct proxy errors are immediate; generic timeout is retried once."""
    initial = _read_chrome_network_error_code(driver)
    if initial in IP_EXPIRED_DIRECT_ERROR_CODES or not initial:
        return initial

    reload_started = False
    try:
        driver.execute_cdp_cmd("Page.reload", {"ignoreCache": True})
        reload_started = True
    except Exception:
        try:
            driver.refresh()
            reload_started = True
        except Exception:
            pass
    if not reload_started:
        return initial

    time.sleep(0.8)
    deadline = time.monotonic() + 7.0
    last_error = initial
    clear_observations = 0
    while time.monotonic() < deadline:
        current = _read_chrome_network_error_code(driver)
        if current in IP_EXPIRED_DIRECT_ERROR_CODES:
            return current
        if current in IP_EXPIRED_RETRY_ERROR_CODES:
            last_error = current
            clear_observations = 0
        else:
            try:
                recovered = bool(driver.execute_script(
                    """
                    return location.hostname.endsWith('facebook.com') &&
                           ['interactive','complete'].includes(document.readyState) &&
                           !document.documentElement.innerHTML.includes('chrome-error://');
                    """
                ))
            except Exception:
                recovered = False
            clear_observations = clear_observations + 1 if recovered else 0
            if clear_observations >= 2:
                return ""
        time.sleep(0.35)
    return last_error


def _detect_tunnel_or_login(driver) -> FacebookAccountStatusResult | None:
    """Detect expired proxy/IP failures and explicit Facebook login pages."""
    try:
        url = str(driver.current_url or "")
    except Exception:
        url = ""
    try:
        title = str(driver.title or "")
    except Exception:
        title = ""
    ip_error_code = _confirmed_ip_expired_error_code(driver)
    if ip_error_code:
        return FacebookAccountStatusResult(
            True,
            kind="tunnel_connection_failed",
            reason=ip_error_code.casefold(),
            url=url,
            title=title,
        )

    url_folded = url.casefold()
    facebook_login_url = "facebook.com" in url_folded and any(
        marker in url_folded
        for marker in ("/login", "login.php", "/recover/initiate")
    )
    try:
        has_email = bool(
            driver.find_elements(
                "css selector",
                'input[name="email"],input[type="email"],'
                'input[autocomplete="username"]',
            )
        )
        has_password = bool(
            driver.find_elements(
                "css selector",
                'input[name="pass"],input[type="password"],'
                'input[autocomplete="current-password"]',
            )
        )
    except Exception:
        has_email = False
        has_password = False

    if facebook_login_url or (has_email and has_password):
        return FacebookAccountStatusResult(
            True,
            kind="login_page",
            reason=(
                "facebook_login_url"
                if facebook_login_url
                else "facebook_login_form"
            ),
            url=url,
            title=title,
        )
    return None


def detect_facebook_account_status(driver) -> FacebookAccountStatusResult:
    """Detect account, login, and browser-connection failure states."""
    early_result = _detect_tunnel_or_login(driver)
    if early_result is not None:
        return early_result
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
