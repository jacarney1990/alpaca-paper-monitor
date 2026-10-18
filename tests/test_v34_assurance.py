"""Offline tests. No external APIs, broker orders or costs."""
import os
import pathlib
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from urllib.error import HTTPError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import v34_assurance as v

NOW = datetime(2026, 10, 20, 19, 37, tzinfo=timezone.utc)

def quote(ts=None, symbol='SERV', bp=3.00, ap=3.01, bs=2, ask_size=2):
    return {'symbol': symbol, 'quote': {
        'bp': bp, 'ap': ap, 'bs': bs, 'as': ask_size,
        't': (ts or (NOW - timedelta(seconds=10))).isoformat()}}

def asset(sym='SERV', tradable=True):
    return {'symbol': sym, 'status': 'active', 'tradable': tradable}

def report(history=None):
    history = history if history is not None else [
        {'market_date': '2026-10-16', 'virtual_equity': '500.00', 'benchmark_value': '500'}]
    return {'virtual_state': {'virtual_equity': history[-1]['virtual_equity'] if history else '500',
                              'performance_history': history, 'trade_ledger': []},
            'v31_performance': {'observed_sessions': len(history)},
            'benchmark': {'status':'daily_price_comparison_only','vti_value_of_500':'500'},
            'v31_candidate_selection': {'priced_candidates': ['SERV', 'IONQ', 'VTI']}}


class GuardTests(unittest.TestCase):
    def test_non_allowlisted_host(self):
        with self.assertRaisesRegex(v.AssuranceError, 'HOST_NOT_ALLOWED'):
            v.http_bytes('https://attacker.example/send', {})

    def test_no_order_post_literal(self):
        source = pathlib.Path(v.__file__).read_text()
        self.assertNotIn("method='POST'", source)
        self.assertNotIn('/v2/orders', source)
        self.assertNotIn('submit_order(', source)
        self.assertIn('execution_enabled', source)

    def test_symbol_allowlist(self):
        self.assertEqual(v.selected_symbols(report()), ['SERV','IONQ'])

    def test_malicious_symbols_are_ignored(self):
        r = report(); r['v31_candidate_selection']['priced_candidates'] = ['SERV','SERV','BAD/../../orders',None]
        self.assertEqual(v.selected_symbols(r), ['SERV'])

    def test_many_candidates_fails(self):
        r = report(); r['v31_candidate_selection']['priced_candidates'] = ['SERV','IONQ','IGC','CRSP']
        with self.assertRaisesRegex(v.AssuranceError, 'TOO_MANY'):
            v.selected_symbols(r)

    def test_no_candidate_list_blocks(self):
        with self.assertRaises(v.AssuranceError):
            v.selected_symbols({})

    def test_invalid_decimal_fails(self):
        for d in ('NaN','Infinity','-Infinity',True,None,'foobar'):
            with self.subTest(d=d), self.assertRaises(v.AssuranceError):
                v.checked_decimal(d)

    def test_naive_timestamp_rejected(self):
        with self.assertRaisesRegex(v.AssuranceError,'NAIVE_TIME'):
            v.checked_dt('2026-10-20T19:30:00')

    def test_secret_safe_error(self):
        self.assertEqual(v.safe_error(RuntimeError('my private key')), 'RuntimeError')


class SECMetadataTests(unittest.TestCase):
    def setUp(self):
        self.index = {'tickers':['SERV'], 'filings': {'recent': {
            'form':['8-K','10-Q', '4','8-K'],
            'filingDate':['2026-10-18','2026-10-11','2026-10-18','2026-10-18'],
            'accessionNumber':['0001112222-26-000005','0001112222-26-000003',
                               '0001112222-26-000004','0001112222-26-000006'],
            'primaryDocument':['a.htm','q.htm','form4.xml','../../malicious.htm']}}}
        self.cik = {'SERV':'0001112222'}

    def test_verified_metadata_allowlist(self):
        items, status = v.sec_metadata('SERV', self.cik, self.index, NOW)
        self.assertEqual(status, 'METADATA_MATCHED')
        self.assertEqual(len(items), 2)
        self.assertTrue(all(x['official_url'].startswith('https://www.sec.gov/Archives/') for x in items))
        self.assertTrue(all(x['form'] in v.FORMS for x in items))

    def test_ticker_mismatch(self):
        self.index['tickers']=['OTHER']
        self.assertEqual(v.sec_metadata('SERV', self.cik, self.index, NOW)[1], 'SUBMISSION_TICKER_MISMATCH')

    def test_missing_cik(self):
        self.assertEqual(v.sec_metadata('SERV', {}, self.index, NOW)[1], 'CIK_NOT_FOUND')

    def test_old_filings_not_accepted(self):
        self.index['filings']['recent']['filingDate']=['2026-08-01']*4
        self.assertEqual(v.sec_metadata('SERV', self.cik, self.index, NOW)[0], [])

    def test_future_filing_not_accepted(self):
        self.index['filings']['recent']['filingDate']=['2026-12-01']*4
        self.assertEqual(v.sec_metadata('SERV', self.cik, self.index, NOW)[0], [])

    @patch.dict(os.environ, {'SEC_USER_AGENT':'InvestmentMonitor contact@example.com'})
    @patch.object(v.time, 'sleep')
    @patch.object(v, 'http_bytes', return_value=b'<html><body>Original SEC filing contents go here. 1234567890</body></html>')
    @patch.object(v, 'http_json')
    def test_document_retrieved_does_not_verify_claim(self, fetch_json, get_document, _):
        fetch_json.side_effect=[{'1': {'ticker':'SERV','cik_str':1112222}},self.index]
        data=v.verify_sec_sources(['SERV'],NOW)
        self.assertEqual(data['claim_verification'],'NOT_PERFORMED')
        self.assertEqual(data['by_symbol']['SERV']['verified_document_count'],2)
        self.assertTrue(data['by_symbol']['SERV']['filings'][0]['sha256'])

    @patch.dict(os.environ, {'SEC_USER_AGENT':'InvestmentMonitor contact@example.com'})
    @patch.object(v, 'http_json', side_effect=HTTPError('url',403,'Forbidden',{},None))
    def test_sec_403_nonfatal(self, _):
        self.assertEqual(v.verify_sec_sources(['SERV'], NOW)['status'],'SOURCE_UNAVAILABLE')


