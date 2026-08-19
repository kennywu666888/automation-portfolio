from 環境管理客戶端 import ProfileInfo, _extract_proxy_ip
from 個人資料工具 import profile_matches_search, sort_profiles_by_number


profiles = [
    ProfileInfo("id-10", "私訊10", "一般", "beta", "S10", "10.0.0.10"),
    ProfileInfo("id-none", "沒有號碼", "其他"),
    ProfileInfo("id-2", "專業2", "專業", "", "S2", "proxy-two"),
    ProfileInfo("id-1", "IP到期私訊1", "到期"),
]

assert [profile.name for profile in sort_profiles_by_number(profiles)] == [
    "IP到期私訊1",
    "專業2",
    "私訊10",
    "沒有號碼",
]
assert profile_matches_search(profiles[0], "私訊")
assert profile_matches_search(profiles[0], "一般")
assert profile_matches_search(profiles[0], "ID-10")
assert profile_matches_search(profiles[0], "s10")
assert profile_matches_search(profiles[0], "10.0.0.10")
assert profile_matches_search(profiles[0], "BETA")
assert not profile_matches_search(profiles[0], "不存在")
assert _extract_proxy_ip({"proxy_config": {"host": "proxy.example"}}) == "proxy.example"

print("RC18 profile numeric order and AdsPower-style search tests passed")
