"""
logger.py
=========
Facebook Auto Warm-up Lite — V7.4 完整日誌記錄模組
提供結構化、可 rotate 的日誌，並記錄每個 Profile 的執行摘要。
"""

import logging
import os
import time
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

from 設定 import CONFIG, LoggerConfig


# ─────────────────────────────────────────────
# 日誌格式常數
# ─────────────────────────────────────────────
LOG_FORMAT = "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def build_logger(
    name: str,
    cfg: Optional[LoggerConfig] = None,
) -> logging.Logger:
    """
    建立並回傳一個已設定好 Handler 的 Logger 實例。

    Args:
        name: Logger 名稱（通常使用模組名稱）。
        cfg:  LoggerConfig；若省略則使用全域 CONFIG.logger。

    Returns:
        設定完成的 logging.Logger。
    """
    if cfg is None:
        cfg = CONFIG.logger

    logger = logging.getLogger(name)

    # 避免重複新增 Handler（多次呼叫同名 logger 時）
    if logger.handlers:
        return logger

    level = getattr(logging, cfg.log_level.upper(), logging.INFO)
    logger.setLevel(level)

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # ── File Handler（RotatingFileHandler）──
    os.makedirs(cfg.log_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    log_path = os.path.join(cfg.log_dir, f"{cfg.log_prefix}_{today}.log")

    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=cfg.max_mb * 1024 * 1024,
        backupCount=cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    with _PROFILE_HANDLER_LOCK:
        for handler in _PROFILE_HANDLERS.values():
            if handler not in logger.handlers:
                logger.addHandler(handler)

    # ── Console Handler ──
    if cfg.console_output:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


# ─────────────────────────────────────────────
# 每個 Profile 的獨立完整 LOG
# ─────────────────────────────────────────────
_PROFILE_HANDLERS: dict[int, logging.Handler] = {}
_PROFILE_LOG_PATHS: dict[int, str] = {}
_PROFILE_HANDLER_LOCK = threading.RLock()


class _ThreadLogFilter(logging.Filter):
    """只把目前執行線程的紀錄寫入該環境獨立 LOG。"""
    def __init__(self, thread_id: int) -> None:
        super().__init__()
        self.thread_id = thread_id

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread == self.thread_id


def _safe_filename(value: str) -> str:
    """移除 Windows 檔名不允許的字元。"""
    return "".join("_" if ch in '<>:"/\\|?*' else ch for ch in value).strip() or "profile"


def start_profile_log(profile_name: str) -> str:
    """為目前 Profile 建立獨立 LOG，並附加到所有模組 Logger。"""
    thread_id = threading.get_ident()
    with _PROFILE_HANDLER_LOCK:
        stop_profile_log()
        cfg = CONFIG.logger
        profile_dir = os.path.join(cfg.log_dir, "profiles")
        os.makedirs(profile_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{stamp}_{_safe_filename(profile_name)}.log"
        log_path = os.path.join(profile_dir, filename)

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT))
        handler.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
        handler.addFilter(_ThreadLogFilter(thread_id))
        _PROFILE_HANDLERS[thread_id] = handler
        _PROFILE_LOG_PATHS[thread_id] = log_path

        for obj in logging.Logger.manager.loggerDict.values():
            if isinstance(obj, logging.Logger) and handler not in obj.handlers:
                obj.addHandler(handler)
        return log_path


def stop_profile_log() -> None:
    """關閉目前 Profile 的獨立 LOG。"""
    thread_id = threading.get_ident()
    with _PROFILE_HANDLER_LOCK:
        handler = _PROFILE_HANDLERS.pop(thread_id, None)
        _PROFILE_LOG_PATHS.pop(thread_id, None)
        if handler is None:
            return
        for obj in logging.Logger.manager.loggerDict.values():
            if isinstance(obj, logging.Logger) and handler in obj.handlers:
                obj.removeHandler(handler)
        handler.flush()
        handler.close()


# ─────────────────────────────────────────────
# 模組層級預設 Logger
# ─────────────────────────────────────────────
# 各模組可直接 import 使用：
#   from logger import get_logger
#   log = get_logger(__name__)
def get_logger(name: str) -> logging.Logger:
    """快速取得已設定的 Logger（統一入口）。"""
    return build_logger(name)


# ─────────────────────────────────────────────
# Profile 執行摘要記錄器
# ─────────────────────────────────────────────
class ProfileSummary:
    """
    記錄單一 Profile 完整執行結果的摘要物件。
    流程結束後呼叫 finish() 以寫入日誌。
    """

    def __init__(self, profile_id: str, profile_name: str) -> None:
        self.profile_id: str = profile_id
        self.profile_name: str = profile_name
        self.start_time: float = time.time()
        self.end_time: float = 0.0
        self.success: bool = False
        self.failure_reason: str = ""
        self.issues: list[str] = []
        self.actions_done: list[str] = []
        self._log = get_logger("ProfileSummary")

    def add_action(self, action: str) -> None:
        """記錄已執行的操作（如 like / comment / share）。"""
        self.actions_done.append(action)

    def add_issue(self, issue: str) -> None:
        """記錄單項任務失敗；不中止後續任務。"""
        self.issues.append(issue)

    def finish(self, success: bool, reason: str = "") -> None:
        """
        標記 Profile 執行結束並寫入摘要日誌。

        Args:
            success: 是否成功完成整個流程。
            reason:  失敗原因（成功時可省略）。
        """
        self.end_time = time.time()
        self.success = success
        self.failure_reason = reason
        elapsed = self.end_time - self.start_time
        self._write_summary(elapsed)

    def _write_summary(self, elapsed: float) -> None:
        """將摘要格式化後寫入日誌。"""
        status_tag = (
            "△ PARTIAL" if self.success and self.issues
            else ("✓ SUCCESS" if self.success else "✗ FAILED")
        )
        actions_str = ", ".join(self.actions_done) if self.actions_done else "—"
        lines = [
            "─" * 60,
            f"  Profile   : {self.profile_name} (id={self.profile_id})",
            f"  Status    : {status_tag}",
            f"  Duration  : {elapsed:.1f}s",
            f"  Actions   : {actions_str}",
        ]
        if not self.success and self.failure_reason:
            lines.append(f"  Reason    : {self.failure_reason}")
        if self.issues:
            lines.append(f"  Issues    : {'; '.join(self.issues)}")
        lines.append("─" * 60)

        summary_text = "\n".join(lines)
        if self.success and not self.issues:
            self._log.info("\n%s", summary_text)
        else:
            self._log.warning("\n%s", summary_text)
