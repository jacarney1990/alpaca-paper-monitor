import json
import os
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import v3_engine as v3

NOW = datetime(2026,10,17,19,30,tzinfo=timezone.utc)


def market(symbol='NXE', asof='2026-10-16', price='20.00', bullish=True):
    return {symbol:{'status':'READY', 'last_close':price, 'next_open':price,
                    'bar_date':asof, 'bullish':bullish, 'bearish':not bullish}}


def audit(symbol='NXE', kind='opportunity'):
    return {'status':'recent','by_symbol':{symbol:{'reviewer_count':2,
            'consensus':{'status':'REVIEW_REQUIRED','classification':kind}}}}


class TestV3(unittest.TestCase):
    def test_no_broker_credentials_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(v3.paper_snapshot()['status'],'not_configured')

    def test_paper_api_only_reads(self):
        with patch.dict(os.environ, {'ALPACA_PAPER_API_KEY':'x','ALPACA_PAPER_API_SECRET':'y'}, clear=True):
            def fake(url, headers):
                self.assertTrue(url.startswith(v3.PAPER + '/v2/'))
                if url.endswith('/account'):
                    return {'status':'ACTIVE','equity':'500','cash':'500','buying_power':'500',
                            'trading_blocked':False,'account_number':'SENSITIVE'}
                return []
            with patch.object(v3,'get_json',side_effect=fake) as get:
                result=v3.paper_snapshot()
        self.assertEqual(result['status'],'read_only')
        self.assertNotIn('account_number',json.dumps(result))
        self.assertEqual(get.call_count,3)

    def test_tech_insufficient_bars(self):
        self.assertEqual(v3.technical([], NOW.date())['status'],'INSUFFICIENT_DATA')

    def test_tech_stale(self):
        items=[{'date':f'2026-09-{i:02d}', 'close':'10','open':'10'} for i in range(1,21)]
        self.assertEqual(v3.technical(items, NOW.date())['status'],'STALE')

    def test_tech_bullish(self):
        items=[{'date':f'2026-10-{i:02d}','close':str(10+i/10),'open':'10'} for i in range(1,21)]
        self.assertTrue(v3.technical(items, date(2026,10,21))['bullish'])

    def test_bad_market_symbol_rejected(self):
        with self.assertRaises(ValueError):v3.bars('AA?BAD',NOW.date(),'x')

    def test_no_ai_audit_blocks_queued_buy(self):
        state,events=v3.virtual_step(v3.empty_state(),market(),{'status':'unavailable'},NOW)
        self.assertIsNone(state['pending'])
        self.assertFalse(any(e['action']=='VIRTUAL_BUY_QUEUED' for e in events))

    def test_one_review_blocks(self):
        x=audit();x['by_symbol']['NXE']['reviewer_count']=1
        self.assertFalse(v3.safe_ai_for_virtual_buy(x,'NXE')[0])

    def test_risk_classification_blocks(self):
        self.assertFalse(v3.safe_ai_for_virtual_buy(audit(kind='risk'),'NXE')[0])

    def test_two_reviews_and_trend_queue_virtual_only(self):
        state,events=v3.virtual_step(v3.empty_state(),market(),audit(),NOW)
        self.assertEqual(state['cash'],'500.00')
        self.assertEqual(state['pending']['side'],'buy')
        self.assertEqual(state['positions'],{})
        self.assertIn('NEXT_BAR',str(events))

    def test_same_bar_never_fills(self):
        state,_=v3.virtual_step(v3.empty_state(),market(),audit(),NOW)
        state2,events=v3.virtual_step(state,market(),audit(),NOW)
        self.assertEqual(state2['cash'],'500.00')
        self.assertTrue(any(x['action']=='PENDING_NEXT_BAR' for x in events))

    def test_next_bar_open_fills_at_model_spread(self):
        state,_=v3.virtual_step(v3.empty_state(),market(),audit(),NOW)
        future=market(asof='2026-10-19')
        state2,events=v3.virtual_step(state,future,audit(),datetime(2026,10,19,22,tzinfo=timezone.utc))
        self.assertEqual(state2['positions']['NXE']['qty'],'1')
        self.assertEqual(state2['cash'],'479.96')
        self.assertTrue(any(x['action']=='VIRTUAL_BUY_FILLED' for x in events))

    def test_above_25_per_share_no_queue(self):
        state,events=v3.virtual_step(v3.empty_state(),market(price='25.01'),audit(),NOW)
        self.assertIsNone(state['pending'])
        self.assertEqual(events,[])

    def test_next_bar_gaps_above_cap_blocked(self):
        state,_=v3.virtual_step(v3.empty_state(),market(price='20'),audit(),NOW)
        state2,events=v3.virtual_step(state,market(asof='2026-10-19',price='30'),audit(),datetime(2026,10,19,22,tzinfo=timezone.utc))
        self.assertIsNone(state2['pending'])
        self.assertEqual(state2['positions'],{})
        self.assertTrue(any(x['action']=='VIRTUAL_BUY_BLOCKED' for x in events))

    def test_missing_held_price_blocks_changes(self):
        state=v3.empty_state();state['positions']={'NXE':{'qty':'1','basis':'20','entry_date':'2026-10-10'}}
        state2,events=v3.virtual_step(state,{},audit(),NOW)
        self.assertEqual(state,state2)
        self.assertEqual(events[0]['action'],'HOLD')

    def test_sell_queue_on_downtrend(self):
        s=v3.empty_state();s['positions']={'NXE':{'qty':'1','basis':'20','entry_date':'2026-10-10'}}
        s['cash']='480'
        state,events=v3.virtual_step(s,market(bullish=False),audit(),NOW)
        self.assertEqual(state['pending']['side'],'sell')
        self.assertEqual(state['positions']['NXE']['qty'],'1')
        self.assertTrue(any(x['action']=='VIRTUAL_SELL_QUEUED' for x in events))

    def test_sell_fills_only_on_new_bar(self):
        s=v3.empty_state();s['positions']={'NXE':{'qty':'1','basis':'20','entry_date':'2026-10-10'}}
        s['cash']='480';s['pending']={'symbol':'NXE','side':'sell','signal_date':'2026-10-16'}
        state,events=v3.virtual_step(s,market(asof='2026-10-19',bullish=False),audit(),datetime(2026,10,19,22,tzinfo=timezone.utc))
        self.assertEqual(state['positions'],{})
        self.assertEqual(state['cash'],'499.96')
        self.assertTrue(any(x['action']=='VIRTUAL_SELL_FILLED' for x in events))

    def test_broker_credentials_not_placed_in_report(self):
        with patch.dict(os.environ,{'ALPACA_PAPER_API_KEY':'secret_a','ALPACA_PAPER_API_SECRET':'secret_b'},clear=True):
            with patch.object(v3,'get_json', side_effect=ValueError('unexpected shape')):
                broker=v3.paper_snapshot()
        self.assertNotIn('secret_a',json.dumps(broker))
        self.assertNotIn('secret_b',json.dumps(broker))

    def test_corrupt_state_refused(self):
        state=v3.empty_state();state['positions']={'NOT_ON_WATCH':{'qty':'1','basis':'1'}}
        with self.assertRaises(ValueError):v3.validate_state(state)

    def test_fractional_share_refused(self):
        state=v3.empty_state();state['positions']={'NXE':{'qty':'.5','basis':'20'}}
        with self.assertRaises(ValueError):v3.validate_state(state)

    def test_all_paper_trading_is_virtual_only(self):
        with open(v3.__file__,encoding='utf-8') as f:
            src=f.read()
        self.assertNotIn("/v2/orders', method='POST'",src)
        self.assertNotIn('submit_order(',src)
        self.assertNotIn('api.alpaca.markets/v2/orders',src)

    def test_artifact_github_token_not_on_redirect(self):
        with open(v3.__file__,encoding='utf-8') as f:
            src=f.read()
        self.assertIn('build_opener(NoRedirect())',src)
        self.assertIn('urlopen(Request(redirect)',src)


