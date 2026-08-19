"""
adspower.py
===========
Facebook Auto Warm-up Lite — AdsPower Local API 模組
所有 AdsPower 操作集中於此，外部模組只透過此介面存取。
"""

import time
from dataclasses import dataclass
from typing import Optional

import requests

from 設定 import CONFIG, AdsPowerConfig
from 日誌 import get_logger

_log = get_logger(__name__)


# ─────────────────────────────────────────────
# 資料結構
# ─────────────────────────────────────────────

@dataclass
class ProfileInfo:
    """AdsPower Profile 基本資訊。"""
    profile_id: str      # AdsPower 內部 user_id
    name: str            # Profile 顯示名稱
    group_name: str = "" # 分組名稱（可選）
    remark: str = ""     # 備註
    serial_number: str = ""
    proxy_ip: str = ""


def _extract_proxy_ip(data: dict) -> str:
    """Read proxy IP/host across AdsPower API response variants."""
    keys = ("ip", "proxy_ip", "proxy_host", "host", "proxy_server", "server")
    containers = [
        data,
        data.get("proxy_config") or {},
        data.get("proxy") or {},
        data.get("proxy_info") or {},
        data.get("user_proxy_config") or {},
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value not in (None, ""):
                return str(value).strip()
    return ""


@dataclass
class BrowserSession:
    """已開啟的 Browser 連線資訊。"""
    profile_id: str
    ws_endpoint: str      # Selenium 用的 WebSocket debugger URL
    selenium_address: str # chromedriver 的 HTTP 位址（ip:port）
    webdriver_path: str   # chromedriver 執行檔路徑


# ─────────────────────────────────────────────
# AdsPower API 用戶端
# ─────────────────────────────────────────────

class AdsPowerClient:
    """
    封裝 AdsPower Local API 的所有操作。
    使用 requests 直接呼叫 localhost API。
    """

    def __init__(self, cfg: Optional[AdsPowerConfig] = None) -> None:
        self._cfg = cfg or CONFIG.adspower
        self._base = self._cfg.base_url.rstrip("/")
        self._api_key = str(getattr(self._cfg, "api_key", "") or "").strip()

    def set_api_key(self, api_key: str) -> None:
        """更新後續 AdsPower API 請求使用的 API Key。"""
        self._api_key = str(api_key or "").strip()
        self._cfg.api_key = self._api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    # ── 私有輔助方法 ────────────────────────────

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """
        送出 GET 請求並回傳 JSON 資料。
        若 API 回應 code != 0 則拋出 RuntimeError。
        """
        url = f"{self._base}{endpoint}"
        try:
            resp = requests.get(
                url,
                params=params or {},
                headers=self._headers(),
                timeout=self._cfg.request_timeout,
            )
            resp.raise_for_status()
            data: dict = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"AdsPower API 請求失敗：{url}，原因：{exc}") from exc

        if data.get("code") != 0:
            msg = data.get("msg", "未知錯誤")
            raise RuntimeError(f"AdsPower API 回應錯誤：{msg}（endpoint={endpoint}）")
        return data

    def _post(self, endpoint: str, payload: Optional[dict] = None) -> dict:
        """送出 POST 請求並回傳 JSON 資料。"""
        url = f"{self._base}{endpoint}"
        try:
            resp = requests.post(
                url,
                json=payload or {},
                headers=self._headers(),
                timeout=self._cfg.request_timeout,
            )
            resp.raise_for_status()
            data: dict = resp.json()
        except requests.RequestException as exc:
            raise RuntimeError(f"AdsPower API 請求失敗：{url}，原因：{exc}") from exc

        if data.get("code") != 0:
            msg = data.get("msg", "未知錯誤")
            raise RuntimeError(f"AdsPower API 回應錯誤：{msg}（endpoint={endpoint}）")
        return data

    # ── 公開 API ────────────────────────────────


    def test_connection(self) -> bool:
        """測試 AdsPower API 與 API Key 是否可正常授權。"""
        if not self._api_key:
            raise RuntimeError("請先輸入 AdsPower API Key")
        self._get(
            "/api/v1/user/list",
            params={"page": 1, "page_size": 1},
        )
        return True

    def list_groups(self) -> list[dict]:
        """讀取 AdsPower 群組，供 GUI 下拉選單使用。"""
        data = self._get(
            "/api/v1/group/list",
            params={"page": 1, "page_size": 100},
        )
        groups: list[dict] = []
        for item in data.get("data", {}).get("list", []):
            group_id = str(item.get("group_id", "")).strip()
            if not group_id:
                continue
            groups.append({
                "group_id": group_id,
                "group_name": str(item.get("group_name", "")).strip() or group_id,
            })
        return groups

    def list_profiles(
        self,
        page: int = 1,
        page_size: int = 100,
        group_id: str = "0",
    ) -> list[ProfileInfo]:
        """
        讀取 AdsPower Profile 清單。

        Args:
            page:      分頁頁碼（從 1 開始）。
            page_size: 每頁筆數（最大 100）。
            group_id:  分組 ID，"0" 表示全部。

        Returns:
            ProfileInfo 列表。
        """
        params = {
            "page": page,
            "page_size": page_size,
        }
        # AdsPower 部分版本會把 group_id=0 當成真正的「第 0 群組」，
        # 因而在 GUI 選擇「全部群組」時回傳 0 筆。全部群組必須省略
        # group_id，只有指定群組時才傳入。
        normalized_group_id = str(group_id or "").strip()
        if normalized_group_id not in ("", "0"):
            params["group_id"] = normalized_group_id
        data = self._get("/api/v1/user/list", params=params)
        profiles: list[ProfileInfo] = []
        for item in data.get("data", {}).get("list", []):
            profiles.append(
                ProfileInfo(
                    profile_id=item.get("user_id", ""),
                    name=item.get("name", ""),
                    group_name=item.get("group_name", ""),
                    remark=item.get("remark", ""),
                    serial_number=str(item.get("serial_number", "") or ""),
                    proxy_ip=_extract_proxy_ip(item),
                )
            )
        _log.info("讀取到 %d 個 Profile。", len(profiles))
        return profiles

    def list_profiles_by_group(self, group_id: str = "0") -> list[ProfileInfo]:
        """自動翻頁讀取 GUI 所選群組的全部環境。"""
        profiles: list[ProfileInfo] = []
        page = 1
        while True:
            batch = self.list_profiles(page=page, page_size=100, group_id=group_id)
            profiles.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            time.sleep(self._cfg.list_page_wait)
        return profiles

    def list_all_profiles(self) -> list[ProfileInfo]:
        """
        自動翻頁，讀取指定群組內的全部 Profile。

        Returns:
            所有 ProfileInfo 的完整列表。
        """
        _log.info(
            "讀取 AdsPower 群組：%s（group_id=%s）",
            self._cfg.target_group,
            self._cfg.target_group_id,
        )

        all_profiles: list[ProfileInfo] = []
        page = 1

        while True:
            batch = self.list_profiles(
                page=page,
                page_size=100,
                group_id=self._cfg.target_group_id,
            )

            if not batch:
                break

            all_profiles.extend(batch)
            _log.info("第 %d 頁讀取完成，目前累計 %d 個 Profile。", page, len(all_profiles))

            if len(batch) < 100:
                # 已是最後一頁
                break

            page += 1

            # AdsPower API 容易限流，翻頁前等待數秒。
            time.sleep(self._cfg.list_page_wait)

        _log.info("共讀取 %d 個 Profile（全部翻頁）。", len(all_profiles))
        return all_profiles

    def open_browser(self, profile_id: str) -> BrowserSession:
        """
        開啟指定 Profile 的瀏覽器，並回傳連線資訊。

        Args:
            profile_id: AdsPower Profile 的 user_id。

        Returns:
            BrowserSession 連線資訊。

        Raises:
            RuntimeError: 開啟失敗時拋出。
        """
        for attempt in range(self._cfg.launch_retries + 1):
            try:
                _log.info("開啟 Browser（Profile=%s，第 %d 次）。", profile_id, attempt + 1)
                data = self._get(
                    "/api/v1/browser/start",
                    params={"user_id": profile_id},
                )
                ws_info = data.get("data", {})
                session = BrowserSession(
                        profile_id=profile_id,
                        ws_endpoint=ws_info.get("ws", {}).get("puppeteer", ""),
                        selenium_address=ws_info.get("ws", {}).get("selenium", ""),
                        webdriver_path=ws_info.get("webdriver", ""),
                    )
                # 等待瀏覽器完全啟動
                time.sleep(self._cfg.browser_launch_wait)
                _log.info(
                    "Browser 已開啟（Profile=%s，Selenium=%s）。",
                    profile_id, session.selenium_address,
                )
                return session
            except RuntimeError as exc:
                if attempt < self._cfg.launch_retries:
                    _log.warning(
                        "開啟 Browser 失敗，%s 秒後重試：%s",
                        self._cfg.retry_wait, exc,
                    )
                    time.sleep(self._cfg.retry_wait)
                else:
                    raise

        # 此行不應被執行到，保留型別完整性
        raise RuntimeError(f"無法開啟 Browser（Profile={profile_id}）")

    def get_or_open_browser(self, profile_id: str) -> BrowserSession:
        """
        智慧取得瀏覽器連線資訊。

        - 已開啟：呼叫 browser/start 取回現有 Selenium 連線資訊並直接接管。
        - 未開啟：由 browser/start 開啟後再接管。

        AdsPower 對已啟動環境呼叫 browser/start 會回傳該環境目前的
        debugger／webdriver 資訊，不會建立第二個同名環境。
        """
        already_active = self.check_status(profile_id)
        if already_active:
            _log.info("Browser 已在執行中，準備直接接管（Profile=%s）。", profile_id)
        else:
            _log.info("Browser 尚未開啟，準備自動開啟（Profile=%s）。", profile_id)

        session = self.open_browser(profile_id)
        _log.info(
            "%s（Profile=%s，Selenium=%s）。",
            "已取得現有 Browser 連線" if already_active else "新 Browser 已開啟並取得連線",
            profile_id,
            session.selenium_address,
        )
        return session

    def close_browser(self, profile_id: str) -> None:
        """
        關閉指定 Profile 的瀏覽器。
        失敗時只記錄警告，不拋出例外（確保流程繼續）。

        Args:
            profile_id: AdsPower Profile 的 user_id。
        """
        try:
            self._get(
                "/api/v1/browser/stop",
                params={"user_id": profile_id},
            )
            _log.info("Browser 已關閉（Profile=%s）。", profile_id)
        except RuntimeError as exc:
            _log.warning("關閉 Browser 失敗（Profile=%s）：%s", profile_id, exc)

    def rename_profile(self, profile_id: str, new_name: str) -> None:
        """
        重新命名指定 Profile。

        Args:
            profile_id: AdsPower Profile 的 user_id。
            new_name:   新的 Profile 名稱。
        """
        try:
            self._post(
                "/api/v1/user/update",
                payload={"user_id": profile_id, "name": new_name},
            )
            _log.info("Profile 已重新命名（id=%s，新名稱=%s）。", profile_id, new_name)
            return True
        except RuntimeError as exc:
            _log.warning("重新命名 Profile 失敗（id=%s）：%s", profile_id, exc)
            return False

    def delete_profile(self, profile_id: str) -> bool:
        """永久刪除指定 AdsPower Profile。

        刪除前應先由呼叫端解除 Selenium 接管並關閉瀏覽器。
        """
        profile_id = str(profile_id or "").strip()
        if not profile_id:
            raise RuntimeError("AdsPower 刪除需要環境 ID")
        if not self._api_key:
            raise RuntimeError("AdsPower 刪除環境需要 API Key，請先在 GUI 輸入並測試連線")
        try:
            self._post(
                "/api/v1/user/delete",
                payload={"user_ids": [profile_id]},
            )
            _log.info("Profile 已刪除（id=%s）。", profile_id)
            return True
        except RuntimeError as exc:
            _log.warning("刪除 Profile 失敗（id=%s）：%s", profile_id, exc)
            return False

    def check_status(self, profile_id: str) -> bool:
        """
        確認 Profile 的瀏覽器目前是否開啟中。

        Args:
            profile_id: Profile ID。

        Returns:
            True 表示已開啟，False 表示未開啟。
        """
        try:
            data = self._get(
                "/api/v1/browser/active",
                params={"user_id": profile_id},
            )
            status = data.get("data", {}).get("status", "Inactive")
            return status == "Active"
        except RuntimeError:
            return False
