import copy
import io
import json
import os
import sys
import unittest
import zipfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import ops_supervisor as ops

NOW = datetime(2026, 10, 20, 1, 37, tzinfo=timezone.utc)
RUN = {
    'id': 177,
    'event': 'schedule',
    'created_at': '2026-10-19T22:27:00Z',
    'conclusion': 'success',
    'status': 'completed',
}


def valid_report():
    return {
        'as_of_utc': '2026-10-19T22:35:00Z',
        'environment': 'PAPER_READ_ONLY_AND_VIRTUAL_ONLY',
        'real_broker_orders_submitted': 0,
        'alpaca_paper': {'status': 'read_only'},
        'ai_audit': {'status': 'recent'},
        'market_data_status': 'ready',
        'market_errors': [],
        'daily_market': {
            'VTI': {'status': 'READY', 'last_close': '380', 'bar_date':'2026-10-19'},
            'SERV': {'status': 'READY', 'last_close': '5', 'bar_date':'2026-10-19'},
        },
        'virtual_state': {
            'cash':'490.00', 'virtual_equity':'500.00',
            'day_start_equity':'500.00', 'peak_equity':'500.00',
            'positions': {'SERV': {'qty':'2', 'basis':'5.00'}},
        },
        'v31_decisions': {'SERV': {'verdict':'HOLD_POSITION', 'reasons': []}},
        'v31_performance': {'status':'OBSERVATIONS_IN_PROGRESS', 'observed_sessions':3,
                            'most_recent_market_day':'2026-10-19'},
        'events': [],
    }


def zip_report(report):
    f=io.BytesIO()
    with zipfile.ZipFile(f, 'w') as z:
        z.writestr('v3-report.json', json.dumps(report))
    return f.getvalue()


