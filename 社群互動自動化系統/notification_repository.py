import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DB = Path(__file__).resolve().parent / "notification_history.db"


class NotificationRepository:
    def __init__(self, path=DB):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_column(self, connection, name: str, sql_type: str, default_sql: str = ""):
        columns = {row[1] for row in connection.execute("PRAGMA table_info(comment_reply_notifications)")}
        if name not in columns:
            suffix = f" DEFAULT {default_sql}" if default_sql else ""
            connection.execute(
                f"ALTER TABLE comment_reply_notifications ADD COLUMN {name} {sql_type}{suffix}"
            )

    def init(self):
        with self.lock, closing(self.connect()) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS comment_reply_notifications("
                    "id INTEGER PRIMARY KEY,profile_id TEXT,profile_name TEXT,"
                    "notification_key TEXT UNIQUE,notification_text TEXT,notification_url TEXT,"
                    "reply_user TEXT,reply_text TEXT,original_comment TEXT,notification_time TEXT,"
                    "status TEXT,telegram_sent INTEGER DEFAULT 0,first_seen_at TEXT,processed_at TEXT,"
                    "retry_count INTEGER DEFAULT 0,last_error TEXT)"
                )
                self._ensure_column(connection, "facebook_reply_sent", "INTEGER", "0")
                self._ensure_column(connection, "sent_reply_text", "TEXT", "''")
                self._ensure_column(connection, "incoming_telegram_sent", "INTEGER", "0")
                self._ensure_column(connection, "replied_telegram_sent", "INTEGER", "0")
                self._ensure_column(connection, "post_author", "TEXT", "''")
                self._ensure_column(connection, "facebook_url", "TEXT", "''")

    def is_reported(self, key):
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT status FROM comment_reply_notifications WHERE notification_key=?",
                (key,),
            ).fetchone()
            return bool(row and row["status"] == "reported")

    def get_state(self, key):
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM comment_reply_notifications WHERE notification_key=?",
                (key,),
            ).fetchone()
            return dict(row) if row else None

    def pending_replied(self, profile_id):
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM comment_reply_notifications "
                "WHERE profile_id=? AND facebook_reply_sent=1 "
                "AND replied_telegram_sent=0 AND status<>'reported' "
                "ORDER BY processed_at",
                (profile_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark(
        self,
        profile_id,
        profile_name,
        candidate,
        status,
        reply=None,
        error="",
        reply_sent=False,
        sent_reply_text="",
        incoming_telegram_sent=False,
        replied_telegram_sent=False,
    ):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        values = (
            profile_id,
            profile_name,
            candidate.key,
            candidate.text,
            candidate.url,
            getattr(reply, "reply_user", ""),
            getattr(reply, "reply_text", ""),
            getattr(reply, "original_comment", ""),
            getattr(reply, "notification_time", ""),
            status,
            1 if status == "reported" else 0,
            now,
            now,
            1 if status == "failed" else 0,
            error[:1000],
            1 if reply_sent else 0,
            sent_reply_text,
            1 if incoming_telegram_sent else 0,
            1 if replied_telegram_sent else 0,
            getattr(reply, "post_author", ""),
            getattr(reply, "facebook_url", ""),
        )
        sql = (
            "INSERT INTO comment_reply_notifications("
            "profile_id,profile_name,notification_key,notification_text,notification_url,"
            "reply_user,reply_text,original_comment,notification_time,status,telegram_sent,"
            "first_seen_at,processed_at,retry_count,last_error,facebook_reply_sent,sent_reply_text,"
            "incoming_telegram_sent,replied_telegram_sent,post_author,facebook_url"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(notification_key) DO UPDATE SET "
            "status=excluded.status,"
            "telegram_sent=MAX(comment_reply_notifications.telegram_sent,excluded.telegram_sent),"
            "processed_at=excluded.processed_at,last_error=excluded.last_error,"
            "retry_count=comment_reply_notifications.retry_count+excluded.retry_count,"
            "reply_user=CASE WHEN excluded.reply_user<>'' THEN excluded.reply_user ELSE comment_reply_notifications.reply_user END,"
            "reply_text=CASE WHEN excluded.reply_text<>'' THEN excluded.reply_text ELSE comment_reply_notifications.reply_text END,"
            "original_comment=CASE WHEN excluded.original_comment<>'' THEN excluded.original_comment ELSE comment_reply_notifications.original_comment END,"
            "notification_time=CASE WHEN excluded.notification_time<>'' THEN excluded.notification_time ELSE comment_reply_notifications.notification_time END,"
            "facebook_reply_sent=MAX(comment_reply_notifications.facebook_reply_sent,excluded.facebook_reply_sent),"
            "sent_reply_text=CASE WHEN excluded.sent_reply_text<>'' THEN excluded.sent_reply_text ELSE comment_reply_notifications.sent_reply_text END,"
            "incoming_telegram_sent=MAX(comment_reply_notifications.incoming_telegram_sent,excluded.incoming_telegram_sent),"
            "replied_telegram_sent=MAX(comment_reply_notifications.replied_telegram_sent,excluded.replied_telegram_sent),"
            "post_author=CASE WHEN excluded.post_author<>'' THEN excluded.post_author ELSE comment_reply_notifications.post_author END,"
            "facebook_url=CASE WHEN excluded.facebook_url<>'' THEN excluded.facebook_url ELSE comment_reply_notifications.facebook_url END"
        )
        with self.lock, closing(self.connect()) as connection:
            with connection:
                connection.execute(sql, values)
