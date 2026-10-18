import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

import shared_intelligence as v

NOW = datetime(2026,10,18,12,0,tzinfo=timezone.utc)
SOURCES = [
  {'id':'news:SERV:abc','kind':'NEWS LEAD (verify source)', 'title':'SERV earnings news','url':'https://example.org/news'},
  {'id':'sec:SERV:abc','kind':'OFFICIAL SEC FILING (review contents)', 'title':'SERV 8-K','url':'https://www.sec.gov/example'},
]

def review(cls='watch', ids=None):
    return {'classification':cls,'summary':'Headline does not prove the catalyst','main_risk':'Unverified claims',
            'confidence':70,'evidence_ids':ids or ['news:SERV:abc']}


def row(models=None):
    return {'symbol':'SERV','sources':SOURCES,'reviews':models or {'OpenAI':review(), 'Gemini':review('uncertain')}}


class TestSharedIntelligence(unittest.TestCase):
    def test_timestamp_rejects_naive(self):
        with self.assertRaisesRegex(v.SharedIntelligenceError, 'NAIVE'):
            v.utc_time('2026-10-18T00:00:00')

    def test_timestamp_rejects_bad(self):
        with self.assertRaises(v.SharedIntelligenceError):
            v.utc_time('tomorrow')

    def test_repo_no_path_traversal(self):
        for name in ('x/../y', 'a/b/c', 'a/b?x=2', 'a\nb'):
            with self.subTest(name=name), self.assertRaises(v.SharedIntelligenceError):
                v.safe_repo(name)

    def test_source_symbol_whitelist(self):
        bad=row()
        bad['sources']=[dict(SOURCES[0],id='news:IONQ:abc')]
        with self.assertRaisesRegex(v.SharedIntelligenceError, 'SOURCE_SYMBOL_MISMATCH'):
            v.clean_report(bad)

    def test_source_rejects_http(self):
        bad=row()
        bad['sources']=[dict(SOURCES[0], url='http://example.org')]
        with self.assertRaises(v.SharedIntelligenceError):
            v.clean_report(bad)

    def test_invalid_ticker_rejected(self):
        bad=row()
        bad['symbol']='NOT-A-TICKER'
        with self.assertRaises(v.SharedIntelligenceError):
            v.clean_report(bad)

    def test_two_independent_valid_reviews_required(self):
        bad=row({'OpenAI':review()})
        with self.assertRaises(v.SharedIntelligenceError):
            v.clean_report(bad)

    def test_invalid_ai_citation_rejected(self):
        bad=row({'OpenAI':review(ids=['fabricated']), 'Gemini':review()})
        with self.assertRaisesRegex(v.SharedIntelligenceError, 'INVALID_MODEL_REVIEW'):
            v.clean_report(bad)

    def test_unsupported_claim_of_opened_source_never_in_system_prompt(self):
        prompt=v.challenge_prompt('SERV',SOURCES,row()['reviews'],'OpenAI',[],NOW)
        data=json.loads(prompt)
        self.assertIn('UNVERIFIED',next(k for k in data if k.startswith('source_records_')))
        self.assertIn('Gemini',data['peer_reviews_UNVERIFIED_OPINIONS'])
        self.assertNotIn('OpenAI',data['peer_reviews_UNVERIFIED_OPINIONS'])
        self.assertIn('do not claim you opened URLs',data['rule'])

    def test_memory_filters_old_entries(self):
        entries=[{'symbol':'SERV','as_of':(NOW-timedelta(days=20)).isoformat(),'agreement':'AGREE_REVIEW_ONLY','shared_summary':'old'},
                 {'symbol':'SERV','as_of':(NOW-timedelta(days=3)).isoformat(),'agreement':'DISAGREEMENT_OR_UNCERTAIN','shared_summary':'current'}]
        self.assertEqual([x['summary'] for x in v.memory_for('SERV',entries,NOW)],['current'])

    def test_disagreement_cannot_authorize(self):
        status,_=v.compare(row()['reviews'],{'OpenAI':review('risk'), 'Gemini':review('opportunity')})
        self.assertEqual(status,'DISAGREEMENT_OR_UNCERTAIN')

    def test_agreement_still_research_only(self):
        status,_=v.compare(row()['reviews'],{'OpenAI':review('opportunity'),'Gemini':review('opportunity')})
        self.assertEqual(status,'AGREE_REVIEW_ONLY')
        self.assertNotIn('APPROVED_TO_TRADE',status)

    def test_collaborate_preserves_no_trade_and_2_responses(self):
        with patch.object(v.ai_team,'configured_models',return_value=['OpenAI','Gemini']), \
             patch.object(v.ai_team,'ask_provider',return_value=review()):
            out=v.collaborate(row(),[],NOW)
        self.assertEqual(out['investment_action'],'RESEARCH_ONLY')
        self.assertEqual(out['paper_broker_orders'],0)
        self.assertEqual(out['live_orders'],0)
        self.assertFalse(out['sources_verified_by_original_document'])
        self.assertEqual(len(out['cross_reviews']),2)

    def test_collaboration_does_not_store_incomplete_reviews(self):
        with patch.object(v.ai_team,'configured_models',return_value=['OpenAI','Gemini']), \
             patch.object(v.ai_team,'ask_provider',side_effect=[review(), ValueError('bad')]):
            with self.assertRaises(v.SharedIntelligenceError):
                v.collaborate(row(),[],NOW)

    def test_skips_unconfigured_peer(self):
        with patch.object(v.ai_team,'configured_models',return_value=['OpenAI']), \
             patch.object(v.ai_team,'ask_provider',return_value=review()):
            with self.assertRaises(v.SharedIntelligenceError):
                v.collaborate(row(),[],NOW)

    def test_latest_ai_run_failure_does_not_fallback(self):
        response={'workflow_runs':[
          {'id':5,'created_at':NOW.isoformat(),'status':'completed','conclusion':'failure'},
          {'id':4,'created_at':(NOW-timedelta(hours=1)).isoformat(),'status':'completed','conclusion':'success'}]}
        with patch.object(v.ops,'github_json',return_value=response):
            with self.assertRaisesRegex(v.SharedIntelligenceError,'LATEST_AI_TEAM_RUN_NOT_SUCCESSFUL'):
                v.recent_ai_artifact('a/b','not-a-real-secret',NOW)

    def test_no_run_when_no_sources(self):
        response={'workflow_runs':[{'id':4,'created_at':NOW.isoformat(),'status':'completed','conclusion':'success'}]}
        with patch.object(v.ops,'github_json',side_effect=[response,{'artifacts':[]}]):
            rows,run_id,when=v.recent_ai_artifact('a/b','a',NOW)
        self.assertEqual(rows,[])
        self.assertIsNone(run_id)

    def test_rejects_untrusted_ai_report_execution(self):
        response={'workflow_runs':[{'id':4,'created_at':NOW.isoformat(),'status':'completed','conclusion':'success'}]}
        with patch.object(v.ops,'github_json',side_effect=[response, {'artifacts':[{'id':10,'name':'ai-team-review-4','expired':False}]}]), \
             patch.object(v,'artifact_json',return_value={'execution':'ENABLED','portfolio_connected':False,'as_of':NOW.isoformat(),'reports':[row()]}):
            with self.assertRaises(v.SharedIntelligenceError):
                v.recent_ai_artifact('a/b','token',NOW)

    def test_noop_existing_run_is_idempotent(self):
        journal={'schema':1,'entries':[{'research_key':'21:SERV','symbol':'SERV','as_of':NOW.isoformat()}]}
        with patch.object(v,'load_journal',return_value=(3,journal)), \
             patch.object(v,'recent_ai_artifact',return_value=([row()],21,NOW)), \
             patch.object(v,'store_journal') as save, \
             patch.object(v.ai_team,'configured_models',return_value=['OpenAI','Gemini']):
            result=v.run('a/b','token',NOW)
        self.assertEqual(result['status'],'NO_NEW_RESEARCH_TO_CROSS_REVIEW')
        save.assert_not_called()

    def test_first_run_creates_journal(self):
        with patch.object(v,'load_journal',return_value=(None,{'entries':[]})), \
             patch.object(v,'recent_ai_artifact',return_value=([row()],21,NOW)), \
             patch.object(v,'collaborate',return_value={'symbol':'SERV','as_of':NOW.isoformat(),'agreement':'DISAGREEMENT_OR_UNCERTAIN','shared_summary':'watch','cross_reviews':{'OpenAI':review(), 'Gemini':review()},'changes':{},'source_evidence':SOURCES}), \
             patch.object(v,'store_journal') as store, \
             patch.object(v.ai_team,'configured_models',return_value=['OpenAI','Gemini']):
            result=v.run('a/b','token',NOW)
        self.assertEqual(result['status'],'CROSS_REVIEWS_SAVED')
        self.assertEqual(store.call_args[0][2][0]['research_key'],'21:SERV')

    def test_invalid_journal_state_aborts_not_overwrites(self):
        with patch.object(v.base,'github',return_value=[{'number':1,'title':v.JOURNAL_TITLE,'body':'not json'}]):
            with self.assertRaisesRegex(v.SharedIntelligenceError,'INVALID_JOURNAL_STATE'):
                v.load_journal('a/b')

    def test_journal_issue_payload_excludes_broker_data(self):
        with patch.object(v.base,'github') as send:
            v.store_journal('a/b',None,[{'symbol':'SERV','research_key':'1:SERV','shared_summary':'test','investment_action':'RESEARCH_ONLY'}])
        request=send.call_args[0][2]
        self.assertEqual(request['title'],v.JOURNAL_TITLE)
        self.assertNotIn('API_KEY',request['body'])
        self.assertNotIn('buying_power',request['body'])

    def test_compact_journal_excludes_raw_urls_and_original_payloads(self):
        example={
            'research_key':'21:SERV','source_ai_run_id':21,'symbol':'SERV',
            'as_of':NOW.isoformat(),'agreement':'DISAGREEMENT_OR_UNCERTAIN',
            'shared_summary':'watch only', 'cross_reviews':{'OpenAI':review(), 'Gemini':review()},
            'changes':{},'source_evidence':SOURCES,
        }
        compact=v.compact_journal_entry(example)
        data=json.dumps(compact)
        self.assertIn('source_fingerprints',compact)
        self.assertNotIn('https://',data)
        self.assertNotIn('source_evidence',data)
        self.assertEqual(compact['investment_action'],'RESEARCH_ONLY')

    def test_issue_prunes_old_entries_if_size_limit_is_reached(self):
        old=[{'research_key':f'{i}:SERV','symbol':'SERV','shared_summary':'x'*9000}
             for i in range(30)]
        with patch.object(v.base,'github') as send:
            retained=v.store_journal('a/b',1,old)
        body=send.call_args[0][2]['body']
        self.assertLess(len(body.encode()),95_001)
        self.assertLess(retained,30)
        self.assertIn('29:SERV',body)

    def test_no_github_credential_in_redirected_download(self):
        headers={'Location':'https://blob.actions.githubusercontent.com/secure/file.zip'}
        exc=HTTPError('https://api.github.com',302,'Found',headers,None)
        class FakedHTTP:
            def open(self, req, timeout=0):
                self.req=req
                raise exc
        http=FakedHTTP()
        artifact={'execution':'DISABLED'}
        buff=BytesIO()
        with zipfile.ZipFile(buff,'w') as z:
            z.writestr('ai-team-report.json',json.dumps(artifact))
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return buff.getvalue()
        urls=[]
        def safe_urlopen(req,timeout=0):
            urls.append(req)
            return Response()
        with patch.object(v,'build_opener',return_value=http), patch.object(v,'urlopen',side_effect=safe_urlopen):
            r=v.artifact_json('a/b','sensitive-token',2)
        self.assertEqual(r,artifact)
        self.assertEqual(len(urls),1)
        self.assertNotIn('Authorization',urls[0].headers)
        self.assertIn('Authorization',http.req.headers)

    def test_disallow_untrusted_artifact_redirect(self):
        exc=HTTPError('https://api.github.com',302,'Found',{'Location':'https://attacker.example/x'},None)
        class FakedHTTP:
            def open(self,req,timeout=0): raise exc
        with patch.object(v,'build_opener',return_value=FakedHTTP()):
            with self.assertRaisesRegex(v.SharedIntelligenceError,'ARTIFACT_HOST_NOT_ALLOWED'):
                v.artifact_json('a/b','token',2)

    def test_no_broker_paths_in_new_source(self):
        from pathlib import Path
        source=Path(v.__file__).read_text()
        self.assertNotIn('POST',source.split('def collaborate')[1].split('def run')[0])
        self.assertNotIn('paper-api.alpaca.markets',source)
        self.assertNotIn('api.robinhood.com',source)


if __name__ == '__main__':
    unittest.main()