if __name__ == '__main__': unittest.main()

class TestNewDailyRisk(unittest.TestCase):
    def test_day_start_does_not_reset_after_market_loss(self):
        state=v3.empty_state()
        state['positions']={'NXE':{'qty':'1','basis':'20','entry_date':'2026-10-10'}}
        state['cash']='480.00'
        state['virtual_equity']='500.00'
        state['last_mark_day']='2026-10-15'
        today=datetime(2026,10,16,22,tzinfo=timezone.utc)
        market_data=market(asof='2026-10-16',price='9.00',bullish=False)
        actual,_=v3.virtual_step(state,market_data,audit(),today)
        self.assertEqual(actual['day_start_equity'],'500.00')
        self.assertEqual(actual['virtual_equity'],'489.00')
        self.assertEqual(actual['last_mark_day'],'2026-10-16')

    def test_paper_concentration_warning(self):
        broker={'status':'read_only','equity':'500','positions':[{'symbol':'NXE','market_value':'125'}],
                'non_stock_position_count':0,'open_orders':[]}
        self.assertIn('NXE',v3.paper_warnings(broker)[0])

    def test_market_day_not_calendar_day_drives_reset(self):
        st=v3.empty_state()
        st['virtual_equity']='496.00';st['last_mark_day']='2026-10-16';st['day_start_equity']='500'
        actual,_=v3.virtual_step(st,market(asof='2026-10-16'),{'status':'unavailable'},
                                 datetime(2026,10,17,22,tzinfo=timezone.utc))
        self.assertEqual(actual['day_start_equity'],'500')

