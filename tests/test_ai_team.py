import unittest
from unittest.mock import patch

import ai_team


SOURCES = [
    {'id': 'news:TEST:1', 'kind': 'NEWS LEAD (verify source)', 'title': 'Testing event', 'url': 'https://example.org/1'},
    {'id': 'sec:TEST:2', 'kind': 'OFFICIAL SEC FILING (review contents)', 'title': 'TEST 8-K', 'url': 'https://example.org/2'},
]


def review(kind='watch', score=75):
    return {'classification': kind, 'summary': 'Metadata only; underlying story not verified',
            'main_risk': 'No original document has been read', 'confidence': score,
            'evidence_ids': ['news:TEST:1']}


class TeamTests(unittest.TestCase):
    def test_unconfigured_models(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(ai_team.configured_models(), [])

    def test_no_model_single_cannot_approve(self):
        self.assertEqual(ai_team.consensus({'Claude': review()}, SOURCES)['status'], 'NO_CONSENSUS')

    def test_two_disagree_fail_closed(self):
        result = ai_team.consensus({'Claude': review('risk'), 'OpenAI': review('opportunity')}, SOURCES)
        self.assertEqual(result['status'], 'NO_CONSENSUS')

    def test_two_agree_still_review_only(self):
        result = ai_team.consensus({'Claude': review(), 'OpenAI': review()}, SOURCES)
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertNotIn('APPROVED_TO_TRADE', str(result))

    def test_low_confidence_not_counted(self):
        self.assertEqual(ai_team.consensus({'Claude': review(score=55), 'OpenAI': review()}, SOURCES)['status'], 'NO_CONSENSUS')

    def test_fake_citation_rejected(self):
        bad = review()
        bad['evidence_ids'] = ['not-in-sources']
        with self.assertRaises(ValueError):
            ai_team.validate_review(bad, {'news:TEST:1'})

    def test_extra_field_rejected(self):
        bad = review()
        bad['place_order'] = True
        with self.assertRaises(ValueError):
            ai_team.validate_review(bad, {'news:TEST:1'})

    def test_boolean_confidence_rejected(self):
        bad = review()
        bad['confidence'] = True
        with self.assertRaises(ValueError):
            ai_team.validate_review(bad, {'news:TEST:1'})

    def test_no_external_provider_call_in_tests(self):
        with patch.object(ai_team, 'ask_provider', side_effect=AssertionError('should not call')):
            self.assertEqual(ai_team.consensus({}, SOURCES)['status'], 'NO_CONSENSUS')

    def test_report_says_no_trades(self):
        text = ai_team.format_report([{'symbol': 'TEST', 'consensus': {'status': 'NO_CONSENSUS', 'classification': 'uncertain', 'why': 'No vote'}, 'reviews': {}, 'sources': SOURCES}], ai_team.datetime.now(ai_team.timezone.utc))
        self.assertIn('No trades placed', text)


if __name__ == '__main__':
    unittest.main()
