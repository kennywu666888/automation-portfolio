"""Messenger 查詢與回覆使用的 SQLite 持久化工作佇列。"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 profile_id TEXT NOT NULL,
 profile_name TEXT NOT NULL DEFAULT '',
 chat_id TEXT NOT NULL,
 chat_name TEXT NOT NULL DEFAULT '',
 chat_url TEXT NOT NULL DEFAULT '',
 message_text TEXT NOT NULL,
 message_hash TEXT NOT NULL,
 message_direction TEXT NOT NULL DEFAULT 'incoming',
 direction_verified INTEGER NOT NULL DEFAULT 1,
 is_unread INTEGER NOT NULL DEFAULT 0,
 telegram_account TEXT NOT NULL DEFAULT '',
 is_lead INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending'
   CHECK(status IN ('pending','processing','replied','skipped','changed','failed','restricted')),
 locked_by TEXT NOT NULL DEFAULT '',
 locked_at TEXT,
 query_time TEXT NOT NULL,
 reply_time TEXT,
 reply_text TEXT NOT NULL DEFAULT '',
 retry_count INTEGER NOT NULL DEFAULT 0,
 max_retries INTEGER NOT NULL DEFAULT 3,
 last_error TEXT NOT NULL DEFAULT '',
 diagnostic_zip TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(profile_id, chat_id, message_hash)
);
CREATE INDEX IF NOT EXISTS idx_chat_jobs_claim
 ON chat_jobs(profile_id, status, retry_count, id);
CREATE TABLE IF NOT EXISTS telegram_reports (
 report_key TEXT PRIMARY KEY,
 telegram_account TEXT NOT NULL,
 sent_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_message_hash(profile_id: str, chat_id: str, text: str) -> str:
    normalized = " ".join((text or "").split())
    raw = f"{profile_id}\n{chat_id}\n{normalized}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ChatJob:
    id: int
    profile_id: str
    profile_name: str
    chat_id: str
    chat_name: str
    chat_url: str
    message_text: str
    message_hash: str
    is_unread: bool
    telegram_account: str
    is_lead: bool
    retry_count: int
    max_retries: int
    locked_by: str


class ChatRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    @contextmanager
    def connection(self):
        """提供一定會關閉的唯讀／一般 SQLite 連線。"""
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(SCHEMA)
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(chat_jobs)")
            }
            if "direction_verified" not in columns:
                # V4.0.9 舊資料的方向曾以 incoming 作預設，不能直接回覆。
                conn.execute(
                    "ALTER TABLE chat_jobs ADD COLUMN direction_verified "
                    "INTEGER NOT NULL DEFAULT 0"
                )

    def enqueue(
        self, *, profile_id: str, profile_name: str, chat_id: str,
        chat_name: str, chat_url: str, message_text: str, is_unread: bool,
        telegram_account: str = "", is_lead: bool = False, max_retries: int = 3,
    ) -> tuple[int, bool]:
        message_hash = make_message_hash(profile_id, chat_id, message_text)
        now = utc_now()
        with self.transaction() as conn:
            existing = conn.execute(
                """SELECT id,direction_verified FROM chat_jobs
                   WHERE profile_id=? AND chat_id=? AND message_hash=?""",
                (profile_id, chat_id, message_hash),
            ).fetchone()
            if existing and not int(existing["direction_verified"]):
                conn.execute(
                    """UPDATE chat_jobs SET profile_name=?,chat_name=?,chat_url=?,
                       message_direction='incoming',direction_verified=1,is_unread=?,
                       telegram_account=?,is_lead=?,status='pending',locked_by='',
                       locked_at=NULL,retry_count=0,last_error='',query_time=?,
                       max_retries=?,updated_at=? WHERE id=?""",
                    (profile_name, chat_name, chat_url, int(is_unread),
                     telegram_account, int(is_lead), now, max(1, max_retries),
                     now, int(existing["id"])),
                )
                return int(existing["id"]), True
            cursor = conn.execute(
                """INSERT OR IGNORE INTO chat_jobs (
                 profile_id,profile_name,chat_id,chat_name,chat_url,message_text,
                 message_hash,message_direction,direction_verified,is_unread,
                 telegram_account,is_lead,
                 status,query_time,max_retries,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,'incoming',1,?,?,?,'pending',?,?,?,?)""",
                (profile_id, profile_name, chat_id, chat_name, chat_url, message_text,
                 message_hash, int(is_unread), telegram_account, int(is_lead),
                 now, max(1, max_retries), now, now),
            )
            row = conn.execute(
                "SELECT id FROM chat_jobs WHERE profile_id=? AND chat_id=? AND message_hash=?",
                (profile_id, chat_id, message_hash),
            ).fetchone()
            return int(row["id"]), cursor.rowcount == 1

    def recover_stale(self, minutes: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self.transaction() as conn:
            cur = conn.execute(
                """UPDATE chat_jobs SET status='pending',locked_by='',locked_at=NULL,
                   updated_at=? WHERE status='processing' AND locked_at < ?""",
                (utc_now(), cutoff),
            )
            return cur.rowcount

    def claim(self, profile_id: str, limit: int, worker_id: str | None = None) -> list[ChatJob]:
        worker = worker_id or uuid.uuid4().hex
        with self.transaction() as conn:
            rows = conn.execute(
                """SELECT * FROM chat_jobs WHERE profile_id=? AND status='pending'
                   AND direction_verified=1
                   AND retry_count < max_retries ORDER BY is_unread DESC,id ASC LIMIT ?""",
                (profile_id, max(1, limit)),
            ).fetchall()
            claimed: list[ChatJob] = []
            for row in rows:
                cur = conn.execute(
                    """UPDATE chat_jobs SET status='processing',locked_by=?,locked_at=?,
                       updated_at=? WHERE id=? AND status='pending'""",
                    (worker, utc_now(), utc_now(), row["id"]),
                )
                if cur.rowcount:
                    claimed.append(ChatJob(
                        id=row["id"], profile_id=row["profile_id"],
                        profile_name=row["profile_name"], chat_id=row["chat_id"],
                        chat_name=row["chat_name"], chat_url=row["chat_url"],
                        message_text=row["message_text"], message_hash=row["message_hash"],
                        is_unread=bool(row["is_unread"]),
                        telegram_account=row["telegram_account"],
                        is_lead=bool(row["is_lead"]), retry_count=row["retry_count"],
                        max_retries=row["max_retries"], locked_by=worker,
                    ))
            return claimed

    def _finish(self, job: ChatJob, status: str, **values) -> bool:
        allowed = {"reply_text", "last_error", "diagnostic_zip", "reply_time"}
        sets = ["status=?", "locked_by=''", "locked_at=NULL", "updated_at=?"]
        params: list[object] = [status, utc_now()]
        for key, value in values.items():
            if key in allowed:
                sets.append(f"{key}=?")
                params.append(value)
        params.extend([job.id, job.locked_by])
        with self.transaction() as conn:
            cur = conn.execute(
                f"UPDATE chat_jobs SET {','.join(sets)} WHERE id=? AND status='processing' AND locked_by=?",
                params,
            )
            return cur.rowcount == 1

    def mark_replied(self, job: ChatJob, reply_text: str) -> bool:
        return self._finish(job, "replied", reply_text=reply_text, reply_time=utc_now())

    def mark_changed(self, job: ChatJob, error: str) -> bool:
        return self._finish(job, "changed", last_error=error)

    def mark_skipped(self, job: ChatJob, reason: str) -> bool:
        return self._finish(job, "skipped", last_error=reason)

    def mark_restricted(self, job: ChatJob, reason: str, diagnostic_zip: str = "") -> bool:
        return self._finish(job, "restricted", last_error=reason, diagnostic_zip=diagnostic_zip)

    def release(self, job: ChatJob, reason: str = "") -> bool:
        """使用者停止或尚未操作時解除鎖定，不消耗重試次數。"""
        params = (reason, utc_now(), job.id, job.locked_by)
        with self.transaction() as conn:
            cur = conn.execute(
                """UPDATE chat_jobs SET status='pending',last_error=?,locked_by='',
                   locked_at=NULL,updated_at=?
                   WHERE id=? AND status='processing' AND locked_by=?""",
                params,
            )
            return cur.rowcount == 1

    def mark_failed(self, job: ChatJob, error: str, diagnostic_zip: str = "") -> bool:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT retry_count,max_retries FROM chat_jobs WHERE id=? AND status='processing' AND locked_by=?",
                (job.id, job.locked_by),
            ).fetchone()
            if not row:
                return False
            retry = int(row["retry_count"]) + 1
            status = "failed" if retry >= int(row["max_retries"]) else "pending"
            cur = conn.execute(
                """UPDATE chat_jobs SET status=?,retry_count=?,last_error=?,diagnostic_zip=?,
                   locked_by='',locked_at=NULL,updated_at=? WHERE id=? AND locked_by=?""",
                (status, retry, error, diagnostic_zip, utc_now(), job.id, job.locked_by),
            )
            return cur.rowcount == 1

    def get(self, job_id: int) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM chat_jobs WHERE id=?", (job_id,)).fetchone()

    def counts(self) -> dict[str, int]:
        with self.connection() as conn:
            return {row["status"]: row["n"] for row in conn.execute(
                "SELECT status,COUNT(*) n FROM chat_jobs GROUP BY status"
            )}
