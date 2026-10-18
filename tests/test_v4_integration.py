import unittest
from unittest.mock import patch
from datetime import datetime, timezone

import gemini_budget
import shared_intelligence as sh


class TestV4Integration(unittest.TestCase):
    def test_gemini_budget_error_is_research_failure_not_crash(self):
        item = {
            'symbol': 'IONQ',
            'sources': [{'id':'news:IONQ:123', 'title':'Headline', 'url':'https://example.com/a', 'kind':'NEWS'}],
            'reviews': {
                'OpenAI': {'classification':'watch', 'summary':'watch','main_risk':'risk','confidence':75,'evidence_ids':['news:IONQ:123']},
                'Gemini': {'classification':'watch', 'summary':'watch','main_risk':'risk','confidence':72,'evidence_ids':['news:IONQ:123']},
            }
        }
        with patch.object(sh, 'memory_for', return_value=[]):
            with patch.object(sh.ai_team, 'configured_models', return_value=['OpenAI','Gemini']):
                with patch.object(sh.ai_team,'ask_provider', side_effect=[item['reviews']['OpenAI'], gemini_budget.GeminiBudgetError('GEMINI_RESEARCH_DAILY_BUDGET_EXHAUSTED')]):
                    with self.assertRaisesRegex(sh.SharedIntelligenceError, 'TWO_MODEL_CROSS_REVIEW_INCOMPLETE'):
                        sh.collaborate(item, [], datetime(2026,10,18,tzinfo=timezone.utc))
    def test_coordinated_workflows_share_same_lock_and_cap(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]/'.github'/'workflows'
        files = [(root/x).read_text() for x in ('ai-team.yml','investment-v35.yml')]
        for data in files:
            self.assertIn('group: gemini-research-shared-budget', data)
            self.assertIn("GEMINI_DAILY_REQUEST_CAP: '12'", data)
            self.assertIn('issues: write',data)
            self.assertIn("- cron: '39 ", data)
    def test_no_broker_execution_in_combined_workflows(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]/'.github'/'workflows'
        for filename in ('ai-team.yml','investment-v35.yml'):
            data=(root/filename).read_text()
            self.assertNotIn('ALPACA_PAPER_API_SECRET', data)
            self.assertNotIn('ALPACA_LIVE', data)

if __name__=='__main__': unittest.main()
