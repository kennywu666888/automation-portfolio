from 臉書帳號狀態 import (
    detect_facebook_account_status,
    sleep_mode_profile_name,
    suspended_profile_name,
)


class FakeDriver:
    def __init__(self, payload):
        self.payload = payload

    def execute_script(self, _script):
        return self.payload


suspended = detect_facebook_account_status(FakeDriver({
    "detected": True,
    "kind": "suspended",
    "reason": "suspended_account_text",
    "url": "https://www.facebook.com/checkpoint/",
    "title": "Facebook",
}))
assert suspended.detected and suspended.kind == "suspended"

sleep_mode = detect_facebook_account_status(FakeDriver({
    "detected": True,
    "kind": "sleep_mode",
    "reason": "sleep_mode_dialog_text",
    "url": "https://www.facebook.com/notifications",
    "title": "Facebook",
}))
assert sleep_mode.detected and sleep_mode.kind == "sleep_mode"

assert suspended_profile_name("禁言229", "id") == "停權禁言229"
assert sleep_mode_profile_name("禁言229", "id") == "睡眠禁言229"
print("RC15 account-status tests passed")
