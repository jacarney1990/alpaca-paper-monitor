"""Contract tests for the single-provider three-role research pipeline."""
import os
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import engine


class ThreeAgentTests(unittest.TestCase):
    def test_roles_run_in_sequence_and_synthesize(self):
        calls = []
        def fake_invoke(provider, system, prompt):
            calls.append((provider, system, prompt))
            return "Result " + str(len(calls))
        with patch.object(engine, "invoke", side_effect=fake_invoke):
            result = engine.run_task("Assess Acme using supplied documents", "team", "Researcher", ["OpenRouter Free"], "research")
        self.assertEqual([item["role"] for item in result["initial"]], ["Collector", "Analyst", "Validator"])
        self.assertEqual(len(calls), 4)
        self.assertEqual(result["team_status"], "COMPLETE")
        self.assertIn("Result 1", calls[1][2])
        self.assertIn("Result 2", calls[2][2])
        self.assertEqual(result["trade_execution"], "DISABLED")

    def test_provider_failure_is_not_marked_complete(self):
        def fake_invoke(provider, system, prompt):
            if "Validator" in system:
                raise engine.ProviderError("Rate limited")
            return "Preliminary analysis"
        with patch.object(engine, "invoke", side_effect=fake_invoke):
            result = engine.run_task("Assess Acme", "team", "Researcher", ["OpenRouter Free"], "research")
        self.assertEqual(result["team_status"], "PARTIAL")
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["synthesis"], "")
        self.assertEqual(result["initial"][-1]["role"], "Validator")
        self.assertEqual(result["initial"][-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
