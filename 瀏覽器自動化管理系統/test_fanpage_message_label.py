from pathlib import Path

from 訊息選擇器 import LANGUAGE_WORDS


assert "i-message" in LANGUAGE_WORDS["message"]

source = (Path(__file__).parent / "fanpage_message_task.py").read_text(
    encoding="utf-8"
)
assert '"i-message", "magpadala ng mensahe", "mensahe"' in source

print("Filipino I-message label tests passed")
