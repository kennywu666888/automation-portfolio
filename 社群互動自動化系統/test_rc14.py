import json
from pathlib import Path

import 環境管理客戶端 as client_module
import 社團留言核心 as v64
from 環境管理客戶端 import AdsPowerClient
from 設定 import settings_for_storage
from 診斷工具 import safe_settings
from 社團留言任務 import configure_adspower_api


placeholder_key = "test-placeholder-key"
client = AdsPowerClient(
    "http://local.adspower.net:50325",
    api_key=placeholder_key,
)
assert client.headers == {
    "Authorization": f"Bearer {placeholder_key}"
}
configure_adspower_api({
    "adspower_base_url": "http://localhost:50325",
    "adspower_api_key": placeholder_key,
})
assert v64.ADSPOWER_API == "http://localhost:50325/api/v1"
assert v64.ADSPOWER_HEADERS == {
    "Authorization": f"Bearer {placeholder_key}"
}


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"code": 0, "data": {}}


calls = []
original_get = client_module.requests.get
original_post = client_module.requests.post
try:
    def fake_get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        return FakeResponse()

    def fake_post(url, **kwargs):
        calls.append(("POST", url, kwargs))
        return FakeResponse()

    client_module.requests.get = fake_get
    client_module.requests.post = fake_post
    client._get("/api/v1/group/list", retries=1)
    client._post(
        "/api/v1/user/delete",
        {"user_ids": ["id-1"]},
        retries=1,
    )
finally:
    client_module.requests.get = original_get
    client_module.requests.post = original_post

for method, _url, kwargs in calls:
    assert kwargs["headers"] == {
        "Authorization": f"Bearer {placeholder_key}"
    }, method
assert calls[1][2]["json"] == {"user_ids": ["id-1"]}

stored = settings_for_storage({
    "adspower_base_url": "http://local.adspower.net:50325",
    "adspower_api_key": placeholder_key,
})
assert "adspower_api_key" not in stored
snapshot = safe_settings({
    "adspower_api_key": placeholder_key,
    "threads": 1,
})
assert snapshot["adspower_api_key"] == "***"
assert placeholder_key not in json.dumps(snapshot)

settings_path = Path(__file__).with_name("settings.json")
assert placeholder_key not in settings_path.read_text(encoding="utf-8")
assert "adspower_api_key" not in json.loads(
    settings_path.read_text(encoding="utf-8")
)

print("RC14 API-key transport and secret-safety tests passed")
