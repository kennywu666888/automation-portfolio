"""十一項任務共用執行結果與執行統計。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from threading import Lock
from time import monotonic


@dataclass
class TaskResult:
    task_name: str
    status: str = "success"
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    restricted_count: int = 0
    detail: str = ""
    diagnostic_zip: str = ""
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskStats:
    task_name: str
    success: int = 0
    failed: int = 0
    skipped: int = 0
    restricted: int = 0
    elapsed_seconds: float = 0.0
    _started_at: float = field(default=0.0, repr=False)

    def start(self) -> None:
        self._started_at = monotonic()

    def add(self, result: TaskResult) -> None:
        self.success += result.success_count
        self.failed += result.failed_count
        self.skipped += result.skipped_count
        self.restricted += result.restricted_count
        self.elapsed_seconds += result.elapsed_seconds
        if self._started_at:
            self.elapsed_seconds += max(0.0, monotonic() - self._started_at)
            self._started_at = 0.0


class TaskStatsRegistry:
    def __init__(self) -> None:
        self._items: dict[str, TaskStats] = {}
        self._lock = Lock()

    def add(self, result: TaskResult) -> None:
        with self._lock:
            item = self._items.setdefault(result.task_name, TaskStats(result.task_name))
            item.success += result.success_count
            item.failed += result.failed_count
            item.skipped += result.skipped_count
            item.restricted += result.restricted_count
            item.elapsed_seconds += result.elapsed_seconds

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {
                key: {
                    "success": value.success,
                    "failed": value.failed,
                    "skipped": value.skipped,
                    "restricted": value.restricted,
                    "elapsed_seconds": round(value.elapsed_seconds, 2),
                }
                for key, value in self._items.items()
            }
