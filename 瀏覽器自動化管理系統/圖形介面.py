"""Facebook 養號十一項任務的單一 GUI 控制台。"""

from __future__ import annotations

import logging
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from 環境管理介面 import AdsPowerClient, ProfileInfo
from 設定 import CONFIG
from 媒體來源 import MediaPool
from 個人資料工具 import profile_matches_search, sort_profiles_by_number


POST_MEDIA_MODE_LABELS = {
    "相片／影片隨機": "random",
    "固定相片／影片": "fixed",
}
POST_MEDIA_MODE_VALUES = {value: label for label, value in POST_MEDIA_MODE_LABELS.items()}


@dataclass
class GuiSettings:
    profiles: list[ProfileInfo]
    professional_mode: bool
    profile_setup: bool
    avatar: bool
    banner: bool
    profile_name: bool
    facebook_language: bool
    avatar_dir: str
    banner_dir: str
    name_text_file: str
    facebook_language_target: str
    pin: bool
    add_friend: bool
    add_friend_count: int
    confirm_friend: bool
    confirm_friend_count: int
    post: bool
    post_text_file: str
    post_media_enabled: bool
    post_media_mode: str
    post_random_media_dir: str
    post_fixed_media_file: str
    reels: bool
    reels_dry_run: bool
    reels_comment: bool
    reels_comment_mode: str
    reels_comment_text_file: str
    reels_video_dir: str
    reels_text_file: str
    browse_like: bool
    like_count: int
    fanpage_message: bool
    query_chats: bool
    reply_chats: bool
    fanpage_url_file: str
    fanpage_text_file: str
    fanpage_mode: str
    fanpage_max_urls: int
    chat_database: str
    query_max_chats: int
    query_unread_only: bool
    reply_text_file: str
    reply_mode: str
    reply_max_count: int
    reply_max_retries: int
    telegram_report: bool
    lead_report: bool
    task_order: list[str]
    loop_count: int
    worker_count: int
    shuffle: bool
    close_after: bool
    bring_to_front: bool


class QueueLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue) -> None:
        super().__init__()
        self.events = events
        self.setFormatter(logging.Formatter(
            "[%(asctime)s] [%(levelname)s] %(message)s", "%H:%M:%S"
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.events.put(("log", self.format(record)))
        except Exception:
            pass


class SettingsWindow:
    def __init__(
        self,
        api: AdsPowerClient,
        runner: Callable[[GuiSettings, threading.Event], None],
    ) -> None:
        self.api = api
        self.runner = runner
        self.groups: dict[str, str] = {}
        self.profiles: list[ProfileInfo] = []
        self.visible_profiles: list[ProfileInfo] = []
        self.checked_profile_ids: set[str] = set()
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.schedule_file = Path(__file__).with_name("schedules.json")
        self.reels_settings_file = Path(__file__).with_name("reels_settings.json")
        self.gui_settings_file = Path(__file__).with_name("gui_settings.json")
        self.smart_schedule_file = Path(__file__).with_name("smart_schedule_settings.json")
        self.schedules: list[dict] = self._load_schedules()
        self.last_schedule_minute: dict[str, str] = {}
        self.last_run_fingerprint: str = ""
        self.last_run_started_at: float = 0.0

        self.root = tk.Tk()
        self.root.title("Facebook 養號十二項任務 GUI V4.6.37")
        self.root.geometry("1420x940")
        self.root.minsize(1060, 720)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        self._configure_styles(style)

        self.status = tk.StringVar(value="正在讀取 AdsPower 群組……")
        self.group_var = tk.StringVar()
        self.search_var = tk.StringVar()
        self.loop_var = tk.StringVar(value="1")
        self.worker_count_var = tk.StringVar(value="1")
        self.shuffle_var = tk.BooleanVar(value=False)
        self.close_after_var = tk.BooleanVar(value=False)
        self.bring_to_front_var = tk.BooleanVar(value=True)
        self.adspower_api_key = tk.StringVar(value=str(CONFIG.adspower.api_key or ""))
        self.api_key_status = tk.StringVar(value="尚未測試 API Key")
        self.task_vars = {
            "professional": tk.BooleanVar(value=False),
            "profile_setup": tk.BooleanVar(value=False),
            "avatar": tk.BooleanVar(value=False),
            "banner": tk.BooleanVar(value=False),
            "profile_name": tk.BooleanVar(value=False),
            "facebook_language": tk.BooleanVar(value=False),
            "pin": tk.BooleanVar(value=False),
            "add_friend": tk.BooleanVar(value=False),
            "confirm_friend": tk.BooleanVar(value=False),
            "post": tk.BooleanVar(value=False),
            "reels": tk.BooleanVar(value=False),
            "reels_comment": tk.BooleanVar(value=False),
            "browse_like": tk.BooleanVar(value=False),
            "fanpage_message": tk.BooleanVar(value=False),
            "query_chats": tk.BooleanVar(value=False),
            "reply_chats": tk.BooleanVar(value=False),
        }
        base = Path(__file__).resolve().parent
        self.fanpage_url_file = tk.StringVar(value=str(base / "kolurl.txt"))
        self.fanpage_text_file = tk.StringVar(value=str(base / "文二.txt"))
        self.fanpage_mode = tk.StringVar(value="txt")
        self.fanpage_max_urls = tk.StringVar(value="1")
        self.chat_database = tk.StringVar(value=str(base / "chat_tasks.db"))
        self.query_max_chats = tk.StringVar(value="5")
        self.query_unread_only = tk.BooleanVar(value=False)
        self.reply_text_file = tk.StringVar(value=str(base / "文一.txt"))
        self.reply_mode = tk.StringVar(value="txt")
        self.reply_max_count = tk.StringVar(value="3")
        self.reply_max_retries = tk.StringVar(value="3")
        self.telegram_report = tk.BooleanVar(value=True)
        self.lead_report = tk.BooleanVar(value=False)
        self.task_order = [
            "professional", "profile_setup", "avatar", "banner", "profile_name", "facebook_language", "pin", "confirm_friend", "post", "reels",
            "reels_comment", "browse_like", "add_friend", "fanpage_message", "query_chats", "reply_chats",
        ]
        reels_paths = self._load_reels_settings()
        self.post_text_file = tk.StringVar(value="")
        self.post_media_enabled = tk.BooleanVar(value=False)
        self.post_media_mode = tk.StringVar(value=POST_MEDIA_MODE_VALUES["random"])
        self.post_random_media_dir = tk.StringVar(value=str(Path.home() / "Desktop" / "view"))
        self.post_fixed_media_file = tk.StringVar(value="")
        self.reels_video_dir = tk.StringVar(
            value=reels_paths.get("video_dir", r"C:\Users\USER\Desktop\reelsv")
        )
        self.reels_text_file = tk.StringVar(
            value=reels_paths.get("text_file", r"C:\Users\USER\Desktop\reelsw.txt")
        )
        self.reels_dry_run = tk.BooleanVar(value=False)
        self.reels_comment_mode = tk.StringVar(
            value=reels_paths.get("comment_mode", "default")
        )
        self.avatar_dir = tk.StringVar(value=str(Path.home() / "Desktop" / "頭像圖片"))
        self.banner_dir = tk.StringVar(value=str(Path.home() / "Desktop" / "Banner"))
        self.name_text_file = tk.StringVar(value=str(Path.home() / "Desktop" / "名字.txt"))
        self.facebook_language_target = tk.StringVar(value="Filipino")
        self.reels_comment_text_file = tk.StringVar(
            value=reels_paths.get("comment_text_file", str(base / "reels_comment.txt"))
        )
        self.add_count = tk.StringVar(value="1")
        self.confirm_count = tk.StringVar(value="2")
        self.like_count = tk.StringVar(value="1")
        self.smart_start_var = tk.StringVar(value="08:00")
        self.smart_end_var = tk.StringVar(value="22:00")
        self.smart_max_workers_var = tk.StringVar(value="6")
        self.smart_result_var = tk.StringVar(
            value="先勾選環境與任務，再按「自動計算排程」。"
        )
        self.smart_task_seconds = self._load_smart_schedule_settings()
        self.smart_last_result: dict | None = None
        self.control_widgets: list[tk.Widget] = []

        self._restore_gui_settings()
        self.shuffle_var.set(False)
        self.api.set_api_key(self.adspower_api_key.get())
        self._build()
        self.search_var.trace_add("write", lambda *_: self._render_profiles())
        self.log_handler = QueueLogHandler(self.events)
        logging.getLogger().addHandler(self.log_handler)
        self.root.after(100, self._drain_events)
        self.root.after(1000, self._check_schedules)
        self._load_groups()

    def _load_schedules(self) -> list[dict]:
        try:
            if not self.schedule_file.exists():
                return []
            data = json.loads(self.schedule_file.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            for item in data:
                if isinstance(item, dict):
                    item.setdefault("type", "daily")
            return data
        except Exception as exc:
            logging.getLogger("main").warning("讀取排程設定失敗：%s", exc)
            return []

    def _restore_gui_settings(self) -> None:
        try:
            data = json.loads(self.gui_settings_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return
        for key, variable in self.task_vars.items():
            source_key = "professional_mode" if key == "professional" else key
            variable.set(bool(data.get(source_key, variable.get())))
        self.task_vars["profile_setup"].set(any(
            self.task_vars[key].get()
            for key in ("avatar", "banner", "profile_name", "facebook_language")
        ))
        mapping = {
            "add_friend_count": self.add_count,
            "confirm_friend_count": self.confirm_count,
            "like_count": self.like_count,
            "fanpage_url_file": self.fanpage_url_file,
            "fanpage_text_file": self.fanpage_text_file,
            "fanpage_mode": self.fanpage_mode,
            "fanpage_max_urls": self.fanpage_max_urls,
            "chat_database": self.chat_database,
            "query_max_chats": self.query_max_chats,
            "query_unread_only": self.query_unread_only,
            "reply_text_file": self.reply_text_file,
            "reply_mode": self.reply_mode,
            "reply_max_count": self.reply_max_count,
            "reply_max_retries": self.reply_max_retries,
            "telegram_report": self.telegram_report,
            "lead_report": self.lead_report,
            "loop_count": self.loop_var,
            "worker_count": self.worker_count_var,
            "shuffle": self.shuffle_var,
            "close_after": self.close_after_var,
            "bring_to_front": self.bring_to_front_var,
            "adspower_api_key": self.adspower_api_key,
            "post_text_file": self.post_text_file,
            "post_media_enabled": self.post_media_enabled,
            "post_random_media_dir": self.post_random_media_dir,
            "post_fixed_media_file": self.post_fixed_media_file,
            "reels_dry_run": self.reels_dry_run,
            "avatar_dir": self.avatar_dir,
            "banner_dir": self.banner_dir,
            "name_text_file": self.name_text_file,
            "facebook_language_target": self.facebook_language_target,
        }
        portable_files = {
            "fanpage_url_file": "kolurl.txt",
            "fanpage_text_file": "文二.txt",
            "chat_database": "chat_tasks.db",
            "reply_text_file": "文一.txt",
        }
        for key, variable in mapping.items():
            if key in data:
                value = data[key]
                if key in portable_files and isinstance(value, str):
                    candidate = Path(value)
                    if not candidate.exists():
                        local_path = Path(__file__).resolve().with_name(
                            portable_files[key]
                        )
                        logging.getLogger("main").info(
                            "設定中的舊路徑不存在，改用目前程式資料夾：%s",
                            local_path,
                        )
                        value = str(local_path)
                variable.set(value)
        stored_media_mode = str(data.get("post_media_mode", "random")).lower()
        self.post_media_mode.set(POST_MEDIA_MODE_VALUES.get(stored_media_mode, POST_MEDIA_MODE_VALUES["random"]))
        order = data.get("task_order")
        if isinstance(order, list) and set(order) == set(self.task_order):
            self.task_order = list(order)

    def _save_schedules(self) -> None:
        temp = self.schedule_file.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(self.schedules, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.schedule_file)

    def _load_reels_settings(self) -> dict:
        try:
            data = json.loads(self.reels_settings_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def _save_reels_settings(self) -> None:
        temp = self.reels_settings_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps({
            "video_dir": self.reels_video_dir.get().strip(),
            "text_file": self.reels_text_file.get().strip(),
            "comment_mode": self.reels_comment_mode.get(),
            "comment_text_file": self.reels_comment_text_file.get().strip(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.reels_settings_file)

    def _load_smart_schedule_settings(self) -> dict[str, float]:
        defaults = {
            "startup": 45, "professional": 180, "profile_setup": 0, "avatar": 120, "banner": 120, "profile_name": 90, "facebook_language": 90, "pin": 150,
            "add_friend": 45, "confirm_friend": 30, "post": 100, "reels": 300, "reels_comment": 45,
            "browse_like": 90, "fanpage_message": 75, "query_chats": 40,
            "reply_chats": 65, "close": 15, "buffer_percent": 15,
        }
        try:
            data = json.loads(self.smart_schedule_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in defaults:
                    if key in data:
                        defaults[key] = max(0.0, float(data[key]))
        except (FileNotFoundError, ValueError, TypeError, OSError):
            pass
        return defaults

    def _save_smart_schedule_settings(self) -> None:
        temp = self.smart_schedule_file.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(self.smart_task_seconds, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.smart_schedule_file)

    def _save_gui_settings(self, settings: GuiSettings) -> None:
        """原子保存十一項一般 GUI 設定，重新啟動後可完整還原。"""
        data = self._settings_to_dict(settings)
        data.pop("profiles", None)
        data["adspower_api_key"] = self.adspower_api_key.get().strip()
        temp = self.gui_settings_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.gui_settings_file)

    def _test_adspower_connection(self) -> None:
        api_key = self.adspower_api_key.get().strip()
        if not api_key:
            self.api_key_status.set("尚未輸入 API Key")
            messagebox.showwarning("AdsPower API Key", "請先輸入 AdsPower API Key。", parent=self.root)
            return
        self.api.set_api_key(api_key)
        CONFIG.adspower.api_key = api_key
        self.api_key_status.set("測試中……")

        def worker() -> None:
            try:
                self.api.test_connection()
                self.events.put(("api_key_test", True, "連線成功，API Key 可用"))
            except Exception as exc:
                self.events.put(("api_key_test", False, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _choose_text_path(self, variable: tk.StringVar, title: str) -> None:
        value = filedialog.askopenfilename(
            title=title, filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")]
        )
        if value:
            variable.set(value)

    def _choose_database_path(self) -> None:
        value = filedialog.asksaveasfilename(
            title="選擇待回覆 SQLite 資料庫", defaultextension=".db",
            filetypes=[("SQLite", "*.db"), ("所有檔案", "*.*")],
        )
        if value:
            self.chat_database.set(value)

    def _choose_avatar_dir(self) -> None:
        selected = filedialog.askdirectory(title="選擇頭像圖片資料夾")
        if selected:
            self.avatar_dir.set(selected)

    def _choose_name_text_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="選擇名字 TXT 檔案",
            filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")],
        )
        if selected:
            self.name_text_file.set(selected)

    def _toggle_profile_setup_children(self) -> None:
        enabled = self.task_vars["profile_setup"].get()
        state = "normal" if enabled else "disabled"
        if not enabled:
            for key in ("avatar", "banner", "profile_name", "facebook_language"):
                self.task_vars[key].set(False)
        for widget in getattr(self, "profile_setup_child_widgets", []):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _choose_banner_dir(self) -> None:
        selected = filedialog.askdirectory(title="選擇 Banner 圖片資料夾")
        if selected:
            self.banner_dir.set(selected)

    def _choose_post_random_media_dir(self) -> None:
        selected = filedialog.askdirectory(title="選擇 PO 文隨機相片／影片資料夾")
        if selected:
            self.post_random_media_dir.set(selected)

    def _choose_post_fixed_media_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="選擇 PO 文固定相片／影片",
            filetypes=[
                ("相片與影片", "*.jpg *.jpeg *.png *.webp *.gif *.heic *.heif *.mp4 *.m4v *.mov *.webm *.avi *.mkv *.3gp"),
                ("所有檔案", "*.*"),
            ],
        )
        if selected:
            self.post_fixed_media_file.set(selected)

    def _refresh_post_media_controls(self) -> None:
        enabled = bool(self.post_media_enabled.get()) and not self.running
        mode = POST_MEDIA_MODE_LABELS.get(self.post_media_mode.get(), "random")
        combo = getattr(self, "post_media_mode_combo", None)
        if combo is not None:
            combo.configure(state="readonly" if enabled else "disabled")
        for widget_name in ("post_random_media_entry", "post_random_media_button"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(state="normal" if enabled and mode == "random" else "disabled")
        for widget_name in ("post_fixed_media_entry", "post_fixed_media_button"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.configure(state="normal" if enabled and mode == "fixed" else "disabled")

    def _choose_reels_video_dir(self) -> None:
        value = filedialog.askdirectory(title="選擇 Reels 影片資料夾")
        if value:
            self.reels_video_dir.set(value)
            self._save_reels_settings()

    def _choose_reels_text_file(self) -> None:
        value = filedialog.askopenfilename(
            title="選擇 Reels 描述文字檔",
            filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")],
        )
        if value:
            self.reels_text_file.set(value)
            self._save_reels_settings()

    def _choose_reels_comment_text_file(self) -> None:
        value = filedialog.askopenfilename(
            title="選擇 Reels 留言文字檔",
            filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")],
        )
        if value:
            self.reels_comment_text_file.set(value)
            self._save_reels_settings()

    def _open_reels_diagnostics(self) -> None:
        folder = Path(__file__).with_name("diagnostics")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            messagebox.showerror("無法開啟", f"無法開啟診斷資料夾：\n{exc}")

    def _remember(self, widget):
        self.control_widgets.append(widget)
        return widget

    def _configure_styles(self, style: ttk.Style) -> None:
        """集中管理 GUI 視覺樣式，不影響任何任務或排程邏輯。"""
        colors = {
            "app": "#f3f6fa",
            "card": "#ffffff",
            "navy": "#17324d",
            "blue": "#2563a6",
            "blue_hover": "#1e548d",
            "teal": "#0f766e",
            "red": "#b42318",
            "red_hover": "#912018",
            "text": "#243447",
            "muted": "#667085",
            "border": "#d8e0ea",
            "selected": "#dcecff",
        }
        self.colors = colors
        self.root.configure(background=colors["app"])
        style.configure(".", font=("Microsoft JhengHei UI", 10), foreground=colors["text"])
        style.configure("App.TFrame", background=colors["app"])
        style.configure("Card.TFrame", background=colors["card"])
        style.configure(
            "Card.TLabelframe",
            background=colors["card"],
            bordercolor=colors["border"],
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=colors["card"],
            foreground=colors["navy"],
            font=("Microsoft JhengHei UI", 10, "bold"),
        )
        style.configure(
            "Section.TLabel",
            background=colors["card"],
            foreground=colors["navy"],
            font=("Microsoft JhengHei UI", 11, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=colors["card"],
            foreground=colors["muted"],
            font=("Microsoft JhengHei UI", 9),
        )
        style.configure(
            "HeaderTitle.TLabel",
            background=colors["navy"],
            foreground="#ffffff",
            font=("Microsoft JhengHei UI", 16, "bold"),
        )
        style.configure(
            "HeaderSub.TLabel",
            background=colors["navy"],
            foreground="#cfdeec",
            font=("Microsoft JhengHei UI", 9),
        )
        style.configure(
            "Primary.TButton",
            background=colors["blue"],
            foreground="#ffffff",
            bordercolor=colors["blue"],
            padding=(14, 7),
            font=("Microsoft JhengHei UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", "#9aa9ba"), ("pressed", colors["blue_hover"]),
                        ("active", colors["blue_hover"])],
            foreground=[("disabled", "#e7ecf2"), ("!disabled", "#ffffff")],
        )
        style.configure(
            "Danger.TButton",
            background=colors["red"],
            foreground="#ffffff",
            bordercolor=colors["red"],
            padding=(12, 7),
        )
        style.map(
            "Danger.TButton",
            background=[("pressed", colors["red_hover"]), ("active", colors["red_hover"])],
            foreground=[("!disabled", "#ffffff")],
        )
        style.configure("Secondary.TButton", padding=(12, 7))
        style.configure(
            "Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground=colors["text"],
            rowheight=29,
            bordercolor=colors["border"],
            lightcolor=colors["border"],
            darkcolor=colors["border"],
        )
        style.map(
            "Treeview",
            background=[("selected", colors["selected"])],
            foreground=[("selected", colors["navy"])],
        )
        style.configure(
            "Treeview.Heading",
            background="#e9eff6",
            foreground=colors["navy"],
            font=("Microsoft JhengHei UI", 9, "bold"),
            padding=(8, 7),
            relief="flat",
        )
        style.map("Treeview.Heading", background=[("active", "#dce5ef")])
        style.configure("TEntry", padding=(7, 5))
        style.configure("TCombobox", padding=(7, 5))
        style.configure("TSpinbox", padding=(5, 4))

    def _reset_log_pane(self) -> None:
        """將 LOG 恢復為適合閱讀的預設高度。"""
        try:
            pane_height = self.main_pane.winfo_height()
            split_at = max(400, pane_height - 260)
            self.main_pane.sash_place(0, 0, split_at)
        except (AttributeError, tk.TclError):
            pass

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12, style="App.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(1, weight=1)

        source = ttk.LabelFrame(
            outer, text="AdsPower 群組與環境", padding=10, style="Card.TLabelframe"
        )
        source.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        source.columnconfigure(1, weight=1)
        ttk.Label(source, text="群組：").grid(row=0, column=0, sticky="w")
        self.group_box = self._remember(ttk.Combobox(
            source, textvariable=self.group_var, state="readonly", width=42
        ))
        self.group_box.grid(row=0, column=1, sticky="ew", padx=6)
        self.group_box.bind("<<ComboboxSelected>>", lambda _e: self._load_profiles())
        self._remember(ttk.Button(source, text="重新讀取群組", command=self._load_groups)).grid(
            row=0, column=2, padx=4
        )
        self._remember(ttk.Button(source, text="讀取環境", command=self._load_profiles)).grid(
            row=0, column=3, padx=4
        )
        ttk.Label(source, text="搜尋（名稱／群組／ID／序號／IP）：").grid(row=1, column=0, sticky="w", pady=(8, 0))
        search = self._remember(ttk.Entry(source, textvariable=self.search_var))
        search.grid(row=1, column=1, sticky="ew", padx=6, pady=(8, 0))

        ttk.Label(source, text="AdsPower API Key：").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )
        self.api_key_entry = self._remember(ttk.Entry(
            source, textvariable=self.adspower_api_key, show="•"
        ))
        self.api_key_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=(8, 0))
        self._remember(ttk.Button(
            source, text="測試連線", command=self._test_adspower_connection,
            style="Primary.TButton",
        )).grid(row=2, column=2, padx=4, pady=(8, 0))
        ttk.Label(
            source, textvariable=self.api_key_status, style="Muted.TLabel"
        ).grid(row=2, column=3, sticky="w", padx=4, pady=(8, 0))

        self.main_pane = tk.PanedWindow(
            outer,
            orient=tk.VERTICAL,
            sashwidth=9,
            sashpad=2,
            sashrelief=tk.RAISED,
            showhandle=True,
            handlepad=12,
            handlesize=9,
            borderwidth=0,
            relief=tk.FLAT,
            background=self.colors["border"],
        )
        self.main_pane.grid(row=1, column=0, columnspan=2, sticky="nsew")

        upper = ttk.Frame(self.main_pane, style="App.TFrame")
        upper.columnconfigure(0, weight=3)
        upper.columnconfigure(1, weight=2)
        upper.rowconfigure(0, weight=1)
        self.main_pane.add(upper, minsize=400, stretch="always")

        profiles_box = ttk.LabelFrame(
            upper,
            text="環境勾選清單（點擊方框或環境名稱切換）",
            padding=10,
            style="Card.TLabelframe",
        )
        profiles_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        profiles_box.rowconfigure(0, weight=1)
        profiles_box.columnconfigure(0, weight=1)
        self.profile_list = self._remember(ttk.Treeview(
            profiles_box, columns=("name", "id"), show="headings",
            selectmode="none", height=16
        ))
        self.profile_list.heading("name", text="勾選／環境名稱")
        self.profile_list.heading("id", text="Profile ID")
        self.profile_list.column("name", width=310, anchor="w")
        self.profile_list.column("id", width=150, anchor="w")
        self.profile_list.bind("<Button-1>", self._toggle_profile)
        scroll = ttk.Scrollbar(profiles_box, orient="vertical", command=self.profile_list.yview)
        self.profile_list.configure(yscrollcommand=scroll.set)
        self.profile_list.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        buttons = ttk.Frame(profiles_box)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._remember(ttk.Button(buttons, text="全選目前清單", command=self._check_all_visible)).pack(side="left")
        self._remember(ttk.Button(buttons, text="取消全部勾選", command=self._uncheck_all)).pack(side="left", padx=6)
        self.profile_count = ttk.Label(buttons, text="0 個環境")
        self.profile_count.pack(side="right")

        tasks = ttk.LabelFrame(
            upper, text="十二項獨立任務設定", padding=12, style="Card.TLabelframe"
        )
        tasks.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        tasks.columnconfigure(0, weight=1)
        tasks.columnconfigure(1, weight=1)
        left_rows = [
            ("professional", "成為 Facebook 專業模式", None),
            ("pin", "建立／確認 Messenger PIN", None),
            ("post", "PO 文", None),
            ("reels", "發布 Reels", None),
            ("reels_comment", "Reels 留言", None),
        ]

        right_rows = [
            ("add_friend", "主動加好友", self.add_count),
            ("confirm_friend", "同意好友邀請", self.confirm_count),
            ("browse_like", "瀏覽／按讚（同一功能）", self.like_count),
            ("query_chats", "查詢聊天室（只讀、不發送）", None),
            ("reply_chats", "回覆聊天室（讀取待回覆佇列）", None),
            ("fanpage_message", "粉專私訊", None),
        ]

        left_box = ttk.Frame(tasks)
        left_box.grid(row=1, column=0, sticky="new", padx=(0, 10))
        left_box.columnconfigure(1, weight=1)

        profile_row = ttk.Frame(left_box)
        profile_row.grid(row=0, column=0, columnspan=2, sticky="w", pady=3)
        self._remember(ttk.Checkbutton(
            profile_row, text="個人資料設定", variable=self.task_vars["profile_setup"],
            command=self._toggle_profile_setup_children,
        )).pack(side="left")
        self.profile_setup_child_widgets = []
        for key, text in (
            ("avatar", "頭像"),
            ("banner", "Banner"),
            ("profile_name", "Name"),
            ("facebook_language", "語言"),
        ):
            child = self._remember(ttk.Checkbutton(
                profile_row, text=text, variable=self.task_vars[key]
            ))
            child.pack(side="left", padx=(8, 0))
            self.profile_setup_child_widgets.append(child)

        for row, (key, label, count_var) in enumerate(left_rows, start=1):
            if key == "post":
                post_row = ttk.Frame(left_box)
                post_row.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
                self._remember(ttk.Checkbutton(
                    post_row, text=label, variable=self.task_vars[key]
                )).pack(side="left")
                self._remember(ttk.Checkbutton(
                    post_row,
                    text="加相片／影片",
                    variable=self.post_media_enabled,
                    command=self._refresh_post_media_controls,
                )).pack(side="left", padx=(8, 3))
                self.post_media_mode_combo = self._remember(ttk.Combobox(
                    post_row,
                    textvariable=self.post_media_mode,
                    values=tuple(POST_MEDIA_MODE_LABELS),
                    state="readonly",
                    width=14,
                ))
                self.post_media_mode_combo.pack(side="left")
                self.post_media_mode_combo.bind(
                    "<<ComboboxSelected>>", lambda _event: self._refresh_post_media_controls()
                )
            else:
                cb = self._remember(ttk.Checkbutton(
                    left_box, text=label, variable=self.task_vars[key]
                ))
                cb.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)

        right_box = ttk.Frame(tasks)
        right_box.grid(row=1, column=1, sticky="new", padx=(10, 0))
        right_box.columnconfigure(1, weight=1)
        for row, (key, label, count_var) in enumerate(right_rows):
            cb = self._remember(ttk.Checkbutton(
                right_box, text=label, variable=self.task_vars[key]
            ))
            cb.grid(row=row, column=0, columnspan=2 if count_var is None else 1,
                    sticky="w", pady=3)
            if count_var is not None:
                holder = ttk.Frame(right_box)
                holder.grid(row=row, column=1, sticky="e")
                entry = self._remember(ttk.Entry(holder, textvariable=count_var, width=7))
                entry.pack(side="left")
                ttk.Label(
                    holder, text="次" if key == "browse_like" else "人"
                ).pack(side="left", padx=(4, 0))

        self._toggle_profile_setup_children()

        detail_tabs = ttk.Notebook(tasks)
        detail_tabs.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 5))

        post_box = ttk.Frame(detail_tabs, padding=7)
        detail_tabs.add(post_box, text="PO 文文案")
        post_box.columnconfigure(1, weight=1)
        ttk.Label(post_box, text="隨機文案 TXT：").grid(row=0, column=0, sticky="w")
        self._remember(ttk.Entry(post_box, textvariable=self.post_text_file)).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        self._remember(ttk.Button(
            post_box,
            text="選擇 TXT",
            command=lambda: self._choose_text_path(self.post_text_file, "選擇 PO 文隨機文案 TXT"),
        )).grid(row=0, column=2)
        ttk.Label(
            post_box,
            text="RC19 規則：未使用 --- 時每個非空行是一篇；單獨一行 --- 可分隔多行文案，保留空白行與 Emoji。未選 TXT 時使用文案.xlsx。",
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(post_box, text="隨機素材資料夾：").grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.post_random_media_entry = self._remember(ttk.Entry(
            post_box, textvariable=self.post_random_media_dir
        ))
        self.post_random_media_entry.grid(row=2, column=1, sticky="ew", padx=4, pady=(7, 0))
        self.post_random_media_button = self._remember(ttk.Button(
            post_box, text="選擇資料夾", command=self._choose_post_random_media_dir
        ))
        self.post_random_media_button.grid(row=2, column=2, pady=(7, 0))
        ttk.Label(post_box, text="固定素材檔案：").grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.post_fixed_media_entry = self._remember(ttk.Entry(
            post_box, textvariable=self.post_fixed_media_file
        ))
        self.post_fixed_media_entry.grid(row=3, column=1, sticky="ew", padx=4, pady=(4, 0))
        self.post_fixed_media_button = self._remember(ttk.Button(
            post_box, text="選擇檔案", command=self._choose_post_fixed_media_file
        ))
        self.post_fixed_media_button.grid(row=3, column=2, pady=(4, 0))
        ttk.Label(
            post_box,
            text="隨機模式會遞迴搜尋資料夾內所有支援的相片與影片；固定模式每個環境都使用同一個檔案。",
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))
        self._refresh_post_media_controls()

        reels_box = ttk.Frame(detail_tabs, padding=7)
        detail_tabs.add(reels_box, text="Reels 素材")
        reels_box.columnconfigure(1, weight=1)
        ttk.Label(reels_box, text="影片：").grid(row=0, column=0, sticky="w")
        self._remember(ttk.Entry(reels_box, textvariable=self.reels_video_dir)).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        self._remember(ttk.Button(
            reels_box, text="瀏覽", command=self._choose_reels_video_dir
        )).grid(row=0, column=2)
        ttk.Label(reels_box, text="描述：").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._remember(ttk.Entry(reels_box, textvariable=self.reels_text_file)).grid(
            row=1, column=1, sticky="ew", padx=4, pady=(4, 0)
        )
        self._remember(ttk.Button(
            reels_box, text="選擇 TXT", command=self._choose_reels_text_file
        )).grid(row=1, column=2, pady=(4, 0))
        ttk.Label(
            reels_box,
            text="描述依 RC19 規則隨機抽取；使用單獨一行 --- 可建立保留換行、空白行與 Emoji 的多行文案。",
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(reels_box, text="留言文案：").grid(row=3, column=0, sticky="w", pady=(6, 0))
        comment_mode_box = ttk.Frame(reels_box)
        comment_mode_box.grid(row=3, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self._remember(ttk.Radiobutton(
            comment_mode_box, text="預設文案", variable=self.reels_comment_mode,
            value="default", command=self._save_reels_settings
        )).pack(side="left")
        self._remember(ttk.Radiobutton(
            comment_mode_box, text="自選檔案", variable=self.reels_comment_mode,
            value="custom", command=self._save_reels_settings
        )).pack(side="left", padx=(14, 0))
        ttk.Label(reels_box, text="自選檔案：").grid(row=4, column=0, sticky="w", pady=(4, 0))
        self._remember(ttk.Entry(reels_box, textvariable=self.reels_comment_text_file)).grid(
            row=4, column=1, sticky="ew", padx=4, pady=(4, 0)
        )
        self._remember(ttk.Button(
            reels_box, text="選擇檔案", command=self._choose_reels_comment_text_file
        )).grid(row=4, column=2, pady=(4, 0))
        ttk.Label(
            reels_box,
            text="預設文案會整段送出；自選檔案會讀取 TXT 內全部文字，保留換行、空白行與 Emoji。",
            style="Muted.TLabel",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._remember(ttk.Checkbutton(
            reels_box,
            text="Reels 測試發送（跑完整流程，到 Publish 前停止，不會真的發佈）",
            variable=self.reels_dry_run,
        )).grid(row=6, column=0, columnspan=3, sticky="w", pady=(7, 0))
        self._remember(ttk.Button(
            reels_box, text="開啟十二項異常診斷包", command=self._open_reels_diagnostics
        )).grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        private_box = ttk.Frame(detail_tabs, padding=7)
        detail_tabs.add(private_box, text="三項私訊任務設定")
        private_box.columnconfigure(1, weight=1)
        private_rows = [
            ("粉專 URL", self.fanpage_url_file, lambda: self._choose_text_path(self.fanpage_url_file, "選擇 kolurl.txt")),
            ("粉專文二", self.fanpage_text_file, lambda: self._choose_text_path(self.fanpage_text_file, "選擇文二.txt")),
            ("待回覆 DB", self.chat_database, self._choose_database_path),
            ("回覆文一", self.reply_text_file, lambda: self._choose_text_path(self.reply_text_file, "選擇文一.txt")),
        ]
        for row, (label, variable, command) in enumerate(private_rows):
            ttk.Label(private_box, text=label).grid(row=row, column=0, sticky="w")
            self._remember(ttk.Entry(private_box, textvariable=variable)).grid(row=row, column=1, sticky="ew", padx=4)
            self._remember(ttk.Button(private_box, text="瀏覽", command=command)).grid(row=row, column=2)
        modes = ttk.Frame(private_box)
        modes.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Label(modes, text="粉專模式").pack(side="left")
        self._remember(ttk.Combobox(modes, textvariable=self.fanpage_mode, values=("txt", "openai"), width=8, state="readonly")).pack(side="left", padx=3)
        ttk.Label(modes, text="回覆模式").pack(side="left", padx=(8, 0))
        self._remember(ttk.Combobox(modes, textvariable=self.reply_mode, values=("txt", "openai"), width=8, state="readonly")).pack(side="left", padx=3)
        ttk.Label(modes, text="粉專數").pack(side="left", padx=(8, 0))
        self._remember(ttk.Entry(modes, textvariable=self.fanpage_max_urls, width=4)).pack(side="left")
        ttk.Label(modes, text="查詢數").pack(side="left", padx=(8, 0))
        self._remember(ttk.Entry(modes, textvariable=self.query_max_chats, width=4)).pack(side="left")
        ttk.Label(modes, text="回覆數").pack(side="left", padx=(8, 0))
        self._remember(ttk.Entry(modes, textvariable=self.reply_max_count, width=4)).pack(side="left")
        ttk.Label(modes, text="重試").pack(side="left", padx=(8, 0))
        self._remember(ttk.Entry(modes, textvariable=self.reply_max_retries, width=4)).pack(side="left")
        options = ttk.Frame(private_box)
        options.grid(row=5, column=0, columnspan=3, sticky="w")
        self._remember(ttk.Checkbutton(options, text="只查未讀", variable=self.query_unread_only)).pack(side="left")
        self._remember(ttk.Checkbutton(options, text="Telegram 回報", variable=self.telegram_report)).pack(side="left", padx=8)
        self._remember(ttk.Checkbutton(options, text="Lead 關鍵字回報", variable=self.lead_report)).pack(side="left")

        execution = ttk.Frame(detail_tabs, padding=10)
        detail_tabs.add(execution, text="執行設定")
        execution.columnconfigure(5, weight=1)

        ttk.Label(execution, text="循環次數").grid(row=0, column=0, sticky="w")
        self._remember(ttk.Entry(
            execution, textvariable=self.loop_var, width=6
        )).grid(row=0, column=1, sticky="w", padx=(5, 5))
        ttk.Label(execution, text="次（0＝無限）", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 18)
        )

        ttk.Label(execution, text="執行線程").grid(row=0, column=3, sticky="w")
        self._remember(ttk.Entry(
            execution, textvariable=self.worker_count_var, width=6
        )).grid(row=0, column=4, sticky="w", padx=(5, 5))
        ttk.Label(
            execution, text="條（環境平均分配）", style="Muted.TLabel"
        ).grid(row=0, column=5, sticky="w")

        ttk.Label(execution, text="AdsPower 完成後").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self._remember(ttk.Radiobutton(
            execution,
            text="保持環境開啟",
            variable=self.close_after_var,
            value=False,
        )).grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self._remember(ttk.Radiobutton(
            execution,
            text="關閉環境",
            variable=self.close_after_var,
            value=True,
        )).grid(row=1, column=3, columnspan=2, sticky="w", pady=(6, 0))

        execution_options = ttk.Frame(execution)
        execution_options.grid(
            row=2, column=0, columnspan=6, sticky="w", pady=(4, 0)
        )
        ttk.Label(
            execution_options,
            text="環境固定依名稱尾端數字由小到大執行",
            style="Muted.TLabel",
        ).pack(side="left")
        self._remember(ttk.Checkbutton(
            execution_options,
            text="處理新環境時將 AdsPower 瀏覽器移到最前面",
            variable=self.bring_to_front_var,
        )).pack(side="left", padx=(16, 0))

        smart = ttk.Frame(detail_tabs, padding=10)
        detail_tabs.add(smart, text="智慧排程")
        smart.columnconfigure(7, weight=1)
        ttk.Label(smart, text="每日時段").grid(row=0, column=0, sticky="w")
        self._remember(ttk.Entry(
            smart, textvariable=self.smart_start_var, width=7
        )).grid(row=0, column=1, padx=(5, 2))
        ttk.Label(smart, text="～").grid(row=0, column=2)
        self._remember(ttk.Entry(
            smart, textvariable=self.smart_end_var, width=7
        )).grid(row=0, column=3, padx=(2, 14))
        ttk.Label(smart, text="最多線程").grid(row=0, column=4, sticky="w")
        self._remember(ttk.Entry(
            smart, textvariable=self.smart_max_workers_var, width=6
        )).grid(row=0, column=5, padx=(5, 12))
        self._remember(ttk.Button(
            smart, text="自動計算排程", command=self._calculate_smart_schedule,
            style="Primary.TButton",
        )).grid(row=0, column=6, padx=(0, 6))
        self._remember(ttk.Button(
            smart, text="進階耗時設定", command=self._open_smart_time_settings,
            style="Secondary.TButton",
        )).grid(row=0, column=7, sticky="w")

        profile_box = ttk.Frame(detail_tabs, padding=10)
        detail_tabs.add(profile_box, text="個人設定")
        for col in (1, 4):
            profile_box.columnconfigure(col, weight=1)

        ttk.Label(profile_box, text="頭像資料夾：").grid(row=0, column=0, sticky="w")
        self._remember(ttk.Entry(profile_box, textvariable=self.avatar_dir)).grid(row=0, column=1, sticky="ew", padx=4)
        self._remember(ttk.Button(profile_box, text="瀏覽", command=self._choose_avatar_dir)).grid(row=0, column=2)

        ttk.Label(profile_box, text="Banner 資料夾：").grid(row=0, column=3, sticky="w", padx=(12, 0))
        self._remember(ttk.Entry(profile_box, textvariable=self.banner_dir)).grid(row=0, column=4, sticky="ew", padx=4)
        self._remember(ttk.Button(profile_box, text="瀏覽", command=self._choose_banner_dir)).grid(row=0, column=5)

        ttk.Label(profile_box, text="名字 TXT 檔案：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._remember(ttk.Entry(profile_box, textvariable=self.name_text_file)).grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))
        self._remember(ttk.Button(profile_box, text="選擇", command=self._choose_name_text_file)).grid(row=1, column=2, pady=(6, 0))

        ttk.Label(profile_box, text="語言：").grid(row=1, column=3, sticky="w", padx=(12, 0), pady=(6, 0))
        language_combo = self._remember(ttk.Combobox(
            profile_box,
            textvariable=self.facebook_language_target,
            state="readonly",
            values=("Filipino", "English (US)", "العربية", "繁體中文", "简体中文", "Français", "Deutsch", "Español"),
        ))
        language_combo.grid(row=1, column=4, columnspan=2, sticky="ew", padx=4, pady=(6, 0))
        ttk.Label(
            smart, textvariable=self.smart_result_var, wraplength=620,
            justify="left", style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))
        self.smart_apply_button = self._remember(ttk.Button(
            smart, text="套用排程", command=self._apply_smart_schedule,
            state="disabled", style="Secondary.TButton",
        ))
        self.smart_apply_button.grid(row=1, column=7, sticky="e", pady=(8, 0))

        ttk.Label(
            tasks,
            text="每個環境開始先回個人主頁；每項任務完成後再回個人主頁。",
            foreground="#555555", wraplength=360,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(3, 0))

        log_panel = ttk.Frame(self.main_pane, padding=(0, 8, 0, 0), style="App.TFrame")
        log_panel.rowconfigure(1, weight=1)
        log_panel.columnconfigure(0, weight=1)
        self.main_pane.add(log_panel, minsize=130, stretch="always")

        log_header = ttk.Frame(log_panel, padding=(10, 5), style="Card.TFrame")
        log_header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(log_header, text="執行狀態與 TERMINAL LOG", style="Section.TLabel").pack(
            side="left"
        )
        ttk.Label(
            log_header,
            text="拖曳上方分隔線可放大／縮小，雙擊按鈕可恢復預設高度",
            style="Muted.TLabel",
        ).pack(side="left", padx=(14, 0))
        ttk.Button(
            log_header,
            text="恢復預設高度",
            command=self._reset_log_pane,
            style="Secondary.TButton",
        ).pack(side="right")

        self.log_text = tk.Text(log_panel, height=12, wrap="word", state="disabled",
                                bg="#111827", fg="#e5e7eb", insertbackground="white",
                                selectbackground="#315a86", borderwidth=0,
                                padx=10, pady=8, font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_panel, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        log_scroll.grid(row=1, column=1, sticky="ns")

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Button(
            footer, text="關閉", command=self._close, style="Secondary.TButton"
        ).pack(side="right")
        self.stop_button = ttk.Button(
            footer, text="停止執行", command=self._stop,
            state="disabled", style="Danger.TButton"
        )
        self.stop_button.pack(side="right", padx=8)
        self.start_button = ttk.Button(
            footer, text="開始執行", command=self._submit, style="Primary.TButton"
        )
        self.start_button.pack(side="right")
        self.schedule_button = ttk.Button(
            footer, text="定時任務排程", command=self._open_scheduler,
            style="Secondary.TButton"
        )
        self.schedule_button.pack(side="right", padx=8)
        self.root.after(250, self._reset_log_pane)

    @staticmethod
    def _parse_clock(value: str, label: str) -> tuple[int, int]:
        try:
            hour_text, minute_text = value.strip().split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
        except (ValueError, TypeError):
            raise ValueError(f"{label}請使用 HH:MM 格式") from None
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError(f"{label}必須介於 00:00～23:59")
        return hour, minute

    def _selected_profile_count(self) -> int:
        return len({
            profile.profile_id for profile in self.profiles
            if profile.profile_id in self.checked_profile_ids
        })

    def _estimate_seconds_per_profile(self) -> float:
        t = self.smart_task_seconds
        total = t["startup"]
        if self.task_vars["professional"].get():
            total += t["professional"]
        if self.task_vars["avatar"].get():
            total += t["avatar"]
        if self.task_vars["pin"].get():
            total += t["pin"]
        if self.task_vars["post"].get():
            total += t["post"]
        if self.task_vars["reels"].get():
            total += t["reels"]
        if self.task_vars["reels_comment"].get():
            total += t["reels_comment"]
        if self.task_vars["add_friend"].get():
            total += t["add_friend"] * self._positive_int(
                self.add_count.get(), "主動加好友數量"
            )
        if self.task_vars["confirm_friend"].get():
            total += t["confirm_friend"] * self._positive_int(
                self.confirm_count.get(), "同意好友數量"
            )
        if self.task_vars["browse_like"].get():
            total += t["browse_like"] * self._positive_int(
                self.like_count.get(), "按讚數量"
            )
        if self.task_vars["fanpage_message"].get():
            total += t["fanpage_message"] * self._positive_int(
                self.fanpage_max_urls.get(), "粉專最大數"
            )
        if self.task_vars["query_chats"].get():
            total += t["query_chats"] * self._positive_int(
                self.query_max_chats.get(), "聊天室查詢數"
            )
        if self.task_vars["reply_chats"].get():
            total += t["reply_chats"] * self._positive_int(
                self.reply_max_count.get(), "聊天室回覆數"
            )
        if self.close_after_var.get():
            total += t["close"]
        return total * (1.0 + t["buffer_percent"] / 100.0)

    @staticmethod
    def _duration_text(seconds: float) -> str:
        minutes = max(0, math.ceil(seconds / 60))
        hours, minutes = divmod(minutes, 60)
        return f"{hours} 小時 {minutes} 分" if hours else f"{minutes} 分"

    def _calculate_smart_schedule(self, show_error: bool = True) -> dict | None:
        try:
            profile_count = self._selected_profile_count()
            if profile_count < 1:
                raise ValueError("請先勾選至少一個 AdsPower 環境")
            if not any(
                variable.get() for key, variable in self.task_vars.items()
                if key != "profile_setup"
            ):
                raise ValueError("請先勾選至少一項任務")
            loop_count = self._positive_int(
                self.loop_var.get(), "循環次數", allow_zero=True
            )
            if loop_count == 0:
                raise ValueError("智慧排程無法估算無限循環，請先填入 1～100")
            max_workers = self._positive_int(
                self.smart_max_workers_var.get(), "最多線程數"
            )
            start_h, start_m = self._parse_clock(
                self.smart_start_var.get(), "開始時間"
            )
            end_h, end_m = self._parse_clock(self.smart_end_var.get(), "結束時間")
            start_minutes, end_minutes = start_h * 60 + start_m, end_h * 60 + end_m
            window_minutes = (end_minutes - start_minutes) % (24 * 60)
            if window_minutes == 0:
                raise ValueError("每日開始與結束時間不可相同")
            per_profile = self._estimate_seconds_per_profile()
            workload = profile_count * loop_count * per_profile
            window_seconds = window_minutes * 60
            required_workers = max(1, math.ceil(workload / window_seconds))
            while (
                required_workers < profile_count
                and math.ceil(profile_count / required_workers)
                * loop_count * per_profile > window_seconds
            ):
                required_workers += 1
            applied_workers = min(required_workers, max_workers, profile_count)
            batch_size = math.ceil(profile_count / applied_workers)
            elapsed = batch_size * loop_count * per_profile
            fits = elapsed <= window_seconds
            days = max(1, math.ceil(elapsed / window_seconds))
            result = {
                "profile_count": profile_count,
                "loop_count": loop_count,
                "workers": applied_workers,
                "required_workers": required_workers,
                "per_profile": per_profile,
                "elapsed": elapsed,
                "fits": fits,
                "days": days,
                "start_time": f"{start_h:02d}:{start_m:02d}",
            }
            status = "可在指定時段完成" if fits else f"需約 {days} 天，單日無法完成"
            worker_note = (
                f"建議 {required_workers} 條"
                if required_workers <= max_workers
                else f"理想需 {required_workers} 條，目前上限套用 {applied_workers} 條"
            )
            self.smart_result_var.set(
                f"{profile_count} 個環境 × {loop_count} 輪｜"
                f"每環境安全預估 {self._duration_text(per_profile)}｜"
                f"{worker_note}｜總工期約 {self._duration_text(elapsed)}｜{status}"
            )
            self.smart_last_result = result
            self.smart_apply_button.configure(state="normal")
            return result
        except ValueError as exc:
            self.smart_last_result = None
            self.smart_apply_button.configure(state="disabled")
            self.smart_result_var.set(str(exc))
            if show_error:
                messagebox.showwarning("無法計算智慧排程", str(exc), parent=self.root)
            return None

    def _apply_smart_schedule(self) -> None:
        result = self._calculate_smart_schedule()
        if not result:
            return
        self.worker_count_var.set(str(result["workers"]))
        try:
            settings = self._collect_settings()
        except ValueError as exc:
            messagebox.showwarning("無法套用排程", str(exc), parent=self.root)
            return
        schedule = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "enabled": True,
            "type": "daily",
            "time": result["start_time"],
            "smart_schedule": True,
            "estimate_seconds": round(float(result["elapsed"]), 1),
            "settings": self._settings_to_dict(settings),
        }
        self.schedules.append(schedule)
        self._save_schedules()
        self._append_log(
            f"已套用智慧排程：每天 {result['start_time']}，"
            f"{result['profile_count']} 個環境，{result['workers']} 條線程。"
        )
        self.status.set(
            f"智慧排程已建立：每天 {result['start_time']}，"
            f"{result['workers']} 條線程"
        )
        messagebox.showinfo(
            "智慧排程已套用",
            f"已建立每日 {result['start_time']} 排程。\n"
            f"執行線程：{result['workers']} 條\n"
            f"預估工期：{self._duration_text(result['elapsed'])}",
            parent=self.root,
        )

    def _open_smart_time_settings(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("智慧排程｜進階耗時設定")
        window.geometry("580x590")
        window.minsize(520, 500)
        window.configure(background=self.colors["app"])
        holder = ttk.LabelFrame(
            window, text="各任務單次安全耗時（秒）", padding=12,
            style="Card.TLabelframe",
        )
        holder.pack(fill="both", expand=True, padx=12, pady=12)
        labels = [
            ("startup", "啟動 AdsPower／載入 Facebook"),
            ("professional", "成為專業模式"),
            ("avatar", "更換頭像"),
            ("pin", "Messenger PIN"),
            ("post", "PO 文"),
            ("reels", "Reels"),
            ("add_friend", "主動加好友（每人）"),
            ("confirm_friend", "同意好友（每人）"),
            ("browse_like", "瀏覽／按讚（每次）"),
            ("fanpage_message", "粉專私訊（每筆）"),
            ("query_chats", "查詢聊天室（每間）"),
            ("reply_chats", "回覆聊天室（每筆）"),
            ("close", "關閉 AdsPower"),
            ("buffer_percent", "安全緩衝（百分比）"),
        ]
        variables: dict[str, tk.StringVar] = {}
        holder.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate(labels):
            ttk.Label(holder, text=label).grid(
                row=row, column=0, sticky="w", pady=3
            )
            variable = tk.StringVar(value=f"{self.smart_task_seconds[key]:g}")
            variables[key] = variable
            ttk.Entry(holder, textvariable=variable, width=12).grid(
                row=row, column=1, sticky="e", pady=3
            )

        def save() -> None:
            try:
                values = {key: float(variable.get()) for key, variable in variables.items()}
                if any(value < 0 for value in values.values()):
                    raise ValueError
                if values["buffer_percent"] > 100:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "格式錯誤", "耗時需為 0 以上數字，緩衝比例需介於 0～100。",
                    parent=window,
                )
                return
            self.smart_task_seconds.update(values)
            self._save_smart_schedule_settings()
            self._calculate_smart_schedule(show_error=False)
            window.destroy()

        actions = ttk.Frame(holder)
        actions.grid(row=len(labels), column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(
            actions, text="取消", command=window.destroy, style="Secondary.TButton"
        ).pack(side="right")
        ttk.Button(
            actions, text="儲存並重新計算", command=save, style="Primary.TButton"
        ).pack(side="right", padx=(0, 8))

    def _background(self, action: str, fn) -> None:
        if self.running:
            return
        self.status.set(action)

        def worker():
            try:
                self.events.put(("data", action, fn()))
            except Exception as exc:
                self.events.put(("error", action, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _load_groups(self) -> None:
        self._background("正在讀取 AdsPower 群組……", self.api.list_groups)

    def _load_profiles(self) -> None:
        label = self.group_var.get()
        if label:
            group_id = self.groups.get(label, "0")
            self._background("正在讀取群組環境……",
                             lambda: self.api.list_profiles_by_group(group_id=group_id))

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text.rstrip() + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._append_log(event[1])
                elif kind == "error":
                    _, action, payload = event
                    self.status.set(f"{action}失敗：{payload}")
                    messagebox.showerror("讀取失敗", str(payload))
                elif kind == "api_key_test":
                    success, message = bool(event[1]), str(event[2])
                    if success:
                        self.api_key_status.set("連線成功")
                        try:
                            data = {}
                            if self.gui_settings_file.exists():
                                loaded = json.loads(self.gui_settings_file.read_text(encoding="utf-8"))
                                if isinstance(loaded, dict):
                                    data = loaded
                            data["adspower_api_key"] = self.adspower_api_key.get().strip()
                            temp = self.gui_settings_file.with_suffix(".json.tmp")
                            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                            temp.replace(self.gui_settings_file)
                        except Exception as exc:
                            logging.getLogger("main").warning("儲存 AdsPower API Key 失敗：%s", exc)
                        messagebox.showinfo("AdsPower 連線測試", message, parent=self.root)
                    else:
                        self.api_key_status.set("連線失敗")
                        messagebox.showerror("AdsPower 連線測試失敗", message, parent=self.root)
                elif kind == "finished":
                    was_stopped = self.stop_event.is_set()
                    self.running = False
                    self._set_controls_enabled(True)
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.status.set(
                        "執行已停止，可直接再次開始" if was_stopped else "全部執行完成"
                    )
                    self.stop_event.clear()
                elif kind == "data":
                    _, action, payload = event
                    if action.startswith("正在讀取 AdsPower 群組"):
                        values = ["全部群組"]
                        self.groups = {"全部群組": "0"}
                        for item in payload:
                            label = f"{item['group_name']}（{item['group_id']}）"
                            self.groups[label] = item["group_id"]
                            values.append(label)
                        self.group_box["values"] = values
                        preferred = next((v for v in values if self.api._cfg.target_group in v), values[0])
                        self.group_var.set(preferred)
                        self.status.set(f"已讀取 {len(values) - 1} 個群組")
                        self._load_profiles()
                    else:
                        self.profiles = sort_profiles_by_number(payload)
                        self.checked_profile_ids.clear()
                        self._render_profiles()
                        self.status.set(f"已讀取 {len(self.profiles)} 個環境")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _render_profiles(self) -> None:
        keyword = self.search_var.get().strip()
        for item in self.profile_list.get_children():
            self.profile_list.delete(item)
        self.visible_profiles = [
            p for p in self.profiles if profile_matches_search(p, keyword)
        ]
        for profile in self.visible_profiles:
            checked = profile.profile_id in self.checked_profile_ids
            self.profile_list.insert(
                "", "end", iid=profile.profile_id,
                values=(f"{'☑' if checked else '☐'}  {profile.name}", profile.profile_id),
            )
        self._update_profile_count()

    def _toggle_profile(self, event) -> str | None:
        if self.running:
            return "break"
        item = self.profile_list.identify_row(event.y)
        if not item:
            return None
        if item in self.checked_profile_ids:
            self.checked_profile_ids.remove(item)
        else:
            self.checked_profile_ids.add(item)
        self._render_profiles()
        return "break"

    def _check_all_visible(self) -> None:
        self.checked_profile_ids.update(p.profile_id for p in self.visible_profiles)
        self._render_profiles()

    def _uncheck_all(self) -> None:
        self.checked_profile_ids.clear()
        self._render_profiles()

    def _update_profile_count(self) -> None:
        self.profile_count.configure(
            text=f"顯示 {len(self.visible_profiles)} 個／已勾選 {len(self.checked_profile_ids)} 個"
        )

    @staticmethod
    def _positive_int(value: str, label: str, allow_zero: bool = False) -> int:
        value = value.strip()
        if not value.isdigit():
            raise ValueError(f"{label}必須是整數")
        number = int(value)
        minimum = 0 if allow_zero else 1
        if not minimum <= number <= 100:
            raise ValueError(f"{label}必須介於 {minimum}～100")
        return number

    def _collect_settings(self) -> GuiSettings:
        api_key = self.adspower_api_key.get().strip()
        if not api_key:
            raise ValueError("此版本會自動刪除驗證／停權／睡眠環境，請先輸入 AdsPower API Key 並測試連線")
        self.api.set_api_key(api_key)
        CONFIG.adspower.api_key = api_key
        selected = []
        seen_profile_ids: set[str] = set()
        for profile in self.profiles:
            if (
                profile.profile_id in self.checked_profile_ids
                and profile.profile_id not in seen_profile_ids
            ):
                selected.append(profile)
                seen_profile_ids.add(profile.profile_id)
        selected = sort_profiles_by_number(selected)
        if not selected:
            raise ValueError("請至少選擇一個 AdsPower 環境")
        if not any(
            variable.get() for key, variable in self.task_vars.items()
            if key != "profile_setup"
        ):
            raise ValueError("請至少勾選一項任務")
        post_media_mode = POST_MEDIA_MODE_LABELS.get(self.post_media_mode.get(), "random")
        if self.task_vars["post"].get() and self.post_media_enabled.get():
            try:
                MediaPool.from_settings({
                    "post_media_mode": post_media_mode,
                    "post_random_media_dir": self.post_random_media_dir.get().strip(),
                    "post_fixed_media_file": self.post_fixed_media_file.get().strip(),
                })
            except Exception as exc:
                raise ValueError(f"PO 文相片／影片設定無法使用：{exc}") from exc
        self._save_reels_settings()
        profile_setup_enabled = any(
            self.task_vars[key].get()
            for key in ("avatar", "banner", "profile_name", "facebook_language")
        )
        self.task_vars["profile_setup"].set(profile_setup_enabled)
        settings = GuiSettings(
            profiles=selected,
            professional_mode=self.task_vars["professional"].get(),
            profile_setup=profile_setup_enabled,
            avatar=self.task_vars["avatar"].get(),
            banner=self.task_vars["banner"].get(),
            profile_name=self.task_vars["profile_name"].get(),
            facebook_language=self.task_vars["facebook_language"].get(),
            avatar_dir=self.avatar_dir.get().strip(),
            banner_dir=self.banner_dir.get().strip(),
            name_text_file=self.name_text_file.get().strip(),
            facebook_language_target=self.facebook_language_target.get().strip() or "Filipino",
            pin=self.task_vars["pin"].get(),
            add_friend=self.task_vars["add_friend"].get(),
            add_friend_count=self._positive_int(self.add_count.get(), "主動加好友數量"),
            confirm_friend=self.task_vars["confirm_friend"].get(),
            confirm_friend_count=self._positive_int(self.confirm_count.get(), "同意好友數量"),
            post=self.task_vars["post"].get(),
            post_text_file=self.post_text_file.get().strip(),
            post_media_enabled=self.post_media_enabled.get(),
            post_media_mode=post_media_mode,
            post_random_media_dir=self.post_random_media_dir.get().strip(),
            post_fixed_media_file=self.post_fixed_media_file.get().strip(),
            reels=self.task_vars["reels"].get(),
            reels_dry_run=self.reels_dry_run.get(),
            reels_comment=self.task_vars["reels_comment"].get(),
            reels_comment_mode=self.reels_comment_mode.get(),
            reels_comment_text_file=self.reels_comment_text_file.get().strip(),
            reels_video_dir=self.reels_video_dir.get().strip(),
            reels_text_file=self.reels_text_file.get().strip(),
            browse_like=self.task_vars["browse_like"].get(),
            like_count=self._positive_int(self.like_count.get(), "按讚數量"),
            fanpage_message=self.task_vars["fanpage_message"].get(),
            query_chats=self.task_vars["query_chats"].get(),
            reply_chats=self.task_vars["reply_chats"].get(),
            fanpage_url_file=self.fanpage_url_file.get().strip(),
            fanpage_text_file=self.fanpage_text_file.get().strip(),
            fanpage_mode=self.fanpage_mode.get(),
            fanpage_max_urls=self._positive_int(self.fanpage_max_urls.get(), "粉專最大數"),
            chat_database=self.chat_database.get().strip(),
            query_max_chats=self._positive_int(self.query_max_chats.get(), "聊天室查詢數"),
            query_unread_only=self.query_unread_only.get(),
            reply_text_file=self.reply_text_file.get().strip(),
            reply_mode=self.reply_mode.get(),
            reply_max_count=self._positive_int(self.reply_max_count.get(), "聊天室回覆數"),
            reply_max_retries=self._positive_int(self.reply_max_retries.get(), "單筆重試數"),
            telegram_report=self.telegram_report.get(),
            lead_report=self.lead_report.get(),
            task_order=list(self.task_order),
            loop_count=self._positive_int(self.loop_var.get(), "循環次數", allow_zero=True),
            worker_count=self._positive_int(self.worker_count_var.get(), "執行線程數"),
            shuffle=False,
            close_after=self.close_after_var.get(),
            bring_to_front=self.bring_to_front_var.get(),
        )
        self._save_gui_settings(settings)
        return settings

    @staticmethod
    def _settings_to_dict(settings: GuiSettings) -> dict:
        return {
            "profiles": [
                {
                    "profile_id": p.profile_id,
                    "name": p.name,
                    "group_name": p.group_name,
                    "remark": p.remark,
                    "serial_number": p.serial_number,
                    "proxy_ip": p.proxy_ip,
                }
                for p in settings.profiles
            ],
            "professional_mode": settings.professional_mode,
            "profile_setup": settings.profile_setup,
            "avatar": settings.avatar,
            "banner": settings.banner,
            "profile_name": settings.profile_name,
            "facebook_language": settings.facebook_language,
            "avatar_dir": settings.avatar_dir,
            "banner_dir": settings.banner_dir,
            "name_text_file": settings.name_text_file,
            "facebook_language_target": settings.facebook_language_target,
            "pin": settings.pin,
            "add_friend": settings.add_friend,
            "add_friend_count": settings.add_friend_count,
            "confirm_friend": settings.confirm_friend,
            "confirm_friend_count": settings.confirm_friend_count,
            "post": settings.post,
            "post_text_file": settings.post_text_file,
            "post_media_enabled": settings.post_media_enabled,
            "post_media_mode": settings.post_media_mode,
            "post_random_media_dir": settings.post_random_media_dir,
            "post_fixed_media_file": settings.post_fixed_media_file,
            "reels": settings.reels,
            "reels_dry_run": settings.reels_dry_run,
            "reels_comment": settings.reels_comment,
            "reels_comment_mode": settings.reels_comment_mode,
            "reels_comment_text_file": settings.reels_comment_text_file,
            "reels_video_dir": settings.reels_video_dir,
            "reels_text_file": settings.reels_text_file,
            "browse_like": settings.browse_like,
            "like_count": settings.like_count,
            "fanpage_message": settings.fanpage_message,
            "query_chats": settings.query_chats,
            "reply_chats": settings.reply_chats,
            "fanpage_url_file": settings.fanpage_url_file,
            "fanpage_text_file": settings.fanpage_text_file,
            "fanpage_mode": settings.fanpage_mode,
            "fanpage_max_urls": settings.fanpage_max_urls,
            "chat_database": settings.chat_database,
            "query_max_chats": settings.query_max_chats,
            "query_unread_only": settings.query_unread_only,
            "reply_text_file": settings.reply_text_file,
            "reply_mode": settings.reply_mode,
            "reply_max_count": settings.reply_max_count,
            "reply_max_retries": settings.reply_max_retries,
            "telegram_report": settings.telegram_report,
            "lead_report": settings.lead_report,
            "task_order": settings.task_order,
            "loop_count": settings.loop_count,
            "worker_count": settings.worker_count,
            "shuffle": settings.shuffle,
            "close_after": settings.close_after,
            "bring_to_front": settings.bring_to_front,
        }

    @staticmethod
    def _settings_from_dict(data: dict) -> GuiSettings:
        def portable_file(key: str, filename: str) -> str:
            value = str(data.get(key) or "")
            candidate = Path(value) if value else None
            if candidate is not None and candidate.exists():
                return str(candidate)
            return str(Path(__file__).resolve().with_name(filename))

        return GuiSettings(
            profiles=sort_profiles_by_number(
                ProfileInfo(**item) for item in data.get("profiles", [])
            ),
            professional_mode=bool(data.get("professional_mode")),
            profile_setup=bool(data.get("profile_setup", any(data.get(k, False) for k in ("avatar", "banner", "profile_name", "facebook_language")))),
            avatar=bool(data.get("avatar")),
            banner=bool(data.get("banner", False)),
            profile_name=bool(data.get("profile_name", False)),
            facebook_language=bool(data.get("facebook_language", False)),
            avatar_dir=str(data.get("avatar_dir", Path.home() / "Desktop" / "頭像圖片")),
            banner_dir=str(data.get("banner_dir", Path.home() / "Desktop" / "Banner")),
            name_text_file=str(data.get("name_text_file", Path.home() / "Desktop" / "名字.txt")),
            facebook_language_target=str(data.get("facebook_language_target", "Filipino")),
            pin=bool(data.get("pin")),
            add_friend=bool(data.get("add_friend")),
            add_friend_count=int(data.get("add_friend_count", 1)),
            confirm_friend=bool(data.get("confirm_friend")),
            confirm_friend_count=int(data.get("confirm_friend_count", 2)),
            post=bool(data.get("post")),
            post_text_file=str(data.get("post_text_file", "")),
            post_media_enabled=bool(data.get("post_media_enabled", False)),
            post_media_mode=str(data.get("post_media_mode", "random")),
            post_random_media_dir=str(data.get("post_random_media_dir", Path.home() / "Desktop" / "view")),
            post_fixed_media_file=str(data.get("post_fixed_media_file", "")),
            reels=bool(data.get("reels", False)),
            reels_dry_run=bool(data.get("reels_dry_run", False)),
            reels_comment=bool(data.get("reels_comment", False)),
            reels_comment_mode=str(data.get("reels_comment_mode", "default")),
            reels_comment_text_file=str(data.get("reels_comment_text_file", Path(__file__).with_name("reels_comment.txt"))),
            reels_video_dir=str(data.get("reels_video_dir", r"C:\Users\USER\Desktop\reelsv")),
            reels_text_file=str(data.get("reels_text_file", r"C:\Users\USER\Desktop\reelsw.txt")),
            browse_like=bool(data.get("browse_like")),
            like_count=int(data.get("like_count", 1)),
            fanpage_message=bool(data.get("fanpage_message")),
            query_chats=bool(data.get("query_chats")),
            reply_chats=bool(data.get("reply_chats")),
            fanpage_url_file=portable_file("fanpage_url_file", "kolurl.txt"),
            fanpage_text_file=portable_file("fanpage_text_file", "文二.txt"),
            fanpage_mode=str(data.get("fanpage_mode", "txt")),
            fanpage_max_urls=int(data.get("fanpage_max_urls", 1)),
            chat_database=portable_file("chat_database", "chat_tasks.db"),
            query_max_chats=int(data.get("query_max_chats", 5)),
            query_unread_only=bool(data.get("query_unread_only")),
            reply_text_file=portable_file("reply_text_file", "文一.txt"),
            reply_mode=str(data.get("reply_mode", "txt")),
            reply_max_count=int(data.get("reply_max_count", 3)),
            reply_max_retries=int(data.get("reply_max_retries", 3)),
            telegram_report=bool(data.get("telegram_report")),
            lead_report=bool(data.get("lead_report")),
            task_order=list(data.get("task_order") or [
                "professional", "profile_setup", "avatar", "banner", "profile_name", "facebook_language", "pin", "confirm_friend", "post", "reels",
                "reels_comment", "browse_like", "add_friend", "fanpage_message",
                "query_chats", "reply_chats",
            ]),
            loop_count=int(data.get("loop_count", 1)),
            worker_count=max(1, int(data.get("worker_count", 1))),
            shuffle=False,
            close_after=bool(data.get("close_after")),
            bring_to_front=bool(data.get("bring_to_front", True)),
        )

    @staticmethod
    def _task_summary(data: dict) -> str:
        labels = [
            ("professional_mode", "專業模式"),
            ("avatar", "頭像"),
            ("banner", "Banner"),
            ("profile_name", "Name"),
            ("facebook_language", "語言"),
            ("pin", "PIN"),
            ("add_friend", "加好友"),
            ("confirm_friend", "同意好友"),
            ("post", "PO文"),
            ("reels", "Reels"),
            ("reels_dry_run", "Reels測試發送"),
            ("reels_comment", "Reels留言"),
            ("browse_like", "瀏覽按讚"),
            ("fanpage_message", "粉專私訊"),
            ("query_chats", "查詢聊天室"),
            ("reply_chats", "回覆聊天室"),
        ]
        return "、".join(label for key, label in labels if data.get(key)) or "未設定"

    def _open_scheduler(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("定時任務排程｜每日與間隔循環")
        window.geometry("1160x700")
        window.minsize(980, 560)
        window.configure(background=self.colors["app"])
        window.transient(self.root)

        header = ttk.Frame(window, padding=(20, 14), style="Card.TFrame")
        header.pack(fill="x")
        header.configure(style="Card.TFrame")
        title_area = ttk.Frame(header, style="Card.TFrame")
        title_area.pack(side="left", fill="x", expand=True)
        ttk.Label(
            title_area, text="定時任務排程", style="Section.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            title_area,
            text="建立每日固定時間或間隔循環；排程會保存目前主畫面的環境與任務設定。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        content = ttk.Frame(window, padding=(14, 12), style="App.TFrame")
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        top = ttk.LabelFrame(
            content, text="新增排程條件", padding=12, style="Card.TLabelframe"
        )
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        mode_var = tk.StringVar(value="daily")

        daily_box = ttk.LabelFrame(
            top, text="每日固定時間", padding=10, style="Card.TLabelframe"
        )
        daily_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ttk.Radiobutton(
            daily_box, text="啟用每天指定時間", variable=mode_var, value="daily"
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        hour_var = tk.StringVar(value="09")
        minute_var = tk.StringVar(value="00")
        ttk.Label(daily_box, text="執行時間").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Spinbox(
            daily_box, from_=0, to=23, width=5,
            textvariable=hour_var, format="%02.0f"
        ).grid(
            row=1, column=1, padx=(10, 0), pady=(10, 0)
        )
        ttk.Label(daily_box, text="：").grid(row=1, column=2, pady=(10, 0))
        ttk.Spinbox(
            daily_box, from_=0, to=59, width=5,
            textvariable=minute_var, format="%02.0f"
        ).grid(
            row=1, column=3, pady=(10, 0)
        )

        interval_box = ttk.LabelFrame(
            top, text="間隔循環", padding=10, style="Card.TLabelframe"
        )
        interval_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        ttk.Radiobutton(
            interval_box, text="啟用間隔循環", variable=mode_var, value="interval"
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(interval_box, text="每隔").grid(row=1, column=0, sticky="w", pady=(10, 0))
        interval_var = tk.StringVar(value="9")
        ttk.Spinbox(
            interval_box, from_=1, to=168, width=7, textvariable=interval_var
        ).grid(
            row=1, column=1, padx=8, pady=(10, 0)
        )
        ttk.Label(interval_box, text="小時執行一次").grid(
            row=1, column=2, sticky="w", pady=(10, 0)
        )
        run_now_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            interval_box,
            text="新增後立即執行一次",
            variable=run_now_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(
            top,
            text="新增時會擷取目前已勾選環境、十一項任務、Reels 路徑與關閉／置前設定。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        list_box = ttk.LabelFrame(
            content, text="排程清單", padding=10, style="Card.TLabelframe"
        )
        list_box.grid(row=1, column=0, sticky="nsew")
        list_box.rowconfigure(1, weight=1)
        list_box.columnconfigure(0, weight=1)
        schedule_status_var = tk.StringVar(value="共 0 筆排程")
        ttk.Label(
            list_box, textvariable=schedule_status_var, style="Muted.TLabel"
        ).grid(row=0, column=0, sticky="w", pady=(0, 7))

        frame = ttk.Frame(list_box, style="Card.TFrame")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(
            frame,
            columns=("enabled", "mode", "rule", "next_run", "profiles", "tasks"),
            show="headings",
            selectmode="browse",
        )
        tree.heading("enabled", text="狀態")
        tree.heading("mode", text="類型")
        tree.heading("rule", text="執行規則")
        tree.heading("next_run", text="下次執行")
        tree.heading("profiles", text="環境")
        tree.heading("tasks", text="執行動作")
        tree.column("enabled", width=78, minwidth=70, anchor="center", stretch=False)
        tree.column("mode", width=95, minwidth=85, anchor="center", stretch=False)
        tree.column("rule", width=130, minwidth=115, anchor="center", stretch=False)
        tree.column("next_run", width=165, minwidth=155, anchor="center", stretch=False)
        tree.column("profiles", width=82, minwidth=75, anchor="center", stretch=False)
        tree.column("tasks", width=510, minwidth=300, anchor="w")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        x_scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=x_scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        x_scrollbar.grid(row=1, column=0, sticky="ew")
        tree.tag_configure("enabled", foreground=self.colors["teal"])
        tree.tag_configure("disabled", foreground=self.colors["muted"])

        def refresh() -> None:
            selected = tree.selection()
            for item in tree.get_children():
                tree.delete(item)
            def sort_key(item: dict) -> str:
                if item.get("type", "daily") == "interval":
                    return item.get("next_run", "9999")
                return item.get("time", "99:99")

            for schedule in sorted(self.schedules, key=sort_key):
                settings = schedule.get("settings", {})
                is_interval = schedule.get("type", "daily") == "interval"
                next_run_display = "每日"
                if is_interval:
                    try:
                        next_run_display = datetime.fromisoformat(
                            str(schedule.get("next_run", ""))
                        ).strftime("%Y-%m-%d %H:%M:%S")
                    except (TypeError, ValueError):
                        next_run_display = "等待重新計算"
                enabled = schedule.get("enabled", True)
                tree.insert(
                    "", "end", iid=schedule["id"],
                    values=(
                        "● 啟用" if enabled else "○ 停用",
                        "間隔循環" if is_interval else "每日",
                        (
                            f"每 {schedule.get('interval_hours', '?')} 小時"
                            if is_interval else f"每天 {schedule.get('time', '--:--')}"
                        ),
                        next_run_display,
                        f"{len(settings.get('profiles', []))} 個",
                        self._task_summary(settings),
                    ),
                    tags=("enabled" if enabled else "disabled",),
                )
            enabled_count = sum(1 for item in self.schedules if item.get("enabled", True))
            schedule_status_var.set(
                f"共 {len(self.schedules)} 筆排程｜啟用 {enabled_count}｜停用 {len(self.schedules) - enabled_count}"
            )
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])

        def add_schedule() -> None:
            try:
                schedule_type = mode_var.get()
                if schedule_type == "daily":
                    hour = int(hour_var.get())
                    minute = int(minute_var.get())
                    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                        raise ValueError("時間必須介於 00:00～23:59")
                else:
                    interval_hours = int(interval_var.get())
                    if not 1 <= interval_hours <= 168:
                        raise ValueError("間隔小時必須介於 1～168")
                settings = self._collect_settings()
                if settings.loop_count == 0:
                    raise ValueError("定時排程不可使用無限循環，請把循環次數改為 1～100")
            except ValueError as exc:
                messagebox.showwarning(
                    "無法新增排程",
                    str(exc) if str(exc) else "時間必須介於 00:00～23:59",
                    parent=window,
                )
                return
            schedule_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
            schedule = {
                "id": schedule_id,
                "enabled": True,
                "type": schedule_type,
                "settings": self._settings_to_dict(settings),
            }
            if schedule_type == "daily":
                schedule["time"] = f"{hour:02d}:{minute:02d}"
                log_rule = f"每天 {hour:02d}:{minute:02d}"
            else:
                created_at = datetime.now()
                run_now = bool(run_now_var.get())
                next_run = (
                    created_at
                    if run_now
                    else created_at + timedelta(hours=interval_hours)
                )
                schedule["interval_hours"] = interval_hours
                schedule["next_run"] = next_run.isoformat(timespec="seconds")
                schedule["run_immediately_on_create"] = run_now
                if run_now:
                    log_rule = (
                        f"立即執行一次，之後每 {interval_hours} 小時；"
                        f"下次週期約為 "
                        f"{(created_at + timedelta(hours=interval_hours)).strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                else:
                    log_rule = (
                        f"每 {interval_hours} 小時，"
                        f"首次 {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
            self.schedules.append(schedule)
            self._save_schedules()
            refresh()
            self._append_log(
                f"已新增排程 {log_rule}："
                f"{len(settings.profiles)} 個環境，{self._task_summary(self._settings_to_dict(settings))}"
            )

        def selected_schedule() -> dict | None:
            selection = tree.selection()
            if not selection:
                messagebox.showwarning("請選擇排程", "請先點選一筆排程。", parent=window)
                return None
            schedule_id = selection[0]
            return next((item for item in self.schedules if item.get("id") == schedule_id), None)

        def toggle_schedule() -> None:
            schedule = selected_schedule()
            if schedule is None:
                return
            schedule["enabled"] = not schedule.get("enabled", True)
            self._save_schedules()
            refresh()

        def delete_schedule() -> None:
            schedule = selected_schedule()
            if schedule is None:
                return
            if not messagebox.askyesno(
                "刪除排程",
                "確定刪除選取的排程？",
                parent=window,
            ):
                return
            self.schedules = [item for item in self.schedules if item.get("id") != schedule.get("id")]
            self._save_schedules()
            refresh()

        # 將排程操作列放進主要 grid，固定保留在視窗底部。
        # 原本直接 pack 在 window 最後方，在 Windows 高 DPI 縮放或視窗較矮時，
        # 會被上方可伸縮內容擠出可視範圍，造成看不到「新增排程」按鈕。
        buttons = ttk.Frame(content, padding=(0, 10, 0, 0), style="App.TFrame")
        buttons.grid(row=2, column=0, sticky="ew")
        ttk.Button(
            buttons,
            text="＋ 新增排程任務",
            command=add_schedule,
            style="Primary.TButton",
        ).pack(side="left")
        ttk.Button(
            buttons, text="啟用／停用", command=toggle_schedule,
            style="Secondary.TButton"
        ).pack(side="left", padx=8)
        ttk.Button(
            buttons, text="刪除選取排程", command=delete_schedule, style="Danger.TButton"
        ).pack(side="left")
        ttk.Button(
            buttons, text="關閉", command=window.destroy, style="Secondary.TButton"
        ).pack(side="right")
        refresh()

    def _check_schedules(self) -> None:
        try:
            now = datetime.now()
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            current_time = now.strftime("%H:%M")
            for schedule in list(self.schedules):
                schedule_id = str(schedule.get("id", ""))
                if not schedule.get("enabled", True):
                    continue

                schedule_type = schedule.get("type", "daily")
                due = False
                source = ""
                next_run = None
                interval_hours = 0
                if schedule_type == "interval":
                    try:
                        interval_hours = max(1, int(schedule.get("interval_hours", 1)))
                        next_run_text = str(schedule.get("next_run", ""))
                        next_run = datetime.fromisoformat(next_run_text) if next_run_text else None
                    except (TypeError, ValueError):
                        next_run = None
                    if next_run is None:
                        next_run = now + timedelta(hours=interval_hours)
                        schedule["next_run"] = next_run.isoformat(timespec="seconds")
                        self._save_schedules()
                    due = now >= next_run
                    source = f"間隔排程（每 {interval_hours} 小時）"
                else:
                    due = (
                        schedule.get("time") == current_time
                        and self.last_schedule_minute.get(schedule_id) != minute_key
                    )
                    source = f"每日排程 {current_time}"

                if due:
                    self.last_schedule_minute[schedule_id] = minute_key
                    claim_key = (
                        f"{schedule_id}:{next_run.isoformat(timespec='seconds')}"
                        if schedule_type == "interval" and next_run is not None
                        else f"{schedule_id}:{minute_key}"
                    )
                    if schedule.get("last_claim_key") == claim_key:
                        continue
                    schedule["last_claim_key"] = claim_key
                    schedule["last_started_at"] = now.isoformat(timespec="seconds")
                    if schedule_type == "interval" and next_run is not None:
                        while next_run <= now:
                            next_run += timedelta(hours=interval_hours)
                        schedule["next_run"] = next_run.isoformat(timespec="seconds")
                    self._save_schedules()
                    if self.running:
                        self._append_log(
                            f"{source}已到時間，但上一批任務仍在執行，本次跳過。"
                        )
                        continue
                    try:
                        settings = self._settings_from_dict(schedule.get("settings", {}))
                        if not settings.profiles:
                            raise ValueError("排程內沒有環境")
                        self._start_run(settings, source)
                    except Exception as exc:
                        self._append_log(f"{source}無法啟動：{exc}")
        finally:
            self.root.after(1000, self._check_schedules)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in self.control_widgets:
            try:
                if widget is self.group_box:
                    widget.configure(state="readonly" if enabled else "disabled")
                else:
                    widget.configure(state=state)
            except tk.TclError:
                pass
        if enabled:
            self._refresh_post_media_controls()
            self._toggle_profile_setup_children()

    def _submit(self) -> None:
        try:
            settings = self._collect_settings()
        except ValueError as exc:
            messagebox.showwarning("設定未完成", str(exc))
            return
        self._start_run(settings, "手動執行")

    def _start_run(self, settings: GuiSettings, source: str) -> None:
        if self.running:
            self._append_log(f"{source}未啟動：目前已有任務正在執行。")
            return
        settings.profiles = sort_profiles_by_number(settings.profiles)
        settings.shuffle = False
        fingerprint = json.dumps(
            self._settings_to_dict(settings), ensure_ascii=False, sort_keys=True
        )
        now_monotonic = time.monotonic()
        if (
            fingerprint == self.last_run_fingerprint
            and now_monotonic - self.last_run_started_at < 60
        ):
            self._append_log(
                f"{source}未啟動：相同環境與任務設定在 60 秒內已啟動過，已阻止重複執行。"
            )
            return
        self.last_run_fingerprint = fingerprint
        self.last_run_started_at = now_monotonic
        self.stop_event.clear()
        self.running = True
        self._set_controls_enabled(False)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set(f"正在執行，共選擇 {len(settings.profiles)} 個環境")
        self._append_log("=" * 72)
        self._append_log(f"開始執行：{source}")

        def worker() -> None:
            try:
                self.runner(settings, self.stop_event)
            except Exception:
                logging.getLogger("main").exception("GUI 背景執行發生未預期錯誤")
            finally:
                self.events.put(("finished",))

        threading.Thread(target=worker, daemon=True).start()

    def _stop(self) -> None:
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.status.set("已要求停止；正在中止目前等待……")
        self._append_log("已按下停止；正在中止目前等待，不會開始下一個 AdsPower 環境。")

    def _close(self) -> None:
        if self.running:
            if not messagebox.askyesno("程式仍在執行", "目前任務仍在執行。要要求停止並關閉視窗嗎？"):
                return
            self.stop_event.set()
        logging.getLogger().removeHandler(self.log_handler)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def launch_configuration_gui(
    api: AdsPowerClient,
    runner: Callable[[GuiSettings, threading.Event], None],
) -> None:
    SettingsWindow(api, runner).run()
