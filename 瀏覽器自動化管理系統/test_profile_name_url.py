from pathlib import Path

from 個人資料設定 import (
    build_accounts_center_name_url,
    facebook_profile_id_from_url,
)


PROFILE_ID = "61590617932210"
EXPECTED = (
    "https://accountscenter.facebook.com/profiles/61590617932210/name/"
    "?entrypoint=account_overview"
)

assert facebook_profile_id_from_url(
    "https://www.facebook.com/profile.php?id=61590617932210"
) == PROFILE_ID
assert facebook_profile_id_from_url(
    "https://www.facebook.com/profile.php?sk=about&id=61590617932210&locale=zh_TW"
) == PROFILE_ID
assert facebook_profile_id_from_url(
    "https://accountscenter.facebook.com/profiles/61590617932210/name/"
) == PROFILE_ID
assert build_accounts_center_name_url(PROFILE_ID) == EXPECTED
assert build_accounts_center_name_url(
    "https://www.facebook.com/profile.php?id=61590617932210"
) == EXPECTED

try:
    facebook_profile_id_from_url("https://www.facebook.com/example.name")
except ValueError:
    pass
else:
    raise AssertionError("沒有數字 ID 的網址必須拒絕")

source = (Path(__file__).parent / "profile_setup.py").read_text(encoding="utf-8")
assert "61590314495542" not in source
assert "?entrypoint=account_overview" in source

print("profile name Accounts Center URL tests passed")
