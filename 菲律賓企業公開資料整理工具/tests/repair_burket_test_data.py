import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database
from scrapers.burket import BurketScraper

db = Database()
with db.connect() as conn:
    bad_rows = conn.execute(
        "SELECT id FROM companies WHERE source='Burket' AND email LIKE '%demo@example.com%'"
    ).fetchall()
    for row in bad_rows:
        company_id = row[0]
        conn.execute("DELETE FROM emails WHERE company_id=?", (company_id,))
        conn.execute("DELETE FROM phones WHERE company_id=?", (company_id,))
        conn.execute("DELETE FROM companies WHERE id=?", (company_id,))

rows = list(BurketScraper(.1, .2, log=print).search(max_results=10))
for row in rows:
    db.upsert_company(row)
print("BURKET_REPAIRED", len(rows), [row.company_name for row in rows])
print("TOTAL", len(db.all_companies()))
