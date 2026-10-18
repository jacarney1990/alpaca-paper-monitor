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

    def test_static_assets_and_csp_are_compatible(self):
        response = self.client.get("/")
        markup = response.get_data(as_text=True)
        csp = response.headers["Content-Security-Policy"]
        self.assertEqual(response.status_code, 200)
        self.assertIn('src="/static/app.js"', markup)
        self.assertIn('href="/static/style.css"', markup)
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<style>", markup)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("style-src 'self'", csp)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/style.css").status_code, 200)

    def test_sidebar_navigation_is_interactive(self):
        markup = self.client.get("/").get_data(as_text=True)
        script = self.client.get("/static/app.js").get_data(as_text=True)
        for target in ("command", "history", "investment", "research", "project"):
            self.assertIn('id="nav-' + target + '"', markup)
            self.assertIn("'nav-" + target + "'", script)
        self.assertIn("chooseWorkflow('investment')", script)
        self.assertIn("chooseWorkflow('project')", script)

    def test_setup_panel_lists_missing_provider_names_safely(self):
        response = self.client.get("/")
        markup = response.get_data(as_text=True)
        self.assertIn('id="missing-keys"', markup)
        self.assertIn('id="provider-guidance"', markup)
        self.assertIn('https://dashboard.render.com/web/srv-dbaedtbncjis73d8hm60', markup)
        script = self.client.get("/static/app.js").get_data(as_text=True)
        for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
            self.assertIn(key, script)
        self.assertIn("loadFinance", script)
        self.assertIn("Practice missions", script)

    def test_health_has_no_trade_execution(self):
        data = self.client.get("/health").json
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["trade_execution"], "DISABLED")
        self.assertFalse(data["api_usage_enabled"])

    def test_api_requires_authentication(self):
        self.assertEqual(self.client.get("/api/providers").status_code, 401)
        self.assertEqual(self.client.get("/api/providers", headers=self.headers).status_code, 200)

    def test_finance_status_is_read_only_and_authenticated(self):
        self.assertEqual(self.client.get("/api/finance/status").status_code, 401)
        snapshot = {"workflows": [{"name": "V4.3 Paper Shadow Pilot", "status": "completed",
                                    "conclusion": "success", "updated": "",
                                    "url": ""}], "brokerage_execution": "DISABLED_IN_STUDIO"}
        with patch("app.latest_workflows", return_value=snapshot):
            response = self.client.get("/api/finance/status", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["brokerage_execution"], "DISABLED_IN_STUDIO")

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

    def test_safe_system_check_is_authenticated(self):
        self.assertEqual(self.client.get("/api/self-test").status_code, 401)
        result = self.client.get("/api/self-test", headers=self.headers).json
        self.assertEqual(result["service"], "online")
        self.assertEqual(result["storage"], "available")
        self.assertEqual(result["practice_mode"], "available")
        self.assertEqual(result["provider_authentication"], "not tested")
        self.assertEqual(result["trade_execution"], "DISABLED")
        self.assertEqual(result["usage"]["daily_limit"], 0)

    def test_free_practice_runs_offline_without_keys_or_live_permission(self):
        from time import sleep
        request = {"topic": "Explore a possible software project",
                   "mode": "team", "workflow": "project", "role": "Engineer",
                   "providers": ["OpenAI", "Gemini", "Groq"], "demo": True}
        with patch.object(engine, "invoke", side_effect=AssertionError("no network calls allowed")):
            posted = self.client.post("/api/jobs", json=request, headers=self.headers)
            self.assertEqual(posted.status_code, 202)
            self.assertEqual(posted.json["reserved_calls"], 0)
            result = None
            for _ in range(50):
                result = self.client.get("/api/jobs/"+posted.json["id"], headers=self.headers).json
                if result["status"] != "running":
                    break
                sleep(0.02)
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["output"]["simulation"])
        self.assertEqual(len(result["output"]["initial"]), 3)
        self.assertEqual(len(result["output"]["cross_review"]), 3)
        self.assertIn("simulated", result["output"]["synthesis"])
        self.assertIn("no AI providers", result["output"]["notice"])

    def test_live_calls_blocked_even_if_usage_toggle_on_without_cap(self):
        request = {"topic": "Real investment research",
                   "mode": "team", "workflow": "investment", "role": "Risk Auditor",
                   "providers": ["OpenAI", "Gemini", "Groq"], "demo": False}
        with patch.dict(os.environ, {"STUDIO_ALLOW_API_USAGE": "true",
                                     "STUDIO_DAILY_CALL_CAP": "0",
                                     "OPENAI_API_KEY": "test-key",
                                     "GEMINI_API_KEY": "test-key",
                                     "GROQ_API_KEY": "test-key"}):
            with patch.object(engine, "invoke", side_effect=AssertionError("no provider calls")):
                response = self.client.post("/api/jobs", json=request, headers=self.headers)
        self.assertEqual(response.status_code, 403)
        self.assertIn("daily AI-call safety limit", response.json["error"])

    def test_no_simulated_labels_on_real_engine(self):
        with patch.object(engine, "invoke", return_value="Stubbed independent answer"):
            result = engine.run_task("Check engineering assumptions", "independent",
                                     "Engineer", ["OpenAI"], "project")
        self.assertNotIn("simulation", result)
        self.assertEqual(result["trade_execution"], "DISABLED")

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
