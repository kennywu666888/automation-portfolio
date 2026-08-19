import threading

from 瀏覽器工作階段 import BrowserSession


PROFILE_URL = "https://www.facebook.com/profile.php?id=61590000000123"


class FakeDriver:
    def __init__(self):
        self.current_url = "https://www.facebook.com/notifications"
        self.visited = []
        self.timeouts = []

    def set_page_load_timeout(self, value):
        self.timeouts.append(value)

    def execute_script(self, script, *_args):
        if "document.querySelectorAll('a[href]')" in script:
            return [{
                "href": PROFILE_URL + "&sk=about",
                "label": "Timeline",
                "timeline": True,
                "topAvatar": True,
            }]
        if "document.readyState" in script:
            return True
        return ""

    def get(self, url):
        self.visited.append(url)
        self.current_url = url

    def refresh(self):
        pass


session = BrowserSession(object())
session.driver = FakeDriver()
resolved = session.ensure_startup_personal_profile_url(threading.Event())
assert resolved == PROFILE_URL
assert session.driver.visited == ["https://www.facebook.com", PROFILE_URL]
assert session.driver.current_url == PROFILE_URL
assert session.personal_profile_url == PROFILE_URL
assert session.driver._facebook_personal_profile_url == PROFILE_URL

print("RC18 startup non-profile recovery test passed")
