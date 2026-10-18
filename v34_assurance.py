"""V3.4 read-only SEC source / Alpaca quote / performance assurance.

Never submits, modifies, or cancels orders. Does not authorize paper execution.
Official filing retrieval verifies provenance, NOT the truth of a news claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, InvalidOperation, ROUND_UP
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

import ops_supervisor as ops

SEC_API = 'https://data.sec.gov'
SEC_WEB = 'https://www.sec.gov'
ALPACA_PAPER = 'https://paper-api.alpaca.markets'
ALPACA_DATA = 'https://data.alpaca.markets'
ALLOWED = frozenset(('NXE', 'IONQ', 'OKLO', 'ASTS', 'VRT', 'RKLB',
                     'CRSP', 'POET', 'SERV', 'SLDP', 'IGC', 'TLRY'))
FORMS = frozenset(('8-K', '10-Q', '10-K', '6-K', '20-F', 'S-1', 'S-3', '424B5'))
MAX_BYTES = 2_000_000
MAX_SYMBOLS = 3
MAX_FILINGS_PER_SYMBOL = 2
SEC_LOOKBACK_DAYS = 14
QUOTE_AGE_SECONDS = 90
MAX_SPREAD_BPS = Decimal('50')       # maximum 0.50% of midpoint
MAX_ENTRY_USD = Decimal('25.00')
LIMIT_CUSHION = Decimal('1.002')
MAX_V3_REPORT_HOURS = 96


class AssuranceError(RuntimeError):
    """Non-sensitive error identifier only."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_error(error):
    if isinstance(error, HTTPError):
        return 'HTTP_' + str(error.code)
    if isinstance(error, (URLError, TimeoutError)):
        return 'NETWORK_OR_TIMEOUT'
    if isinstance(error, AssuranceError):
        return str(error)
    return type(error).__name__


def checked_decimal(x):
    if isinstance(x, bool):
        raise AssuranceError('INVALID_DECIMAL')
    try:
        n = Decimal(str(x))
    except (ValueError, TypeError, InvalidOperation):
        raise AssuranceError('INVALID_DECIMAL') from None
    if not n.is_finite():
        raise AssuranceError('INVALID_DECIMAL')
    return n


def checked_dt(value):
    if not isinstance(value, str):
        raise AssuranceError('INVALID_TIME')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise AssuranceError('INVALID_TIME') from None
    if dt.tzinfo is None:
        raise AssuranceError('NAIVE_TIME')
    return dt.astimezone(timezone.utc)


def http_bytes(url, headers, limit=MAX_BYTES):
    """GET-only, no redirects or credential forwarding; caller supplies fixed host URLs."""
    if not (url.startswith(SEC_WEB + '/') or url.startswith(SEC_API + '/') or
            url.startswith(ALPACA_PAPER + '/') or url.startswith(ALPACA_DATA + '/')):
        raise AssuranceError('HOST_NOT_ALLOWED')
    req = Request(url, headers={'Accept': 'application/json',
                                'User-Agent': 'InvestmentAssurance/3.4', **headers}, method='GET')
    with build_opener(NoRedirect()).open(req, timeout=16) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise AssuranceError('RESPONSE_TOO_LARGE')
    return data


def http_json(url, headers):
    try:
        obj = json.loads(http_bytes(url, headers))
    except (ValueError, UnicodeError):
        raise AssuranceError('NON_JSON_RESPONSE') from None
    if not isinstance(obj, dict):
        raise AssuranceError('BAD_JSON_SHAPE')
    return obj


def selected_symbols(report):
    """V3.1 must supply the shortlist; never broaden to random tickers."""
    selection = report.get('v31_candidate_selection') or {}
    stated = selection.get('priced_candidates')
    if not isinstance(stated, list):
        raise AssuranceError('NO_V31_CANDIDATE_LIST')
    out = []
    for sym in stated:
        if not isinstance(sym, str) or sym not in ALLOWED:
            continue
        if sym not in out:
            out.append(sym)
    if len(out) > MAX_SYMBOLS:
        raise AssuranceError('TOO_MANY_CANDIDATES')
    return out


