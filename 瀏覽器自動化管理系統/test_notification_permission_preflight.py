from __future__ import annotations

import inspect

from 瀏覽器 import (
    BrowserController,
    FACEBOOK_NOTIFICATION_ORIGINS,
    dismiss_facebook_notification_overlay,
    grant_facebook_notification_permission,
)


class FakeDriver:
    def __init__(self):
        self.commands = []
        self.scripts = []

    def execute_cdp_cmd(self, command, params):
        self.commands.append((command, params))
        return {}

    def execute_script(self, script):
        self.scripts.append(script)
        return {"matched": True, "clicked": True}


driver = FakeDriver()
assert grant_facebook_notification_permission(driver)
assert len(driver.commands) == len(FACEBOOK_NOTIFICATION_ORIGINS)
assert all(command == "Browser.setPermission" for command, _ in driver.commands)
assert all(params["permission"]["name"] == "notifications" for _, params in driver.commands)
assert all(params["setting"] == "granted" for _, params in driver.commands)
assert dismiss_facebook_notification_overlay(driver)

connect_source = inspect.getsource(BrowserController.connect)
assert "grant_facebook_notification_permission(self.driver)" in connect_source
switch_source = inspect.getsource(BrowserController.switch_to_facebook_tab)
assert "dismiss_facebook_notification_overlay(self.driver)" in switch_source

print("V12 early Facebook notification permission tests passed")
