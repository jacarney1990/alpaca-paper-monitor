"""V4.3 independent paper-only execution and shadow gating regression tests."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.error import HTTPError

import paper_pilot as p

NOW = datetime(2026, 10, 19, 15, 50, tzinfo=timezone.utc)


def good_report():
    return {
        'environment':'PAPER_READ_ONLY_AND_VIRTUAL_ONLY','real_broker_orders_submitted':0,
        'as_of_utc':(NOW-timedelta(hours=16)).isoformat(),
        'market_data_status':'ready','market_errors':[],
        'ai_audit':{'status':'recent'},
        'v31_performance':{'observed_sessions':25,'closed_virtual_trades':6,
                           'max_observed_drawdown_percent':'1.5'},
        'virtual_state':{'pending':None,'virtual_equity':'505',
                         'day_start_equity':'505','peak_equity':'509'},
        'daily_market':{'SERV':{'status':'READY','bullish':True}},
        'v31_decisions':{'SERV':{'verdict':'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED',
            'reasons':[],'ai_research_quality':{
                'status':'MODEL_AGREEMENT_REVIEW_ONLY', 'classification':'watch',
                'evidence_verification':'INDEPENDENTLY_VERIFIED'},
            'shared_ai_context':{'status':'RECENT',
               'team_views':{'OpenAI':{},'Gemini':{},'Groq':{}}}}}}


def good_broker():
    return {'account':{'status':'ACTIVE','trading_blocked':False,'cash':'100000',
                       'equity':'100000'},
            'positions':[],'orders':[], 'clock':{'is_open':True}}


class PaperPilotSafety(unittest.TestCase):
    def test_build_shadow_plan_requires_all_gates(self):
        result = p.build_plan(good_report(),good_broker(),NOW,lambda s:Decimal('12.34'))
        self.assertEqual(result['symbol'],'SERV')
        self.assertEqual(result['qty'],'1')
        self.assertEqual(result['time_in_force'],'day')
        self.assertEqual(result['side'],'buy')
        self.assertEqual(result['client_order_id'],'v43p-20261019-SERV-buy')

    def test_no_verified_evidence_blocks_all_orders(self):
        r=good_report()
        r['v31_decisions']['SERV']['ai_research_quality']['evidence_verification']='NOT_VERIFIED'
        with self.assertRaisesRegex(p.PilotBlock,'NO_VERIFIED_THREE_AI_ENTRY_SIGNAL'):
            p.build_plan(r,good_broker(),NOW,lambda s:Decimal('12.34'))

    def test_missing_one_team_member_blocks(self):
        r=good_report();r['v31_decisions']['SERV']['shared_ai_context']['team_views'].pop('Groq')
        with self.assertRaisesRegex(p.PilotBlock,'NO_VERIFIED_THREE_AI_ENTRY_SIGNAL'):
            p.build_plan(r,good_broker(),NOW,lambda s:Decimal('12.34'))

    def test_virtual_sessions_gate(self):
        r=good_report();r['v31_performance']['observed_sessions']=3
        with self.assertRaisesRegex(p.PilotBlock,'INSUFFICIENT_VIRTUAL_SESSIONS'):
            p.build_plan(r,good_broker(),NOW,lambda s:Decimal('12.34'))

    def test_closed_trades_gate(self):
        r=good_report();r['v31_performance']['closed_virtual_trades']=0
        with self.assertRaisesRegex(p.PilotBlock,'INSUFFICIENT_VIRTUAL_CLOSED_TRADES'):
            p.build_plan(r,good_broker(),NOW,lambda s:Decimal('12.34'))

    def test_daily_loss_gate(self):
        r=good_report();r['virtual_state']['day_start_equity']='600'
        with self.assertRaisesRegex(p.PilotBlock,'VIRTUAL_RISK_HALT'):
            p.build_plan(r,good_broker(),NOW,lambda s:Decimal('12.34'))

    def test_old_report_gate(self):
        r=good_report();r['as_of_utc']=(NOW-timedelta(hours=25)).isoformat()
        with self.assertRaisesRegex(p.PilotBlock,'V3_REPORT_NOT_FRESH'):
            p.build_plan(r,good_broker(),NOW,lambda s:Decimal('12.34'))

    def test_account_with_other_positions_blocks(self):
        b=good_broker();b['positions']=[{'symbol':'AAPL','qty':'1'}]
        with self.assertRaisesRegex(p.PilotBlock,'PAPER_ACCOUNT_NOT_DEDICATED_AND_EMPTY'):
            p.build_plan(good_report(),b,NOW,lambda s:Decimal('12.34'))

    def test_market_closed_blocks(self):
        b=good_broker();b['clock']['is_open']=False
        with self.assertRaisesRegex(p.PilotBlock,'US_STOCK_MARKET_CLOSED'):
            p.build_plan(good_report(),b,NOW,lambda s:Decimal('12.34'))

    def test_quote_limit_blocks(self):
        with self.assertRaisesRegex(p.PilotBlock,'PILOT_POSITION_LIMIT'):
            p.build_plan(good_report(),good_broker(),NOW,lambda s:Decimal('25.01'))

    def test_pinned_broker_host(self):
        with self.assertRaisesRegex(p.PilotBlock,'NON_PAPER_API_HOST_BLOCKED'):
            p.checked_http('https://api.alpaca.markets/v2/orders', {},payload={'side':'buy'})

    def test_quote_spread_and_timestamp(self):
        broker_headers={'X-Test':'fake'}
        def provider(_,__,payload=None):
            pass
        asset={'tradable':True,'status':'active','class':'us_equity'}
        bad_quote={'quote':{'bp':'10','ap':'10.5','t':NOW.isoformat()}}
        with patch.object(p,'checked_http',side_effect=[asset,bad_quote]):
            with self.assertRaisesRegex(p.PilotBlock,'QUOTE_SPREAD_TOO_WIDE'):
                p.executable_quote('SERV',broker_headers,NOW)
        stale_quote={'quote':{'bp':'10','ap':'10.01','t':(NOW-timedelta(minutes=5)).isoformat()}}
        with patch.object(p,'checked_http',side_effect=[asset,stale_quote]):
            with self.assertRaisesRegex(p.PilotBlock,'QUOTE_STALE'):
                p.executable_quote('SERV',broker_headers,NOW)

    def test_quote_valid_limit_is_round_up(self):
        asset={'tradable':True,'status':'active','class':'us_equity'}
        quote={'quote':{'bp':'12.30','ap':'12.34','t':NOW.isoformat()}}
        with patch.object(p,'checked_http',side_effect=[asset,quote]):
            self.assertEqual(p.executable_quote('SERV',{},NOW),Decimal('12.38'))

    def test_submit_once_rejects_existing_ledger(self):
        record={'schema':1,'orders':[{'client_order_id':'v43p-a','state':'RESERVED'}]}
        plan=p.build_plan(good_report(),good_broker(),NOW,lambda s:Decimal('12.34'))
        with patch.object(p,'load_ledger',return_value=(7,record)):
            with self.assertRaisesRegex(p.PilotBlock,'PAPER_PILOT_ALREADY_HAS_ORDER'):
                p.submit_once('a/b',plan,NOW,{})

    def test_submit_stores_intent_before_order_post(self):
        ledger={'schema':1,'orders':[]}
        plan=p.build_plan(good_report(),good_broker(),NOW,lambda s:Decimal('12.34'))
        events=[]
        def save(repo,num,state):
            events.append(('save',state['orders'][0]['state']))
            return 22
        def request(url, headers, payload=None):
            if 'orders:by_client_order_id' in url:
                raise p.PilotBlock('PAPER_HTTP_404')
            events.append(('post',payload['client_order_id']))
            return {'id':'test-id','client_order_id':plan['client_order_id'],
                    'symbol':plan['symbol'],'side':'buy','status':'new'}
        with patch.object(p,'load_ledger',return_value=(None,ledger)),\
             patch.object(p,'save_ledger',side_effect=save),\
             patch.object(p,'checked_http',side_effect=request):
            result=p.submit_once('a/b',plan,NOW,{})
        self.assertEqual(result['state'],'SUBMITTED')
        self.assertEqual([x[0] for x in events],['save','post','save'])

    def test_transient_order_post_never_retries_and_leaves_intent(self):
        ledger={'schema':1,'orders':[]}
        plan=p.build_plan(good_report(),good_broker(),NOW,lambda s:Decimal('12.34'))
        events=[]
        def request(url,headers,payload=None):
            if 'orders:by_client_order_id' in url:
                raise p.PilotBlock('PAPER_HTTP_404')
            events.append('POST')
            raise p.PilotBlock('PAPER_TRANSPORT_OR_FORMAT_ERROR')
        with patch.object(p,'load_ledger',return_value=(None,ledger)),\
             patch.object(p,'save_ledger',return_value=20),\
             patch.object(p,'checked_http',side_effect=request):
            with self.assertRaisesRegex(p.PilotBlock,'PAPER_POST_RESULT_UNKNOWN'):
                p.submit_once('a/b',plan,NOW,{})
        self.assertEqual(events,['POST'])
        self.assertEqual(ledger['orders'][0]['state'],'RESERVED')

    def test_submission_requires_explicit_dual_approval(self):
        with patch.dict(os.environ, {'PAPER_PILOT_MODE':'submit',
                                     'PAPER_PILOT_APPROVED':'false'},clear=True),\
             patch.object(p.paper_bridge,'find_v3_report') as finder:\
            p.main()
        finder.assert_not_called()
        with open(p.REPORT_PATH,encoding='utf-8') as handle:
            data=json.load(handle)
        self.assertEqual(data['reason'],'PAPER_PILOT_SUBMISSION_NOT_APPROVED')
        self.assertEqual(data['paper_orders_submitted'],0)

    def test_shadow_mode_never_submits(self):
        with patch.dict(os.environ, {'PAPER_PILOT_MODE':'shadow',
                                     'GITHUB_TOKEN':'token','GITHUB_REPOSITORY':'a/b'},clear=True),\
             patch.object(p.paper_bridge,'find_v3_report',return_value=(good_report(),100)),\
             patch.object(p,'paper_headers',return_value={}),\
             patch.object(p,'get_broker_snapshot',return_value=good_broker()),\
             patch.object(p,'executable_quote',return_value=Decimal('12.34')),\
             patch.object(p,'submit_once') as submit,\
             patch.object(p,'datetime',wraps=datetime) as mock_time:
            mock_time.now.return_value=NOW
            p.main()
        submit.assert_not_called()
        with open(p.REPORT_PATH,encoding='utf-8') as handle:
            data=json.load(handle)
        self.assertEqual(data['status'],'ELIGIBLE_SHADOW_PLAN')
        self.assertEqual(data['paper_orders_submitted'],0)

if __name__ == '__main__':
    unittest.main()
