import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, Mock
from urllib.error import HTTPError

import gemini_budget as q
import ai_team


class TestGeminiBudget(unittest.TestCase):
    def now(self, iso):
        return datetime.fromisoformat(iso.replace('Z', '+00:00'))

    def test_first_attempt(self):
        wait, state = q.next_reservation(None, self.now('2026-10-18T21:00:00Z'), 12)
        self.assertEqual(wait, 0)
        self.assertEqual(state['used'], 1)
        self.assertEqual(state['date_pt'], '2026-10-18')

    def test_consecutive_calls_are_spaced(self):
        _, state = q.next_reservation(None, self.now('2026-10-18T21:00:00Z'), 12)
        wait, state2 = q.next_reservation(state, self.now('2026-10-18T21:00:03Z'), 12)
        self.assertEqual(wait, 12)
        self.assertEqual(state2['used'], 2)
        self.assertEqual(state2['last_attempt_utc'], '2026-10-18T21:00:15+00:00')

    def test_daily_cap_is_hard_stop(self):
        _, state = q.next_reservation(None, self.now('2026-10-18T21:00:00Z'), 2)
        _, state = q.next_reservation(state, self.now('2026-10-18T21:00:30Z'), 2)
        with self.assertRaisesRegex(q.GeminiBudgetError, 'EXHAUSTED'):
            q.next_reservation(state, self.now('2026-10-18T21:01:00Z'), 2)

    def test_pacific_day_reset_not_utc_day(self):
        _, state = q.next_reservation(None, self.now('2026-10-18T06:59:00Z'), 1)
        self.assertEqual(state['date_pt'], '2026-10-17')
        _, reset = q.next_reservation(state, self.now('2026-10-18T07:01:00Z'), 1)
        self.assertEqual(reset['date_pt'], '2026-10-18')
        self.assertEqual(reset['used'], 1)

    def test_corrupted_state_fails_closed(self):
        for state in ({'schema': 1, 'date_pt': '2026-10-18', 'used': True},
                      {'schema': 77, 'date_pt': '2026-10-18', 'used': 0},
                      {'schema': 1, 'date_pt': '2026-10-19', 'used': 0}):
            with self.subTest(state=state), self.assertRaises(q.GeminiBudgetError):
                q.next_reservation(state, self.now('2026-10-18T21:00:00Z'), 12)

    def test_invalid_caps_rejected(self):
        for val in ('0', '20', '15.0', 'abc', '-5', None):
            with self.subTest(val=val), self.assertRaises(q.GeminiBudgetError):
                q.check_cap(val)

    def test_repo_storage_has_no_keys(self):
        actions = []
        def gh(path, method='GET', payload=None):
            actions.append((path, method, payload))
            if method == 'GET':
                return []
            return {'number': 101}
        with patch.dict(os.environ, {'GITHUB_REPOSITORY':'example/private-repo',
                                      'GITHUB_TOKEN':'test-token',
                                      'GEMINI_DAILY_REQUEST_CAP':'12'}, clear=False):
            with patch.object(q.base, 'github', side_effect=gh):
                with patch.object(q.time, 'sleep'):
                    q.reserve()
        self.assertEqual(actions[-1][1], 'POST')
        body = actions[-1][2]['body']
        self.assertNotIn('test-token', body)
        self.assertEqual(json.loads(body)['used'], 1)

    def test_budget_unavailable_aborts_request_before_http(self):
        # Gemini must never be contacted if quota service cannot reserve.
        with patch.dict(os.environ, {'GEMINI_DAILY_REQUEST_CAP': '12'}, clear=False):
            with patch.object(q, 'reserve', side_effect=q.GeminiBudgetError('EXHAUSTED')):
                with patch.object(ai_team, 'json_http') as unused:
                    # Exercise the real low-level HTTP function, not provider calls.
                    pass
                with patch.object(ai_team, 'urlopen') as external:
                    with self.assertRaises(q.GeminiBudgetError):
                        ai_team.json_http('https://generativelanguage.googleapis.com/v1beta/models/abc:generateContent',
                                          {'test': 1})
                    external.assert_not_called()

    def test_429_does_not_retry_on_free_tier(self):
        err = HTTPError('https://generativelanguage.googleapis.com/', 429,
                        'quota', {}, None)
        with patch.dict(os.environ, {'GEMINI_DAILY_REQUEST_CAP': '12'}, clear=False):
            with patch.object(q, 'reserve') as reserve:
                with patch.object(ai_team, 'urlopen', side_effect=err) as external:
                    with self.assertRaises(HTTPError):
                        ai_team.json_http('https://generativelanguage.googleapis.com/v1beta/models/abc:generateContent',
                                          {'test': 1})
        reserve.assert_called_once()
        external.assert_called_once()

    def test_other_provider_not_budgeted(self):
        mock_response = Mock()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        with patch.dict(os.environ, {'GEMINI_DAILY_REQUEST_CAP':'12'}, clear=False):
            with patch.object(q, 'reserve') as reserve:
                with patch.object(ai_team, 'urlopen', return_value=mock_response):
                    with patch.object(ai_team.json, 'load', return_value={'ok': True}):
                        self.assertEqual(ai_team.json_http('https://api.openai.com/v1/responses', {'x': 1}), {'ok': True})
        reserve.assert_not_called()


if __name__ == '__main__':
    unittest.main()
