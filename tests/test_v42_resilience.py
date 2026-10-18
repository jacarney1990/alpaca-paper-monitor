"""V4.2 regression: free-tier failure isolation and safe research handoff."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import ai_team as team
import shared_intelligence as shared

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
SRC = {'id': 'news:SERV:test', 'kind': 'NEWS LEAD (verify source)',
       'title': 'Public source lead', 'url': 'https://example.org/lead'}

def opinion():
    return {'classification': 'watch', 'summary': 'Evidence is unverified',
            'main_risk': 'Insufficient corroboration', 'confidence': 67,
            'evidence_ids': [SRC['id']]}


def sample_report(symbol, complete):
    reviews = {'OpenAI': opinion(), 'Gemini': opinion(), 'Groq': opinion()}
    if not complete:
        reviews.pop('Gemini')
    return {'symbol': symbol, 'sources': [dict(SRC, id=SRC['id'].replace('SERV', symbol))],
            'reviews': reviews, 'team_status': 'COMPLETE' if complete else 'INCOMPLETE',
            'expected_models': ['OpenAI', 'Gemini', 'Groq'],
            'missing_models': [] if complete else ['Gemini'], 'trade_execution':'DISABLED',
            'consensus':{'status':'NO_CONSENSUS','classification':'uncertain','why':'unverified'}}

class V42ResilienceTests(unittest.TestCase):
    def test_gemini_503_is_deferred_without_retry_storm(self):
        exc = HTTPError('https://generativelanguage.googleapis.com', 503, 'Unavailable', {}, None)
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'dummy'}, clear=True), \
             patch.object(team, 'json_http', side_effect=exc) as http:
            with self.assertRaises(HTTPError):
                team.ask_provider('Gemini', '{}')
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.kwargs['retries'], 0)

    def test_validation_reason_code_does_not_include_provider_text(self):
        bad = opinion()
        bad['evidence_ids'] = ['not-a-real-id']
        with self.assertRaisesRegex(team.ReviewValidationError, '^INVALID_EVIDENCE_IDS$'):
            team.validate_review(bad, {SRC['id']})
        bad = opinion()
        bad['classification'] = 'BUY IMMEDIATELY'
        with self.assertRaisesRegex(team.ReviewValidationError, '^INVALID_CLASSIFICATION$'):
            team.validate_review(bad, {SRC['id']})

    def test_missing_gemini_reports_reason_without_marking_complete(self):
        def respond(name, prompt):
            if name == 'Gemini':
                bad = opinion()
                bad['evidence_ids'] = ['fabricated']
                return bad
            return opinion()
        with patch.object(team, 'configured_models', return_value=['OpenAI','Gemini','Groq']), \
             patch.object(team, 'quote_snapshot', return_value={'status':'unavailable'}), \
             patch.object(team, 'ask_provider', side_effect=respond):
            result = team.agent_report('SERV', [SRC], NOW)
        self.assertEqual(result['team_status'], 'INCOMPLETE')
        self.assertEqual(result['provider_errors']['Gemini'], 'INVALID_EVIDENCE_IDS')
        self.assertEqual(set(result['reviews']), {'OpenAI','Groq'})

    def test_ai_team_partial_success_keeps_completed_only_and_emits_artifact(self):
        leads = [dict(SRC, symbol='SERV'), dict(SRC, symbol='OKLO', id='news:OKLO:test')]
        def review(symbol, sources, now):
            return sample_report(symbol, complete=(symbol == 'SERV'))
        with tempfile.TemporaryDirectory() as tmp:
            original = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {'GITHUB_TOKEN':'x','GITHUB_REPOSITORY':'o/r',
                     'RESEND_API_KEY':'x','ALERT_EMAIL_TO':'test@example.org',
                     'MAX_AI_SYMBOLS_PER_RUN':'2'}, clear=True), \
                     patch.object(team, 'configured_models', return_value=['OpenAI','Gemini','Groq']), \
                     patch.object(team, 'load_ai_state', return_value=(1, {'processed':[]})), \
                     patch.object(team, 'collect_leads', return_value=(leads,{'rss_failures':[], 'sec_status':'OK', 'sec_failures':0})), \
                     patch.object(team, 'agent_report', side_effect=review), \
                     patch.object(team.base, 'send_email', return_value='id') as email, \
                     patch.object(team, 'save_ai_state') as save:
                    team.main()
                report = json.loads(Path('ai-team-report.json').read_text())
                self.assertEqual(report['research_status'], 'PARTIAL')
                self.assertEqual(len(report['reports']), 2)
                self.assertEqual(len(save.call_args.args[2]['processed']), 1)
                self.assertIn('news:SERV:test', save.call_args.args[2]['processed'])
                self.assertNotIn('news:OKLO:test', save.call_args.args[2]['processed'])
                email.assert_called_once()
            finally:
                os.chdir(original)

    def test_incomplete_team_cannot_be_accepted_as_complete(self):
        bad = sample_report('SERV', complete=False)
        with self.assertRaisesRegex(shared.SharedIntelligenceError, 'INCOMPLETE_SOURCE_TEAM_REVIEW'):
            shared.clean_report(bad)
        bad = sample_report('SERV', complete=True)
        bad['expected_models'].append('Claude')
        with self.assertRaisesRegex(shared.SharedIntelligenceError, 'EXPECTED_TEAM_NOT_COMPLETE'):
            shared.clean_report(bad)

    def test_shared_run_skips_incomplete_ticker_and_uses_complete_one(self):
        complete = sample_report('SERV', True)
        incomplete = sample_report('OKLO', False)
        out={'symbol':'SERV', 'as_of':NOW.isoformat(), 'agreement':'DISAGREEMENT_OR_UNCERTAIN',
             'shared_summary':'research only', 'cross_reviews':{'OpenAI':opinion(),'Gemini':opinion(),'Groq':opinion()},
             'changes':{},'source_evidence':[SRC]}
        with patch.object(shared.ai_team,'configured_models',return_value=['OpenAI','Gemini','Groq']), \
             patch.object(shared,'load_journal', return_value=(None,{'entries':[]})), \
             patch.object(shared,'recent_ai_artifact',return_value=([complete,incomplete],123,NOW)), \
             patch.object(shared,'collaborate',return_value=out) as collab, \
             patch.object(shared,'store_journal',return_value=1) as stored:
            result=shared.run('o/r','private-token',NOW)
        self.assertEqual(result['status'],'CROSS_REVIEWS_SAVED')
        self.assertEqual(result['entries_written'],1)
        self.assertEqual(result['deferred_incomplete_symbols'],['OKLO'])
        self.assertEqual(collab.call_count,1)
        self.assertEqual(stored.call_count,1)

    def test_schedules_are_unattended_and_order_execution_remains_disabled(self):
        root = Path(__file__).resolve().parents[1]
        a = (root/'.github/workflows/ai-team.yml').read_text()
        shared_workflow = (root/'.github/workflows/investment-v35.yml').read_text()
        v3 = (root/'.github/workflows/investment-v3.yml').read_text()
        self.assertIn("cron: '39 21 * * *'", a)
        self.assertIn("MAX_AI_SYMBOLS_PER_RUN: '1'", a)
        self.assertIn("cron: '39 22 * * *'", shared_workflow)
        self.assertIn("cron: '23 23 * * 1-5'", v3)
        self.assertNotIn('api.alpaca.markets/v2/orders', (root/'ai_team.py').read_text())
        self.assertNotIn('/v2/orders', (root/'shared_intelligence.py').read_text())

if __name__ == '__main__':
    unittest.main()