class QuoteTests(unittest.TestCase):
    def test_fresh_valid_quote(self):
        r=v.assess_quote('SERV',quote(),asset(),NOW)
        self.assertEqual(r['status'],'QUOTE_VALIDATED_READ_ONLY')
        self.assertTrue(Decimal(r['illustrative_max_limit_price'])<=25)

    def test_future_quote_blocks(self):
        r=v.assess_quote('SERV',quote(NOW+timedelta(seconds=4)),asset(),NOW)
        self.assertIn('QUOTE_STALE_OR_FUTURE',r['reasons'])

    def test_stale_quote_blocks(self):
        r=v.assess_quote('SERV',quote(NOW-timedelta(seconds=91)),asset(),NOW)
        self.assertIn('QUOTE_STALE_OR_FUTURE',r['reasons'])

    def test_large_spread_blocks(self):
        r=v.assess_quote('SERV',quote(bp=2.50,ap=3.01),asset(),NOW)
        self.assertIn('SPREAD_EXCEEDS_50_BPS',r['reasons'])

    def test_high_price_blocks_whole_share(self):
        r=v.assess_quote('SERV',quote(bp=55.00,ap=55.02),asset(),NOW)
        self.assertIn('ONE_SHARE_EXCEEDS_25_USD_CAP',r['reasons'])

    def test_nontradable_asset_blocks(self):
        r=v.assess_quote('SERV',quote(),asset(tradable=False),NOW)
        self.assertIn('ASSET_NOT_ACTIVE_OR_TRADABLE',r['reasons'])

    def test_symbol_mismatch_blocks(self):
        self.assertIn('QUOTE_SYMBOL_OR_SHAPE_MISMATCH', v.assess_quote('SERV',quote(symbol='IONQ'),asset(),NOW)['reasons'])

    def test_crossed_quote_blocks(self):
        self.assertIn('INVALID_OR_CROSSED_QUOTE',v.assess_quote('SERV',quote(bp=3.02,ap=3.01),asset(),NOW)['reasons'])

    def test_zero_bid_blocks(self):
        self.assertIn('INVALID_OR_CROSSED_QUOTE',v.assess_quote('SERV',quote(bp=0),asset(),NOW)['reasons'])

    def test_missing_timestamp_blocks(self):
        q=quote(); q['quote'].pop('t')
        self.assertEqual(v.assess_quote('SERV',q,asset(),NOW)['reasons'],['MALFORMED_QUOTE'])

    @patch.dict(os.environ,{'ALPACA_PAPER_API_KEY':'fake','ALPACA_PAPER_API_SECRET':'fake'})
    @patch.object(v,'http_json',return_value={'timestamp':NOW.isoformat(),'is_open':False})
    def test_weekend_closed_prevents_quote_requests(self, api):
        res=v.verify_quotes(['SERV'],NOW)
        self.assertEqual(res['status'],'MARKET_CLOSED')
        api.assert_called_once()

    @patch.dict(os.environ,{'ALPACA_PAPER_API_KEY':'fake','ALPACA_PAPER_API_SECRET':'fake'})
    @patch.object(v,'http_json',side_effect=[
        {'timestamp':NOW.isoformat(),'is_open':True},asset(),quote()])
    def test_open_market_validates_quote_only(self, api):
        res=v.verify_quotes(['SERV'],NOW)
        self.assertEqual(res['by_symbol']['SERV']['status'],'QUOTE_VALIDATED_READ_ONLY')
        self.assertEqual(res['paper_orders_sent'],0)
        self.assertEqual(api.call_count,3)

    @patch.dict(os.environ,{'ALPACA_PAPER_API_KEY':'fake','ALPACA_PAPER_API_SECRET':'fake'})
    @patch.object(v,'http_json',return_value={'timestamp':(NOW-timedelta(hours=1)).isoformat(),'is_open':True})
    def test_stale_clock_blocks(self, _):
        self.assertEqual(v.verify_quotes(['SERV'],NOW)['status'],'UNTRUSTED_BROKER_CLOCK')


