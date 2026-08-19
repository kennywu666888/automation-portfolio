from pathlib import Path
from types import SimpleNamespace

import 主程式 as main_module

from 臉書帳號狀態 import (
    detect_facebook_account_status,
    login_profile_name,
    tunnel_profile_name,
)


class FakeDriver:
    def __init__(self, url, page_source="", email=False, password=False):
        self.current_url = url
        self.page_source = page_source
        self.title = "Facebook"
        self.email = email
        self.password = password

    def find_elements(self, _by, selector):
        if 'name="email"' in selector:
            return [object()] if self.email else []
        if 'name="pass"' in selector:
            return [object()] if self.password else []
        return []

    def execute_script(self, _script):
        return {
            "detected": False,
            "kind": "normal",
            "reason": "",
            "url": self.current_url,
            "title": self.title,
        }


tunnel = detect_facebook_account_status(
    FakeDriver(
        "chrome-error://chromewebdata/",
        "The webpage is not available ERR_TUNNEL_CONNECTION_FAILED",
    )
)
assert tunnel.detected and tunnel.kind == "tunnel_connection_failed"
assert tunnel.reason == "err_tunnel_connection_failed"
assert tunnel_profile_name("私訊455", "id") == "IP到期私訊455"
assert tunnel_profile_name("IP到期私訊455", "id") == "IP到期私訊455"
assert tunnel_profile_name("隧道私訊455", "id") == "IP到期私訊455"

proxy_auth = detect_facebook_account_status(
    FakeDriver(
        "chrome-error://chromewebdata/",
        "No se puede acceder ERR_PROXY_AUTH_REQUESTED",
    )
)
assert proxy_auth.detected and proxy_auth.kind == "tunnel_connection_failed"
assert proxy_auth.reason == "err_proxy_auth_requested"


class TimeoutThenProxyDriver(FakeDriver):
    def __init__(self):
        super().__init__(
            "chrome-error://chromewebdata/",
            "This site can't be reached ERR_TIMED_OUT",
        )
        self.reloaded = False

    def execute_cdp_cmd(self, command, _params):
        assert command == "Page.reload"
        self.reloaded = True
        self.page_source = "ERR_PROXY_CONNECTION_FAILED"


timeout_driver = TimeoutThenProxyDriver()
timeout_result = detect_facebook_account_status(timeout_driver)
assert timeout_driver.reloaded
assert timeout_result.detected
assert timeout_result.kind == "tunnel_connection_failed"
assert timeout_result.reason == "err_proxy_connection_failed"

login_url = detect_facebook_account_status(
    FakeDriver("https://www.facebook.com/login/?next=%2Fnotifications")
)
assert login_url.detected and login_url.kind == "login_page"
assert login_url.reason == "facebook_login_url"

login_form = detect_facebook_account_status(
    FakeDriver("https://www.facebook.com/", email=True, password=True)
)
assert login_form.detected and login_form.kind == "login_page"
assert login_form.reason == "facebook_login_form"
assert login_profile_name("私訊455", "id") == "登入私訊455"
assert login_profile_name("登入私訊455", "id") == "登入私訊455"

normal = detect_facebook_account_status(
    FakeDriver("https://www.facebook.com/profile.php?id=123")
)
assert not normal.detected and normal.kind == "normal"

main_source = (Path(__file__).parent / "main.py").read_text(encoding="utf-8")
assert "account_status.kind=='tunnel_connection_failed'" in main_source
assert "account_status.kind=='login_page'" in main_source
assert "new_name=tunnel_profile_name" in main_source
assert "new_name=login_profile_name" in main_source
assert "if status_kind=='tunnel_connection_failed':" in main_source
assert "環境已更名為 IP到期、關閉並保留（未刪除）" in main_source
status_source = (Path(__file__).parent / "facebook_account_status.py").read_text(
    encoding="utf-8"
)
assert "ERR_PROXY_AUTH_REQUESTED" in status_source
assert "ERR_TIMED_OUT" in status_source


class FakeLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def critical(self, *_args, **_kwargs):
        pass

    def exception(self, *_args, **_kwargs):
        pass


class FakeStop:
    def wait(self, _seconds):
        return False

    def is_set(self):
        return False


class FlowClient:
    def __init__(self, events):
        self.events = events

    def open_browser(self, profile_id):
        self.events.append(("open", profile_id))
        return object()

    def rename_profile(self, profile_id, name):
        self.events.append(("rename", profile_id, name))
        return True

    def close_browser(self, profile_id):
        self.events.append(("close", profile_id))
        return True

    def delete_profile(self, profile_id, expected_name=""):
        self.events.append(("delete", profile_id, expected_name))
        return True


class FlowSession:
    def __init__(self, _info, events):
        self.events = events
        self.driver = object()

    def connect(self):
        self.events.append(("connect",))
        return self.driver

    def switch_to_facebook(self):
        self.events.append(("switch",))
        return True

    def detach(self):
        self.events.append(("detach",))
        self.driver = None


events = []
flow_client = FlowClient(events)
original_client = main_module.AdsPowerClient
original_session = main_module.BrowserSession
original_logger = main_module.profile_logger
original_verification = main_module.detect_human_verification_page
original_status = main_module.detect_facebook_account_status
try:
    main_module.AdsPowerClient = lambda *_args, **_kwargs: flow_client
    main_module.BrowserSession = lambda info: FlowSession(info, events)
    main_module.profile_logger = lambda _name: (FakeLogger(), Path("test.log"))
    main_module.detect_human_verification_page = lambda _driver: SimpleNamespace(
        detected=False,
        reason="normal",
    )
    main_module.detect_facebook_account_status = lambda _driver: SimpleNamespace(
        detected=True,
        kind="tunnel_connection_failed",
        reason="err_tunnel_connection_failed",
        url="chrome-error://chromewebdata/",
    )
    engine = object.__new__(main_module.MonitorEngine)
    engine.s = {
        "adspower_base_url": "http://local.adspower.net:50325",
        "adspower_api_key": "test-key",
        "delete_verified_profile": True,
        "close_browser": False,
    }
    engine.stop = FakeStop()
    result = engine._profile(SimpleNamespace(profile_id="profile-1", name="私訊455"))
finally:
    main_module.AdsPowerClient = original_client
    main_module.BrowserSession = original_session
    main_module.profile_logger = original_logger
    main_module.detect_human_verification_page = original_verification
    main_module.detect_facebook_account_status = original_status

assert result.status == "FAILED"
assert events == [
    ("open", "profile-1"),
    ("connect",),
    ("switch",),
    ("rename", "profile-1", "IP到期私訊455"),
    ("detach",),
    ("close", "profile-1"),
]
assert not any(event[0] == "delete" for event in events)

print("RC18 tunnel and login abnormal-status tests passed")