class SupervisorTests(unittest.TestCase):
    def test_scheduled_only_selects_previous_market_day_schedule(self):
        later_manual = dict(RUN, id=200, event='workflow_dispatch', created_at='2026-10-20T00:00:00Z')
        older = dict(RUN, id=100, created_at='2026-10-16T22:23:00Z')
        r, reason = ops.find_run([later_manual, older, RUN], NOW, scheduled=True)
        self.assertEqual(r['id'], 177)
        self.assertIsNone(reason)

    def test_missing_expected_run_is_critical(self):
        result=ops.find_run([dict(RUN, event='workflow_dispatch')],NOW,True)
        self.assertEqual(result,(None,'MISSING_SCHEDULED_RUN'))

    def test_manual_preflight_accepts_recent_manual_run(self):
        manual=dict(RUN,event='workflow_dispatch')
        r,reason=ops.find_run([manual],NOW,False)
        self.assertEqual(r['id'],177)
        self.assertIsNone(reason)

    def test_healthy(self):
        r=ops.inspect_report(valid_report(),NOW,True)
        self.assertEqual(r['status'],'HEALTHY')
        self.assertEqual(r['critical'],[])

    def test_market_data_missing(self):
        r=valid_report();r['market_data_status']='unavailable';r['daily_market']={}
        x=ops.inspect_report(r,NOW,True)
        self.assertIn('NO_USABLE_MARKET_DATA',x['critical'])

    def test_bad_broker_environment(self):
        r=valid_report();r['environment']='LIVE_TRADING';r['real_broker_orders_submitted']=1
        x=ops.inspect_report(r,NOW,True)
        self.assertIn('UNEXPECTED_EXECUTION_ENVIRONMENT',x['critical'])
        self.assertIn('UNEXPECTED_BROKER_ORDER_COUNT',x['critical'])

    def test_stale_report(self):
        r=valid_report();r['as_of_utc']='2026-10-10T10:00:00Z'
        x=ops.inspect_report(r,NOW,True)
        self.assertIn('STALE_OR_FUTURE_V3_REPORT',x['critical'])

    def test_missing_held_price(self):
        r=valid_report();r['daily_market'].pop('SERV')
        x=ops.inspect_report(r,NOW,True)
        self.assertIn('MISSING_HELD_POSITION_MARK',x['critical'])

    def test_simulation_disabled(self):
        r=valid_report();r['v31_performance']['status']='SIMULATION_DISABLED'
        x=ops.inspect_report(r,NOW,True)
        self.assertIn('EXPECTED_SIMULATION_NOT_ENABLED',x['critical'])
        x2=ops.inspect_report(r,NOW,False)
        self.assertNotIn('EXPECTED_SIMULATION_NOT_ENABLED',x2['critical'])

    def test_ordinary_no_consensus_is_not_health_outage(self):
        r=valid_report();r['v31_decisions']['SERV']={'verdict':'WATCH','reasons':['AI_NO_CONSENSUS']}
        x=ops.inspect_report(r,NOW,True)
        self.assertEqual(x['status'],'HEALTHY')

    def test_cannot_download_artifact_to_untrusted_hostname(self):
        from urllib.error import HTTPError
        fake_headers={'Location':'https://evil.example.net/report.zip'}
        ex=HTTPError('url',302,'Moved',fake_headers,None)
        with patch.object(ops, 'build_opener') as mock:
            mock.return_value.open.side_effect = ex
            with self.assertRaisesRegex(ops.SupervisorError,'Untrusted'):
                ops.download_artifact('owner/repo',123,'TOKEN')

    def test_download_ignores_token_on_signed_redirect(self):
        from urllib.error import HTTPError
        ex=HTTPError('url',302,'Moved',{'Location':'https://example.actions.githubusercontent.com/report.zip'},None)
        with patch.object(ops,'build_opener') as fake, patch.object(ops,'urlopen') as second:
            fake.return_value.open.side_effect=ex
            obj=second.return_value.__enter__.return_value
            obj.read.return_value=zip_report(valid_report())
            got=ops.download_artifact('owner/repo',42,'TOPSECRET')
            self.assertEqual(got['virtual_state']['cash'],'490.00')
            req=second.call_args.args[0]
            self.assertNotIn('Authorization',req.headers)

    def test_report_failure_and_success(self):
        def fake_json(path,token):
            if path.endswith('runs?per_page=30'):
                return {'workflow_runs':[RUN]}
            return {'artifacts':[{'name':'investment-v3-177','id':22,'expired':False}]}
        with patch.object(ops,'github_json',side_effect=fake_json),patch.object(ops,'download_artifact',return_value=valid_report()):
            r=ops.supervise('owner/repo','secret',NOW,True,True)
        self.assertEqual(r['status'],'HEALTHY')
        with patch.object(ops,'github_json',return_value={'workflow_runs':[dict(RUN,conclusion='failure')]}):
            r=ops.supervise('owner/repo','secret',NOW,True,True)
        self.assertIn('V3_RUN_NOT_SUCCESSFUL',r['critical'])

    def test_fails_closed_if_artifact_missing(self):
        with patch.object(ops,'github_json',side_effect=[{'workflow_runs':[RUN]},{'artifacts':[]}]):
            r=ops.supervise('owner/repo','secret',NOW,True,True)
        self.assertIn('V3_AUDIT_ARTIFACT_MISSING',r['critical'])

    def test_negative_and_nan_balances_rejected(self):
        for invalid in ('NaN','-100','Infinity',None,'oops'):
            r=valid_report();r['virtual_state']['cash']=invalid
            result=ops.inspect_report(r,NOW,True)
            self.assertIn('INVALID_VIRTUAL_CASH',result['critical'])

    def test_warning_not_forced_trading(self):
        r=valid_report();r['ai_audit']['status']='unavailable'
        x=ops.inspect_report(r,NOW,True)
        self.assertEqual(x['status'],'WARNING')
        self.assertIn('NO_RECENT_TWO_MODEL_AUDIT',x['warnings'])

    def test_alert_does_not_copy_portfolio_or_keys(self):
        report=valid_report(); result=ops.inspect_report(report,NOW,True)
        result['status']='CRITICAL';result['critical'].append('MISSING_SCHEDULED_RUN')
        with patch.dict(os.environ, {'RESEND_API_KEY':'SECRET','ALERT_EMAIL_TO':'recipient@example.com',
                                      'GITHUB_REPOSITORY':'owner/repo'}), patch.object(ops.intelligence,'send_email') as fake:
            ops.send_alert(result,NOW)
        text=fake.call_args.args[1]
        self.assertNotIn('SECRET',text)
        self.assertNotIn('490.00',text)
        self.assertIn('MISSING_SCHEDULED_RUN',text)


if __name__=='__main__':
    unittest.main()
