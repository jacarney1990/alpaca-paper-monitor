import unittest
from risk_policy import evaluate_shadow_order


ACCOUNT = {'equity': 500, 'day_start_equity': 500, 'peak_equity': 500,
           'available_cash': 500, 'managed_allocation': 500,
           'current_position_value': 0, 'margin_used': 0,
           'options_enabled': False, 'freshness_verified': True}
ORDER = {'symbol': 'DEMO', 'side': 'buy', 'asset_class': 'stock',
         'qty': 2, 'order_type': 'limit', 'limit_price': 10}


class RiskTests(unittest.TestCase):
    def test_emergency_halt_default(self):
        self.assertEqual(evaluate_shadow_order(ORDER, ACCOUNT)['status'], 'BLOCK')

    def test_happy_path_still_no_execution(self):
        r = evaluate_shadow_order(ORDER, ACCOUNT, emergency_stop=False)
        self.assertEqual(r['status'], 'SHADOW_ELIGIBLE')
        self.assertEqual(r['execution'], 'DISABLED')

    def test_five_percent_limit(self):
        r = evaluate_shadow_order({**ORDER, 'qty': 3}, ACCOUNT, emergency_stop=False)
        self.assertIn('5%', ' '.join(r['reasons']))

    def test_daily_halt(self):
        r = evaluate_shadow_order(ORDER, {**ACCOUNT, 'equity': 489}, emergency_stop=False)
        self.assertIn('Daily loss', ' '.join(r['reasons']))

    def test_drawdown_halt(self):
        r = evaluate_shadow_order(ORDER, {**ACCOUNT, 'equity': 440}, emergency_stop=False)
        self.assertIn('drawdown', ' '.join(r['reasons']))

    def test_fractional_rejected(self):
        r = evaluate_shadow_order({**ORDER, 'qty': 1.5}, ACCOUNT, emergency_stop=False)
        self.assertIn('Whole-share', ' '.join(r['reasons']))

    def test_margin_rejected(self):
        r = evaluate_shadow_order(ORDER, {**ACCOUNT, 'margin_used': 5}, emergency_stop=False)
        self.assertIn('Margin', ' '.join(r['reasons']))

    def test_lacking_freshness_rejected(self):
        r = evaluate_shadow_order(ORDER, {**ACCOUNT, 'freshness_verified': False}, emergency_stop=False)
        self.assertIn('freshness', ' '.join(r['reasons']))

    def test_missing_snapshots_rejected(self):
        r = evaluate_shadow_order(ORDER, {}, emergency_stop=False)
        self.assertEqual(r['status'], 'BLOCK')

    def test_no_trade_even_if_live_requested(self):
        r = evaluate_shadow_order(ORDER, ACCOUNT, trading_enabled=True, emergency_stop=False)
        self.assertEqual(r['status'], 'BLOCK')


if __name__ == '__main__':
    unittest.main()
