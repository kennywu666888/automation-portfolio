from __future__ import annotations

import inspect

from 瀏覽器工作階段 import (
    BrowserSession,
    FACEBOOK_NOTIFICATION_ORIGINS,
    dismiss_facebook_notification_overlay,
    grant_facebook_notification_permission,
)
from 社團留言核心 import prepare_facebook_notification_permission


class FakeSwitch:
    class Active:
        def __init__(self, owner):
            self.owner = owner

        def send_keys(self, value):
            self.owner.keys.append(value)

    def __init__(self, owner):
        self.active_element = self.Active(owner)


class FakeDriver:
    def __init__(self):
        self.commands = []
        self.keys = []
        self.switch_to = FakeSwitch(self)

    def execute_cdp_cmd(self, command, params):
        self.commands.append((command, params))
        return {}

    def execute_script(self, _script):
        return {"matched": True, "clicked": True}


class FakeKeyboard:
    def __init__(self):
        self.keys = []

    def press(self, value):
        self.keys.append(value)


class FakePage:
    def __init__(self):
        self.keyboard = FakeKeyboard()

    def evaluate(self, _script):
        return {"matched": True, "clicked": False}


class FakeContext:
    def __init__(self):
        self.grants = []

    def grant_permissions(self, permissions, origin):
        self.grants.append((tuple(permissions), origin))


driver = FakeDriver()
assert grant_facebook_notification_permission(driver)
assert len(driver.commands) == len(FACEBOOK_NOTIFICATION_ORIGINS)
assert dismiss_facebook_notification_overlay(driver)

context, page = FakeContext(), FakePage()
assert prepare_facebook_notification_permission(context, page)
assert len(context.grants) == 4
assert all(permissions == ("notifications",) for permissions, _ in context.grants)
assert page.keyboard.keys == ["Escape"]

connect_source = inspect.getsource(BrowserSession.connect)
assert "grant_facebook_notification_permission(self.driver)" in connect_source
switch_source = inspect.getsource(BrowserSession.switch_to_facebook)
assert "dismiss_facebook_notification_overlay(self.driver)" in switch_source

print("RC19 early Facebook notification permission tests passed")
