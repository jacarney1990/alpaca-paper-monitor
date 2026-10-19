"""Government snapshot offline regression tests. No external requests."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import official_sources_snapshot as gov


class SnapshotTests(unittest.TestCase):
    def test_sec_filings_are_from_official_edgar_urls(self):
        directory = {"0": {"ticker": "CRSP", "cik_str": 1674416}}
        filing = {"filings": {"recent": {
            "form": ["8-K", "10-Q", "EX-99"],
            "filingDate": ["2026-10-16", "2026-08-03", "2026-08-03"],
            "accessionNumber": ["0001193125-26-330644",
                                "0001193125-26-330672", "0001193125-26-330644"],
            "primaryDocument": ["crsp-20260803.htm", "crsp-20260630.htm", "invalid.htm"]}}}
        with patch.dict(os.environ, {"SEC_USER_AGENT": "Research research@example.com"}):
            with patch.object(gov.base, "SYMBOLS", {"CRSP": "CRISPR Therapeutics"}):
                with patch.object(gov.base, "request_json",
                                  side_effect=[directory, filing]) as remote:
                    result = gov.sec_snapshot()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["covered"], 1)
        self.assertEqual(len(result["filings"]["CRSP"]), 2)
        self.assertTrue(result["filings"]["CRSP"][0]["url"].startswith(
            "https://www.sec.gov/Archives/edgar/data/1674416/"))
        self.assertEqual(remote.call_count, 2)

    def test_sec_missing_contact_fails_closed(self):
        with patch.dict(os.environ, {"SEC_USER_AGENT": ""}):
            with patch.object(gov.base, "request_json") as request:
                result = gov.sec_snapshot()
        self.assertEqual(result["status"], "missing_user_agent")
        request.assert_not_called()

    def test_fred_csv_collects_only_provenance_safe_numbers(self):
        def fake_csv(url):
            from urllib.parse import parse_qs, urlsplit
            series = parse_qs(urlsplit(url).query)["id"][0]
            return "DATE," + series + "\n2026-09-01,3.0\n2026-10-01,.\n"
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            with patch.object(gov, "_fred_text", side_effect=fake_csv) as remote:
                result = gov.fred_snapshot()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["series"]), len(gov.SERIES))
        self.assertEqual(len(gov.SERIES), 8)
        self.assertIn("PCEPI", result["series"])
        self.assertIn("INDPRO", result["series"])
        self.assertEqual(result["series"]["UNRATE"][0]["value"], "3.0")
        self.assertEqual(remote.call_count, len(gov.SERIES))

    def test_fred_free_key_can_be_used_without_publishing_it(self):
        payload = {"observations": [{"date": "2026-10-01", "value": "4.0"}]}
        with patch.dict(os.environ, {"FRED_API_KEY": "never-share-key"}):
            with patch.object(gov.base, "request_json", return_value=payload) as request:
                result = gov.fred_snapshot()
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("never-share-key", json.dumps(result))
        self.assertEqual(request.call_count, len(gov.SERIES))

    def test_outage_preserves_previous_snapshot_but_marks_stale(self):
        previous = {"sec": {"status": "ok", "filings": {"CRSP": [{"title": "Prior"}]},
                            "covered": 1},
                    "fred": {"status": "ok", "series": {"UNRATE": [
                            {"date": "2026-09-01", "value": "4.0"}]}}}
        with patch.object(gov, "sec_snapshot", return_value={
                "status": "source_unavailable", "filings": {}, "covered": 0}):
            with patch.object(gov, "fred_snapshot", return_value={
                    "status": "source_unavailable", "series": {}}):
                result = gov.build_snapshot(previous,
                        datetime(2026, 10, 19, tzinfo=timezone.utc))
        self.assertTrue(result["sec"]["stale"])
        self.assertEqual(result["sec"]["latest_refresh_status"], "source_unavailable")
        self.assertEqual(result["sec"]["filings"]["CRSP"][0]["title"], "Prior")
        self.assertTrue(result["fred"]["stale"])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["brokerage_orders"], 0)

    def test_main_writes_only_public_data(self):
        result = {
            "schema": 1, "updated_at": "2026-10-19T00:00:00+00:00",
            "sec": {"status": "ok", "filings": {}, "covered": 0},
            "fred": {"status": "ok", "series": {}},
            "read_only": True, "model_inference_calls": 0,
            "brokerage_orders": 0}
        with tempfile.TemporaryDirectory() as td:
            outfile = Path(td) / "data" / "official_sources_snapshot.json"
            with patch.object(gov, "OUTPUT", outfile):
                with patch.object(gov, "build_snapshot", return_value=result):
                    gov.main()
            saved = json.loads(outfile.read_text(encoding="utf-8"))
        self.assertTrue(saved["read_only"])
        self.assertEqual(saved["brokerage_orders"], 0)


if __name__ == "__main__":
    unittest.main()
