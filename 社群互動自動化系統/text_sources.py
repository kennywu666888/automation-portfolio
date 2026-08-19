from __future__ import annotations

import random
from pathlib import Path


BLOCK_SEPARATOR = "---"


def _clean_block(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(line.rstrip() for line in lines).strip()


def load_text_lines(path: str | Path) -> list[str]:
    source = Path(path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"找不到文案檔：{source}")
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"文案檔不是 UTF-8，請用 UTF-8 格式另存：{source}"
        ) from exc

    # Backward compatible mode: without a separator, every non-empty line is
    # still an independent message exactly as before.
    if not any(line.strip() == BLOCK_SEPARATOR for line in lines):
        usable = [
            " ".join(line.split())
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]
    else:
        # Multiline mode: a line containing only --- separates messages.
        # Newlines, blank lines and emoji inside each block are preserved.
        usable = []
        current: list[str] = []
        for line in lines:
            if line.strip() == BLOCK_SEPARATOR:
                block = _clean_block(current)
                if block:
                    usable.append(block)
                current = []
                continue
            if line.lstrip().startswith("#"):
                continue
            current.append(line)
        block = _clean_block(current)
        if block:
            usable.append(block)

    if not usable:
        raise RuntimeError(f"文案檔沒有可用內容：{source}")
    return usable


def random_text_line(path: str | Path) -> str:
    return random.choice(load_text_lines(path))


def build_customer_reply(path: str | Path, telegram_account: str) -> str:
    message = random_text_line(path)
    account = (telegram_account or "").strip()
    if not account:
        raise RuntimeError("Telegram 帳號不可空白")
    # Two line breaks create one blank line between the selected message and
    # the Telegram account. reply_to_customer sends each newline as Shift+Enter.
    return f"{message}\n\n{account}"
