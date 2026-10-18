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

    def test_nexus_dom_ids_match_external_js(self):
        import re
        markup = self.client.get("/").get_data(as_text=True)
        script = self.client.get("/static/app.js").get_data(as_text=True)
        known_ids = set(re.findall(r'id="([^"]+)"', markup))
        referenced_ids = set(re.findall(r"\$\('([^']+)'\)", script))
        self.assertEqual(referenced_ids - known_ids, set(),
                         "Frontend references IDs missing from rendered HTML")
        self.assertIn("currentWorkflow=workflow", script)
        self.assertIn("workflow:currentWorkflow", script)
        self.assertNotIn("$('workflow')", script)

    def test_practice_mission_runs_without_provider_calls(self):
        import time
        request = {"topic": "Explain a basic research plan", "mode": "team",
                   "workflow": "research", "role": "Researcher",
                   "providers": ["OpenAI", "Gemini", "Groq"], "demo": True}
        with patch.object(engine, "invoke", side_effect=AssertionError("No paid calls allowed")):
            response = self.client.post("/api/jobs", json=request, headers=self.headers)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json["reserved_calls"], 0)
            mission_id = response.json["id"]
            for _ in range(25):
                result = self.client.get("/api/jobs/" + mission_id, headers=self.headers).json
                if result["status"] != "running":
                    break
                time.sleep(0.04)
            self.assertEqual(result["status"], "complete")
            self.assertTrue(result["output"]["simulation"])
            self.assertEqual(len(result["output"]["initial"]), 3)
            self.assertEqual(len(result["output"]["cross_review"]), 3)
            self.assertIn("PRACTICE EXAMPLE", result["output"]["synthesis"])

    def test_opt_in_deployed_safe_check_uses_practice_and_metadata_only(self):
        import app as studio_app
        responses = {"OpenAI": {"status": "valid"},
                     "Gemini": {"status": "valid"},
                     "Groq": {"status": "limited"},
                     "Claude": {"status": "missing"}}
        with patch.dict(os.environ, {"STUDIO_RUN_SAFE_TEST": "true",
                                     "STUDIO_ALLOW_API_USAGE": "false"}):
            with patch.object(studio_app, "check_provider_key",
                              side_effect=lambda name: responses[name]) as checked:
                with patch.object(engine, "invoke",
                                  side_effect=AssertionError("no inference permitted")):
                    with self.assertLogs(studio_app.app.logger, level="WARNING") as logs:
                        studio_app.run_one_time_safe_test()
        summary = " ".join(logs.output)
        self.assertIn("practice=pass", summary)
        self.assertIn("OpenAI=valid", summary)
        self.assertIn("Groq=limited", summary)
        self.assertIn("ai_inference_calls=0", summary)
        self.assertIn("brokerage_orders=0", summary)
        self.assertEqual(checked.call_count, 4)
        recorded = self.client.get("/api/jobs", headers=self.headers).json
        self.assertTrue(any(x["topic"].startswith("Nexus system test:") for x in recorded))

    def test_alpaca_monitor_is_accessible_from_studio_portal(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("https://alpaca-paper-monitor-zgv9.onrender.com", page)
        self.assertIn('id="check-finance"', page)

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

    def test_provider_validation_metadata_only_and_requires_login(self):
        self.assertEqual(self.client.post("/api/providers/check").status_code, 401)
        def mock_result(provider):
            return {"status": "valid" if provider != "Claude" else "missing",
                    "detail": "Fake metadata result only"}
        with patch("app.check_provider_key", side_effect=mock_result) as checked:
            response = self.client.post("/api/providers/check", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["model_inference_performed"])
        self.assertEqual(response.json["brokerage_orders"], 0)
        self.assertEqual(response.json["providers"]["OpenAI"]["status"], "valid")
        self.assertEqual(response.json["providers"]["Claude"]["status"], "missing")
        self.assertEqual(checked.call_count, 4)

    def test_provider_key_check_makes_no_inference_requests(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "dummy-key"}):
            with patch.object(engine.requests, "get") as http:
                http.return_value.status_code = 200
                result = engine.check_provider_key("Groq")
        self.assertEqual(result["status"], "valid")
        self.assertEqual(http.call_count, 1)
        url = http.call_args.args[0]
        self.assertEqual(url, "https://api.groq.com/openai/v1/models")
        self.assertNotIn("dummy-key", url)
        self.assertEqual(http.call_args.kwargs["timeout"], 9)

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
