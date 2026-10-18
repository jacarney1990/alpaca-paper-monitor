"""V4.1 integration: independent Groq review, multi-agent peer review, fail-closed completion."""
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import ai_team as ai
import shared_intelligence as shared

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
SOURCE = {'id': 'news:SERV:https://example.org/research', 'kind': 'NEWS LEAD (verify source)',
          'title': 'Serve Robotics research headline', 'url': 'https://example.org/research'}


def opinion(cls='watch', score=80):
    return {'classification': cls, 'summary': 'Unverified metadata assessment',
            'main_risk': 'Source not independently read', 'confidence': score,
            'evidence_ids': [SOURCE['id']]}


class V41TeamTests(unittest.TestCase):
    def test_all_keyed_models_become_team_members(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy1', 'GEMINI_API_KEY': 'dummy2',
                                     'GROQ_API_KEY': 'dummy3'}, clear=True):
            self.assertEqual(ai.configured_models(), ['OpenAI', 'Gemini', 'Groq'])

    def test_groq_is_optional_without_key(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'a', 'GEMINI_API_KEY': 'b'}, clear=True):
            self.assertEqual(ai.configured_models(), ['OpenAI', 'Gemini'])

    def test_groq_uses_strict_schema_and_only_free_plan_compatible_endpoint(self):
        output = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(opinion())}}]}
        with patch.dict(os.environ, {'GROQ_API_KEY': 'dummy'}, clear=True), \
             patch.object(ai, 'json_http', return_value=output) as request:
            result = ai.ask_provider('Groq', json.dumps({'ticker': 'SERV'}))
        self.assertEqual(result['classification'], 'watch')
        url = request.call_args.args[0]
        payload = request.call_args.kwargs['payload']
        self.assertEqual(url, 'https://api.groq.com/openai/v1/chat/completions')
        self.assertEqual(payload['model'], 'qwen/qwen3.8-27b')
        self.assertEqual(payload['response_format']['json_schema']['schema'], ai.SCHEMA)
        self.assertEqual(payload['response_format']['json_schema']['strict'], True)
        self.assertEqual(request.call_args.kwargs['retries'], 0)
        self.assertNotIn('tool_choice', payload)

    def test_groq_truncated_result_rejected(self):
        with self.assertRaises(ai.ProviderReviewError):
            ai.parse_provider_reply('Groq', {'choices': [{'finish_reason': 'length'}]}, '{}')

    def test_groq_untrusted_extra_instruction_rejected(self):
        bad = dict(opinion(), execute_live_trade=True)
        with self.assertRaises(ValueError):
            ai.validate_review(bad, {SOURCE['id']})

    def test_three_way_disagreement_cannot_yield_consensus(self):
        result = ai.consensus({'OpenAI': opinion('opportunity'),
                               'Gemini': opinion('opportunity'),
                               'Groq': opinion('risk')}, [SOURCE])
        self.assertEqual(result['status'], 'DISPUTED_REVIEW')
        self.assertEqual(result['classification'], 'uncertain')

    def test_team_report_includes_all_three_and_tracks_completion(self):
        with patch.object(ai, 'configured_models', return_value=['OpenAI', 'Gemini', 'Groq']), \
             patch.object(ai, 'quote_snapshot', return_value={'status':'unavailable'}), \
             patch.object(ai, 'ask_provider', return_value=opinion()):
            report = ai.agent_report('SERV', [SOURCE], NOW)
        self.assertEqual(set(report['reviews']), {'OpenAI', 'Gemini', 'Groq'})
        self.assertEqual(report['team_status'], 'COMPLETE')
        self.assertEqual(report['missing_models'], [])
        self.assertEqual(report['trade_execution'], 'DISABLED')

    def test_missing_third_model_cannot_claim_complete(self):
        def respond(name, prompt):
            if name == 'Groq':
                raise ai.ProviderReviewError('NO_FREE_QUOTA')
            return opinion()
        with patch.object(ai, 'configured_models', return_value=['OpenAI', 'Gemini', 'Groq']), \
             patch.object(ai, 'quote_snapshot', return_value={'status':'unavailable'}), \
             patch.object(ai, 'ask_provider', side_effect=respond):
            report = ai.agent_report('SERV', [SOURCE], NOW)
        self.assertEqual(report['team_status'], 'INCOMPLETE')
        self.assertEqual(report['missing_models'], ['Groq'])
        self.assertEqual(len(report['reviews']), 2)

    def test_cross_review_each_of_three_sees_both_peers(self):
        original = {'OpenAI': opinion(), 'Gemini': opinion(), 'Groq': opinion()}
        for model in original:
            prompt = json.loads(shared.challenge_prompt('SERV', [SOURCE], original, model, [], NOW))
            self.assertEqual(set(prompt['peer_reviews_UNVERIFIED_OPINIONS']), set(original) - {model})
            self.assertEqual(prompt['your_original_review'], original[model])

    def test_three_complete_cross_reviews_saved_only_as_research(self):
        report = {'symbol': 'SERV', 'sources': [SOURCE],
                  'reviews': {'OpenAI': opinion(), 'Gemini': opinion(), 'Groq': opinion()}}
        with patch.object(shared.ai_team, 'configured_models', return_value=['OpenAI', 'Gemini', 'Groq']), \
             patch.object(shared.ai_team, 'ask_provider', return_value=opinion()):
            result = shared.collaborate(report, [], NOW)
        self.assertEqual(set(result['cross_reviews']), {'OpenAI', 'Gemini', 'Groq'})
        self.assertEqual(result['team_status'], 'COMPLETE')
        self.assertEqual(result['investment_action'], 'RESEARCH_ONLY')
        self.assertEqual(result['live_orders'], 0)
        saved = shared.compact_journal_entry(dict(result, research_key='1:SERV', source_ai_run_id=1))
        self.assertEqual(set(saved['model_conclusions']), {'OpenAI','Gemini','Groq'})

    def test_missing_groq_cross_review_is_not_journaled(self):
        report = {'symbol': 'SERV', 'sources': [SOURCE],
                  'reviews': {'OpenAI': opinion(), 'Gemini': opinion(), 'Groq': opinion()}}
        def respond(name, prompt):
            if name == 'Groq':
                raise ai.ProviderReviewError('PROVIDER_DOWN')
            return opinion()
        with patch.object(shared.ai_team, 'configured_models', return_value=['OpenAI', 'Gemini', 'Groq']), \
             patch.object(shared.ai_team, 'ask_provider', side_effect=respond):
            with self.assertRaisesRegex(shared.SharedIntelligenceError, 'TWO_MODEL_CROSS_REVIEW_INCOMPLETE'):
                shared.collaborate(report, [], NOW)

    def test_all_models_reused_in_next_research_memory(self):
        entry = {
            'symbol': 'SERV', 'as_of': NOW.isoformat(), 'agreement': 'DISAGREEMENT_OR_UNCERTAIN',
            'shared_summary': 'OpenAI and Gemini only truncated text',
            'model_conclusions': {
                'OpenAI': {'classification':'watch','summary':'review 1'},
                'Gemini': {'classification':'risk','summary':'review 2'},
                'Groq': {'classification':'uncertain','summary':'review 3'},
            },
        }
        out = shared.memory_for('SERV', [entry], NOW)
        self.assertEqual(len(out), 1)
        self.assertIn('OpenAI=', out[0]['summary'])
        self.assertIn('Gemini=', out[0]['summary'])
        self.assertIn('Groq=', out[0]['summary'])

    def test_v3_reads_every_reviewer_without_authorizing_trades(self):
        from unittest.mock import patch
        import v3_engine as v3
        entry = {'symbol':'SERV', 'as_of':NOW.isoformat(), 'agreement':'DISAGREEMENT_OR_UNCERTAIN',
                 'investment_action':'RESEARCH_ONLY', 'shared_summary':'all unverified',
                 'model_conclusions':{m: {'classification':'watch','confidence':75}
                                      for m in ('OpenAI','Gemini','Groq')}}
        with patch.object(v3.base, 'github', return_value=[
            {'title':v3.SHARED_JOURNAL_TITLE, 'body':json.dumps({'schema':1,'entries':[entry]})}]):
            out = v3.shared_research_context('a/b',NOW)
        self.assertEqual(out['status'],'RECENT')
        self.assertEqual(set(out['by_symbol']['SERV']['team_views']), {'OpenAI','Gemini','Groq'})
        self.assertFalse(out['by_symbol']['SERV']['eligible_to_authorize_trades'])
        self.assertFalse(out['used_for_trade_approval'])

    def test_no_paid_broker_path_added_by_team_upgrade(self):
        self.assertNotIn('api.alpaca.markets/v2/orders', Path(ai.__file__).read_text())
        self.assertNotIn('/v2/orders', Path(shared.__file__).read_text())


if __name__ == '__main__':
    unittest.main()
