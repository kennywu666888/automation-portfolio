"""
utils.py
========
Facebook Auto Warm-up Lite — 共用工具模組
提供隨機化、重試裝飾器、人性化等待等通用工具函數。
"""

import random
import time
import functools
from typing import Any, Callable, Optional, TypeVar

from 日誌 import get_logger

_log = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ─────────────────────────────────────────────
# 隨機等待工具
# ─────────────────────────────────────────────

def random_sleep(min_sec: float, max_sec: float) -> None:
    """
    在指定範圍內隨機等待，模擬真人操作節奏。

    Args:
        min_sec: 最短等待秒數。
        max_sec: 最長等待秒數。
    """
    duration = random.uniform(min_sec, max_sec)
    time.sleep(duration)


def brief_pause() -> None:
    """極短暫停（0.3 ~ 0.9 秒），用於點擊後的自然反應延遲。"""
    random_sleep(0.3, 0.9)


def medium_pause() -> None:
    """中等停頓（1.0 ~ 2.5 秒），用於頁面載入後等待。"""
    random_sleep(1.0, 2.5)


def long_pause() -> None:
    """較長停頓（3.0 ~ 6.0 秒），用於重要操作後的緩衝。"""
    random_sleep(3.0, 6.0)


# ─────────────────────────────────────────────
# 機率判斷工具
# ─────────────────────────────────────────────

def should_do(probability: float) -> bool:
    """
    依照機率決定是否執行某個動作。

    Args:
        probability: 0.0 ~ 1.0 之間的機率值。

    Returns:
        True 表示執行，False 表示跳過。

    Example:
        if should_do(0.30):   # 30% 機率按讚
            like_post()
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"機率值必須介於 0.0 ~ 1.0，收到：{probability}")
    return random.random() < probability


# ─────────────────────────────────────────────
# 重試裝飾器
# ─────────────────────────────────────────────

def with_retry(
    max_retries: int = 2,
    wait_sec: float = 3.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    fallback: Any = None,
) -> Callable[[F], F]:
    """
    函數重試裝飾器。
    失敗後等待 wait_sec 秒再重試，超過次數後回傳 fallback 而非拋出例外，
    確保整體流程不會因單一操作失敗而中斷。

    Args:
        max_retries:  最多重試幾次（不含首次執行）。
        wait_sec:     每次重試前等待秒數。
        exceptions:   要捕捉的例外型別。
        fallback:     全部失敗後的回傳值（預設 None）。

    Returns:
        裝飾後的函數。

    Example:
        @with_retry(max_retries=2, wait_sec=3.0)
        def click_like(driver):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        _log.warning(
                            "操作失敗（第 %d/%d 次），%.1f 秒後重試。函數：%s，原因：%s",
                            attempt + 1, max_retries + 1,
                            wait_sec, func.__name__, exc,
                        )
                        time.sleep(wait_sec)
                    else:
                        _log.error(
                            "操作已達最大重試次數，跳過。函數：%s，原因：%s",
                            func.__name__, last_exc,
                        )
            return fallback
        return wrapper  # type: ignore[return-value]
    return decorator


# ─────────────────────────────────────────────
# 滑鼠座標隨機偏移
# ─────────────────────────────────────────────

def jitter_point(x: int, y: int, jitter: int = 8) -> tuple[int, int]:
    """
    對座標加入隨機偏移，模擬真人點擊時的不精確性。

    Args:
        x:      原始 X 座標。
        y:      原始 Y 座標。
        jitter: 最大偏移像素（正負方向均可）。

    Returns:
        偏移後的 (x, y) 座標。
    """
    return (
        x + random.randint(-jitter, jitter),
        y + random.randint(-jitter, jitter),
    )


# ─────────────────────────────────────────────
# 文字截斷工具（日誌用）
# ─────────────────────────────────────────────

def truncate(text: str, max_len: int = 80) -> str:
    """
    截斷過長字串並加上省略符號，用於日誌輸出。

    Args:
        text:    原始字串。
        max_len: 最大長度（預設 80）。

    Returns:
        截斷後的字串。
    """
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ─────────────────────────────────────────────
# 隨機打亂列表順序（不修改原始列表）
# ─────────────────────────────────────────────

def shuffled(items: list[Any]) -> list[Any]:
    """
    回傳打亂順序的新列表，不修改原始列表。

    Args:
        items: 任意列表。

    Returns:
        順序打亂的新列表。
    """
    result = list(items)
    random.shuffle(result)
    return result
