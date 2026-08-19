# -*- coding: utf-8 -*-
"""
AdsPower 環境管理大師 V2.6
功能：
1. 讀取 AdsPower 所有環境（自動翻頁）
2. 依環境名稱第一個中文、英文字母或數字分類為系列
3. 點選系列後列出所有環境
4. 雙擊環境、按 F2、右鍵或按「更名」直接修改名稱
5. 搜尋環境
6. 批次依序更名
7. 檢查重複名稱
8. 匯出 CSV／Excel／TXT，並可自選欄位
9. API 遇到限流自動重試
10. 批次加入前綴、後綴、搜尋取代與移除文字
11. 右鍵複製環境名稱、Profile ID 或 IP，雙擊 IP 欄也可複製
12. 群組對群組大量轉移（來源群組全部搬到目標群組）
13. 刪除單一或多個環境（雙重確認，最多每批 100 個）
14. 打開單一或多個 AdsPower 環境
15. 讀取環境後顯示代理 IP／代理主機
16. 快速開啟 AdsPower 客戶端查看回收站環境
17. 將右側選取的單一或多個環境轉移到指定群組
18. 將已開啟的指定環境視窗帶到最前面
19. 關閉選取的單一或多個環境
20. 已開啟環境顯示淡藍色，未開啟環境顯示粉紅色
21. SunBrowser 核心下載或更新時自動等待，完成後重試開啟環境

安裝套件：
    pip install requests

使用前：
1. 開啟 AdsPower 並登入
2. 到 AdsPower API 頁面開啟 Local API
3. 執行本程式
4. 填入 API Key，按「儲存設定」
"""

import csv
import ctypes
import json
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable, Dict, List, Optional

import requests


APP_TITLE = "AdsPower 環境管理大師 V2.6"
CONFIG_FILE = Path(__file__).with_name("adspower_manager_config.json")
DEFAULT_BASE_URL = "http://local.adspower.net:50325"
PAGE_SIZE = 100
REQUEST_INTERVAL = 0.35
MAX_RETRIES = 6

EXPORT_FIELDS = [
    ("series", "系列"),
    ("name", "環境名稱"),
    ("group_name", "群組名稱"),
    ("group_id", "群組ID"),
    ("proxy_ip", "IP／代理主機"),
    ("serial_number", "AdsPower編號"),
    ("user_id", "Profile ID"),
    ("remark", "備註"),
]


@dataclass
class Profile:
    user_id: str
    name: str
    group_id: str = ""
    group_name: str = ""
    serial_number: str = ""
    proxy_ip: str = ""
    remark: str = ""

    @staticmethod
    def extract_proxy_ip(data: Dict[str, Any]) -> str:
        """
        相容不同 AdsPower 版本的代理欄位。
        優先顯示代理 IP；若 API 回傳的是網域主機名稱，則顯示主機名稱。
        """
        possible_containers = [
            data,
            data.get("proxy_config") or {},
            data.get("proxy") or {},
            data.get("proxy_info") or {},
        ]

        possible_keys = (
            "ip",
            "proxy_ip",
            "proxy_host",
            "host",
            "proxy_server",
            "server",
        )

        for container in possible_containers:
            if not isinstance(container, dict):
                continue
            for key in possible_keys:
                value = container.get(key)
                if value not in (None, ""):
                    return str(value).strip()

        # 某些版本可能將代理資訊放在 user_proxy_config
        nested = data.get("user_proxy_config")
        if isinstance(nested, dict):
            for key in possible_keys:
                value = nested.get(key)
                if value not in (None, ""):
                    return str(value).strip()

        return ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Profile":
        return cls(
            user_id=str(data.get("user_id", "")).strip(),
            name=str(data.get("name", "")).strip(),
            group_id=str(data.get("group_id", "")).strip(),
            group_name=str(data.get("group_name", "")).strip(),
            serial_number=str(data.get("serial_number", "")).strip(),
            proxy_ip=cls.extract_proxy_ip(data),
            remark=str(data.get("remark", "")).strip(),
        )


def get_series_key(text: str) -> str:
    """
    依環境名稱第一個有效字元分類：
    - 中文：保留原中文字
    - 英文：統一轉大寫，例如 a001、ABC001 都歸 A 系列
    - 數字：依第一個數字分類
    - 開頭若是空白或符號，會繼續往後找中文、英文或數字
    - 完全找不到則歸「其他」
    """
    for char in (text or "").strip():
        if re.match(r"[\u3400-\u4DBF\u4E00-\u9FFF]", char):
            return char
        if char.isascii() and char.isalpha():
            return char.upper()
        if char.isdigit():
            return char
    return "其他"


# 相容舊版函式名稱
def first_chinese_character(text: str) -> str:
    return get_series_key(text)


def natural_sort_key(text: str):
    """自然排序：新2 會排在 新10 前面。"""
    parts = re.split(r"(\d+)", text or "")
    return [int(p) if p.isdigit() else p.casefold() for p in parts]


def export_field_value(profile: Profile, field_key: str) -> str:
    if field_key == "series":
        return get_series_key(profile.name)
    return str(getattr(profile, field_key, "") or "")


def write_xlsx(path: str, headers: List[str], rows: List[List[str]]) -> None:
    """使用標準庫產生真正的 XLSX，不需額外安裝 openpyxl。"""
    def xml_text(value: Any) -> str:
        text = str(value or "")
        # XML 1.0 不允許大部分控制字元。
        text = "".join(
            char for char in text
            if char in "\t\n\r" or ord(char) >= 32
        )
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    all_rows = [headers] + rows
    sheet_rows = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            number = column_index
            letters = ""
            while number:
                number, remainder = divmod(number - 1, 26)
                letters = chr(65 + remainder) + letters
            style = ' s="1"' if row_index == 1 else ""
            cells.append(
                f'<c r="{letters}{row_index}" t="inlineStr"{style}>'
                f'<is><t xml:space="preserve">{xml_text(value)}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData><autoFilter ref="A1:'
        f'{chr(64 + len(headers))}{len(all_rows)}"/></worksheet>'
    )

    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="AdsPower環境清單" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        "xl/styles.xml": '<?xml version="1.0" encoding="UTF-8"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font/><font><b/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
            '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
            '<cellXfs count="2"><xf/><xf fontId="1" applyFont="1"/></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            '</styleSheet>',
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as workbook:
        for part_name, content in parts.items():
            workbook.writestr(part_name, content.encode("utf-8"))


