import inspect

from 粉絲專頁訊息任務 import FanpageMessageTask
from 訊息選擇器 import find_message_input


typing_source = inspect.getsource(FanpageMessageTask._type_multiline_message)
send_source = inspect.getsource(FanpageMessageTask._send_message_with_stale_retry)

assert "Keys.SHIFT" in typing_source
assert "Keys.ENTER" in typing_source
assert "input_box.send_keys(message)" not in send_source
assert "self._type_multiline_message(input_box, message)" in send_source


class TallMessengerInput:
    rect = {"x": 600, "y": 620, "width": 300, "height": 220}

    def is_displayed(self):
        return True

    def get_attribute(self, name):
        return {
            "aria-label": "",
            "placeholder": "",
            "aria-placeholder": "Aa",
            "data-lexical-editor": "true",
        }.get(name, "")


class MessengerContainer:
    def __init__(self, editor):
        self.editor = editor

    def find_elements(self, _by, _selector):
        return [self.editor]


class DummyDriver:
    pass


tall_editor = TallMessengerInput()
assert find_message_input(
    DummyDriver(), MessengerContainer(tall_editor)
) is tall_editor

print("fanpage multiline Shift+Enter tests passed")
