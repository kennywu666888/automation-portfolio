from pathlib import Path

from 個人資料設定 import (
    LANGUAGE_OPTIONS,
    _facebook_document_language_matches,
)


source = (Path(__file__).parent / "profile_setup.py").read_text(encoding="utf-8")

assert "العربية" in LANGUAGE_OPTIONS
assert _facebook_document_language_matches("العربية", "ar")
assert _facebook_document_language_matches("العربية", "ar_AR")
assert _facebook_document_language_matches("Filipino", "fil")
assert not _facebook_document_language_matches("Filipino", "ar")
assert "لغة الحساب" in source
assert "لغة فيسبوك" in source
assert "بحث عن لغات" in source
assert "حفظ التغييرات" in source
assert "تطبيق" in source
assert "موافق" in source
assert "cancel_or_close_labels" in source
assert "len(visible_dialogs()) >= 2" in source
assert "頁面可能已自動套用" not in source

gui_source = (Path(__file__).parent / "gui.py").read_text(encoding="utf-8")
assert '"العربية"' in gui_source

main_source = (Path(__file__).parent / "main.py").read_text(encoding="utf-8")
language_priority = main_source.index("最高優先任務：Facebook 介面語言")
assert main_source.index("執行 Health Check") < language_priority
for later_task in (
    "獨立任務：成為專業模式",
    "獨立任務：換頭像",
    "個人資料設定：Banner",
    "個人資料設定：名字",
    "獨立任務：Messenger PIN",
):
    assert language_priority < main_source.index(later_task)

print("Arabic RTL Facebook language labels tests passed")
