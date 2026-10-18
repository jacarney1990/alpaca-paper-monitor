"""Offline, no-provider-call smoke tests for the isolated AI Studio."""
import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import engine

class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["STUDIO_DB_PATH"] = os.path.join(cls.temp.name, "jobs.sqlite3")
        os.environ["STUDIO_ACCESS_TOKEN"] = "unit-test-only"
        os.environ.pop("STUDIO_ALLOW_API_USAGE", None)
        import app
        cls.web = importlib.reload(app).app
        cls.web.testing = True
        cls.client = cls.web.test_client()
        cls.headers = {"X-Studio-Token": "unit-test-only"}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_health_has_no_trade_execution(self):
        data = self.client.get("/health").json
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["trade_execution"], "DISABLED")
        self.assertFalse(data["api_usage_enabled"])

    def test_api_requires_authentication(self):
        self.assertEqual(self.client.get("/api/providers").status_code, 401)
        self.assertEqual(self.client.get("/api/providers", headers=self.headers).status_code, 200)

    def test_invalid_input_is_rejected(self):
        data = {"topic": "x", "mode": "team", "workflow": "research",
                "role": "Researcher", "providers": ["OpenAI"]}
        self.assertEqual(self.client.post("/api/jobs", json=data,
                                          headers=self.headers).status_code, 400)

    def test_usage_disabled_even_with_key_configured(self):
        data = {"topic": "Research this question", "mode": "team",
                "workflow": "research", "role": "Researcher", "providers": ["OpenAI"]}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "dummy-never-sent"}):
            result = self.client.post("/api/jobs", json=data, headers=self.headers)
        self.assertEqual(result.status_code, 403)

    def test_independent_and_team_execution_offline(self):
        def stub(name, instruction, prompt):
            return "Independent finding from "+name
        with patch.object(engine, "invoke", side_effect=stub):
            a = engine.run_task("Examine research tradeoffs", "independent",
                                "Researcher", ["OpenAI", "Claude"], "research")
            b = engine.run_task("Examine research tradeoffs", "team",
                                "Researcher", ["OpenAI", "Claude"], "research")
        self.assertEqual(len(a["initial"]), 2)
        self.assertEqual(len(a["cross_review"]), 0)
        self.assertEqual(len(b["cross_review"]), 2)
        self.assertTrue(b["synthesis"])
        self.assertEqual(b["trade_execution"], "DISABLED")

if __name__ == "__main__":
    unittest.main()
