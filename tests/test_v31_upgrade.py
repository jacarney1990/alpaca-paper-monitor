import copy
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import v3_engine as v3

NOW = datetime(2026, 10, 17, 21, 30, tzinfo=timezone.utc)


def ready(price='5.00', bullish=True, bar_day='2026-10-16'):
    return {'status':'READY','last_close':price,'next_open':price,
            'bar_date':bar_day,'bullish':bullish,'bearish':not bullish}


def ai(symbols, qualified=False):
    reports={}
    for symbol in symbols:
        reports[symbol]={'reviewer_count':2,'consensus': {
            'status':'REVIEW_REQUIRED' if qualified else 'NO_CONSENSUS',
            'classification':'opportunity' if qualified else 'uncertain',
            'evidence_level':'unverified_headlines_only'}}
    return {'status':'recent','by_symbol':reports}


class V31CandidateTests(unittest.TestCase):
    def test_report_based_selection_matches_user_report(self):
        audit=ai(['SERV','IONQ','IGC'])
        selected,mode=v3.prioritize_research_candidates(audit, v3.empty_state())
        self.assertEqual(selected,['IGC','IONQ','SERV'])
        self.assertEqual(mode,'RECENT_AI_AUDIT')

    def test_consensus_first_but_risk_and_uncertain_still_reviewed(self):
        a=ai(['IGC','SERV','IONQ'])
        a['by_symbol']['SERV']['consensus']={'status':'REVIEW_REQUIRED','classification':'opportunity'}
        self.assertEqual(v3.prioritize_research_candidates(a,v3.empty_state())[0][0],'SERV')

    def test_no_ai_falls_back_to_rotating_watch(self):
        st=v3.empty_state();st['rotation']=6
        symbols,mode=v3.prioritize_research_candidates({'status':'unavailable'},st)
        self.assertEqual(symbols,list(v3.WATCH[6:9]))
        self.assertTrue(mode.startswith('WATCHLIST_FALLBACK'))

    def test_market_data_uses_oversight_candidates_and_VTI(self):
        with patch.object(v3,'bars',return_value=[{'date':'2026-10-16','open':'4'}]) as mocked, \
             patch.object(v3,'technical',return_value=ready()),patch.object(v3.time,'sleep'):
            market,errors,rotation=v3.market_data(NOW,v3.empty_state(),'fake_key',ai(['IGC','SERV','IONQ']))
        symbols=[c.args[0] for c in mocked.call_args_list]
        self.assertEqual(symbols,['IGC','IONQ','SERV','VTI'])
        self.assertEqual(set(market),set(symbols))
        self.assertEqual(rotation,0)
        self.assertFalse(errors)

    def test_pending_and_held_never_dropped(self):
        st=v3.empty_state()
        st['positions']={'CRSP':{'qty':'1','basis':'20','entry_date':'2026-10-10'}}
        st['pending']={'side':'sell','symbol':'CRSP','signal_date':'2026-10-16'}
        with patch.object(v3,'bars',return_value=[{'date':'2026-10-16','open':'4'}]) as mocked, \
             patch.object(v3,'technical',return_value=ready()),patch.object(v3.time,'sleep'):
            v3.market_data(NOW,st,'key',ai(['IGC','IONQ','SERV']))
        self.assertIn('CRSP',[c.args[0] for c in mocked.call_args_list])