def sec_metadata(symbol, cik_dir, submission, now):
    """Verify EDGAR metadata, filing accession, date and issuer ticker identity.

    Does NOT independently validate any investment claim.
    """
    cik = cik_dir.get(symbol)
    if not isinstance(cik, str) or not re.fullmatch(r'\d{10}', cik):
        return [], 'CIK_NOT_FOUND'
    if not isinstance(submission, dict):
        return [], 'BAD_SEC_SUBMISSION'
    issuer_tickers = submission.get('tickers') or []
    if symbol not in issuer_tickers:
        return [], 'SUBMISSION_TICKER_MISMATCH'
    recent = (submission.get('filings') or {}).get('recent') or {}
    if not isinstance(recent, dict):
        return [], 'BAD_FILING_INDEX'
    output = []
    forms, dates, accessions, docs = (recent.get(k) or [] for k in
                                      ('form', 'filingDate', 'accessionNumber', 'primaryDocument'))
    for form, filed, accession, doc in list(zip(forms, dates, accessions, docs))[:80]:
        if form not in FORMS or not isinstance(filed, str) or not isinstance(doc, str):
            continue
        try:
            day = date.fromisoformat(filed)
        except ValueError:
            continue
        if not (0 <= (now.date() - day).days <= SEC_LOOKBACK_DAYS):
            continue
        if not (isinstance(accession, str) and re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession)):
            continue
        # Reject paths, query parameters, traversal and unexpected filenames.
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,110}', doc) or '..' in doc:
            continue
        filing_url = (f'{SEC_WEB}/Archives/edgar/data/{int(cik)}/'
                      f'{accession.replace("-", "")}/{quote(doc, safe="")}')
        output.append({'symbol': symbol, 'form': form, 'filing_date': filed,
                       'accession': accession, 'official_url': filing_url})
        if len(output) >= MAX_FILINGS_PER_SYMBOL:
            break
    return output, 'METADATA_MATCHED' if output else 'NO_RECENT_MATERIAL_FORMS'


