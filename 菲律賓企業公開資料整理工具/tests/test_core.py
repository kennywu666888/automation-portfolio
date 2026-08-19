import tempfile
import unittest
from pathlib import Path
from database import Database
from exporter import Exporter
from models import Company
from 工具 import extract_emails, normalize_name


class CoreTests(unittest.TestCase):
    def test_email_and_name(self):
        self.assertEqual(extract_emails("MAILTO:demo@example.com demo@example.com"),["demo@example.com"])
        self.assertEqual(normalize_name("ABC Construction, Inc."),normalize_name("ABC Construction Incorporated"))
    def test_database_dedup_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"companies.db")
            db.upsert_company(Company("Real Builder, Inc.",emails=["demo@example.com"],phones=["+63 2 8123 4567"],sources=["Test"],source_urls=["https://source.invalid/1"]))
            _,duplicate=db.upsert_company(Company("REAL BUILDER INC",city="Manila",sources=["Second"]));self.assertTrue(duplicate);self.assertEqual(len(db.all_companies()),1)
            exporter=Exporter(db);xlsx=exporter.export_excel(tmp);txt=exporter.export_txt(tmp)
            self.assertTrue(xlsx.exists());self.assertEqual(len(txt),5)


if __name__=="__main__":unittest.main()
