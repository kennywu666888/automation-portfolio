"""十一項任務共用的異常診斷包產生器。"""

from __future__ import annotations

import re
import time
import json
import traceback
import zipfile
from pathlib import Path

from 日誌 import get_logger

_log = get_logger("task_diagnostics")
DIAGNOSTIC_ROOT = Path(__file__).with_name("diagnostics")


def _safe_name(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(value)).strip(" ._")
    return value[:80] or "unknown"


def _redact(value: str) -> str:
    value = str(value or "")
    patterns = [
        (r"(?i)(api[_ -]?key|token|api[_ -]?hash|secret|password)\s*[:=]\s*\S+", r"\1=<REDACTED>"),
        (r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b", "<REDACTED_BOT_TOKEN>"),
        (r"sk-[A-Za-z0-9_-]{16,}", "<REDACTED_OPENAI_KEY>"),
    ]
    for pattern, replacement in patterns:
        value = re.sub(pattern, replacement, value)
    return value


def save_task_diagnostic(
    driver, profile_name: str, task_name: str, reason: str,
    *, profile_id: str = "", stage: str = "", job_id: object = "",
    settings: dict | None = None, log_text: str = "", traceback_text: str = "",
) -> Path | None:
    """只在錯誤時保存完整診斷 ZIP；敏感設定會先遮罩。"""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    task_key = _safe_name(task_name)
    folder = DIAGNOSTIC_ROOT / task_key / f"{stamp}_{_safe_name(profile_name)}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        url = ""
        try:
            url = driver.current_url if driver is not None else ""
        except Exception as exc:
            url = f"<讀取網址失敗：{exc}>"
        (folder / "diagnostic.txt").write_text(
            "\n".join([
                f"時間：{time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"環境：{profile_name}",
                f"Profile ID：{profile_id}",
                f"任務：{task_name}",
                f"階段：{stage}",
                f"工作識別：{job_id}",
                f"網址：{url}",
                f"原因：{_redact(reason)}",
                "",
                "Python traceback：",
                _redact(traceback_text or traceback.format_exc()),
            ]),
            encoding="utf-8",
        )
        (folder / "task_settings.json").write_text(
            _redact(json.dumps(settings or {}, ensure_ascii=False, indent=2, default=str)),
            encoding="utf-8",
        )
        (folder / "task.log").write_text(_redact(log_text), encoding="utf-8")
        try:
            driver.save_screenshot(str(folder / "screenshot.png"))
        except Exception as exc:
            (folder / "screenshot_error.txt").write_text(str(exc), encoding="utf-8")
        try:
            (folder / "page_source.html").write_text(
                _redact(driver.page_source or ""), encoding="utf-8", errors="replace"
            )
        except Exception as exc:
            (folder / "dom_error.txt").write_text(str(exc), encoding="utf-8")

        archive = folder.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for item in folder.iterdir():
                if item.is_file():
                    output.write(item, item.name)
        _log.error("[%s] %s 異常診斷包已保存：%s", profile_name, task_name, archive)
        return archive
    except Exception as exc:
        _log.warning("[%s] %s 診斷資料保存失敗：%s", profile_name, task_name, exc)
        return None
