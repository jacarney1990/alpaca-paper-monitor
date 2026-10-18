"""V4 integration: shared journal informs reports but never changes trade eligibility."""
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import v3_engine as v3

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
JOURNAL = {'schema': 1, 'entries': [
    {'symbol': 'SERV', 'as_of': NOW.isoformat(), 'agreement': 'AGREE_REVIEW_ONLY',
     'investment_action': 'RESEARCH_ONLY', 'shared_summary': 'Review-only opinion.'},
]}


class SharedContextTests(unittest.TestCase):
    def test_reads_recent_journal_as_unverified_context(self):
        with patch.object(v3.base, 'github', return_value=[
            {'title': v3.SHARED_JOURNAL_TITLE, 'number': 7, 'body': __import__('json').dumps(JOURNAL)}]):
            data = v3.shared_research_context('owner/repo', NOW)
        self.assertEqual(data['status'], 'RECENT')
        self.assertEqual(data['by_symbol']['SERV']['agreement'], 'AGREE_REVIEW_ONLY')
        self.assertFalse(data['used_for_trade_approval'])
        self.assertFalse(data['by_symbol']['SERV']['eligible_to_authorize_trades'])

    def test_malformed_first_entry_doesnt_hide_valid_later_entry(self):
        import json
        entries = [{'symbol': 'SERV', 'as_of': 'nonsense', 'agreement': 'AGREE_REVIEW_ONLY',
                    'investment_action': 'RESEARCH_ONLY'}, *JOURNAL['entries']]
        with patch.object(v3.base, 'github', return_value=[
            {'title': v3.SHARED_JOURNAL_TITLE, 'body': json.dumps({'schema': 1, 'entries': entries})}]):
            data = v3.shared_research_context('owner/repo', NOW)
        self.assertIn('SERV', data['by_symbol'])

    def test_rejects_stale_and_nonresearch_entries(self):
        import json
        rows = [dict(JOURNAL['entries'][0], as_of=(NOW-timedelta(days=22)).isoformat()),
                dict(JOURNAL['entries'][0], investment_action='BUY')]
        with patch.object(v3.base, 'github', return_value=[
            {'title': v3.SHARED_JOURNAL_TITLE, 'body': json.dumps({'schema': 1, 'entries': rows})}]):
            data = v3.shared_research_context('owner/repo', NOW)
        self.assertEqual(data['status'], 'EMPTY_OR_STALE')
        self.assertEqual(data['by_symbol'], {})

    def test_corrupt_journal_sanitized_and_nonfatal(self):
        with patch.object(v3.base, 'github', return_value=[
            {'title': v3.SHARED_JOURNAL_TITLE, 'body': 'not json'}]):
            data = v3.shared_research_context('owner/repo', NOW)
        self.assertEqual(data['status'], 'UNAVAILABLE')
        self.assertEqual(data['by_symbol'], {})
        self.assertNotIn('not json', str(data))

    def test_missing_issue_unavailable(self):
        with patch.object(v3.base, 'github', return_value=[]):
            data = v3.shared_research_context('owner/repo', NOW)
        self.assertEqual(data['status'], 'UNAVAILABLE')

    def test_ai_entry_gate_still_rejects_without_original_audit(self):
        from copy import deepcopy
        state = v3.empty_state()
        market = {'SERV': {'status': 'READY', 'last_close': '3.00', 'bullish': True, 'bearish': False}}
        audit = {'status': 'unavailable'}
        before = deepcopy(state)
        decisions = v3.explain_virtual_decisions(market, audit, state, True, now=NOW)
        self.assertNotIn('VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED', [x['verdict'] for x in decisions.values()])
        self.assertEqual(state, before)
        self.assertFalse(v3.safe_ai_for_virtual_buy(audit, 'SERV')[0])

if __name__ == '__main__':
    unittest.main()
