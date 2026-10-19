"""Offline source-adapter safety and normalization tests. Never call network."""
import json
import os
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import public_sources as ps

ORIGINAL_GITHUB_SNAPSHOT = ps._github_snapshot


class SourcesTest(unittest.TestCase):
    def setUp(self):
        ps._CACHE.clear()
        # All tests are strictly offline; simulate the GitHub snapshot as
        # unavailable except in explicit cache-fallback test cases.
        self.snapshot_stub = patch.object(
            ps, "_github_snapshot", side_effect=ps.SourceError("snapshot_unavailable"))
        self.snapshot_stub.start()
        self.addCleanup(self.snapshot_stub.stop)

    def test_sec_snapshot_fallback_from_current_official_github_data(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        content = {
            "schema": 1, "read_only": True, "updated_at": now,
            "sec": {"status": "ok", "stale": False,
                    "filings": {"CRSP": [{
                        "accession": "0001193125-26-330644",
                        "title": "CRSP • Form 8-K", "date": "2026-08-03",
                        "url": "https://www.sec.gov/Archives/edgar/data/1674416/"
                               "000119312526330644/crsp-20260803.htm"}]}},
            "fred": {"status": "ok", "series": {}}
        }
        with patch.object(ps, "_github_snapshot", return_value=content):
            with patch.object(ps, "_get_json") as direct:
                found = ps.sec_filings("CRSP")
        direct.assert_not_called()
        self.assertEqual(found["status"], "ok")
        self.assertTrue(found["snapshot"])
        self.assertFalse(found["stale"])
        self.assertEqual(len(found["items"]), 1)
        self.assertIn("2026-08-03", found["items"][0]["date"])

    def test_fred_snapshot_fallback_works_without_key(self):
        from datetime import datetime, timezone
        current = datetime.now(timezone.utc).isoformat()
        content = {
            "schema": 1, "read_only": True, "updated_at": current,
            "sec": {"status": "ok", "filings": {}},
            "fred": {"status": "ok", "stale": False,
                     "method": "Public FRED graph CSV",
                     "series": {"UNRATE": [{"date": "2026-08-01", "value": "4.3"}]}}
        }
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            with patch.object(ps, "_github_snapshot", return_value=content):
                with patch.object(ps, "_get_csv") as direct:
                    found = ps.fred_observations("UNRATE")
        direct.assert_not_called()
        self.assertEqual(found["status"], "ok")
        self.assertEqual(found["items"][0]["value"], "4.3")
        self.assertIn("Public FRED", found["note"])

    def test_old_cache_is_explicitly_stale_never_mislabeled_fresh(self):
        content = {
            "schema": 1, "read_only": True,
            "updated_at": "2020-01-01T00:00:00+00:00",
            "sec": {"status": "ok", "stale": False, "filings": {"CRSP": []}},
            "fred": {"status": "ok", "stale": False, "method": "Public FRED graph CSV",
                     "series": {"UNRATE": [{"date": "2020-01-01", "value": "3.3"}]}}
        }
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            with patch.object(ps, "_github_snapshot", return_value=content):
                found = ps.fred_observations("UNRATE")
        self.assertTrue(found["stale"])
        self.assertEqual(found["status"], "stale")

    def test_github_snapshot_validates_shape_and_does_not_contact_models(self):
        entry = {"schema": 1, "read_only": True,
                 "sec": {"status": "ok", "filings": {}},
                 "fred": {"status": "ok", "series": {}}}
        with patch.object(ps, "_get_json", return_value=entry) as get:
            with patch.object(ps, "_github_snapshot", side_effect=ORIGINAL_GITHUB_SNAPSHOT) as snapshot:
                item = snapshot()
        self.assertEqual(item["schema"], 1)
        self.assertEqual(get.call_count, 1)
        self.assertNotIn("api_key", json.dumps(item))

    def test_sec_filings_have_only_official_provenance(self):
        company = {"0": {"ticker": "CRSP", "title": "CRISPR Therapeutics", "cik_str": 12345}}
        recent = {"filings": {"recent": {
            "form": ["8-K", "10-Q", "unrelated"],
            "filingDate": ["2026-10-10", "2026-09-12", "2026-01-01"],
            "accessionNumber": ["0000012345-26-000001", "0000012345-26-000002",
                                "0000012345-26-000003"],
            "primaryDocument": ["event.htm", "report.htm", "ignore.htm"]}}}
        with patch.object(ps, "_get_json", side_effect=[company, recent]) as get:
            result = ps.sec_filings("crsp")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["title"], "CRSP • Form 8-K")
        self.assertIn("/Archives/edgar/data/12345/", result["items"][0]["url"])
        self.assertEqual(get.call_count, 2)
        self.assertTrue(all(x["authority"] == "SEC EDGAR" for x in result["items"]))
        with patch.object(ps, "_get_json") as get:
            cached = ps.sec_filings("CRSP")
        self.assertTrue(cached["cached"])
        get.assert_not_called()
        self.assertEqual(result["trading"], "DISABLED")

    def test_sec_invalid_ticker_fails_before_network(self):
        with patch.object(ps, "_get_json") as get:
            for name in ("AAPL/../../../etc", "a b", "", "A" * 30, "http://"):
                with self.assertRaises(ps.SourceError):
                    ps.sec_filings(name)
            get.assert_not_called()

    def test_sec_missing_ticker_is_no_result_not_invented(self):
        with patch.object(ps, "_get_json", return_value={}):
            result = ps.sec_filings("CRSP")
        self.assertEqual(result["status"], "no_results")
        self.assertFalse(result["items"])

    def test_fda_drug_and_device_recalls_only_official_urls(self):
        payload = {"results": [{"recall_number": "R-2026-77",
                    "product_description": "Aspirin 100mg", "reason_for_recall": "Voluntary sample",
                    "classification": "Class II", "report_date": "20261015"}]}
        with patch.object(ps, "_get_json", return_value=payload) as get:
            drug = ps.fda_records("aspirin", "drug_recalls")
            device = ps.fda_records("aspirin", "device_recalls")
        self.assertEqual(drug["status"], "ok")
        self.assertEqual(device["status"], "ok")
        self.assertTrue(drug["items"][0]["url"].startswith(
            "https://api.fda.gov/drug/enforcement.json?"))
        self.assertTrue(device["items"][0]["url"].startswith(
            "https://api.fda.gov/device/enforcement.json?"))
        self.assertEqual(get.call_count, 2)
        self.assertEqual(drug["ai_requests"], 0)

    def test_fda_drug_applications_and_no_results(self):
        data = {"results": [{"application_number": "NDA000001",
                "sponsor_name": "Example Labs",
                "products": [{"brand_name": "ExampleRx"}],
                "submissions": [{"submission_status_date": "20260101"}]}]}
        with patch.object(ps, "_get_json", return_value=data):
            result = ps.fda_records("Example Labs", "drug_approvals")
        self.assertEqual(result["items"][0]["title"], "ExampleRx")
        self.assertIn("NDA000001", result["items"][0]["summary"])
        with patch.object(ps, "_get_json", side_effect=ps.SourceError("no_results")):
            empty = ps.fda_records("Unlikely Product", "drug_approvals")
        self.assertEqual(empty["status"], "no_results")
        self.assertFalse(empty["items"])

    def test_fda_rejects_operator_injection(self):
        with patch.object(ps, "_get_json") as get:
            for value in ('hello" OR status:ongoing', "*:*", "", "x" * 120):
                with self.assertRaises(ps.SourceError):
                    ps.fda_records(value)
            with self.assertRaises(ps.SourceError):
                ps.fda_records("aspirin", "other")
            get.assert_not_called()

    def test_optional_openfda_key_never_in_returned_links(self):
        with patch.dict(os.environ, {"OPENFDA_API_KEY": "secret-test-api-key"}):
            with patch.object(ps, "_get_json", return_value={"results": []}) as get:
                result = ps.fda_records("aspirin")
        self.assertEqual(get.call_args.kwargs["params"]["api_key"],
                         "secret-test-api-key")
        self.assertNotIn("secret-test-api-key", json.dumps(result))
        self.assertEqual(result["status"], "no_results")

    def test_fred_works_without_key_using_public_csv(self):
        csv_data = "DATE,UNRATE\n2026-06-01,4.0\n2026-07-01,.\n2026-08-01,4.2\n"
        with patch.dict(os.environ, {"FRED_API_KEY": ""}):
            with patch.object(ps, "_get_json") as api:
                with patch.object(ps, "_get_csv", return_value=csv_data) as csv_req:
                    result = ps.fred_observations("UNRATE")
                api.assert_not_called()
        self.assertEqual(csv_req.call_count, 1)
        self.assertEqual([x["value"] for x in result["items"]], ["4.0", "4.2"])
        self.assertIn("Public FRED CSV", result["note"])
        self.assertEqual(result["source"], "FRED")

    def test_fred_api_key_is_never_returned_and_uses_json(self):
        data = {"observations": [{"date": "2026-08-01", "value": "4.2"},
                                 {"date": "2026-07-01", "value": "4.1"}]}
        with patch.dict(os.environ, {"FRED_API_KEY": "dont-leak-key"}):
            with patch.object(ps, "_get_json", return_value=data) as api:
                result = ps.fred_observations("UNRATE")
        self.assertEqual(api.call_args.kwargs["params"]["api_key"], "dont-leak-key")
        self.assertNotIn("dont-leak-key", json.dumps(result))
        self.assertIn("FRED API", result["note"])
        self.assertEqual(result["items"][0]["date"], "2026-07-01")

    def test_fred_only_allowed_indicator_ids(self):
        with patch.object(ps, "_get_json") as api:
            with self.assertRaises(ps.SourceError):
                ps.fred_observations("https://attacker.test")
            with self.assertRaises(ps.SourceError):
                ps.fred_observations("secret-custom-id")
            api.assert_not_called()

    def test_lookup_dispatch_and_status_have_no_credentials(self):
        status = ps.source_status()
        self.assertEqual(set(status), {"SEC", "FDA", "FRED"})
        self.assertTrue(all(x["ready"] for x in status.values()))
        with self.assertRaises(ps.SourceError):
            ps.lookup("OTHER", {"ticker": "CRSP"})
        self.assertNotIn("secret", json.dumps(status).lower())

    def test_download_fails_closed_on_redirect_without_following(self):
        class FakeResponse:
            status_code = 302
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        with patch.object(ps.requests, "get", return_value=FakeResponse()) as http:
            with self.assertRaisesRegex(ps.SourceError, "source_unavailable"):
                ps._download("https://api.fda.gov/drug/enforcement.json")
        self.assertFalse(http.call_args.kwargs["allow_redirects"])

    def test_response_has_size_cap(self):
        class FakeResponse:
            status_code = 200
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def iter_content(self, chunk_size):
                yield b"x" * (ps.MAX_BYTES + 1)
        with patch.object(ps.requests, "get", return_value=FakeResponse()):
            with self.assertRaisesRegex(ps.SourceError, "response_too_large"):
                ps._download("https://api.fda.gov/drug/enforcement.json")


if __name__ == "__main__":
    unittest.main()
