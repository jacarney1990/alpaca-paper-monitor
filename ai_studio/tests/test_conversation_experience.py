"""Regression tests: multi-turn Nexus conversations and chat-first UI safety."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class ConversationExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        os.environ["STUDIO_DB_PATH"] = os.path.join(cls.work.name, "threads.sqlite3")
        os.environ["STUDIO_ACCESS_TOKEN"] = "chat-test-private-only"
        os.environ.pop("STUDIO_ALLOW_API_USAGE", None)
        import app
        cls.module = importlib.reload(app)
        cls.client = cls.module.app.test_client()
        cls.token = {"X-Studio-Token": "chat-test-private-only"}

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def request(self, topic, **extra):
        return {"topic": topic, "workflow": "research", "mode": "team",
                "role": "Researcher", "providers": ["Gemini"], **extra}

    def test_conversations_are_private_and_parent_requires_existing_job(self):
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertEqual(self.client.post("/api/jobs", json=self.request("Hi", demo=True)).status_code, 401)
        not_found = self.client.post("/api/jobs", headers=self.token, json=self.request(
            "Continue previous discussion", demo=True, reply_to="missing-parent"))
        self.assertEqual(not_found.status_code, 404)
        self.assertIn("Previous conversation is unavailable", not_found.json["error"])

    def test_create_followup_and_context_with_previous_answer(self):
        import app
        with patch.object(app.threading, "Thread") as thread:
            posted = self.client.post("/api/jobs", headers=self.token, json=self.request(
                "What are the risks of a new project?", demo=True))
            self.assertEqual(posted.status_code, 202)
            first = posted.json["id"]
            thread.assert_called_once()
        with app.connection() as db:
            db.execute("UPDATE jobs SET status='complete', output=? WHERE id=?",
                       (json.dumps({"simulation": True, "mode": "team",
                                    "synthesis": "Practice-only example answer about risks.",
                                    "initial": [], "completed": 1, "requested": 1}), first))
        with patch.dict(os.environ, {"STUDIO_ALLOW_API_USAGE": "true",
                                     "STUDIO_DAILY_CALL_CAP": "15",
                                     "GEMINI_API_KEY": "fake-test-placeholder"}):
            with patch.object(app.threading, "Thread") as worker:
                followup = self.client.post("/api/jobs", headers=self.token, json=self.request(
                    "What should I prioritize next?", demo=False, reply_to=first))
                self.assertEqual(followup.status_code, 202)
                args = worker.call_args.kwargs["args"]
                self.assertIn("What are the risks of a new project?", args[1])
                self.assertIn("Simulated example, not verified", args[1])
                self.assertIn("What should I prioritize next?", args[1])
        self.assertEqual(followup.json["parent_id"], first)
        detail = self.client.get("/api/jobs/" + followup.json["id"], headers=self.token).json
        self.assertEqual(detail["parent_id"], first)
        self.assertEqual(detail["topic"], "What should I prioritize next?")
        recent = self.client.get("/api/jobs", headers=self.token).json
        self.assertTrue(any(item["parent_id"] == first for item in recent))

    def test_chat_shell_assets_navigation_and_markup_present(self):
        markup = self.client.get("/").get_data(as_text=True)
        self.assertIn('/static/chat-shell.js', markup)
        self.assertIn('/static/chat-shell.css', markup)
        self.assertIn('id="nexus-attached-preview"', markup)
        shell = self.client.get("/static/chat-shell.js")
        self.assertEqual(shell.status_code, 200)
        script = shell.get_data(as_text=True)
        self.assertIn("New conversation", script)
        self.assertIn("nexus-chat-pane", script)
        self.assertIn("nexus-tool-pane", script)
        self.assertIn("Clear", script.replace("clearChat","Clear"))
        self.assertIn("nexus-mobile-tabs", script)
        self.assertIn("No brokerage orders", self.client.get("/static/app.js").get_data(as_text=True))
        self.assertNotIn(".innerHTML=", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)


if __name__ == "__main__":
    unittest.main()
