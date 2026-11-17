import inspect
import json
import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import paper_bridge as b

NOW = datetime(2026,10,19,23,15,tzinfo=timezone.utc)

def fixture():
    report = {'environment':'PAPER_READ_ONLY_AND_VIRTUAL_ONLY',
       'real_broker_orders_submitted':0, 'as_of_utc':(NOW-timedelta(minutes=25)).isoformat(),
       'alpaca_paper':{'status':'read_only','positions':[]},
       'ai_audit':{'status':'recent','as_of':(NOW-timedelta(hours=2)).isoformat()},
       'market_data_status':'ready','market_errors':[],
       'v31_performance':{'observed_sessions':25},
       'virtual_state':{'virtual_equity':'500','day_start_equity':'500','peak_equity':'500','pending':None},
       'v31_decisions':{'SERV':{'verdict':'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED','reasons':[],
           'ai_research_quality':{'status':'MODEL_AGREEMENT_REVIEW_ONLY','evidence_verification':'NOT_VERIFIED'}}},
       'daily_market':{'SERV':{'status':'READY','bullish':True,'last_close':'4','bar_date':'2026-10-19'}}}
    broker = {'status':'read_only','environment':'ALPACA_PAPER_ONLY','cash':'100000','equity':'100000',
         'positions':[],'open_orders':[]}
    return report,broker