def verify_sec_sources(symbols, now):
    user_agent = os.environ.get('SEC_USER_AGENT', '').strip()
    if not user_agent or ' ' not in user_agent:
        return {'status': 'NOT_CONFIGURED', 'by_symbol': {},
                'claim_verification': 'NOT_PERFORMED'}
    headers = {'User-Agent': user_agent, 'Accept': 'application/json'}
    output = {}
    try:
        ticker_index = http_json(SEC_WEB + '/files/company_tickers.json', headers)
        directory = {}
        for item in ticker_index.values():
            if isinstance(item, dict) and item.get('ticker') in symbols:
                directory[item['ticker']] = str(item.get('cik_str', '')).zfill(10)
    except (HTTPError, URLError, AssuranceError, TimeoutError) as exc:
        return {'status': 'SOURCE_UNAVAILABLE', 'reason': safe_error(exc),
                'by_symbol': {}, 'claim_verification': 'NOT_PERFORMED'}
    for symbol in symbols:
        cik = directory.get(symbol)
        if not cik:
            output[symbol] = {'status': 'CIK_NOT_FOUND', 'filings': []}
            continue
        try:
            time.sleep(.13)  # conservative SEC API pacing
            index = http_json(f'{SEC_API}/submissions/CIK{cik}.json', headers)
            filings, status = sec_metadata(symbol, directory, index, now)
            verified = []
            for filing in filings:
                try:
                    time.sleep(.13)
                    data = http_bytes(filing['official_url'], {'User-Agent': user_agent,
                                                               'Accept': 'text/html,application/xml,application/pdf'})
                    if len(data) < 64:
                        raise AssuranceError('DOCUMENT_UNEXPECTEDLY_SMALL')
                    # Signature heuristic only; SEC HTTPS host and metadata are the provenance.
                    if not (data.startswith(b'%PDF-') or
                            b'<html' in data[:500].lower() or
                            b'<?xml' in data[:100].lower() or
                            b'<!doctype html' in data[:500].lower()):
                        raise AssuranceError('UNEXPECTED_DOCUMENT_FORMAT')
                    filing['document_retrieval'] = 'OFFICIAL_DOCUMENT_RETRIEVED'
                    filing['sha256'] = hashlib.sha256(data).hexdigest()
                    filing['bytes'] = len(data)
                except (HTTPError, URLError, AssuranceError, TimeoutError) as exc:
                    filing['document_retrieval'] = 'UNAVAILABLE'
                    filing['error_code'] = safe_error(exc)
                verified.append(filing)
            output[symbol] = {'status': status, 'filings': verified,
                              'verified_document_count': sum(f['document_retrieval'] ==
                                  'OFFICIAL_DOCUMENT_RETRIEVED' for f in verified)}
        except (HTTPError, URLError, AssuranceError, TimeoutError) as exc:
            output[symbol] = {'status': 'SEC_UNAVAILABLE', 'reason': safe_error(exc), 'filings': []}
    docs = sum(x.get('verified_document_count', 0) for x in output.values())
    return {'status': 'SEC_DOCUMENTS_RETRIEVED' if docs else 'NO_DOCUMENTS_RETRIEVED',
            'by_symbol': output, 'claim_verification': 'NOT_PERFORMED',
            'note': 'Authenticates SEC filing metadata/document retrieval only. Does not verify news claims or determine investment merit.'}


def alpaca_headers():
    key = os.environ.get('ALPACA_PAPER_API_KEY', '').strip()
    secret = os.environ.get('ALPACA_PAPER_API_SECRET', '').strip()
    if not key or not secret:
        raise AssuranceError('PAPER_KEYS_MISSING')
    return {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}


def limit_from_ask(ask):
    tick = Decimal('.01') if ask >= 1 else Decimal('.0001')
    return (ask * LIMIT_CUSHION).quantize(tick, rounding=ROUND_UP)


def assess_quote(symbol, payload, asset, now):
    """Assess read-only IEX quote, never create an order or imply full SIP NBBO."""
    reason = []
    if payload.get('symbol') != symbol or not isinstance(payload.get('quote'), dict):
        return {'status': 'BLOCKED', 'reasons': ['QUOTE_SYMBOL_OR_SHAPE_MISMATCH']}
    if asset.get('symbol') != symbol or asset.get('status') != 'active' or not asset.get('tradable'):
        reason.append('ASSET_NOT_ACTIVE_OR_TRADABLE')
    q = payload['quote']
    try:
        bid = checked_decimal(q['bp'])
        ask = checked_decimal(q['ap'])
        bs, az = checked_decimal(q['bs']), checked_decimal(q['as'])
        timestamp = checked_dt(q['t'])
        seconds = (now - timestamp).total_seconds()
        if seconds < 0 or seconds > QUOTE_AGE_SECONDS:
            reason.append('QUOTE_STALE_OR_FUTURE')
        if min(bid, ask, bs, az) <= 0 or ask < bid:
            reason.append('INVALID_OR_CROSSED_QUOTE')
        spread_bps = None
        target = None
        if not reason or not any(x == 'INVALID_OR_CROSSED_QUOTE' for x in reason):
            mid = (ask + bid) / 2
            spread_bps = (ask - bid) / mid * 10000
            if spread_bps > MAX_SPREAD_BPS:
                reason.append('SPREAD_EXCEEDS_50_BPS')
            target = limit_from_ask(ask)
            if target > MAX_ENTRY_USD:
                reason.append('ONE_SHARE_EXCEEDS_25_USD_CAP')
    except (KeyError, TypeError, AssuranceError, ArithmeticError):
        return {'status': 'BLOCKED', 'reasons': ['MALFORMED_QUOTE']}
    return {'status': 'QUOTE_VALIDATED_READ_ONLY' if not reason else 'BLOCKED',
            'feed': 'iex', 'reasons': sorted(set(reason)),
            'quote_age_seconds': int(seconds), 'bid': str(bid), 'ask': str(ask),
            'spread_bps': str(spread_bps.quantize(Decimal('.1'))) if spread_bps is not None else None,
            'illustrative_max_limit_price': str(target) if target is not None and not reason else None,
            'note': 'IEX quote only; market coverage is limited. This is not an order approval or price guarantee.'}


