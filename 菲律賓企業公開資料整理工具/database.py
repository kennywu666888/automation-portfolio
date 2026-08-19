import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from models import Company
from 工具 import normalize_name, normalize_phone


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback and close reliably; important for Windows file locking."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


class Database:
    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).parent / "database" / "companies.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.init_schema()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init_schema(self):
        sql = """
        CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, company_name TEXT NOT NULL,
          normalized_name TEXT NOT NULL, category TEXT, email TEXT, phone TEXT, mobile TEXT, fax TEXT,
          address TEXT, city TEXT, province TEXT, website TEXT, facebook TEXT, contact_person TEXT,
          description TEXT, source TEXT, source_url TEXT, email_source_url TEXT, license_number TEXT,
          completeness INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(normalized_name);
        CREATE TABLE IF NOT EXISTS emails(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
          email TEXT COLLATE NOCASE, first_source TEXT, other_sources TEXT, source_url TEXT, source_type TEXT,
          UNIQUE(company_id,email), FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS phones(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
          phone TEXT, normalized_phone TEXT, kind TEXT, source TEXT, UNIQUE(company_id,normalized_phone,kind),
          FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS crawl_queue(id INTEGER PRIMARY KEY, source TEXT, category TEXT, page INTEGER,
          company_url TEXT, status TEXT, task_id TEXT, updated_at TEXT, UNIQUE(task_id,source,company_url));
        CREATE TABLE IF NOT EXISTS crawl_history(id INTEGER PRIMARY KEY, task_id TEXT, source TEXT, category TEXT,
          page INTEGER, company_url TEXT, status TEXT, message TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        """
        with self.connect() as conn:
            conn.executescript(sql)

    def _match(self, conn, c):
        name = normalize_name(c.company_name)
        rows = conn.execute("SELECT * FROM companies WHERE normalized_name=?", (name,)).fetchall()
        if len(name) >= 5 and rows:
            return rows[0]["id"]
        domain = urlparse(c.website).netloc.lower().removeprefix("www.") if c.website else ""
        for phone in c.phones + c.mobiles:
            norm = normalize_phone(phone)
            if norm:
                row = conn.execute("SELECT company_id FROM phones WHERE normalized_phone=? LIMIT 1", (norm,)).fetchone()
                if row: return row[0]
        for email in c.emails:
            row = conn.execute("SELECT company_id FROM emails WHERE email=? COLLATE NOCASE LIMIT 1", (email,)).fetchone()
            if row: return row[0]
        if domain:
            row = conn.execute("SELECT id FROM companies WHERE lower(website) LIKE ? LIMIT 1", (f"%{domain}%",)).fetchone()
            if row: return row[0]
        return None

    def upsert_company(self, c: Company):
        now = datetime.now().isoformat(timespec="seconds")
        with self.lock, self.connect() as conn:
            cid = self._match(conn, c)
            if cid:
                old = conn.execute("SELECT * FROM companies WHERE id=?", (cid,)).fetchone()
                def merge(field, value): return value or old[field] or ""
                sources = sorted(set(filter(None, (old["source"] or "").split("; ") + c.sources)))
                urls = sorted(set(filter(None, (old["source_url"] or "").split("; ") + c.source_urls)))
                conn.execute("""UPDATE companies SET category=?, address=?, city=?, province=?, website=?,
                    facebook=?, contact_person=?, description=?, source=?, source_url=?, email_source_url=?,
                    license_number=?, completeness=max(completeness,?), updated_at=? WHERE id=?""",
                    (merge("category",c.category), merge("address",c.address), merge("city",c.city),
                     merge("province",c.province), merge("website",c.website), merge("facebook",c.facebook),
                     merge("contact_person",c.contact_person), merge("description",c.description), "; ".join(sources),
                     "; ".join(urls), merge("email_source_url",c.email_source_url), merge("license_number",c.license_number),
                     c.completeness, now, cid))
                duplicate = True
            else:
                cur = conn.execute("""INSERT INTO companies(company_name,normalized_name,category,email,phone,mobile,fax,
                    address,city,province,website,facebook,contact_person,description,source,source_url,email_source_url,
                    license_number,completeness,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (c.company_name,normalize_name(c.company_name),c.category,"; ".join(c.emails),"; ".join(c.phones),
                     "; ".join(c.mobiles),"; ".join(c.fax),c.address,c.city,c.province,c.website,c.facebook,c.contact_person,
                     c.description,"; ".join(c.sources),"; ".join(c.source_urls),c.email_source_url,c.license_number,
                     c.completeness,now,now))
                cid, duplicate = cur.lastrowid, False
            for email in c.emails:
                conn.execute("INSERT OR IGNORE INTO emails(company_id,email,first_source,other_sources,source_url,source_type) VALUES(?,?,?,?,?,?)",
                             (cid,email,c.sources[0] if c.sources else "","; ".join(c.sources[1:]),c.email_source_url,c.email_source_type))
            for kind, values in (("phone",c.phones),("mobile",c.mobiles),("fax",c.fax)):
                for phone in values:
                    conn.execute("INSERT OR IGNORE INTO phones(company_id,phone,normalized_phone,kind,source) VALUES(?,?,?,?,?)",
                                 (cid,phone,normalize_phone(phone),kind,"; ".join(c.sources)))
            self._refresh_flat(conn, cid)
            return cid, duplicate

    def _refresh_flat(self, conn, cid):
        emails = [r[0] for r in conn.execute("SELECT email FROM emails WHERE company_id=? ORDER BY email",(cid,))]
        def phones(kind): return [r[0] for r in conn.execute("SELECT phone FROM phones WHERE company_id=? AND kind=?",(cid,kind))]
        conn.execute("UPDATE companies SET email=?,phone=?,mobile=?,fax=? WHERE id=?",
                     ("; ".join(emails),"; ".join(phones('phone')),"; ".join(phones('mobile')),"; ".join(phones('fax')),cid))

    def all_companies(self):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM companies ORDER BY id")]

    def set_setting(self, key, value):
        with self.connect() as conn:
            conn.execute("INSERT INTO settings VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                         (key,json.dumps(value,ensure_ascii=False),datetime.now().isoformat(timespec='seconds')))

    def get_setting(self, key, default=None):
        with self.connect() as conn:
            row=conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def save_progress(self, task_id, source, category, page, url, status, message=""):
        now=datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO crawl_queue(source,category,page,company_url,status,task_id,updated_at) VALUES(?,?,?,?,?,?,?)",
                         (source,category,page,url,status,task_id,now))
            conn.execute("INSERT INTO crawl_history(task_id,source,category,page,company_url,status,message,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (task_id,source,category,page,url,status,message,now))
