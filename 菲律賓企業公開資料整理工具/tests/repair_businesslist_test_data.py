import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database
from scrapers.businesslist import BusinessListScraper

db = Database()
with db.connect() as conn:
    conn.execute(
        "DELETE FROM phones WHERE company_id=1 AND normalized_phone IN (?, ?)",
        ("09123456789", "324010279"),
    )
    row = conn.execute("SELECT source_url FROM companies WHERE id=1").fetchone()
    urls = [u for u in (row[0] or "").split("; ") if "company/300025/" not in u]
    conn.execute("UPDATE companies SET source_url=? WHERE id=1", ("; ".join(urls),))
    db._refresh_flat(conn, 1)

rows = list(BusinessListScraper(.1, .2, log=print).search(max_results=3))
print("SCRAPED", len(rows), [row.company_name for row in rows])
for row in rows:
    print("UPSERT", row.company_name, db.upsert_company(row))
print("DB_ROWS", len(db.all_companies()))
