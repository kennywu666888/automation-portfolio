from pathlib import Path

import 主程式 as main_module
from 環境管理客戶端 import AdsPowerClient, BrowserInfo, ProfileInfo
from 人工驗證 import (
    HumanVerificationResult,
    VERIFICATION_PREFIX,
    detect_human_verification_page,
    verification_profile_name,
)


assert verification_profile_name("禁言私訊212", "id-1") == "驗証禁言私訊212"
assert verification_profile_name("驗証禁言私訊212", "id-1") == "驗証禁言私訊212"
assert verification_profile_name("", "id-1") == "驗証id-1"
assert VERIFICATION_PREFIX == "驗証"


class FakeDriver:
    def execute_script(self, _script):
        return {
            "detected": True,
            "reason": "exact_phrase_and_continue",
            "url": "https://www.facebook.com/checkpoint/",
            "title": "Confirm",
        }


check = detect_human_verification_page(FakeDriver())
assert check.detected
assert check.reason == "exact_phrase_and_continue"


class FakeAdsPowerClient(AdsPowerClient):
    def __init__(self, current_name):
        self.current_name = current_name
        self.calls = []

    def get_profile(self, pid):
        return ProfileInfo(pid, self.current_name, "test")

    def _post(self, path, payload=None, retries=3):
        self.calls.append((path, payload))
        return {"code": 0}


client = FakeAdsPowerClient("驗証禁言私訊212")
assert client.delete_profile("id-1", expected_name="驗証禁言私訊212")
assert client.calls == [
    ("/api/v1/user/delete", {"user_ids": ["id-1"]})
]

blocked = FakeAdsPowerClient("其他環境")
try:
    blocked.delete_profile("id-1", expected_name="驗証禁言私訊212")
except RuntimeError as exc:
    assert "已取消刪除" in str(exc)
else:
    raise AssertionError("名稱不相符時不應允許刪除")
assert blocked.calls == []


class FakeLogger:
    def info(self, *_args):
        pass

    def warning(self, *_args):
        pass

    def critical(self, *_args):
        pass

    def exception(self, *_args):
        pass


class FakeStop:
    def wait(self, _seconds):
        return False

    def is_set(self):
        return False


class FlowClient:
    def __init__(self, events):
        self.events = events

    def open_browser(self, pid):
        self.events.append(("open", pid))
        return BrowserInfo(pid, "127.0.0.1:1", "driver")

    def rename_profile(self, pid, name):
        self.events.append(("rename", pid, name))
        return True

    def close_browser(self, pid):
        self.events.append(("close", pid))

    def delete_profile(self, pid, expected_name=""):
        self.events.append(("delete", pid, expected_name))
        return True


class FlowSession:
    def __init__(self, _info, events):
        self.events = events
        self.driver = FakeDriver()

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
received_api_keys = []
original_client = main_module.AdsPowerClient
original_session = main_module.BrowserSession
original_logger = main_module.profile_logger
original_detector = main_module.detect_human_verification_page
try:
    main_module.AdsPowerClient = lambda _base, api_key='': (
        received_api_keys.append(api_key) or flow_client
    )
    main_module.BrowserSession = lambda info: FlowSession(info, events)
    main_module.profile_logger = lambda _name: (FakeLogger(), Path("test.log"))
    main_module.detect_human_verification_page = lambda _driver: (
        HumanVerificationResult(
            True,
            reason="exact_phrase_and_continue",
            url="https://www.facebook.com/checkpoint/",
        )
    )
    engine = object.__new__(main_module.MonitorEngine)
    engine.s = {
        "adspower_base_url": "http://local.adspower.net:50325",
        "adspower_api_key": "flow-placeholder-key",
        "delete_verified_profile": True,
        "close_browser": False,
    }
    engine.stop = FakeStop()
    profile = ProfileInfo("id-1", "禁言私訊212", "test")
    result = engine._profile(profile)
finally:
    main_module.AdsPowerClient = original_client
    main_module.BrowserSession = original_session
    main_module.profile_logger = original_logger
    main_module.detect_human_verification_page = original_detector

assert result.status == "FAILED"
assert result.failed == 1
assert received_api_keys == ["flow-placeholder-key"]
assert events == [
    ("open", "id-1"),
    ("connect",),
    ("switch",),
    ("rename", "id-1", "驗証禁言私訊212"),
    ("detach",),
    ("close", "id-1"),
    ("delete", "id-1", "驗証禁言私訊212"),
]

print("RC13 human-verification and delete-safety tests passed")