class V31DecisionTests(unittest.TestCase):
    def test_bearish_and_no_consensus_both_explained(self):
        decisions=v3.explain_virtual_decisions({'SERV':ready('3.23',False)}, ai(['SERV']),
                                                v3.empty_state(),True)
        self.assertEqual(decisions['SERV']['verdict'],'WATCH')
        self.assertIn('NOT_BULLISH_5_20_DAY_TREND', decisions['SERV']['reasons'])
        self.assertIn('AI_NO_CONSENSUS',decisions['SERV']['reasons'])
        self.assertEqual(decisions['SERV']['ai_research_quality']['evidence_verification'],'NOT_VERIFIED')

    def test_whole_share_over_cap_is_visible(self):
        decisions=v3.explain_virtual_decisions({'CRSP':ready('49.25')},ai(['CRSP'],True),
                                                v3.empty_state(),True)
        self.assertEqual(decisions['CRSP']['verdict'],'REJECT_ENTRY')
        self.assertIn('WHOLE_SHARE_EXCEEDS_5_PERCENT_LIMIT',decisions['CRSP']['reasons'])

    def test_a_qualified_favorable_report_is_still_nonexecuting(self):
        state=v3.empty_state()
        decisions=v3.explain_virtual_decisions({'POET':ready('5.43')},ai(['POET'],True),state,True)
        self.assertEqual(decisions['POET']['verdict'],'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED')
        self.assertIsNone(state['pending'])
        self.assertEqual(state['cash'],'500.00')

    def test_audit_missing_blocks_entry(self):
        decisions=v3.explain_virtual_decisions({'SERV':ready()}, {'status':'unavailable'},
                                                v3.empty_state(),True)
        self.assertIn('AI_AUDIT_UNAVAILABLE',decisions['SERV']['reasons'])

    def test_one_virtual_fill_per_day_explained(self):
        state=v3.empty_state();state['last_trade_day']='2026-10-17'
        d=v3.explain_virtual_decisions({'SERV':ready()},ai(['SERV'],True),state,True,now=NOW)
        self.assertIn('ONE_VIRTUAL_TRADE_PER_DAY',d['SERV']['reasons'])

    def test_simulator_off_explained(self):
        decisions=v3.explain_virtual_decisions({'SERV':ready()},ai(['SERV'],True),
                                                v3.empty_state(),False)
        self.assertIn('SIMULATION_DISABLED',decisions['SERV']['reasons'])

    def test_simulated_queued_event_reported_not_broker(self):
        a=ai(['SERV'],True)
        st,events=v3.virtual_step(v3.empty_state(),{'SERV':ready()},a,NOW)
        decisions=v3.explain_virtual_decisions({'SERV':ready()},a,st,True,events)
        self.assertEqual(decisions['SERV']['verdict'],'VIRTUAL_BUY_QUEUED_NEXT_BAR')
        self.assertEqual(st['positions'],{})

    def test_risk_rules_remain_on(self):
        state=v3.empty_state()
        state['positions']={'NXE':{'qty':'1','basis':'20','entry_date':'2026-10-10'}}
        state['cash']='472.00'
        state['day_start_equity']='500.00'
        state['virtual_equity']='480.00'
        d=v3.explain_virtual_decisions({'SERV':ready(),'NXE':ready('8.00')},ai(['SERV'],True),state,True)
        self.assertTrue(any('Daily loss halt' in x for x in d['SERV']['reasons']))

class V31PerformanceTests(unittest.TestCase):
    def test_weekend_repeats_are_one_observed_session(self):
        st=v3.empty_state()
        data={'VTI':ready('374.07')}
        b=v3.benchmark_mark(st,data)
        first=v3.performance_snapshot(st,data,b,True)
        second=v3.performance_snapshot(st,data,b,True)
        self.assertEqual(first['observed_sessions'],1)
        self.assertEqual(second['observed_sessions'],1)
        self.assertEqual(len(st['performance_history']),1)

    def test_next_market_date_increments_and_records_drawdown(self):
        st=v3.empty_state();b={'status':'daily_price_comparison_only','vti_value_of_500':'498'}
        v3.performance_snapshot(st,{'SERV':ready()},b,True)
        st['virtual_equity']='490.00'
        x=v3.performance_snapshot(st,{'SERV':ready(bar_day='2026-10-19')},b,True)
        self.assertEqual(x['observed_sessions'],2)
        self.assertEqual(x['net_return_percent'],'-2.00')
        self.assertEqual(x['max_observed_drawdown_percent'],'2.00')

    def test_state_v1_migrates_optional_history(self):
        st=v3.empty_state()
        del st['performance_history'];del st['trade_ledger']
        v3.validate_state(st)
        x=v3.performance_snapshot(st,{'SERV':ready()}, {}, True)
        self.assertEqual(x['observed_sessions'],1)

    def test_trade_ledger_records_virtual_buy_sell_not_broker(self):
        st=v3.empty_state();a=ai(['SERV'],True)
        first,events=v3.virtual_step(st,{'SERV':ready('5.00')},a,NOW)
        second,events=v3.virtual_step(first, {'SERV':ready('5.00',bar_day='2026-10-19')},a,
                                   datetime(2026,10,19,22,tzinfo=timezone.utc))
        self.assertEqual(second['trade_ledger'][0]['side'],'BUY')
        second['pending']={'symbol':'SERV','side':'sell','signal_date':'2026-10-19'}
        third,events=v3.virtual_step(second,{'SERV':ready('6.00',bar_day='2026-10-20')},a,
                                   datetime(2026,10,20,22,tzinfo=timezone.utc))
        self.assertEqual(third['trade_ledger'][-1]['side'],'SELL')
        p=v3.performance_snapshot(third,{'SERV':ready('6.00',bar_day='2026-10-20')}, {},True)
        self.assertEqual(p['closed_virtual_trades'],1)
        self.assertEqual(p['profitable_closed_trades'],1)
        self.assertNotEqual(p['realized_closed_pnl'],'0.00')

    def test_unpriced_position_does_not_record_fake_equity(self):
        st=v3.empty_state();st['positions']={'NXE':{'qty':'1','basis':'20','entry_date':'2026-10-10'}}
        p=v3.performance_snapshot(st,{'SERV':ready()}, {},True)
        self.assertEqual(p['status'],'UNAVAILABLE_HELD_PRICES')
        self.assertEqual(st.get('performance_history'),[])

if __name__=='__main__':unittest.main()