def verify_quotes(symbols, now):
    try:
        headers = alpaca_headers()
    except AssuranceError as exc:
        return {'status': 'NOT_CONFIGURED', 'reason': safe_error(exc), 'by_symbol': {}}
    try:
        clock = http_json(ALPACA_PAPER + '/v2/clock', headers)
        server_now = checked_dt(clock['timestamp'])
        if abs((now - server_now).total_seconds()) > 180:
            return {'status': 'UNTRUSTED_BROKER_CLOCK', 'by_symbol': {}}
        if clock.get('is_open') is not True:
            return {'status': 'MARKET_CLOSED', 'by_symbol': {},
                    'note': 'No executable quote qualification outside regular US equity hours.'}
    except (HTTPError, URLError, AssuranceError, KeyError, TimeoutError) as exc:
        return {'status': 'CLOCK_UNAVAILABLE', 'reason': safe_error(exc), 'by_symbol': {}}
    reports = {}
    for symbol in symbols:
        try:
            asset = http_json(f'{ALPACA_PAPER}/v2/assets/{quote(symbol)}', headers)
            payload = http_json(f'{ALPACA_DATA}/v2/stocks/{quote(symbol)}/quotes/latest?feed=iex', headers)
            reports[symbol] = assess_quote(symbol, payload, asset, now)
        except (HTTPError, URLError, AssuranceError, TimeoutError) as exc:
            reports[symbol] = {'status': 'BLOCKED', 'reasons': [safe_error(exc)]}
    return {'status': 'READ_ONLY_QUOTE_AUDIT', 'feed': 'iex', 'by_symbol': reports,
            'paper_orders_sent': 0}