class AdsPowerAPI:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.session = requests.Session()

    @property
    def headers(self) -> Dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error = ""

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    params=params,
                    json=json_data,
                    timeout=25,
                )
                response.raise_for_status()
                result = response.json()

                if result.get("code") == 0:
                    time.sleep(REQUEST_INTERVAL)
                    return result

                msg = str(result.get("msg", "未知 API 錯誤"))
                last_error = msg
                lower_msg = msg.lower()

                if (
                    "too many" in lower_msg
                    or "request per second" in lower_msg
                    or "frequency" in lower_msg
                    or "頻繁" in msg
                ):
                    wait_seconds = min(2.0 * attempt, 8.0)
                    time.sleep(wait_seconds)
                    continue

                raise RuntimeError(msg)

            except (requests.RequestException, ValueError) as exc:
                last_error = str(exc)
                if attempt < MAX_RETRIES:
                    time.sleep(min(1.5 * attempt, 6.0))
                    continue
                raise RuntimeError(
                    f"無法連接 AdsPower Local API：{last_error}\n"
                    f"請確認 AdsPower 已開啟、Local API 已啟用，網址與 API Key 正確。"
                ) from exc

        raise RuntimeError(f"API 請求失敗：{last_error}")

    def get_all_profiles(self) -> List[Profile]:
        profiles: List[Profile] = []
        page = 1

        while True:
            result = self._request(
                "GET",
                "/api/v1/user/list",
                params={"page": page, "page_size": PAGE_SIZE},
            )
            data = result.get("data") or {}
            rows = data.get("list") or []

            for row in rows:
                profile = Profile.from_dict(row)
                if profile.user_id:
                    profiles.append(profile)

            if len(rows) < PAGE_SIZE:
                break

            page += 1
            if page > 10000:
                raise RuntimeError("翻頁數異常，為避免無限讀取已停止。")

        return profiles

    def get_all_groups(self) -> List[Dict[str, str]]:
        groups: List[Dict[str, str]] = []
        page = 1
        while True:
            result = self._request(
                "GET", "/api/v1/group/list",
                params={"page": page, "page_size": PAGE_SIZE},
            )
            data = result.get("data") or {}
            rows = data.get("list") or []
            for row in rows:
                group_id = str(row.get("group_id", "")).strip()
                group_name = str(row.get("group_name", "")).strip()
                if group_id:
                    groups.append({"group_id": group_id, "group_name": group_name or f"群組 {group_id}"})
            if len(rows) < PAGE_SIZE:
                break
            page += 1
            if page > 10000:
                raise RuntimeError("群組翻頁數異常，已停止讀取。")
        unique = {g["group_id"]: g for g in groups}
        return sorted(unique.values(), key=lambda g: natural_sort_key(g["group_name"]))

    def move_profiles_to_group(self, user_ids: List[str], group_id: str) -> None:
        if not user_ids:
            raise ValueError("沒有可轉移的環境。")
        self._request(
            "POST", "/api/v1/user/regroup",
            json_data={"user_ids": user_ids, "group_id": str(group_id)},
        )

    @staticmethod
    def _is_browser_kernel_updating_error(error: Exception) -> bool:
        """判斷 AdsPower 是否只是正在下載／更新瀏覽器核心。"""
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "waiting for download",
                "is updating",
                "downloading browser",
                "downloading kernel",
                "等待下載",
                "等待下载",
                "正在更新",
                "正在下載",
                "正在下载",
            )
        )

    def start_profile(
        self,
        user_id: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, Any]:
        """啟動指定環境；瀏覽器核心更新中時最多自動等待 10 分鐘。"""
        if not user_id:
            raise ValueError("Profile ID 不可空白。")

        max_update_attempts = 120
        retry_interval = 5
        for attempt in range(1, max_update_attempts + 1):
            try:
                result = self._request(
                    "GET",
                    "/api/v1/browser/start",
                    params={
                        "user_id": user_id,
                        "open_tabs": 0,
                    },
                )
                return result.get("data") or {}
            except RuntimeError as exc:
                if not self._is_browser_kernel_updating_error(exc):
                    raise
                if attempt >= max_update_attempts:
                    raise RuntimeError(
                        "SunBrowser 核心更新等待超過 10 分鐘。"
                        "請到 AdsPower 檢查下載狀態後再試。"
                    ) from exc
                if progress_callback:
                    progress_callback(attempt, max_update_attempts, str(exc))
                time.sleep(retry_interval)

        raise RuntimeError("SunBrowser 核心更新逾時。")

    def get_profile_status(self, user_id: str) -> Dict[str, Any]:
        """查詢指定環境的開啟狀態與除錯連線資訊。"""
        if not user_id:
            raise ValueError("Profile ID 不可空白。")
        result = self._request(
            "GET", "/api/v1/browser/active", params={"user_id": user_id}
        )
        return result.get("data") or {}

    def get_local_active_profile_ids(self):
        """取得目前電腦上所有已開啟環境的 Profile ID。"""
        result = self._request("GET", "/api/v1/browser/local-active")
        data = result.get("data") or {}
        return {
            str(item.get("user_id", "")).strip()
            for item in (data.get("list") or [])
            if str(item.get("user_id", "")).strip()
        }

    def stop_profile(self, user_id: str) -> None:
        """關閉指定 AdsPower 環境。"""
        if not user_id:
            raise ValueError("Profile ID 不可空白。")
        self._request(
            "GET", "/api/v1/browser/stop", params={"user_id": user_id}
        )

    def delete_profiles(self, user_ids: List[str]) -> None:
        """刪除 AdsPower 環境；官方 API 單批最多 100 個。"""
        if not user_ids:
            raise ValueError("沒有可刪除的環境。")
        if len(user_ids) > 100:
            raise ValueError("單批刪除最多 100 個環境。")
        self._request(
            "POST",
            "/api/v1/user/delete",
            json_data={"user_ids": user_ids},
        )

    def rename_profile(self, user_id: str, new_name: str) -> None:
        self._request(
            "POST",
            "/api/v1/user/update",
            json_data={"user_id": user_id, "name": new_name},
        )


class RenameDialog(tk.Toplevel):
    def __init__(self, parent, old_name: str):
        super().__init__(parent)
        self.result: Optional[str] = None
        self.title("環境更名")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="目前名稱：").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=old_name, font=("Microsoft JhengHei UI", 11, "bold")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 12)
        )

        ttk.Label(frame, text="新名稱：").grid(row=2, column=0, sticky="w")
        self.entry = ttk.Entry(frame, width=42)
        self.entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 14))
        self.entry.insert(0, old_name)
        self.entry.select_range(0, tk.END)

        ttk.Button(frame, text="確定更名", command=self.confirm).grid(
            row=4, column=0, padx=(0, 8)
        )
        ttk.Button(frame, text="取消", command=self.destroy).grid(row=4, column=1)

        self.bind("<Return>", lambda _e: self.confirm())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.entry.focus_set()
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx()+160}+{parent.winfo_rooty()+120}")

    def confirm(self):
        value = self.entry.get().strip()
        if not value:
            messagebox.showwarning("名稱不可空白", "請輸入新環境名稱。", parent=self)
            return
        if len(value) > 100:
            messagebox.showwarning("名稱過長", "AdsPower 環境名稱最多 100 個字元。", parent=self)
            return
        self.result = value
        self.destroy()


class ExportDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("匯出設定")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="檔案格式：").grid(row=0, column=0, sticky="w")
        self.format_var = tk.StringVar(value="CSV (.csv)")
        format_box = ttk.Combobox(
            frame,
            textvariable=self.format_var,
            values=("CSV (.csv)", "Excel (.xlsx)", "TXT (.txt)"),
            state="readonly",
            width=24,
        )
        format_box.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 12))

        ttk.Label(frame, text="選擇匯出欄位：").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 5)
        )
        self.field_vars = {}
        for index, (key, label) in enumerate(EXPORT_FIELDS):
            variable = tk.BooleanVar(value=True)
            self.field_vars[key] = variable
            ttk.Checkbutton(frame, text=label, variable=variable).grid(
                row=2 + index // 2,
                column=index % 2,
                columnspan=1,
                sticky="w",
                padx=(0, 24),
                pady=2,
            )

        controls_row = 6
        ttk.Button(frame, text="全選", command=lambda: self.set_all_fields(True)).grid(
            row=controls_row, column=0, sticky="w", pady=(12, 14)
        )
        ttk.Button(frame, text="取消全選", command=lambda: self.set_all_fields(False)).grid(
            row=controls_row, column=1, sticky="w", pady=(12, 14)
        )

        ttk.Button(frame, text="下一步：選擇儲存位置", command=self.confirm).grid(
            row=controls_row + 1, column=0, columnspan=2, sticky="ew", padx=(0, 8)
        )
        ttk.Button(frame, text="取消", command=self.destroy).grid(
            row=controls_row + 1, column=2
        )

        self.bind("<Return>", lambda _e: self.confirm())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx()+220}+{parent.winfo_rooty()+130}")

    def set_all_fields(self, selected: bool):
        for variable in self.field_vars.values():
            variable.set(selected)

    def confirm(self):
        selected_fields = [
            key for key, _label in EXPORT_FIELDS if self.field_vars[key].get()
        ]
        if not selected_fields:
            messagebox.showwarning("尚未選擇欄位", "請至少選擇一個匯出欄位。", parent=self)
            return

        format_key = {
            "CSV (.csv)": "csv",
            "Excel (.xlsx)": "xlsx",
            "TXT (.txt)": "txt",
        }[self.format_var.get()]
        self.result = (format_key, selected_fields)
        self.destroy()


class AdsPowerManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x720")
        self.minsize(920, 600)

        self.profiles: List[Profile] = []
        self.series_map: Dict[str, List[Profile]] = {}
        self.visible_profiles: List[Profile] = []
        self.active_profile_ids = set()
        self.task_queue: "queue.Queue[tuple]" = queue.Queue()

        self.base_url_var = tk.StringVar(value=DEFAULT_BASE_URL)
        self.api_key_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="請先填入 API Key，再按「讀取所有環境」。")
        self.count_var = tk.StringVar(value="尚未讀取")

        self.load_config()
        self.build_ui()
        self.search_var.trace_add("write", lambda *_: self.refresh_profile_list())
        self.after(150, self.process_task_queue)

    def build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        top = ttk.LabelFrame(self, text="AdsPower Local API 設定", padding=10)
        top.pack(fill="x", padx=12, pady=(12, 6))

        ttk.Label(top, text="API 網址").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.base_url_var, width=36).grid(
            row=0, column=1, padx=(6, 14), sticky="ew"
        )
        ttk.Label(top, text="API Key").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.api_key_var, width=48, show="●").grid(
            row=0, column=3, padx=(6, 10), sticky="ew"
        )
        ttk.Button(top, text="儲存設定", command=self.save_config).grid(row=0, column=4)
        top.columnconfigure(3, weight=1)

        toolbar = ttk.Frame(self, padding=(12, 4))
        toolbar.pack(fill="x")

        self.refresh_btn = ttk.Button(
            toolbar, text="讀取所有環境", command=self.load_profiles_async
        )
        self.refresh_btn.pack(side="left", padx=(0, 6))
        ttk.Button(toolbar, text="打開環境", command=self.open_selected_profiles).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="視窗到最前", command=self.bring_selected_profile_to_front).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="關閉環境", command=self.close_selected_profiles).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="更名", command=self.rename_selected).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="批次依序更名", command=self.batch_rename).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="批次修改名稱", command=self.batch_modify_names).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="大量轉移群組", command=self.move_profiles_dialog).pack(
            side="left", padx=3
        )
        ttk.Button(
            toolbar, text="轉移選取環境", command=self.move_selected_profiles_dialog
        ).pack(side="left", padx=3)
        ttk.Button(toolbar, text="刪除環境", command=self.delete_selected_profiles).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="查看回收站", command=self.open_trash).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="檢查重複名稱", command=self.check_duplicates).pack(
            side="left", padx=3
        )
        ttk.Button(toolbar, text="匯出", command=self.export_profiles).pack(
            side="left", padx=3
        )

        ttk.Label(toolbar, text="搜尋：").pack(side="left", padx=(22, 4))
        ttk.Entry(toolbar, textvariable=self.search_var, width=26).pack(side="left")
        ttk.Label(toolbar, textvariable=self.count_var).pack(side="right")

        content = ttk.Panedwindow(self, orient="horizontal")
        content.pack(fill="both", expand=True, padx=12, pady=6)

        left_frame = ttk.LabelFrame(content, text="系列（中文／英文／數字）", padding=6)
        right_frame = ttk.LabelFrame(
            content, text="環境清單（雙擊或按 F2 可更名）", padding=6
        )
        content.add(left_frame, weight=1)
        content.add(right_frame, weight=4)

        self.series_list = tk.Listbox(
            left_frame,
            exportselection=False,
            font=("Microsoft JhengHei UI", 11),
            activestyle="dotbox",
        )
        series_scroll = ttk.Scrollbar(
            left_frame, orient="vertical", command=self.series_list.yview
        )
        self.series_list.configure(yscrollcommand=series_scroll.set)
        self.series_list.pack(side="left", fill="both", expand=True)
        series_scroll.pack(side="right", fill="y")
        self.series_list.bind("<<ListboxSelect>>", self.on_series_selected)

        columns = ("index", "name", "group", "ip", "serial", "user_id")
        self.tree = ttk.Treeview(
            right_frame, columns=columns, show="headings", selectmode="extended"
        )
        self.tree.heading("index", text="序號")
        self.tree.heading("name", text="環境名稱")
        self.tree.heading("group", text="群組")
        self.tree.heading("ip", text="IP／代理主機")
        self.tree.heading("serial", text="AdsPower 編號")
        self.tree.heading("user_id", text="Profile ID")

        self.tree.column("index", width=60, anchor="center", stretch=False)
        self.tree.column("name", width=300, anchor="w")
        self.tree.column("group", width=140, anchor="w")
        self.tree.column("ip", width=160, anchor="center")
        self.tree.column("serial", width=110, anchor="center")
        self.tree.column("user_id", width=180, anchor="w")

        tree_y = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree.yview)
        tree_x = ttk.Scrollbar(right_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_y.set, xscrollcommand=tree_x.set)
        self.tree.tag_configure("active", background="#D9F0FF")
        self.tree.tag_configure("inactive", background="#FFE1EA")

        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_y.grid(row=0, column=1, sticky="ns")
        tree_x.grid(row=1, column=0, sticky="ew")
        right_frame.rowconfigure(0, weight=1)
        right_frame.columnconfigure(0, weight=1)

        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<Return>", lambda _e: self.rename_selected())
        self.tree.bind("<F2>", lambda _e: self.rename_selected())
        self.tree.bind("<Control-c>", lambda _e: self.copy_selected_ip())
        self.tree.bind("<Control-o>", lambda _e: self.open_selected_profiles())
        self.tree.bind("<Control-w>", lambda _e: self.close_selected_profiles())
        self.tree.bind("<Button-3>", self.show_context_menu)

        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(label="打開環境", command=self.open_selected_profiles)
        self.context_menu.add_command(label="視窗彈到最前", command=self.bring_selected_profile_to_front)
        self.context_menu.add_command(label="關閉選取環境", command=self.close_selected_profiles)
        self.context_menu.add_command(label="更名（F2）", command=self.rename_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="複製環境名稱", command=self.copy_selected_name)
        self.context_menu.add_command(label="複製 Profile ID", command=self.copy_selected_id)
        self.context_menu.add_command(label="複製 IP／代理主機", command=self.copy_selected_ip)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="批次修改本系列名稱", command=self.batch_modify_names)
        self.context_menu.add_command(
            label="轉移選取環境到群組", command=self.move_selected_profiles_dialog
        )
        self.context_menu.add_command(label="大量轉移群組", command=self.move_profiles_dialog)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="刪除選取環境", command=self.delete_selected_profiles)

        status = ttk.Frame(self, padding=(12, 4, 12, 10))
        status.pack(fill="x")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=(0, 10))
        ttk.Label(status, textvariable=self.status_var).pack(side="left", fill="x")

    def api(self) -> AdsPowerAPI:
        base_url = self.base_url_var.get().strip()
        api_key = self.api_key_var.get().strip()
        if not base_url:
            raise ValueError("API 網址不可空白。")
        return AdsPowerAPI(base_url, api_key)

    def load_config(self):
        if not CONFIG_FILE.exists():
            return
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            self.base_url_var.set(data.get("base_url", DEFAULT_BASE_URL))
            self.api_key_var.set(data.get("api_key", ""))
        except Exception:
            pass

    def save_config(self):
        data = {
            "base_url": self.base_url_var.get().strip() or DEFAULT_BASE_URL,
            "api_key": self.api_key_var.get().strip(),
        }
        try:
            CONFIG_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            messagebox.showinfo("已儲存", f"設定已儲存至：\n{CONFIG_FILE}")
        except Exception as exc:
            messagebox.showerror("儲存失敗", str(exc))

    def set_busy(self, busy: bool, message: str = ""):
        self.refresh_btn.configure(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()
        if message:
            self.status_var.set(message)

    def run_background(self, func, success_callback=None):
        def worker():
            try:
                result = func()
                self.task_queue.put(("success", result, success_callback))
            except Exception as exc:
                self.task_queue.put(("error", exc, None))

        threading.Thread(target=worker, daemon=True).start()

    def process_task_queue(self):
        try:
            while True:
                kind, payload, callback = self.task_queue.get_nowait()
                self.set_busy(False)
                if kind == "error":
                    self.status_var.set("操作失敗")
                    messagebox.showerror("操作失敗", str(payload))
                elif callback:
                    callback(payload)
        except queue.Empty:
            pass
        self.after(150, self.process_task_queue)

    def load_profiles_async(self):
        try:
            api = self.api()
        except Exception as exc:
            messagebox.showerror("設定錯誤", str(exc))
            return

        self.set_busy(True, "正在讀取 AdsPower 所有環境，請勿關閉 AdsPower…")
        def action():
            profiles = api.get_all_profiles()
            active_ids = api.get_local_active_profile_ids()
            return profiles, active_ids

        self.run_background(action, self.on_profiles_loaded)

    def on_profiles_loaded(self, result):
        profiles, active_ids = result
        self.active_profile_ids = set(active_ids)
        self.profiles = sorted(profiles, key=lambda p: natural_sort_key(p.name))
        self.rebuild_series_map()
        self.populate_series_list()
        self.status_var.set(
            f"讀取完成，共 {len(self.profiles)} 個環境；"
            f"已開啟 {len(self.active_profile_ids)} 個。"
        )
        self.count_var.set(f"全部：{len(self.profiles)} 個")

    def rebuild_series_map(self):
        grouped: Dict[str, List[Profile]] = defaultdict(list)
        for profile in self.profiles:
            grouped[first_chinese_character(profile.name)].append(profile)

        for key in grouped:
            grouped[key].sort(key=lambda p: natural_sort_key(p.name))

        def series_sort_key(value: str):
            if value == "其他":
                return (3, "")
            if value.isascii() and value.isalpha():
                return (1, value)
            if value.isdigit():
                return (2, int(value))
            return (0, natural_sort_key(value))

        keys = sorted(grouped, key=series_sort_key)
        self.series_map = {key: grouped[key] for key in keys}

    def populate_series_list(self):
        self.series_list.delete(0, tk.END)
        self.series_list.insert(tk.END, f"全部環境（{len(self.profiles)}）")
        for series, rows in self.series_map.items():
            self.series_list.insert(tk.END, f"{series} 系列（{len(rows)}）")
        if self.series_list.size():
            self.series_list.selection_set(0)
            self.series_list.activate(0)
        self.refresh_profile_list()

    def current_series_key(self) -> Optional[str]:
        selection = self.series_list.curselection()
        if not selection or selection[0] == 0:
            return None
        keys = list(self.series_map.keys())
        index = selection[0] - 1
        return keys[index] if 0 <= index < len(keys) else None

    def on_series_selected(self, _event=None):
        self.refresh_profile_list()

    def refresh_profile_list(self):
        series = self.current_series_key()
        rows = self.profiles if series is None else self.series_map.get(series, [])

        keyword = self.search_var.get().strip().casefold()
        if keyword:
            rows = [
                p
                for p in rows
                if keyword in p.name.casefold()
                or keyword in p.group_name.casefold()
                or keyword in p.user_id.casefold()
                or keyword in p.serial_number.casefold()
                or keyword in p.proxy_ip.casefold()
            ]

        self.visible_profiles = list(rows)
        for item in self.tree.get_children():
            self.tree.delete(item)

        for index, profile in enumerate(self.visible_profiles, start=1):
            self.tree.insert(
                "",
                "end",
                iid=profile.user_id,
                values=(
                    index,
                    profile.name,
                    profile.group_name,
                    profile.proxy_ip or "未設定",
                    profile.serial_number,
                    profile.user_id,
                ),
                tags=(
                    "active"
                    if profile.user_id in self.active_profile_ids
                    else "inactive",
                ),
            )

        label = "全部環境" if series is None else f"{series} 系列"
        self.count_var.set(f"{label}：{len(self.visible_profiles)} 個")

    def selected_profile(self) -> Optional[Profile]:
        selection = self.tree.selection()
        if not selection:
            return None
        user_id = selection[0]
        return next((p for p in self.profiles if p.user_id == user_id), None)

    def selected_profiles(self) -> List[Profile]:
        selected_ids = set(self.tree.selection())
        return [p for p in self.profiles if p.user_id in selected_ids]

    def ordered_selected_profiles(self) -> List[Profile]:
        """依右側清單的顯示順序取得選取環境。"""
        selected_ids = set(self.tree.selection())
        return [
            profile
            for profile in self.visible_profiles
            if profile.user_id in selected_ids
        ]

    @staticmethod
    def focus_window_by_debug_address(debug_address: str) -> bool:
        """依 Chromium 除錯連接埠找到視窗，還原並帶到最前。"""
        match = re.search(r":(\d+)$", debug_address or "")
        if not match:
            return False
        debug_port = match.group(1)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        target_pid = None

        # Newer AdsPower builds may omit the debug-port command-line flag.
        # Resolve the owning browser PID from the actual listening TCP port first.
        try:
            netstat = subprocess.run(
                ["netstat.exe", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creation_flags,
            )
            for line in netstat.stdout.splitlines():
                columns = line.split()
                if len(columns) < 5 or columns[0].upper() != "TCP":
                    continue
                local_address = columns[1]
                state = columns[3].upper()
                pid_text = columns[4]
                if (
                    local_address.rsplit(":", 1)[-1] == debug_port
                    and state == "LISTENING"
                    and pid_text.isdigit()
                ):
                    target_pid = int(pid_text)
                    break
        except (OSError, subprocess.SubprocessError):
            pass

        # Fallback for older launch modes that retain the command-line flag.
        if target_pid is None:
            powershell = (
                "$p=Get-CimInstance Win32_Process | Where-Object {"
                f"$_.CommandLine -match '--remote-debugging-port={debug_port}(\\s|$)'"
                "} | Select-Object -First 1 -ExpandProperty ProcessId; if($p){$p}"
            )
            try:
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", powershell],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    creationflags=creation_flags,
                )
                pid_lines = result.stdout.strip().splitlines()
                if pid_lines and pid_lines[-1].strip().isdigit():
                    target_pid = int(pid_lines[-1].strip())
            except (OSError, subprocess.SubprocessError):
                pass

        if target_pid is None:
            return False

        user32 = ctypes.windll.user32
        handles = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type
        def enum_callback(hwnd, _lparam):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == target_pid and user32.IsWindowVisible(hwnd):
                handles.append(hwnd)
            return True

        user32.EnumWindows(enum_callback, 0)
        if not handles:
            return False

        hwnd = handles[0]
        SW_RESTORE = 9
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        user32.ShowWindow(hwnd, SW_RESTORE)
        # 短暫置頂再取消置頂，可穩定跨進程拉到前景，不會長期置頂。
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        user32.SetForegroundWindow(hwnd)
        return True

    def bring_selected_profile_to_front(self):
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("尚未選擇環境", "請先選擇一個已開啟的環境。")
            return

        api = self.api()
        self.set_busy(True, f"正在將「{profile.name}」帶到最前面…")

        def action():
            status = api.get_profile_status(profile.user_id)
            if str(status.get("status", "")).casefold() != "active":
                raise RuntimeError("此環境尚未開啟。")
            ws = status.get("ws") or {}
            debug_address = str(ws.get("selenium", "")) if isinstance(ws, dict) else ""
            if not self.focus_window_by_debug_address(debug_address):
                raise RuntimeError("找不到此環境對應的 Windows 視窗。")
            return profile.name

        self.run_background(action, self.on_bring_profile_to_front_success)

    def on_bring_profile_to_front_success(self, name):
        self.status_var.set(f"已將環境帶到最前：{name}")

    def close_selected_profiles(self):
        profiles = self.ordered_selected_profiles()
        if not profiles:
            messagebox.showwarning(
                "尚未選擇環境",
                "請先在右側選取要關閉的環境。\n\n"
                "可按住 Ctrl 或 Shift 選取多個環境。",
            )
            return

        preview = "\n".join(f"• {profile.name}" for profile in profiles[:15])
        if len(profiles) > 15:
            preview += f"\n……其餘 {len(profiles) - 15} 個"
        if not messagebox.askyesno(
            "確認關閉環境",
            f"即將關閉 {len(profiles)} 個環境：\n\n{preview}\n\n"
            "請先確認網頁中未儲存的資料已處理。確定繼續？",
        ):
            return

        api = self.api()
        self.set_busy(True, f"正在關閉 {len(profiles)} 個環境…")

        def action():
            completed = []
            inactive = []
            failed = []
            for index, profile in enumerate(profiles):
                try:
                    status = api.get_profile_status(profile.user_id)
                    if str(status.get("status", "")).casefold() != "active":
                        inactive.append(profile.name)
                    else:
                        api.stop_profile(profile.user_id)
                        completed.append(profile.user_id)
                except Exception as exc:
                    failed.append((profile.name, str(exc)))
                if index < len(profiles) - 1:
                    time.sleep(0.7)
            return completed, inactive, failed

        self.run_background(action, self.on_close_profiles_success)

    def on_close_profiles_success(self, result):
        completed, inactive, failed = result
        self.active_profile_ids.difference_update(completed)
        self.refresh_profile_list()
        self.status_var.set(
            f"關閉完成：成功 {len(completed)}，原本未開啟 {len(inactive)}，失敗 {len(failed)}。"
        )
        if failed:
            details = "\n".join(f"{name}：{error}" for name, error in failed[:15])
            messagebox.showwarning(
                "關閉環境完成",
                f"成功：{len(completed)}\n原本未開啟：{len(inactive)}\n"
                f"失敗：{len(failed)}\n\n{details}",
            )
        elif len(completed) + len(inactive) > 1:
            messagebox.showinfo(
                "關閉環境完成",
                f"成功關閉：{len(completed)}\n原本未開啟：{len(inactive)}",
            )

    def open_selected_profiles(self):
        """
        打開右側目前選取的 AdsPower 環境。
        支援單選或 Ctrl / Shift 多選。
        """
        selected = self.selected_profiles()
        if not selected:
            messagebox.showwarning(
                "尚未選擇環境",
                "請先在右側選取要打開的環境。\n\n"
                "多選方式：\n"
                "• 按住 Ctrl 點選多個環境\n"
                "• 按住 Shift 選取連續範圍",
            )
            return

        # 依右側畫面順序處理
        selected_ids = set(self.tree.selection())
        ordered = [
            profile
            for profile in self.visible_profiles
            if profile.user_id in selected_ids
        ]
        if ordered:
            selected = ordered

        if len(selected) > 1:
            preview = "\n".join(f"• {p.name}" for p in selected[:15])
            if len(selected) > 15:
                preview += f"\n……其餘 {len(selected) - 15} 個"

            if not messagebox.askyesno(
                "確認打開多個環境",
                f"即將依序打開 {len(selected)} 個環境：\n\n"
                f"{preview}\n\n"
                "環境數量較多時，電腦可能會暫時變慢。\n"
                "確定繼續嗎？",
            ):
                return

        self.execute_open_profiles(selected)

    def execute_open_profiles(self, profiles: List[Profile]):
        api = self.api()
        self.set_busy(True, f"正在打開 {len(profiles)} 個環境…")

        def action():
            completed: List[tuple] = []
            failed: List[tuple] = []

            for index, profile in enumerate(profiles, start=1):
                try:
                    def show_kernel_wait(attempt, max_attempts, _error, name=profile.name):
                        elapsed_seconds = attempt * 5
                        self.after(
                            0,
                            lambda n=name, seconds=elapsed_seconds: self.status_var.set(
                                f"{n}：SunBrowser 正在下載／更新，已等待 {seconds} 秒，完成後會自動開啟…"
                            ),
                        )

                    data = api.start_profile(
                        profile.user_id,
                        progress_callback=show_kernel_wait,
                    )
                    selenium_address = ""
                    ws_data = data.get("ws") or {}
                    if isinstance(ws_data, dict):
                        selenium_address = str(ws_data.get("selenium", ""))

                    completed.append(
                        (
                            profile.user_id,
                            profile.name,
                            selenium_address,
                        )
                    )
                except Exception as exc:
                    failed.append((profile.name, str(exc)))

                # 多開時降低 AdsPower Local API 限流機率
                if index < len(profiles):
                    time.sleep(1.0)

            return completed, failed

        self.run_background(action, self.on_open_profiles_success)

    def on_open_profiles_success(self, result):
        completed, failed = result
        self.active_profile_ids.update(item[0] for item in completed)
        self.refresh_profile_list()

        if len(completed) == 1 and not failed:
            _user_id, name, _selenium_address = completed[0]
            self.status_var.set(f"環境已打開：{name}")
            return

        if failed:
            failed_text = "\n".join(
                f"{name}：{error}" for name, error in failed[:15]
            )
            if len(failed) > 15:
                failed_text += f"\n……其餘 {len(failed) - 15} 個"

            messagebox.showwarning(
                "打開環境完成",
                f"成功打開：{len(completed)} 個\n"
                f"打開失敗：{len(failed)} 個\n\n"
                f"{failed_text}",
            )
        else:
            messagebox.showinfo(
                "打開環境完成",
                f"已成功打開 {len(completed)} 個環境。",
            )

        self.status_var.set(
            f"打開環境完成：成功 {len(completed)}，失敗 {len(failed)}。"
        )

    def delete_selected_profiles(self):
        """
        刪除右側選取的單一或多個環境。
        安全機制：
        1. 顯示刪除清單並確認
        2. 必須手動輸入 DELETE
        3. 每批最多 100 個
        4. 批次失敗時自動逐筆重試，列出失敗項目
        """
        selected = self.selected_profiles()
        if not selected:
            messagebox.showwarning(
                "尚未選擇環境",
                "請先在右側選取要刪除的環境。\n\n"
                "多選方式：\n"
                "• 按住 Ctrl 點選多個環境\n"
                "• 按住 Shift 選取連續範圍",
            )
            return

        # 去除重複並保持畫面順序
        selected_ids = set(self.tree.selection())
        ordered = [p for p in self.visible_profiles if p.user_id in selected_ids]
        if ordered:
            selected = ordered

        preview_lines = []
        for profile in selected[:20]:
            group_text = profile.group_name or "未分組"
            preview_lines.append(f"• {profile.name}　[{group_text}]")

        preview = "\n".join(preview_lines)
        if len(selected) > 20:
            preview += f"\n……其餘 {len(selected) - 20} 個環境"

        first_confirm = messagebox.askyesno(
            "確認刪除環境",
            f"即將永久刪除 {len(selected)} 個 AdsPower 環境：\n\n"
            f"{preview}\n\n"
            "刪除後無法由本程式復原。\n"
            "確定要繼續嗎？",
            icon="warning",
        )
        if not first_confirm:
            return

        confirm_text = simpledialog.askstring(
            "第二次確認",
            f"這是永久刪除操作。\n\n"
            f"要刪除的環境數量：{len(selected)}\n\n"
            "請輸入大寫 DELETE 才會執行：",
            parent=self,
        )

        if confirm_text != "DELETE":
            if confirm_text is not None:
                messagebox.showinfo(
                    "已取消",
                    "輸入內容不是 DELETE，沒有刪除任何環境。",
                )
            return

        self.execute_delete_profiles(selected)

    def execute_delete_profiles(self, profiles: List[Profile]):
        api = self.api()
        self.set_busy(True, f"正在刪除 {len(profiles)} 個環境，請勿關閉程式…")

        def action():
            completed_ids: List[str] = []
            failed: List[tuple] = []
            chunk_size = 100

            for start in range(0, len(profiles), chunk_size):
                chunk = profiles[start : start + chunk_size]
                user_ids = [p.user_id for p in chunk]

                try:
                    api.delete_profiles(user_ids)
                    completed_ids.extend(user_ids)
                except Exception:
                    # 整批失敗時逐個重試，保留精確失敗清單
                    for profile in chunk:
                        try:
                            api.delete_profiles([profile.user_id])
                            completed_ids.append(profile.user_id)
                        except Exception as single_exc:
                            failed.append((profile.name, str(single_exc)))

                time.sleep(REQUEST_INTERVAL)

            return completed_ids, failed

        self.run_background(action, self.on_delete_profiles_success)

    def on_delete_profiles_success(self, result):
        completed_ids, failed = result
        completed_set = set(completed_ids)

        if completed_set:
            self.profiles = [
                profile
                for profile in self.profiles
                if profile.user_id not in completed_set
            ]
            self.rebuild_series_map()
            self.populate_series_list()

        if failed:
            failed_text = "\n".join(
                f"{name}：{error}" for name, error in failed[:15]
            )
            if len(failed) > 15:
                failed_text += f"\n……其餘 {len(failed) - 15} 個"

            messagebox.showwarning(
                "刪除環境完成",
                f"成功刪除：{len(completed_ids)} 個\n"
                f"刪除失敗：{len(failed)} 個\n\n"
                f"{failed_text}",
            )
        else:
            messagebox.showinfo(
                "刪除環境完成",
                f"已成功刪除 {len(completed_ids)} 個環境。",
            )

        self.status_var.set(
            f"刪除完成：成功 {len(completed_ids)}，失敗 {len(failed)}。"
        )

    def move_selected_profiles_dialog(self):
        """將右側目前選取的環境轉移到指定群組。"""
        selected_ids = set(self.tree.selection())
        profiles = [
            profile
            for profile in self.visible_profiles
            if profile.user_id in selected_ids
        ]
        if not profiles:
            messagebox.showwarning(
                "尚未選擇環境",
                "請先在右側選取要轉移的環境。\n\n"
                "多選方式：\n• 按住 Ctrl 點選多個環境\n• 按住 Shift 選取連續範圍",
            )
            return

        dialog = tk.Toplevel(self)
        dialog.title("轉移選取環境")
        dialog.geometry("620x560")
        dialog.minsize(540, 480)
        dialog.transient(self)
        dialog.grab_set()

        main = ttk.Frame(dialog, padding=16)
        main.pack(fill="both", expand=True)
        ttk.Label(
            main,
            text=f"轉移已選取的 {len(profiles)} 個環境",
            font=("Microsoft JhengHei UI", 14, "bold"),
        ).pack(anchor="w")

        preview = "、".join(profile.name for profile in profiles[:8])
        if len(profiles) > 8:
            preview += f"……其餘 {len(profiles) - 8} 個"
        ttk.Label(main, text=preview, wraplength=570).pack(
            anchor="w", fill="x", pady=(5, 12)
        )

        ttk.Label(main, text="搜尋目標群組：").pack(anchor="w")
        search_var = tk.StringVar()
        search_entry = ttk.Entry(main, textvariable=search_var)
        search_entry.pack(fill="x", pady=(3, 8))

        tree_frame = ttk.Frame(main)
        tree_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            tree_frame,
            columns=("name", "count", "id"),
            show="headings",
            selectmode="browse",
        )
        tree.heading("name", text="目標群組")
        tree.heading("count", text="目前數量")
        tree.heading("id", text="群組 ID")
        tree.column("name", width=270)
        tree.column("count", width=90, anchor="center")
        tree.column("id", width=130, anchor="center")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=(12, 0))
        status_var = tk.StringVar(value="正在讀取群組…")
        ttk.Label(bottom, textvariable=status_var).pack(side="left")
        groups_cache: List[Dict[str, str]] = []

        def refresh_groups(*_args):
            previous = tree.selection()
            previous_id = previous[0] if previous else None
            tree.delete(*tree.get_children())
            keyword = search_var.get().strip().casefold()
            counts = Counter(str(profile.group_id) for profile in self.profiles)
            for group in groups_cache:
                group_id = str(group["group_id"])
                group_name = group["group_name"]
                if keyword and keyword not in group_name.casefold() and keyword not in group_id.casefold():
                    continue
                tree.insert(
                    "", "end", iid=group_id,
                    values=(group_name, counts.get(group_id, 0), group_id),
                )
            if previous_id and tree.exists(previous_id):
                tree.selection_set(previous_id)
                tree.focus(previous_id)

        search_var.trace_add("write", refresh_groups)

        def confirm_move():
            selection = tree.selection()
            if not selection:
                messagebox.showwarning(
                    "尚未選擇群組", "請選擇要搬入的目標群組。", parent=dialog
                )
                return
            target_id = selection[0]
            target = next(
                (g for g in groups_cache if str(g["group_id"]) == target_id), None
            )
            if not target:
                return

            already_there = [p for p in profiles if str(p.group_id) == target_id]
            moving = [p for p in profiles if str(p.group_id) != target_id]
            if not moving:
                messagebox.showinfo(
                    "無需轉移",
                    f"選取的環境都已在「{target['group_name']}」。",
                    parent=dialog,
                )
                return

            note = ""
            if already_there:
                note = f"\n其中 {len(already_there)} 個已在目標群組，會自動略過。"
            if not messagebox.askyesno(
                "確認轉移選取環境",
                f"目標群組：{target['group_name']}\n"
                f"即將轉移：{len(moving)} 個環境{note}\n\n確定繼續？",
                parent=dialog,
            ):
                return
            dialog.destroy()
            self.execute_group_move(moving, target_id, target["group_name"])

        move_btn = ttk.Button(
            bottom, text="轉移到選取群組", command=confirm_move, state="disabled"
        )
        move_btn.pack(side="right")
        ttk.Button(bottom, text="取消", command=dialog.destroy).pack(
            side="right", padx=(0, 8)
        )
        tree.bind("<Double-1>", lambda _event: confirm_move())

        def on_groups_loaded(groups):
            if not dialog.winfo_exists():
                return
            groups_cache.extend(groups)
            refresh_groups()
            status_var.set(f"已讀取 {len(groups_cache)} 個群組")
            move_btn.configure(state="normal" if groups_cache else "disabled")
            search_entry.focus_set()

        def load_groups_worker():
            try:
                groups = self.api().get_all_groups()
                dialog.after(0, lambda: on_groups_loaded(groups))
            except Exception as exc:
                dialog.after(
                    0,
                    lambda error=exc: messagebox.showerror(
                        "群組讀取失敗", str(error), parent=dialog
                    ),
                )

        threading.Thread(target=load_groups_worker, daemon=True).start()

    def move_profiles_dialog(self):
        """
        群組對群組大量轉移：
        選擇來源群組後，自動計算該群組內全部環境，
        再選擇目標群組，一鍵全部轉移。
        """
        if not self.profiles:
            messagebox.showwarning("尚未讀取", "請先按「讀取所有環境」。")
            return

        dialog = tk.Toplevel(self)
        dialog.title("群組對群組大量轉移")
        dialog.geometry("760x620")
        dialog.minsize(700, 560)
        dialog.transient(self)
        dialog.grab_set()

        main = ttk.Frame(dialog, padding=16)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="群組對群組大量轉移",
            font=("Microsoft JhengHei UI", 15, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            main,
            text="操作方式：先選來源群組，再選目標群組，來源群組內的全部環境會一次轉移。",
        ).pack(anchor="w", pady=(4, 12))

        content = ttk.Frame(main)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(1, weight=1)

        ttk.Label(
            content,
            text="1. 來源群組（要搬走環境的群組）",
            font=("Microsoft JhengHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))

        ttk.Label(
            content,
            text="2. 目標群組（要搬進去的群組）",
            font=("Microsoft JhengHei UI", 11, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))

        source_frame = ttk.Frame(content)
        source_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(6, 0))
        source_frame.columnconfigure(0, weight=1)
        source_frame.rowconfigure(1, weight=1)

        target_frame = ttk.Frame(content)
        target_frame.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(6, 0))
        target_frame.columnconfigure(0, weight=1)
        target_frame.rowconfigure(1, weight=1)

        source_search_var = tk.StringVar()
        target_search_var = tk.StringVar()

        ttk.Entry(source_frame, textvariable=source_search_var).grid(
            row=0, column=0, sticky="ew", pady=(0, 6)
        )
        ttk.Entry(target_frame, textvariable=target_search_var).grid(
            row=0, column=0, sticky="ew", pady=(0, 6)
        )

        source_tree = ttk.Treeview(
            source_frame,
            columns=("name", "count", "id"),
            show="headings",
            selectmode="browse",
        )
        source_tree.heading("name", text="來源群組")
        source_tree.heading("count", text="環境數量")
        source_tree.heading("id", text="群組 ID")
        source_tree.column("name", width=170)
        source_tree.column("count", width=85, anchor="center")
        source_tree.column("id", width=110, anchor="center")

        source_scroll = ttk.Scrollbar(
            source_frame, orient="vertical", command=source_tree.yview
        )
        source_tree.configure(yscrollcommand=source_scroll.set)
        source_tree.grid(row=1, column=0, sticky="nsew")
        source_scroll.grid(row=1, column=1, sticky="ns")

        target_tree = ttk.Treeview(
            target_frame,
            columns=("name", "count", "id"),
            show="headings",
            selectmode="browse",
        )
        target_tree.heading("name", text="目標群組")
        target_tree.heading("count", text="目前數量")
        target_tree.heading("id", text="群組 ID")
        target_tree.column("name", width=170)
        target_tree.column("count", width=85, anchor="center")
        target_tree.column("id", width=110, anchor="center")

        target_scroll = ttk.Scrollbar(
            target_frame, orient="vertical", command=target_tree.yview
        )
        target_tree.configure(yscrollcommand=target_scroll.set)
        target_tree.grid(row=1, column=0, sticky="nsew")
        target_scroll.grid(row=1, column=1, sticky="ns")

        summary_box = ttk.LabelFrame(main, text="轉移內容確認", padding=12)
        summary_box.pack(fill="x", pady=(14, 8))

        summary_var = tk.StringVar(value="請先選擇來源群組與目標群組。")
        ttk.Label(
            summary_box,
            textvariable=summary_var,
            font=("Microsoft JhengHei UI", 11),
            justify="left",
        ).pack(anchor="w")

        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=(8, 0))

        status_var = tk.StringVar(value="正在讀取群組…")
        ttk.Label(bottom, textvariable=status_var).pack(side="left")

        groups_cache: List[Dict[str, str]] = []

        def count_by_group():
            counts = defaultdict(int)
            for profile in self.profiles:
                counts[str(profile.group_id)] += 1
            return counts

        def refresh_tree(tree, keyword, is_source):
            selected_before = tree.selection()
            selected_id = selected_before[0] if selected_before else None

            for item in tree.get_children():
                tree.delete(item)

            counts = count_by_group()
            keyword = keyword.strip().casefold()

            for group in groups_cache:
                gid = str(group["group_id"])
                gname = group["group_name"]
                if keyword and keyword not in gname.casefold() and keyword not in gid.casefold():
                    continue

                tree.insert(
                    "",
                    "end",
                    iid=gid,
                    values=(gname, counts.get(gid, 0), gid),
                )

            if selected_id and tree.exists(selected_id):
                tree.selection_set(selected_id)
                tree.focus(selected_id)

        def refresh_source(*_args):
            refresh_tree(source_tree, source_search_var.get(), True)
            update_summary()

        def refresh_target(*_args):
            refresh_tree(target_tree, target_search_var.get(), False)
            update_summary()

        source_search_var.trace_add("write", refresh_source)
        target_search_var.trace_add("write", refresh_target)

        def selected_group(tree):
            selection = tree.selection()
            if not selection:
                return None
            gid = selection[0]
            return next(
                (g for g in groups_cache if str(g["group_id"]) == str(gid)),
                None,
            )

        def update_summary(_event=None):
            source = selected_group(source_tree)
            target = selected_group(target_tree)

            if not source or not target:
                summary_var.set("請先選擇來源群組與目標群組。")
                move_btn.configure(state="disabled")
                return

            source_id = str(source["group_id"])
            target_id = str(target["group_id"])
            source_profiles = [
                p for p in self.profiles if str(p.group_id) == source_id
            ]

            if source_id == target_id:
                summary_var.set(
                    f"來源與目標都是「{source['group_name']}」，請選擇不同群組。"
                )
                move_btn.configure(state="disabled")
                return

            summary_var.set(
                f"來源群組：{source['group_name']}（{len(source_profiles)} 個環境）\n"
                f"目標群組：{target['group_name']}\n"
                f"執行後會把來源群組內的 {len(source_profiles)} 個環境全部轉移。"
            )

            move_btn.configure(
                state="normal" if source_profiles else "disabled"
            )

        source_tree.bind("<<TreeviewSelect>>", update_summary)
        target_tree.bind("<<TreeviewSelect>>", update_summary)

        def on_groups_loaded(groups):
            groups_cache.extend(groups)
            refresh_source()
            refresh_target()
            status_var.set(f"已讀取 {len(groups_cache)} 個群組")
            update_summary()

        def load_groups_worker():
            try:
                groups = self.api().get_all_groups()
                dialog.after(0, lambda: on_groups_loaded(groups))
            except Exception as exc:
                dialog.after(
                    0,
                    lambda e=exc: messagebox.showerror(
                        "群組讀取失敗", str(e), parent=dialog
                    ),
                )

        def confirm_move():
            source = selected_group(source_tree)
            target = selected_group(target_tree)

            if not source:
                messagebox.showwarning(
                    "未選來源群組",
                    "請在左側選擇要搬走環境的來源群組。",
                    parent=dialog,
                )
                return

            if not target:
                messagebox.showwarning(
                    "未選目標群組",
                    "請在右側選擇環境要搬入的目標群組。",
                    parent=dialog,
                )
                return

            source_id = str(source["group_id"])
            target_id = str(target["group_id"])

            if source_id == target_id:
                messagebox.showwarning(
                    "群組相同",
                    "來源群組與目標群組不能相同。",
                    parent=dialog,
                )
                return

            profiles = [
                p for p in self.profiles if str(p.group_id) == source_id
            ]

            if not profiles:
                messagebox.showinfo(
                    "來源群組沒有環境",
                    f"「{source['group_name']}」目前沒有任何環境。",
                    parent=dialog,
                )
                return

            preview = "\n".join(p.name for p in profiles[:15])
            if len(profiles) > 15:
                preview += f"\n……其餘 {len(profiles) - 15} 個"

            confirm_text = (
                f"來源群組：{source['group_name']}\n"
                f"目標群組：{target['group_name']}\n\n"
                f"即將轉移：{len(profiles)} 個環境\n\n"
                f"{preview}\n\n"
                "確定把來源群組內的全部環境轉移嗎？"
            )

            if not messagebox.askyesno(
                "確認群組大量轉移",
                confirm_text,
                parent=dialog,
            ):
                return

            dialog.destroy()
            self.execute_group_move(
                profiles,
                target_id,
                target["group_name"],
            )

        move_btn = ttk.Button(
            bottom,
            text="把來源群組全部轉移到目標群組",
            command=confirm_move,
            state="disabled",
        )
        move_btn.pack(side="right")

        ttk.Button(
            bottom,
            text="取消",
            command=dialog.destroy,
        ).pack(side="right", padx=(0, 8))

        threading.Thread(target=load_groups_worker, daemon=True).start()

    def execute_group_move(self, profiles: List[Profile], target_group_id: str, target_group_name: str):
        api = self.api()
        self.set_busy(True, f"正在把 {len(profiles)} 個環境轉移到「{target_group_name}」…")

        def action():
            completed_ids: List[str] = []
            failed: List[tuple] = []
            chunk_size = 100
            for start in range(0, len(profiles), chunk_size):
                chunk = profiles[start:start + chunk_size]
                ids = [p.user_id for p in chunk]
                try:
                    api.move_profiles_to_group(ids, target_group_id)
                    completed_ids.extend(ids)
                except Exception:
                    for profile in chunk:
                        try:
                            api.move_profiles_to_group([profile.user_id], target_group_id)
                            completed_ids.append(profile.user_id)
                        except Exception as single_exc:
                            failed.append((profile.name, str(single_exc)))
                time.sleep(REQUEST_INTERVAL)
            return completed_ids, failed, target_group_id, target_group_name

        self.run_background(action, self.on_group_move_success)

    def on_group_move_success(self, result):
        completed_ids, failed, group_id, group_name = result
        completed_set = set(completed_ids)
        for profile in self.profiles:
            if profile.user_id in completed_set:
                profile.group_id = str(group_id)
                profile.group_name = group_name
        self.refresh_profile_list()
        if failed:
            failed_text = "\n".join(f"{name}：{error}" for name, error in failed[:15])
            if len(failed) > 15:
                failed_text += f"\n……其餘 {len(failed)-15} 個"
            messagebox.showwarning("大量轉移完成", f"目標群組：{group_name}\n成功：{len(completed_ids)} 個\n失敗：{len(failed)} 個\n\n{failed_text}")
        else:
            messagebox.showinfo("大量轉移完成", f"已成功把 {len(completed_ids)} 個環境轉移到「{group_name}」。")
        self.status_var.set(f"群組轉移完成：成功 {len(completed_ids)}，失敗 {len(failed)}。")

    def rename_selected(self):
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("尚未選擇", "請先在右側點選一個環境。")
            return

        dialog = RenameDialog(self, profile.name)
        self.wait_window(dialog)
        new_name = dialog.result

        if not new_name or new_name == profile.name:
            return

        duplicate = next(
            (
                p
                for p in self.profiles
                if p.user_id != profile.user_id and p.name == new_name
            ),
            None,
        )
        if duplicate:
            answer = messagebox.askyesno(
                "名稱已存在",
                f"已有另一個環境使用名稱「{new_name}」。\n仍要繼續更名嗎？",
            )
            if not answer:
                return

        old_name = profile.name
        api = self.api()
        self.set_busy(True, f"正在更名：{old_name} → {new_name}")

        def action():
            api.rename_profile(profile.user_id, new_name)
            return profile.user_id, old_name, new_name

        self.run_background(action, self.on_rename_success)

    def on_rename_success(self, result):
        user_id, old_name, new_name = result
        for profile in self.profiles:
            if profile.user_id == user_id:
                profile.name = new_name
                break

        self.profiles.sort(key=lambda p: natural_sort_key(p.name))
        self.rebuild_series_map()
        current_series = get_series_key(new_name)
        self.populate_series_list()

        keys = list(self.series_map.keys())
        if current_series in keys:
            index = keys.index(current_series) + 1
            self.series_list.selection_clear(0, tk.END)
            self.series_list.selection_set(index)
            self.series_list.activate(index)
            self.refresh_profile_list()

        if self.tree.exists(user_id):
            self.tree.selection_set(user_id)
            self.tree.focus(user_id)
            self.tree.see(user_id)

        self.status_var.set(f"更名成功：{old_name} → {new_name}")

    def batch_rename(self):
        series = self.current_series_key()
        if series is None:
            messagebox.showwarning(
                "請先選擇系列", "批次更名前，請先在左側選擇一個系列。"
            )
            return

        rows = list(self.series_map.get(series, []))
        if not rows:
            return

        prefix = simpledialog.askstring(
            "批次依序更名",
            f"目前系列：{series}（{len(rows)} 個）\n\n"
            "請輸入新名稱前綴：\n"
            "例如輸入「新」，結果會是 新001、新002…",
            initialvalue=series,
            parent=self,
        )
        if prefix is None:
            return
        prefix = prefix.strip()
        if not prefix:
            messagebox.showwarning("前綴不可空白", "請輸入名稱前綴。")
            return

        start = simpledialog.askinteger(
            "起始號碼",
            "請輸入起始號碼：",
            initialvalue=1,
            minvalue=0,
            parent=self,
        )
        if start is None:
            return

        digits = simpledialog.askinteger(
            "號碼位數",
            "請輸入號碼位數：\n例如 3 會產生 001、002、003",
            initialvalue=3,
            minvalue=1,
            maxvalue=10,
            parent=self,
        )
        if digits is None:
            return

        preview = []
        rename_jobs = []
        for offset, profile in enumerate(rows):
            new_name = f"{prefix}{start + offset:0{digits}d}"
            rename_jobs.append((profile, new_name))
            if len(preview) < 8:
                preview.append(f"{profile.name}  →  {new_name}")

        text = "\n".join(preview)
        if len(rename_jobs) > 8:
            text += f"\n……其餘 {len(rename_jobs)-8} 個"

        if not messagebox.askyesno(
            "確認批次更名",
            f"即將修改 {len(rename_jobs)} 個環境：\n\n{text}\n\n確定繼續嗎？",
        ):
            return

        self.execute_batch_jobs(rename_jobs, "批次依序更名")

    def on_batch_rename_success(self, result):
        completed, failed = result
        name_by_id = {user_id: new_name for user_id, _old, new_name in completed}

        for profile in self.profiles:
            if profile.user_id in name_by_id:
                profile.name = name_by_id[profile.user_id]

        self.profiles.sort(key=lambda p: natural_sort_key(p.name))
        self.rebuild_series_map()
        self.populate_series_list()

        if failed:
            failed_text = "\n".join(f"{name}：{error}" for name, error in failed[:10])
            messagebox.showwarning(
                "批次更名完成",
                f"成功：{len(completed)} 個\n失敗：{len(failed)} 個\n\n{failed_text}",
            )
        else:
            messagebox.showinfo(
                "批次更名完成", f"已成功更名 {len(completed)} 個環境。"
            )
        self.status_var.set(
            f"批次更名完成：成功 {len(completed)}，失敗 {len(failed)}。"
        )

    def show_context_menu(self, event):
        row_id = self.tree.identify_row(event.y)
        if row_id:
            self.tree.selection_set(row_id)
            self.tree.focus(row_id)
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def on_tree_double_click(self, event):
        """雙擊 IP 欄時複製 IP；雙擊其他欄位時維持原本的更名功能。"""
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id:
            return

        self.tree.selection_set(row_id)
        self.tree.focus(row_id)
        # Treeview 欄位順序：#1 序號、#2 名稱、#3 群組、#4 IP。
        if column_id == "#4":
            self.copy_selected_ip()
        else:
            self.rename_selected()
        return "break"

    def copy_to_clipboard(self, value: str, label: str):
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()
        self.status_var.set(f"已複製{label}：{value}")

    def copy_selected_name(self):
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("尚未選擇", "請先選擇一個環境。")
            return
        self.copy_to_clipboard(profile.name, "環境名稱")

    def copy_selected_id(self):
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("尚未選擇", "請先選擇一個環境。")
            return
        self.copy_to_clipboard(profile.user_id, " Profile ID")

    def copy_selected_ip(self):
        profile = self.selected_profile()
        if not profile:
            messagebox.showwarning("尚未選擇", "請先選擇一個環境。")
            return
        if not profile.proxy_ip:
            messagebox.showinfo("沒有 IP", "此環境沒有讀取到代理 IP／代理主機。")
            return
        self.copy_to_clipboard(profile.proxy_ip, " IP／代理主機")

    def batch_modify_names(self):
        series = self.current_series_key()
        if series is None:
            messagebox.showwarning(
                "請先選擇系列",
                "請先在左側選擇一個系列，再進行批次修改。"
            )
            return

        rows = list(self.series_map.get(series, []))
        if not rows:
            return

        dialog = tk.Toplevel(self)
        dialog.title("批次修改名稱")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"目前系列：{series}　共 {len(rows)} 個環境",
            font=("Microsoft JhengHei UI", 11, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        mode_var = tk.StringVar(value="prefix")
        ttk.Radiobutton(frame, text="名稱前面加入文字", variable=mode_var, value="prefix").grid(
            row=1, column=0, columnspan=2, sticky="w"
        )
        ttk.Radiobutton(frame, text="名稱後面加入文字", variable=mode_var, value="suffix").grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        ttk.Radiobutton(frame, text="搜尋並取代文字", variable=mode_var, value="replace").grid(
            row=3, column=0, columnspan=2, sticky="w"
        )
        ttk.Radiobutton(frame, text="移除指定文字", variable=mode_var, value="remove").grid(
            row=4, column=0, columnspan=2, sticky="w"
        )

        ttk.Label(frame, text="文字／搜尋內容：").grid(row=5, column=0, sticky="w", pady=(12, 2))
        value_entry = ttk.Entry(frame, width=42)
        value_entry.grid(row=6, column=0, columnspan=2, sticky="ew")

        ttk.Label(frame, text="取代成（只有搜尋取代需要）：").grid(
            row=7, column=0, sticky="w", pady=(10, 2)
        )
        replacement_entry = ttk.Entry(frame, width=42)
        replacement_entry.grid(row=8, column=0, columnspan=2, sticky="ew")

        def confirm():
            value = value_entry.get()
            replacement = replacement_entry.get()
            mode = mode_var.get()

            if not value:
                messagebox.showwarning("內容不可空白", "請輸入要加入、搜尋或移除的文字。", parent=dialog)
                return

            jobs = []
            for profile in rows:
                if mode == "prefix":
                    new_name = value + profile.name
                elif mode == "suffix":
                    new_name = profile.name + value
                elif mode == "replace":
                    new_name = profile.name.replace(value, replacement)
                else:
                    new_name = profile.name.replace(value, "")

                new_name = new_name.strip()
                if new_name and new_name != profile.name:
                    jobs.append((profile, new_name))

            if not jobs:
                messagebox.showinfo("沒有變更", "沒有任何環境名稱需要修改。", parent=dialog)
                return

            preview = "\n".join(
                f"{profile.name}  →  {new_name}" for profile, new_name in jobs[:10]
            )
            if len(jobs) > 10:
                preview += f"\n……其餘 {len(jobs) - 10} 個"

            if not messagebox.askyesno(
                "確認批次修改",
                f"即將修改 {len(jobs)} 個環境：\n\n{preview}\n\n確定繼續嗎？",
                parent=dialog,
            ):
                return

            dialog.destroy()
            self.execute_batch_jobs(jobs, "批次修改名稱")

        ttk.Button(frame, text="開始修改", command=confirm).grid(
            row=9, column=0, pady=(16, 0), padx=(0, 8)
        )
        ttk.Button(frame, text="取消", command=dialog.destroy).grid(
            row=9, column=1, pady=(16, 0)
        )
        value_entry.focus_set()
        dialog.bind("<Escape>", lambda _e: dialog.destroy())

    def execute_batch_jobs(self, jobs, operation_name="批次更名"):
        api = self.api()
        self.set_busy(True, f"正在{operation_name}，共 {len(jobs)} 個…")

        def action():
            completed = []
            failed = []
            for profile, new_name in jobs:
                try:
                    api.rename_profile(profile.user_id, new_name)
                    completed.append((profile.user_id, profile.name, new_name))
                except Exception as exc:
                    failed.append((profile.name, str(exc)))
                time.sleep(REQUEST_INTERVAL)
            return completed, failed

        self.run_background(action, self.on_batch_rename_success)

    def check_duplicates(self):
        if not self.profiles:
            messagebox.showwarning("尚未讀取", "請先讀取所有環境。")
            return

        counts = Counter(p.name for p in self.profiles)
        duplicated_names = sorted(
            [name for name, count in counts.items() if count > 1],
            key=natural_sort_key,
        )

        if not duplicated_names:
            messagebox.showinfo("檢查完成", "沒有發現重複的環境名稱。")
            return

        lines = []
        for name in duplicated_names[:50]:
            matched = [p for p in self.profiles if p.name == name]
            groups = "、".join(
                f"{p.group_name or '未分組'} / {p.user_id}" for p in matched
            )
            lines.append(f"「{name}」共 {len(matched)} 個\n  {groups}")

        if len(duplicated_names) > 50:
            lines.append(f"\n其餘 {len(duplicated_names)-50} 組未顯示。")

        messagebox.showwarning(
            "發現重複名稱",
            f"共有 {len(duplicated_names)} 組重複名稱：\n\n" + "\n\n".join(lines),
        )

    def open_trash(self):
        """開啟 AdsPower 客戶端以查看回收站。

        AdsPower 公開 Local API 目前未提供回收站清單的查詢介面，
        因此使用官方客戶端顯示，避免依賴不穩定的私有 API。
        """
        candidates = [
            Path(r"C:\Program Files\AdsPower Global\AdsPower Global.exe"),
            Path(r"C:\Program Files (x86)\AdsPower Global\AdsPower Global.exe"),
            Path.home()
            / "AppData"
            / "Local"
            / "Programs"
            / "AdsPower Global"
            / "AdsPower Global.exe",
        ]
        executable = next((path for path in candidates if path.is_file()), None)

        if executable is None:
            messagebox.showwarning(
                "找不到 AdsPower",
                "找不到 AdsPower Global 主程式。\n\n"
                "請手動開啟 AdsPower，然後點選左側選單的「回收站」。",
            )
            return

        try:
            subprocess.Popen([str(executable)])
            self.status_var.set("已開啟 AdsPower；請在左側選單點選「回收站」。")
            messagebox.showinfo(
                "查看回收站",
                "AdsPower 已開啟。\n\n"
                "請在 AdsPower 左側選單點選「回收站」，"
                "即可查看已刪除的環境、刪除人與刪除日期，也可進行還原。\n\n"
                "提醒：回收站環境預設保留 30 天，查看與還原需要對應的帳號權限。",
            )
        except OSError as exc:
            messagebox.showerror("開啟失敗", f"無法開啟 AdsPower：\n{exc}")

    def export_profiles(self):
        if not self.profiles:
            messagebox.showwarning("尚未讀取", "請先讀取所有環境。")
            return

        dialog = ExportDialog(self)
        self.wait_window(dialog)
        if not dialog.result:
            return

        format_key, selected_fields = dialog.result
        format_settings = {
            "csv": (".csv", "CSV 檔案", "*.csv"),
            "xlsx": (".xlsx", "Excel 檔案", "*.xlsx"),
            "txt": (".txt", "TXT 檔案", "*.txt"),
        }
        extension, file_label, file_pattern = format_settings[format_key]
        default_name = time.strftime(f"AdsPower環境清單_%Y%m%d_%H%M%S{extension}")
        path = filedialog.asksaveasfilename(
            title="匯出 AdsPower 環境清單",
            defaultextension=extension,
            initialfile=default_name,
            filetypes=[(file_label, file_pattern)],
        )
        if not path:
            return

        try:
            label_by_key = dict(EXPORT_FIELDS)
            headers = [label_by_key[key] for key in selected_fields]
            rows = [
                [export_field_value(profile, key) for key in selected_fields]
                for profile in self.profiles
            ]

            if format_key == "xlsx":
                write_xlsx(path, headers, rows)
            else:
                delimiter = "," if format_key == "csv" else "\t"
                with open(path, "w", newline="", encoding="utf-8-sig") as file:
                    writer = csv.writer(file, delimiter=delimiter)
                    writer.writerow(headers)
                    writer.writerows(rows)

            messagebox.showinfo("匯出完成", f"已匯出 {len(self.profiles)} 個環境：\n{path}")
            self.status_var.set(f"{file_label} 匯出完成：{path}")
        except Exception as exc:
            messagebox.showerror("匯出失敗", str(exc))


if __name__ == "__main__":
    try:
        app = AdsPowerManagerApp()
        app.mainloop()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("程式啟動失敗", str(exc))
        root.destroy()
