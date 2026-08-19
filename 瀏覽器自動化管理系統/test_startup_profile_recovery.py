import threading

from 主程式 import ensure_startup_personal_profile_url


PROFILE_URL = "https://www.facebook.com/profile.php?id=61590000000123"


class FakeDriver:
    def __init__(self):
        self.current_url = "https://www.facebook.com/messages"
        self.visited = []

    def execute_script(self, script, *_args):
        if "Array.from(document.querySelectorAll('a[href]'))" in script:
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


class FakeController:
    def __init__(self):
        self.driver = FakeDriver()

    def navigate(self, url):
        self.driver.get(url)

    def stop_loading(self):
        pass


ctrl = FakeController()
resolved = ensure_startup_personal_profile_url(
    ctrl, "私訊128", threading.Event()
)
assert resolved == PROFILE_URL
assert ctrl.driver.visited == ["https://www.facebook.com", PROFILE_URL]
assert ctrl.driver.current_url == PROFILE_URL
assert ctrl.driver._facebook_personal_profile_url == PROFILE_URL

print("12V startup non-profile recovery test passed")