def validate_performance(report):
    state = report.get('virtual_state') or {}
    perf = report.get('v31_performance') or {}
    if not isinstance(state, dict) or not isinstance(perf, dict):
        return {'status': 'INVALID_PERFORMANCE_REPORT', 'reasons': ['WRONG_SHAPE']}
    history = state.get('performance_history') or []
    if not isinstance(history, list):
        return {'status': 'INVALID_PERFORMANCE_REPORT', 'reasons': ['HISTORY_NOT_ARRAY']}
    dates, values = [], []
    try:
        for item in history:
            if not isinstance(item, dict):
                raise AssuranceError('INVALID_HISTORY_RECORD')
            d = date.fromisoformat(item['market_date'])
            n = checked_decimal(item['virtual_equity'])
            if n <= 0:
                raise AssuranceError('NONPOSITIVE_EQUITY')
            dates.append(d)
            values.append(n)
        if dates != sorted(set(dates)):
            raise AssuranceError('DUPLICATE_OR_UNORDERED_SESSIONS')
        if perf.get('observed_sessions') != len(dates):
            raise AssuranceError('SESSION_COUNT_MISMATCH')
        if checked_decimal(state.get('virtual_equity')) <= 0:
            raise AssuranceError('BAD_CURRENT_EQUITY')
        if history and values[-1] != checked_decimal(state['virtual_equity']):
            raise AssuranceError('LAST_EQUITY_MISMATCH')
    except (ValueError, KeyError, AssuranceError) as exc:
        return {'status': 'INVALID_PERFORMANCE_REPORT', 'reasons': [safe_error(exc)]}
    peak, dd = Decimal('500'), Decimal('0')
    for x in values:
        peak = max(peak, x)
        dd = max(dd, ((peak - x) / peak) * Decimal('100'))
    count = len(values)
    gap = None
    benchmark = report.get('benchmark') or {}
    if benchmark.get('status') == 'daily_price_comparison_only':
        try:
            gap = checked_decimal(state['virtual_equity']) - checked_decimal(benchmark['vti_value_of_500'])
        except (KeyError, AssuranceError):
            pass
    warnings = []
    if count < 20:
        warnings.append('FEWER_THAN_20_DISTINCT_SESSIONS')
    if not state.get('trade_ledger'):
        warnings.append('NO_RECORDED_VIRTUAL_FILLS')
    if gap is None:
        warnings.append('BENCHMARK_NOT_VERIFIED')
    return {'status': 'OBSERVATIONS_IN_PROGRESS' if not warnings else 'INSUFFICIENT_FOR_VALIDATION',
            'observed_distinct_market_sessions': count,
            'first_market_day': str(dates[0]) if dates else None,
            'last_market_day': str(dates[-1]) if dates else None,
            'max_observed_drawdown_percent': str(dd.quantize(Decimal('.01'))),
            'virtual_minus_vti_dollars': str(gap.quantize(Decimal('.01'))) if gap is not None else None,
            'warnings': warnings,
            'limitations': 'Not a backtest, independent out-of-sample proof, total-return benchmark, or demonstration of future profitability.'}


def candidate_gate_summary(symbols, report, source, quotes, performance):
    """Merge independent checks as explanations, never broker authorizations."""
    decisions = report.get('v31_decisions') or {}
    sources = source.get('by_symbol') or {}
    quote_records = quotes.get('by_symbol') or {}
    result = {}
    for symbol in symbols:
        decision = decisions.get(symbol) or {}
        reasons = list(decision.get('reasons') or [])
        blockers = []
        if decision.get('verdict') != 'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED' or reasons:
            blockers.append('V31_ENTRY_NOT_ELIGIBLE')
        src = sources.get(symbol) or {}
        if not src.get('verified_document_count'):
            blockers.append('NO_OFFICIAL_SEC_DOCUMENT_RETRIEVED')
        # Even retrieved original documents do NOT corroborate individual claims.
        blockers.append('NEWS_AND_FILING_CLAIMS_NOT_INDEPENDENTLY_VERIFIED')
        quote_record = quote_records.get(symbol) or {}
        if quote_record.get('status') != 'QUOTE_VALIDATED_READ_ONLY':
            blockers.append('FRESH_IEX_QUOTE_NOT_VALIDATED')
        if performance.get('status') != 'OBSERVATIONS_IN_PROGRESS':
            blockers.append('INSUFFICIENT_OR_INVALID_STRATEGY_OBSERVATIONS')
        # Read-only evidence and IEX quotes can never turn on paper-order execution.
        blockers.append('PAPER_EXECUTION_NOT_AUTHORIZED')
        result[symbol] = {'status': 'BLOCKED_FOR_BROKER_ORDERS',
                          'v31_verdict': decision.get('verdict', 'UNKNOWN'),
                          'v31_reasons': reasons,
                          'sec_document_count': src.get('verified_document_count', 0),
                          'quote_status': quote_record.get('status','NOT_AVAILABLE'),
                          'blocking_reasons': sorted(set(blockers))}
    return result