class PerformanceTests(unittest.TestCase):
    def test_one_session_insufficient(self):
        r=v.validate_performance(report())
        self.assertEqual(r['status'],'INSUFFICIENT_FOR_VALIDATION')
        self.assertEqual(r['observed_distinct_market_sessions'],1)

    def test_duplicate_sessions_rejected(self):
        h=[{'market_date':'2026-10-16','virtual_equity':'500'},
           {'market_date':'2026-10-16','virtual_equity':'499'}]
        self.assertEqual(v.validate_performance(report(h))['status'],'INVALID_PERFORMANCE_REPORT')

    def test_unsorted_sessions_rejected(self):
        h=[{'market_date':'2026-10-17','virtual_equity':'500'},
           {'market_date':'2026-10-16','virtual_equity':'499'}]
        self.assertEqual(v.validate_performance(report(h))['status'],'INVALID_PERFORMANCE_REPORT')

    def test_proven_max_drawdown(self):
        h=[{'market_date':'2026-10-15','virtual_equity':'500'},
           {'market_date':'2026-10-16','virtual_equity':'490'},
           {'market_date':'2026-10-17','virtual_equity':'495'}]
        r=v.validate_performance(report(h))
        self.assertEqual(r['max_observed_drawdown_percent'],'2.00')

    def test_count_mismatch_rejected(self):
        r=report();r['v31_performance']['observed_sessions']=10
        self.assertEqual(v.validate_performance(r)['status'],'INVALID_PERFORMANCE_REPORT')

    def test_unrealistic_last_equity_rejected(self):
        r=report();r['virtual_state']['virtual_equity']='490'
        self.assertEqual(v.validate_performance(r)['status'],'INVALID_PERFORMANCE_REPORT')

    def test_no_history_blocks_profitable_claim(self):
        r=v.validate_performance(report([]))
        self.assertIn('FEWER_THAN_20_DISTINCT_SESSIONS',r['warnings'])
        self.assertEqual(r['status'],'INSUFFICIENT_FOR_VALIDATION')

    def test_performance_does_not_claim_live_profits(self):
        r=v.validate_performance(report())
        self.assertIn('Not a backtest',r['limitations'])


if __name__ == '__main__':
    unittest.main()

class CandidateGateTests(unittest.TestCase):
    def test_even_good_quote_does_not_approve_order(self):
        r=report()
        r['v31_decisions']={'SERV': {'verdict':'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED','reasons':[]}}
        source={'by_symbol':{'SERV':{'verified_document_count':2}}}
        q={'by_symbol':{'SERV':{'status':'QUOTE_VALIDATED_READ_ONLY'}}}
        p={'status':'OBSERVATIONS_IN_PROGRESS'}
        out=v.candidate_gate_summary(['SERV'],r,source,q,p)['SERV']
        self.assertEqual(out['status'],'BLOCKED_FOR_BROKER_ORDERS')
        self.assertIn('NEWS_AND_FILING_CLAIMS_NOT_INDEPENDENTLY_VERIFIED',out['blocking_reasons'])
        self.assertIn('PAPER_EXECUTION_NOT_AUTHORIZED',out['blocking_reasons'])

    def test_bearish_decision_blocks(self):
        r=report();r['v31_decisions']={'SERV':{'verdict':'WATCH','reasons':['NOT_BULLISH']}}
        out=v.candidate_gate_summary(['SERV'],r,{}, {}, {})['SERV']
        self.assertIn('V31_ENTRY_NOT_ELIGIBLE',out['blocking_reasons'])

class ArtifactFetchTests(unittest.TestCase):
    @patch.object(v.ops, 'github_json')
    def test_newer_failed_workflow_blocks(self, gh):
        gh.return_value={'workflow_runs': [
            {'status':'completed','conclusion':'success','created_at':(NOW-timedelta(hours=3)).isoformat()},
            {'status':'completed','conclusion':'failure','created_at':(NOW-timedelta(hours=1)).isoformat()}]}
        with self.assertRaisesRegex(v.AssuranceError,'LATEST_V3_NOT_SUCCESSFUL'):
            v.retrieve_latest_v3('user/repo','not-real',NOW)

    @patch.object(v.ops, 'github_json')
    def test_old_workflow_report_rejected(self, gh):
        gh.return_value={'workflow_runs':[{'status':'completed','conclusion':'success',
             'created_at':(NOW-timedelta(hours=97)).isoformat()}]}
        with self.assertRaisesRegex(v.AssuranceError,'V3_REPORT_TOO_OLD'):
            v.retrieve_latest_v3('user/repo','not-real',NOW)

    def test_stale_quote_cannot_supply_limit(self):
        r=v.assess_quote('SERV',quote(NOW-timedelta(seconds=95)),asset(),NOW)
        self.assertIsNone(r['illustrative_max_limit_price'])
