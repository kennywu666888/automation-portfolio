from pathlib import Path
from types import SimpleNamespace
import threading

import 主程式
import 臨時_聊天室禁言檢查 as temporary_checker
from 臉書帳號狀態 import ip_expired_profile_name


class TunnelDriver:
    page_source = "The webpage is unavailable: ERR_TUNNEL_CONNECTION_FAILED"

    def execute_script(self, _script):
        return "ERR_TUNNEL_CONNECTION_FAILED"


profile = SimpleNamespace(profile_id="profile-1", name="私訊455")
ctrl = SimpleNamespace(driver=TunnelDriver())
kind, new_name = main.detect_account_removal_status(ctrl, profile)
assert kind == "tunnel_connection_failed"
assert new_name == "IP到期私訊455"
assert ip_expired_profile_name("IP到期私訊455", "profile-1") == "IP到期私訊455"
assert ip_expired_profile_name("隧道私訊455", "profile-1") == "IP到期私訊455"


class ProxyAuthDriver(TunnelDriver):
    page_source = "No se puede acceder ERR_PROXY_AUTH_REQUESTED"

    def execute_script(self, _script):
        return "ERR_PROXY_AUTH_REQUESTED"


proxy_kind, proxy_name = main.detect_account_removal_status(
    SimpleNamespace(driver=ProxyAuthDriver()), profile
)
assert proxy_kind == "tunnel_connection_failed"
assert proxy_name == "IP到期私訊455"


class TimeoutThenProxyDriver(TunnelDriver):
    def __init__(self):
        self.page_source = "This site can't be reached ERR_TIMED_OUT"
        self.reloaded = False

    def execute_cdp_cmd(self, command, _params):
        assert command == "Page.reload"
        self.reloaded = True
        self.page_source = "This site can't be reached ERR_PROXY_CONNECTION_FAILED"

    def execute_script(self, _script):
        return self.page_source


timeout_driver = TimeoutThenProxyDriver()
timeout_kind, timeout_name = main.detect_account_removal_status(
    SimpleNamespace(driver=timeout_driver), profile
)
assert timeout_driver.reloaded
assert timeout_kind == "tunnel_connection_failed"
assert timeout_name == "IP到期私訊455"


class FakeApi:
    def __init__(self):
        self.calls = []

    def get_or_open_browser(self, profile_id):
        self.calls.append(("open", profile_id))
        return object()

    def rename_profile(self, profile_id, name):
        self.calls.append(("rename", profile_id, name))
        return True

    def close_browser(self, profile_id):
        self.calls.append(("close", profile_id))
        return True

    def check_status(self, profile_id):
        self.calls.append(("status", profile_id))
        return False

    def delete_profile(self, profile_id):
        self.calls.append(("delete", profile_id))
        return True


class FakeController:
    def __init__(self, _session):
        self.driver = TunnelDriver()

    def connect(self):
        return self.driver

    def switch_to_facebook_tab(self):
        return True

    def navigate(self, _url):
        raise AssertionError("Tunnel 頁面不應再導頁")

    def bring_window_to_front(self):
        return True

    def detach_keep_browser(self):
        self.driver = None


api = FakeApi()
engine = object.__new__(temporary_checker.MutedCheckEngine)
engine.api = api
engine.stop_event = threading.Event()
engine.emit = lambda _message: None
engine.cache = {}
engine._removal_decision = lambda _ctrl, _profile: (
    "tunnel_connection_failed",
    "IP到期私訊455",
)

original_controller = temporary_checker.BrowserController
original_cookie_setup = temporary_checker.configure_chrome_cookie_access
try:
    temporary_checker.BrowserController = FakeController
    temporary_checker.configure_chrome_cookie_access = lambda *_args, **_kwargs: None
    outcome = engine.process(profile)
finally:
    temporary_checker.BrowserController = original_controller
    temporary_checker.configure_chrome_cookie_access = original_cookie_setup

assert outcome["result"] == "ip_expired"
assert outcome["deleted"] == "no"
assert ("rename", "profile-1", "IP到期私訊455") in api.calls
assert ("close", "profile-1") in api.calls
assert not any(call[0] == "delete" for call in api.calls)

main_source = (Path(__file__).parent / "main.py").read_text(encoding="utf-8")
assert 'elif detected_removal_kind == "tunnel_connection_failed":' in main_source
assert "代理／IP連線失效環境已關閉並保留" in main_source
assert "ERR_PROXY_AUTH_REQUESTED" in main_source
assert "ERR_TIMED_OUT" in main_source

temporary_source = (Path(__file__).parent / "臨時_聊天室禁言檢查.py").read_text(
    encoding="utf-8"
)
assert "代理驗證／Tunnel／重試後仍逾時" in temporary_source

print("12V and temporary-checker Tunnel keep-profile tests passed")