def retrieve_latest_v3(repo, token, now):
    """Most recent completed V3 run only; a newer failure invalidates stale successes."""
    response = ops.github_json('/repos/' + quote(repo, safe='/') +
                               '/actions/workflows/investment-v3.yml/runs?per_page=15', token)
    runs = response.get('workflow_runs') or []
    if not runs:
        raise AssuranceError('NO_V3_RUNS')
    latest = max(runs, key=lambda r: r.get('created_at', ''))
    if latest.get('status') != 'completed' or latest.get('conclusion') != 'success':
        raise AssuranceError('LATEST_V3_NOT_SUCCESSFUL')
    created = checked_dt(latest['created_at'])
    hours = (now - created).total_seconds() / 3600
    if not 0 <= hours <= MAX_V3_REPORT_HOURS:
        raise AssuranceError('V3_REPORT_TOO_OLD')
    run_id = ops.safe_int(latest['id'])
    meta = ops.github_json('/repos/' + quote(repo, safe='/') +
                           f'/actions/runs/{run_id}/artifacts?per_page=60', token)
    matches = [x for x in meta.get('artifacts', [])
               if x.get('name') == f'investment-v3-{run_id}' and not x.get('expired')]
    if len(matches) != 1:
        raise AssuranceError('V3_ARTIFACT_MISSING_OR_DUPLICATE')
    report = ops.download_artifact(repo, matches[0]['id'], token)
    if report.get('environment') != 'PAPER_READ_ONLY_AND_VIRTUAL_ONLY' or \
       report.get('real_broker_orders_submitted') != 0:
        raise AssuranceError('V3_REPORT_NOT_READ_ONLY')
    if abs((checked_dt(report['as_of_utc']) - created).total_seconds()) > 3600:
        raise AssuranceError('V3_REPORT_RUN_TIMESTAMP_MISMATCH')
    return report, run_id


def main():
    now = datetime.now(timezone.utc)
    audit = {'version': '3.4', 'as_of_utc': now.isoformat(),
             'status': 'BLOCKED', 'execution_enabled': False,
             'broker_orders_submitted': 0,
             'note': 'Source provenance, quote and performance diagnostics only. Not broker order authorization.'}
    try:
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        token = os.environ.get('GITHUB_TOKEN', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not token:
            raise AssuranceError('GITHUB_ENV_MISSING')
        report, run_id = retrieve_latest_v3(repo, token, now)
        symbols = selected_symbols(report)
        audit['source_v3_run_id'] = run_id
        audit['candidate_symbols'] = symbols
        audit['source_provenance'] = verify_sec_sources(symbols, now)
        audit['quote_preflight'] = verify_quotes(symbols, now)
        audit['performance'] = validate_performance(report)
        audit['candidate_gates'] = candidate_gate_summary(
            symbols, report, audit['source_provenance'],
            audit['quote_preflight'], audit['performance'])
        audit['status'] = 'DIAGNOSTICS_COMPLETE_EXECUTION_LOCKED'
    except (AssuranceError, ops.SupervisorError, HTTPError, URLError,
            KeyError, ValueError, TypeError, TimeoutError) as exc:
        audit['errors'] = [safe_error(exc)]
    finally:
        with open('v34-assurance.json', 'w', encoding='utf-8') as f:
            json.dump(audit, f, indent=2, sort_keys=True)
    print('V3.4 assurance: ' + audit['status'])
    print('Alpaca paper brokerage orders sent: 0; live orders sent: 0')
    for name in ('source_provenance', 'quote_preflight', 'performance'):
        part = audit.get(name) or {}
        print(name + ': ' + part.get('status', 'NOT_COMPLETED'))
        for s, rec in (part.get('by_symbol') or {}).items():
            print(s + ': ' + rec.get('status', 'UNKNOWN'))
    for sym, gate in (audit.get('candidate_gates') or {}).items():
        print('Candidate ' + sym + ': ' + gate['status'] + '; ' + ','.join(gate['blocking_reasons']))
    if audit['status'] == 'BLOCKED':
        raise AssuranceError('ASSURANCE_PREFLIGHT_INCOMPLETE')


if __name__ == '__main__':
    try:
        main()
    except AssuranceError:
        sys.exit(1)