class PaperBridgeTests(unittest.TestCase):
    def test_read_only_code_never_posts_orders(self):
        source = inspect.getsource(b)
        self.assertNotIn('/v2/orders',source)
        self.assertNotIn('method=\'POST\'',source)
    def test_normal_preflight_includes_evidence_block(self):
        r,a=fixture();o=b.evaluate(r,a,NOW,scheduled=True)
        self.assertEqual(o['status'],'MONITORING')
        self.assertFalse(o['execution_enabled'])
        self.assertIn('ORIGINAL_NEWS_AND_FILINGS_NOT_INDEPENDENTLY_VERIFIED',o['candidates'][0]['reasons'])
    def test_bearish_no_ai_agreement_blocks(self):
        r,a=fixture();r['v31_decisions']['SERV']['verdict']='WATCH';r['daily_market']['SERV']['bullish']=False
        o=b.evaluate(r,a,NOW,scheduled=True)
        self.assertIn('NO_ELIGIBLE_V31_ENTRY_SIGNAL',o['candidates'][0]['reasons'])
        self.assertIn('NOT_VALIDATED_BULLISH_DAILY_MARKET_DATA',o['candidates'][0]['reasons'])
    def test_holding_refuses_shared_broker_account(self):
        r,a=fixture();a['positions']=[{'symbol':'NVDA'}]
        self.assertIn('PAPER_ACCOUNT_NOT_EMPTY_OR_HAS_OPEN_ORDERS',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_open_order_refuses_shared_broker_account(self):
        r,a=fixture();a['open_orders']=[{'symbol':'SERV','side':'buy'}]
        self.assertIn('PAPER_ACCOUNT_NOT_EMPTY_OR_HAS_OPEN_ORDERS',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_unknown_account_is_blocked(self):
        r,a=fixture();a['status']='unavailable'
        self.assertIn('ALPACA_PAPER_SNAPSHOT_NOT_VERIFIED',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_stale_run_refused(self):
        r,a=fixture();r['as_of_utc']=(NOW-timedelta(days=4)).isoformat()
        self.assertIn('V3_REPORT_STALE_OR_FUTURE',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_future_run_refused(self):
        r,a=fixture();r['as_of_utc']=(NOW+timedelta(minutes=2)).isoformat()
        self.assertIn('V3_REPORT_STALE_OR_FUTURE',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_too_few_sessions_refused(self):
        r,a=fixture();r['v31_performance']['observed_sessions']=3
        self.assertIn('FEWER_THAN_20_VIRTUAL_MARKET_SESSIONS',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_daily_stop_refused(self):
        r,a=fixture();r['virtual_state']['virtual_equity']='489'
        self.assertIn('DAILY_TWO_PERCENT_LOSS_LIMIT_REACHED',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_drawdown_refused(self):
        r,a=fixture();r['virtual_state']['peak_equity']='600';r['virtual_state']['virtual_equity']='500'
        self.assertIn('TEN_PERCENT_DRAWDOWN_LIMIT_REACHED',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_over_25_refused(self):
        r,a=fixture();r['daily_market']['SERV']['last_close']='49'
        self.assertIn('ONE_WHOLE_SHARE_EXCEEDS_25_USD_CAP',b.evaluate(r,a,NOW,scheduled=True)['candidates'][0]['reasons'])
    def test_missing_market_data_refused(self):
        r,a=fixture();r['market_data_status']='partial'
        self.assertIn('INCOMPLETE_VALID_MARKET_DATA',b.evaluate(r,a,NOW,scheduled=True)['blocks'])
    def test_weekend_refused(self):
        r,a=fixture();o=b.evaluate(r,a,datetime(2026,10,17,23,15,tzinfo=timezone.utc),scheduled=False)
        self.assertIn('WEEKEND_NO_US_EQUITY_SESSION',o['blocks'])
    def test_etf_is_excluded(self):
        r,a=fixture();r['v31_decisions']={'VTI':r['v31_decisions']['SERV']}
        self.assertEqual([],b.evaluate(r,a,NOW,scheduled=True)['candidates'])
    def test_latest_failed_run_not_overridden_by_success(self):
        calls=[]
        def fake_api(path,token):
            calls.append(path)
            return {'workflow_runs':[{'created_at':'2026-10-19T23:00:00Z','event':'schedule','status':'completed','conclusion':'failure','id':2},
                        {'created_at':'2026-10-19T22:24:00Z','event':'schedule','status':'completed','conclusion':'success','id':1}]}
        with patch.object(b.ops,'github_json',side_effect=fake_api):
            with self.assertRaisesRegex(b.PreflightError,'LATEST_V3_NOT_SUCCESSFUL'):
                b.find_v3_report('owner/repo','token',NOW,scheduled=True)
    def test_verified_evidence_still_cannot_order_without_fresh_quote(self):
        r,a=fixture();r['v31_decisions']['SERV']['ai_research_quality']['evidence_verification']='INDEPENDENTLY_VERIFIED'
        c=b.evaluate(r,a,NOW,scheduled=True)['candidates'][0]
        self.assertIn('FRESH_TRADABLE_LIMIT_QUOTE_NOT_YET_IMPLEMENTED',c['reasons'])
    def test_wrong_execution_env_refused(self):
        r,a=fixture();r['environment']='ALPACA_LIVE'
        self.assertIn('UNEXPECTED_V3_EXECUTION_ENVIRONMENT',b.evaluate(r,a,NOW,scheduled=True)['blocks'])


class CompletionTriggerTests(unittest.TestCase):
    def parent(self, **updates):
        r = dict(id=42, event='schedule', head_branch='main',
                 head_repository={'full_name':'owner/repo'},
                 created_at='2026-10-19T23:58:55Z', status='completed', conclusion='success')
        r.update(updates)
        return r

    def find(self, runs, **kwargs):
        metadata = {'artifacts':[{'id':7,'name':'investment-v3-42','expired':False}]}
        with patch.object(b.ops, 'github_json', side_effect=[{'workflow_runs':runs}, metadata]), \
             patch.object(b.ops, 'download_artifact', return_value={'report':'parent'}) as download:
            result = b.find_v3_report('owner/repo', 'token',
                datetime(2026,10,20,0,2,tzinfo=timezone.utc), scheduled=True,
                source_run_id=kwargs.get('source_run_id', '42'))
            download.assert_called_once_with('owner/repo',7,'token')
            return result

    def test_parent_completion_across_midnight(self):
        self.assertEqual(self.find([self.parent()]), ({'report':'parent'},42))

    def test_failed_parent_not_hidden_by_older_success(self):
        with self.assertRaisesRegex(b.PreflightError, 'LATEST_V3_NOT_SUCCESSFUL'):
            self.find([self.parent(conclusion='failure'), self.parent(id=41,created_at='2026-10-19T22:00:00Z')])

    def test_parent_not_completed(self):
        with self.assertRaisesRegex(b.PreflightError, 'LATEST_V3_NOT_SUCCESSFUL'):
            self.find([self.parent(status='in_progress')])

    def test_newer_run_prevents_old_parent_fallback(self):
        with self.assertRaisesRegex(b.PreflightError, 'V3_PARENT_SUPERSEDED'):
            self.find([self.parent(),self.parent(id=43,created_at='2026-10-19T23:59:00Z',conclusion='failure')])

    def test_newer_next_day_run_prevents_old_parent_fallback(self):
        with self.assertRaisesRegex(b.PreflightError, 'V3_PARENT_SUPERSEDED'):
            self.find([self.parent(), self.parent(id=43,
                created_at='2026-10-20T00:01:00Z', conclusion='failure')])

    def test_wrong_parent_origin_refused(self):
        for update in ({'event':'workflow_dispatch'}, {'head_branch':'other'},
                       {'head_repository':{'full_name':'fork/repo'}}):
            with self.subTest(update=update), self.assertRaisesRegex(b.PreflightError,'UNEXPECTED_V3_PARENT_ORIGIN'):
                self.find([self.parent(**update)])

    def test_parent_not_in_expected_workflow_refused(self):
        with self.assertRaisesRegex(b.PreflightError,'V3_PARENT_NOT_IN_WORKFLOW'):
            self.find([self.parent(id=99)])

    def test_invalid_parent_id_refused(self):
        with self.assertRaisesRegex(b.PreflightError,'INVALID_V3_PARENT_RUN_ID'):
            self.find([self.parent()],source_run_id='42/../99')

    def test_stale_or_future_parent_refused(self):
        for stamp in ('2026-10-18T23:00:00Z','2026-10-20T01:00:00Z'):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(b.PreflightError,'V3_PARENT_STALE_OR_FUTURE'):
                self.find([self.parent(created_at=stamp)])

    def test_workflow_uses_completion_not_a_clock_race(self):
        from pathlib import Path
        workflow = (Path(b.__file__).parent / '.github/workflows/investment-paper-bridge.yml').read_text()
        self.assertIn('workflow_run:', workflow)
        self.assertIn("workflows: ['Investment V3 (paper-aware, virtual trades only)']", workflow)
        self.assertNotIn('cron:', workflow)
        self.assertIn('V3_SOURCE_RUN_ID: ${{ github.event.workflow_run.id }}',workflow)
        self.assertNotIn('conclusion ==',workflow)  # failed parents remain visible

if __name__=='__main__':unittest.main()