class TestBenchmark(unittest.TestCase):
    def test_baseline_first_observation(self):
        state=v3.empty_state();data=market('VTI',price='100')
        x=v3.benchmark_mark(state,data)
        self.assertEqual(x['vti_value_of_500'],'500.00')
    def test_benchmark_separate_from_simulated_portfolio(self):
        state=v3.empty_state();state['vti_initial_close']='100';state['vti_initial_date']='2026-10-01'
        state['virtual_equity']='515'
        x=v3.benchmark_mark(state,market('VTI',price='102'))
        self.assertEqual(x['vti_value_of_500'],'510.00')
        self.assertEqual(x['virtual_minus_vti_dollars'],'5.00')

class TestIntegration(unittest.TestCase):
    def test_simulation_disabled_by_default_and_writes_audit(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {'GITHUB_REPOSITORY':'owner/repo', 'GITHUB_TOKEN':'automated',
                                         'MASSIVE_API_KEY':'private', 'V3_SEND_EMAIL':'false'},clear=True):
                with patch.object(v3,'load_state',return_value=(None,v3.empty_state())), \
                     patch.object(v3,'paper_snapshot',return_value={'status':'not_configured'}), \
                     patch.object(v3,'ai_audit',return_value={'status':'unavailable'}), \
                     patch.object(v3,'market_data',return_value=(market(),[],3)), \
                     patch.object(v3,'save_state') as saved:
                    import os as _os
                    previous=_os.getcwd()
                    try:
                        _os.chdir(td)
                        v3.main()
                        report=json.loads(Path(td,'v3-report.json').read_text())
                    finally:
                        _os.chdir(previous)
        self.assertEqual(report['real_broker_orders_submitted'],0)
        self.assertEqual(report['events'][0]['action'],'SIMULATION_OFF')
        saved.assert_called_once()

    def test_invalid_pending_rejected(self):
        x=v3.empty_state();x['pending']={'symbol':'BAD','side':'buy','signal_date':'2026-10-16'}
        with self.assertRaises(ValueError): v3.validate_state(x)
