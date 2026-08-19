from 粉絲專頁訊息任務 import (
    FANPAGE_CONTACT_INFO,
    append_fanpage_contact_info,
)


assert FANPAGE_CONTACT_INFO == (
    "Kung interesado ka, maaari mo akong kontakin sa aking Telegram account\n\n"
    "@phplotto777"
)
assert append_fanpage_contact_info("測試文案") == (
    "測試文案\n\n"
    "Kung interesado ka, maaari mo akong kontakin sa aking Telegram account\n\n"
    "@phplotto777"
)
assert append_fanpage_contact_info("測試文案\n\n") == (
    "測試文案\n\n"
    "Kung interesado ka, maaari mo akong kontakin sa aking Telegram account\n\n"
    "@phplotto777"
)

print("fanpage Telegram contact info tests passed")
