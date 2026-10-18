"""V3: portfolio-aware analysis and NEXT-BAR virtual paper simulation.

Security: Only GETs Alpaca PAPER API. NO broker order POST, no live trading.
Preserves the existing intelligence.py and ai_team.py workflows.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import intelligence as base
import risk_policy

PAPER = 'https://paper-api.alpaca.markets'
MASSIVE = 'https://api.massive.com'
STATE_TITLE = 'V3 Virtual Paper State (automated, do not edit)'
CAPITAL = Decimal('500.00')
MAX_AGE_DAYS = 5  # permits weekends and US market holidays; NOT real-time execution data
WATCH = ('NXE','IONQ','OKLO','ASTS','VRT','RKLB','CRSP','POET','SERV','SLDP','VTI','BOTZ','GRID','IGC','TLRY')
MAX_REQUEST_BYTES = 2_500_000
SHARED_JOURNAL_TITLE = 'Shared AI Intelligence Journal (automated - private; do not edit)'


class MassiveDataError(ValueError):
    """A non-sensitive, actionable error label for rejected daily-bar data."""


def massive_response_hint(status, payload):
    """Derive categorical hints only; never echo provider text or secrets."""
    msg = str(payload.get('message') or payload.get('error') or '').lower()
    if any(word in msg for word in ('api key', 'api_key', 'authentication', 'invalid key')):
        return 'CHECK_API_KEY'
    if any(word in msg for word in ('plan', 'entitle', 'subscri', 'timeframe', 'historical access', 'agreement')):
        return 'CHECK_STOCKS_PLAN_AND_DATA_WINDOW'
    if 'rate' in msg and ('limit' in msg or 'exceed' in msg):
        return 'RATE_LIMIT'
    if status in ('NOT_AUTHORIZED', 'UNAUTHORIZED', 'FORBIDDEN'):
        return 'CHECK_STOCKS_PLAN_AND_DATA_WINDOW'
    return 'CHECK_PROVIDER_RESPONSE'


def safe_massive_status(payload):
    raw = payload.get('status')
    text = str(raw) if isinstance(raw, str) else 'MISSING'
    # A fixed vocabulary ensures untrusted response values cannot enter public logs.
    known = {'OK', 'NOT_AUTHORIZED', 'UNAUTHORIZED', 'FORBIDDEN',
             'ERROR', 'NOT_FOUND', 'DELAYED', 'RATE_LIMITED', 'NO_DATA'}
    return text if text in known else 'UNRECOGNIZED'


def market_error_label(exc):
    """Explain common failures without logging raw HTTP bodies or URLs."""
    if isinstance(exc, MassiveDataError):
        return str(exc)
    if isinstance(exc, HTTPError):
        hint = {401: 'INVALID_OR_INACTIVE_KEY', 403: 'PLAN_ACCESS_OR_DATA_WINDOW',
                429: 'RATE_LIMIT', 500: 'PROVIDER_ERROR', 502: 'PROVIDER_ERROR',
                503: 'PROVIDER_TEMPORARY_OUTAGE', 504: 'PROVIDER_TEMPORARY_OUTAGE'}
        return f'HTTP_{exc.code}_{hint.get(exc.code, "REQUEST_FAILED")}'
    if isinstance(exc, json.JSONDecodeError):
        return 'NON_JSON_API_RESPONSE'
    if isinstance(exc, (URLError, TimeoutError)):
        return 'NETWORK_OR_TIMEOUT'
    if isinstance(exc, ValueError):
        return 'BAD_API_RESPONSE_VALUE'
    return type(exc).__name__


def decimal(value):
    return risk_policy.amount(value)


def money(value):
    return str(decimal(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def is_true(name, default=False):
    return os.environ.get(name, 'true' if default else 'false').strip().lower() == 'true'


def get_json(url, headers=None, timeout=20):
    req = Request(url, headers={'User-Agent': 'InvestmentIntelligence-V3/1.0',
                                'Accept': 'application/json', **(headers or {})})
    with urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise ValueError('Unexpected HTTP status')
        data = resp.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise ValueError('Oversized API response')
        return json.loads(data)


def paper_snapshot():
    """Read-only Alpaca paper account; never reads live URL, never submits orders."""
    key = os.environ.get('ALPACA_PAPER_API_KEY', '').strip()
    secret = os.environ.get('ALPACA_PAPER_API_SECRET', '').strip()
    if not key or not secret:
        return {'status': 'not_configured', 'message': 'Add both ALPACA_PAPER_API_* GitHub secrets'}
    headers = {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}
    try:
        account = get_json(PAPER + '/v2/account', headers)
        positions = get_json(PAPER + '/v2/positions', headers)
        orders = get_json(PAPER + '/v2/orders?status=open&limit=100', headers)
        if not isinstance(account, dict) or not isinstance(positions, list) or not isinstance(orders, list):
            raise ValueError('Unexpected broker data shape')
        if account.get('status') != 'ACTIVE' or account.get('trading_blocked'):
            status = 'account_restricted'
        else:
            status = 'read_only'
        return {'status': status, 'environment': 'ALPACA_PAPER_ONLY',
                'equity': money(account['equity']),
                'cash': money(account['cash']),
                'buying_power': money(account['buying_power']),
                'positions': [
                    {'symbol': p.get('symbol'), 'qty': p.get('qty'),
                     'market_value': p.get('market_value'), 'unrealized_pl': p.get('unrealized_pl')}
                    for p in positions if p.get('asset_class') in ('us_equity','us_stock')
                ],
                'non_stock_position_count': sum(1 for p in positions if p.get('asset_class') not in ('us_equity','us_stock')),
                'open_orders': [{'symbol': o.get('symbol'), 'side': o.get('side'),
                                 'qty': o.get('qty'), 'status': o.get('status')}
                                for o in orders],
                'collected_at_utc': datetime.now(timezone.utc).isoformat(),
                'note': 'Alpaca paper only; no Robinhood connection; account may contain other strategies.'}
    except (HTTPError, URLError, ValueError, KeyError, TimeoutError) as exc:
        return {'status': 'unavailable', 'reason': 'Read-only Alpaca paper request failed: '
                + (f'HTTP {exc.code}' if isinstance(exc, HTTPError) else type(exc).__name__)}


def bars(symbol, today, api_key):
    if symbol not in WATCH:
        raise ValueError('Symbol not in fixed allowlist')
    start = (today - timedelta(days=65)).isoformat()
    url = (f'{MASSIVE}/v2/aggs/ticker/{quote(symbol)}/range/1/day/{start}/{today.isoformat()}?'
           + urlencode({'adjusted': 'true', 'sort': 'asc', 'limit': '80'}))
    try:
        data = get_json(url, {'Authorization': 'Bearer ' + api_key})
    except json.JSONDecodeError as exc:
        raise MassiveDataError('NON_JSON_API_RESPONSE') from exc
    if not isinstance(data, dict):
        raise MassiveDataError('NON_OBJECT_API_RESPONSE')
    status = safe_massive_status(data)
    # Massive may return end-of-day historical bars with status=DELAYED.
    # Accept the *data*, not the status, after validating every bar below.
    # All other non-OK statuses still fail closed.
    if status not in ('OK', 'DELAYED'):
        hint = massive_response_hint(status, data)
        raise MassiveDataError(f'MASSIVE_STATUS_{status}_{hint}')
    # Refuse data for a different symbol, even if the provider marks it OK/DELAYED.
    ticker = data.get('ticker')
    if ticker is not None and ticker != symbol:
        raise MassiveDataError('RESPONSE_TICKER_MISMATCH')
    found = data.get('results')
    if status == 'DELAYED' and (not isinstance(found, list) or not found):
        # "DELAYED" by itself is not proof that price data is available.
        raise MassiveDataError('MASSIVE_DELAYED_WITHOUT_BARS')
    if found is None and status == 'OK':
        return []
    if not isinstance(found, list):
        raise MassiveDataError('INVALID_RESULTS_ARRAY')
    if not found:
        return []
    out = []
    for i, entry in enumerate(found):
        if not isinstance(entry, dict):
            raise MassiveDataError('INVALID_BAR_OBJECT')
        ts = entry.get('t')
        if isinstance(ts, bool) or not isinstance(ts, int):
            raise MassiveDataError('INVALID_BAR_TIMESTAMP')
        try:
            day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
            price = decimal(entry.get('c'))
            opening = decimal(entry.get('o'))
        except (ValueError, OverflowError, OSError) as exc:
            raise MassiveDataError('INVALID_BAR_OPEN_OR_CLOSE') from exc
        if opening <= 0 or price <= 0:
            raise MassiveDataError('NONPOSITIVE_BAR_PRICE')
        # Reject bars outside the interval we actually requested. A status of
        # DELAYED never overrides the temporal or price-integrity checks.
        if day < start or day > today.isoformat():
            raise MassiveDataError('BAR_OUTSIDE_REQUESTED_DATE_RANGE')
        out.append({'date': day, 'close': str(price), 'open': str(opening)})
    dates = [x['date'] for x in out]
    if len(set(dates)) != len(dates):
        raise MassiveDataError('DUPLICATE_MARKET_DATES')
    out = sorted(out, key=lambda b: b['date'])
    if status == 'DELAYED':
        # Logs never include the provider's raw body or API token.
        print(f'Massive daily bars {symbol}: DELAYED_HISTORY_VALIDATED '
              f'({len(out)} bars; latest {out[-1]["date"]})')
    return out


def technical(series, as_of):
    if len(series) < 20:
        return {'status': 'INSUFFICIENT_DATA'}
    age = (as_of - datetime.fromisoformat(series[-1]['date']).date()).days
    if age < 0 or age > MAX_AGE_DAYS:
        return {'status': 'STALE', 'age_days': age}
    closes = [decimal(x['close']) for x in series]
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    last = closes[-1]
    return {'status': 'READY', 'bar_date': series[-1]['date'],
            'last_close': money(last), 'ma5': money(ma5), 'ma20': money(ma20),
            'bullish': bool(ma5 > ma20 and last > ma20),
            'bearish': bool(ma5 < ma20)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ai_audit(repo, token, now):
    """Reuse recent AI Team Actions artifact; never sends GitHub token to redirects."""
    try:
        head = {'Authorization': 'Bearer ' + token,
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28'}
        name = quote(repo, safe='/')
        q = '/actions/workflows/ai-team.yml/runs?status=success&per_page=4'
        runs = get_json('https://api.github.com/repos/' + name + q, head)
        for run in runs.get('workflow_runs', []):
            if run.get('conclusion') != 'success':
                continue
            when = datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
            if now - when > timedelta(hours=48) or when > now + timedelta(minutes=5):
                continue
            artifacts = get_json('https://api.github.com/repos/' + name
                                 + f'/actions/runs/{int(run["id"])}/artifacts?per_page=30', head)
            match = next((a for a in artifacts.get('artifacts', [])
                          if a.get('name', '').startswith('ai-team-review-') and not a.get('expired')), None)
            if not match:
                continue
            url = 'https://api.github.com/repos/' + name + f'/actions/artifacts/{int(match["id"])}/zip'
            try:
                build_opener(NoRedirect()).open(Request(url, headers=head), timeout=20)
            except HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308):
                    raise
                redirect = exc.headers.get('Location', '')
            else:
                return {'status': 'unavailable', 'reason': 'Unexpected artifact response'}
            parsed = urlparse(redirect)
            if parsed.scheme != 'https' or not (parsed.hostname or '').endswith(
                ('.actions.githubusercontent.com', '.blob.core.windows.net')):
                return {'status': 'unavailable', 'reason': 'Untrusted artifact redirect'}
            # Intentionally no authorization headers to cloud artifact URLs.
            with urlopen(Request(redirect), timeout=20) as response:
                blob = response.read(MAX_REQUEST_BYTES + 1)
            if len(blob) > MAX_REQUEST_BYTES:
                raise ValueError('Oversized artifact')
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                if 'ai-team-report.json' not in archive.namelist():
                    continue
                audit = json.loads(archive.read('ai-team-report.json'))
            if not isinstance(audit, dict) or not isinstance(audit.get('reports'), list):
                continue
            return {'status': 'recent', 'as_of': audit.get('as_of'),
                    'by_symbol': {r['symbol']: {'consensus': r.get('consensus'),
                                              'reviewer_count': len(r.get('reviews') or {}),
                                              'reviewer_classifications': {name: {
                                                  'classification': v.get('classification'),
                                                  'self_rated_confidence': v.get('confidence')}
                                                  for name,v in (r.get('reviews') or {}).items()
                                                  if isinstance(v, dict)}}
                                  for r in audit['reports'] if r.get('symbol') in WATCH}}
        return {'status': 'unavailable', 'reason': 'No recent successful AI Team report artifact'}
    except (HTTPError, URLError, ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        return {'status': 'unavailable', 'reason': 'AI audit retrieval: '
                + (f'HTTP {exc.code}' if isinstance(exc, HTTPError) else type(exc).__name__)}



def shared_research_context(repo, now):
    """Read verified-format V3.5 peer reviews as context, never as a trade signal.

    This read intentionally does not change ai_audit(), safe_ai_for_virtual_buy(),
    technical signals or risk_policy. If journal access fails, don't fabricate context.
    """
    try:
        rows = base.github(f'/repos/{repo}/issues?state=all&per_page=100')
        matches = [x for x in rows if isinstance(x, dict) and
                   'pull_request' not in x and x.get('title') == SHARED_JOURNAL_TITLE]
        if len(matches) != 1:
            return {'status': 'UNAVAILABLE', 'reason': 'JOURNAL_MISSING_OR_AMBIGUOUS',
                    'used_for_trade_approval': False, 'by_symbol': {}}
        body = json.loads(matches[0].get('body') or '')
        if not isinstance(body, dict) or body.get('schema') != 1 or not isinstance(body.get('entries'), list):
            raise ValueError('Invalid journal schema')
        by_symbol = {}
        for entry in body['entries'][-60:]:
            if not isinstance(entry, dict):
                continue
            symbol = entry.get('symbol')
            if symbol not in WATCH or symbol == 'VTI':
                continue
            if entry.get('investment_action') != 'RESEARCH_ONLY':
                continue
            try:
                t = datetime.fromisoformat(str(entry.get('as_of', '')).replace('Z', '+00:00'))
                if t.tzinfo is None:
                    continue
                t = t.astimezone(timezone.utc)
                age = (now - t).total_seconds()
            except (ValueError, TypeError, OverflowError):
                continue
            if not 0 <= age <= 14 * 86400:
                continue
            agreement = entry.get('agreement')
            if agreement not in ('AGREE_REVIEW_ONLY', 'DISAGREEMENT_OR_UNCERTAIN'):
                continue
            existing = by_symbol.get(symbol)
            if existing is None or t.isoformat() > existing['as_of']:
                by_symbol[symbol] = {
                    'as_of': t.isoformat(), 'agreement': agreement,
                    'evidence_status': 'UNVERIFIED_MODEL_OPINIONS',
                    'eligible_to_authorize_trades': False,
                    'summary': str(entry.get('shared_summary') or '')[:360],
                }
        return {'status': 'RECENT' if by_symbol else 'EMPTY_OR_STALE',
                'used_for_trade_approval': False, 'by_symbol': by_symbol}
    except (ValueError, TypeError, KeyError, HTTPError, URLError, OSError, OverflowError):
        return {'status': 'UNAVAILABLE', 'reason': 'JOURNAL_RETRIEVAL_OR_VALIDATION_FAILED',
                'used_for_trade_approval': False, 'by_symbol': {}}


def safe_ai_for_virtual_buy(audit, symbol):
    if audit.get('status') != 'recent':
        return False, 'No recent two-model AI audit'
    report = audit.get('by_symbol', {}).get(symbol) or {}
    consensus = report.get('consensus') or {}
    if report.get('reviewer_count', 0) < 2:
        return False, 'Fewer than two model reviews'
    if consensus.get('status') != 'REVIEW_REQUIRED':
        return False, 'No qualifying AI agreement'
    if consensus.get('classification') not in {'opportunity', 'watch'}:
        return False, 'AI consensus is not favorable/neutral'
    return True, 'AI opinions checked; source claims still unverified'


def prioritize_research_candidates(audit, state):
    """Use AI Team's ACTUAL recent research symbols, never independent rotation.

    This is candidate alignment, NOT a buy signal. Rank eligible consensus first
    to spend limited market-data requests efficiently, then uncertain/risk cases.
    """
    if audit.get('status') == 'recent' and isinstance(audit.get('by_symbol'), dict):
        reports = audit['by_symbol']
        symbols = [s for s in reports if s in WATCH and s not in ('VTI', 'BOTZ', 'GRID')]
        if symbols:
            def priority(sym):
                r = reports[sym] if isinstance(reports[sym], dict) else {}
                c = r.get('consensus') or {}
                eligible = (r.get('reviewer_count', 0) >= 2 and
                            c.get('status') == 'REVIEW_REQUIRED' and
                            c.get('classification') in ('opportunity', 'watch'))
                return (0 if eligible else 1, sym)
            return sorted(symbols, key=priority)[:3], 'RECENT_AI_AUDIT'
    rotation = int(state.get('rotation', 0)) % len(WATCH)
    return [WATCH[(rotation+i) % len(WATCH)] for i in range(3)], 'WATCHLIST_FALLBACK_NO_AI_CANDIDATES'


def review_quality(audit, symbol):
    """AI agreement is metadata, not independent verification of any claim."""
    if audit.get('status') != 'recent':
        return {'status': 'AUDIT_UNAVAILABLE', 'reviewer_count': 0,
                'evidence_verification': 'NOT_VERIFIED'}
    r = (audit.get('by_symbol') or {}).get(symbol) or {}
    c = r.get('consensus') or {}
    reviewers = r.get('reviewer_count', 0)
    if reviewers < 2:
        grade = 'INSUFFICIENT_REVIEWERS'
    elif c.get('status') == 'REVIEW_REQUIRED':
        grade = 'MODEL_AGREEMENT_REVIEW_ONLY'
    elif c.get('status') == 'NO_CONSENSUS':
        grade = 'NO_CONSENSUS'
    else:
        grade = 'NO_QUALIFYING_AGREEMENT'
    return {'status': grade, 'reviewer_count': reviewers,
            'classification': c.get('classification', 'unknown'),
            'evidence_level': c.get('evidence_level', 'not_reported'),
            'evidence_verification': 'NOT_VERIFIED',
            'reviewer_classifications': r.get('reviewer_classifications', {})}


def explain_virtual_decisions(market, audit, state, simulation_enabled, events=None, now=None):
    """Deterministic explanations only; does NOT enqueue or submit orders."""
    if not isinstance(market, dict):
        return {}
    recorded = {(e.get('symbol'), e.get('action')) for e in (events or []) if isinstance(e, dict)}
    decisions = {}
    current = equity(state, {s: t for s,t in market.items() if t.get('status') == 'READY'})
    for sym,t in market.items():
        if sym in ('VTI', 'BOTZ', 'GRID'):
            continue
        quality = review_quality(audit, sym)
        reasons = []
        verdict = 'WATCH'
        if sym in state.get('positions', {}):
            verdict = 'HOLD_POSITION'
            if t.get('status') != 'READY':
                reasons.append('HELD_POSITION_PRICE_UNAVAILABLE')
            elif t.get('bearish'):
                reasons.append('HELD_POSITION_BEARISH_REVIEW_EXIT_SIGNAL')
            else:
                reasons.append('HELD_POSITION_MONITOR')
        elif state.get('pending') and state['pending'].get('symbol') == sym:
            verdict = 'PENDING_VIRTUAL_ORDER'
            reasons.append('WAIT_FOR_LATER_MARKET_BAR')
        else:
            if t.get('status') != 'READY':
                reasons.append('PRICE_HISTORY_UNAVAILABLE')
            else:
                if not t.get('bullish', False):
                    reasons.append('NOT_BULLISH_5_20_DAY_TREND')
                try:
                    estimated = decimal(t['last_close']) * Decimal('1.005')
                    if estimated > CAPITAL * risk_policy.MAX_NEW_POSITION_FRACTION:
                        reasons.append('WHOLE_SHARE_EXCEEDS_5_PERCENT_LIMIT')
                    if current is None:
                        reasons.append('HELD_POSITION_PRICE_UNAVAILABLE')
                    else:
                        account = {'equity': str(current), 'day_start_equity': state['day_start_equity'],
                                   'peak_equity': state['peak_equity'], 'available_cash': state['cash'],
                                   'managed_allocation': '500', 'current_position_value': '0',
                                   'freshness_verified': True, 'margin_used': 0, 'options_enabled': False}
                        order = {'symbol': sym, 'side': 'buy', 'asset_class': 'stock',
                                 'qty': 1, 'order_type': 'limit', 'limit_price': str(estimated)}
                        check = risk_policy.evaluate_shadow_order(order, account,
                                                                   trading_enabled=False, emergency_stop=False)
                        if check['status'] != 'SHADOW_ELIGIBLE':
                            reasons.append('RISK_POLICY_BLOCK: ' + '; '.join(check['reasons']))
                except (ValueError, KeyError):
                    reasons.append('INVALID_PRICE_OR_RISK_INPUT')
            okay, _ = safe_ai_for_virtual_buy(audit, sym)
            if not okay:
                reasons.append('AI_' + quality['status'])
            elif quality.get('classification') not in ('opportunity','watch'):
                reasons.append('AI_RISK_OR_UNCERTAIN_CLASSIFICATION')
            if len(state.get('positions', {})) >= 5:
                reasons.append('MAX_FIVE_VIRTUAL_POSITIONS')
            if state.get('pending'):
                reasons.append('ANOTHER_VIRTUAL_ORDER_PENDING')
            if now is not None and state.get('last_trade_day') == now.date().isoformat():
                reasons.append('ONE_VIRTUAL_TRADE_PER_DAY')
            if not simulation_enabled:
                reasons.append('SIMULATION_DISABLED')
            if not reasons:
                verdict = 'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED'
            else:
                verdict = 'REJECT_ENTRY' if any('LIMIT' in x or 'RISK_POLICY_BLOCK' in x or
                                               'UNAVAILABLE' in x or 'INVALID' in x for x in reasons) else 'WATCH'
        if (sym, 'VIRTUAL_BUY_QUEUED') in recorded:
            verdict = 'VIRTUAL_BUY_QUEUED_NEXT_BAR'
            reasons = ['AI_AND_TECHNICAL_AND_RISK_GATES_PASSED']
        decisions[sym] = {'verdict': verdict, 'reasons': reasons,
                          'ai_research_quality': quality, 'market_bar_date': t.get('bar_date'),
                          'technical': ('BULLISH' if t.get('bullish') else 'NOT_BULLISH') if
                                       t.get('status') == 'READY' else t.get('status', 'UNAVAILABLE')}
    return decisions


def paper_warnings(broker):
    if broker.get('status') not in ('read_only','account_restricted'):
        return ['Alpaca paper account unavailable; no live or paper brokerage execution']
    warnings = []
    eq = decimal(broker['equity'])
    if eq <= 0:
        warnings.append('Paper account equity is not positive')
    else:
        for p in broker['positions']:
            try:
                pct = abs(decimal(p['market_value'])) / eq * 100
                if pct >= 20:
                    warnings.append(f"{p['symbol']}: {pct:.1f}% of total paper account equity")
            except (KeyError, ValueError, TypeError):
                warnings.append('A position has unparseable concentration data')
    if broker.get('non_stock_position_count',0):
        warnings.append('Non-stock positions exist; equity-only summary is incomplete')
    if len(broker.get('open_orders',[])) >= 100:
        warnings.append('At least 100 open orders; response may be truncated')
    return warnings


def empty_state():
    return {'schema': 1, 'cash': '500.00', 'positions': {}, 'pending': None,
            'peak_equity': '500.00', 'day_start_equity': '500.00',
            'last_mark_day': None, 'last_trade_day': None,
            'last_report_day': None, 'rotation': 0, 'history': [],
            'trade_ledger': [], 'performance_history': []}


def load_state(repo):
    issues = base.github(f'/repos/{repo}/issues?state=open&per_page=100')
    for item in issues:
        if 'pull_request' not in item and item.get('title') == STATE_TITLE:
            state = json.loads(item.get('body') or '{}')
            if not isinstance(state, dict) or state.get('schema') != 1:
                raise ValueError('Unknown V3 state schema; do not overwrite')
            validate_state(state)
            return item['number'], state
    return None, empty_state()


def validate_state(state):
    cash = decimal(state['cash'])
    if cash < 0 or cash > CAPITAL * 2:
        raise ValueError('Invalid simulator cash')
    for k in ('peak_equity', 'day_start_equity'):
        if decimal(state[k]) <= 0:
            raise ValueError('Invalid simulator equity baseline')
    pending = state.get('pending')
    if pending is not None:
        if not isinstance(pending, dict) or pending.get('symbol') not in WATCH or pending.get('side') not in ('buy','sell'):
            raise ValueError('Invalid simulator pending order')
        try:
            datetime.fromisoformat(pending['signal_date'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('Invalid pending order date') from exc
    for name in ('trade_ledger', 'performance_history'):
        if not isinstance(state.get(name, []), list):
            raise ValueError('Invalid optional V3.1 performance state')
    positions = state['positions']
    if not isinstance(positions, dict) or len(positions) > 10:
        raise ValueError('Invalid simulator positions')
    for sym, p in positions.items():
        if sym not in WATCH or decimal(p['qty']) <= 0 or decimal(p['qty']) % 1:
            raise ValueError('Invalid simulator position')
    return state


def save_state(repo, issue_no, state):
    payload = {'body': json.dumps(state, indent=2, sort_keys=True)}
    if issue_no is None:
        payload['title'] = STATE_TITLE
        base.github(f'/repos/{repo}/issues', method='POST', payload=payload)
    else:
        base.github(f'/repos/{repo}/issues/{issue_no}', method='PATCH', payload=payload)


def equity(state, market):
    cash = decimal(state['cash'])
    for sym, p in state['positions'].items():
        if sym not in market:
            return None
        cash += decimal(p['qty']) * decimal(market[sym]['last_close'])
    return cash


def virtual_step(state, market, audit, now):
    """Mutates supplied copy. Simulated next-bar OPEN fills, never sends real orders."""
    events = []
    today = now.date().isoformat()
    state = json.loads(json.dumps(state))
    validate_state(state)
    # Fail closed on unknown marks for current virtual positions.
    missing = [s for s in state['positions'] if s not in market or market[s].get('status') != 'READY']
    if missing:
        return state, [{'action': 'HOLD', 'reason': 'Missing price for held symbols', 'symbols': missing}]

    # Preserve the PREVIOUS market-day virtual equity for daily-loss checks.
    # Do not reset the daily baseline to a newly fallen market close.
    market_day = max((v['bar_date'] for v in market.values()), default=None)
    if market_day is not None and market_day != state.get('last_mark_day'):
        state['day_start_equity'] = state.get('virtual_equity','500.00')
        state['last_mark_day'] = market_day
    # Market quotes store last CLOSE; virtual fills only on a LATER dated bar's OPEN.
    pending = state.get('pending')
    if pending:
        sym = pending['symbol']
        m = market.get(sym)
        if m and m['status'] == 'READY' and m['bar_date'] > pending['signal_date']:
            open_price = decimal(m['next_open'])
            if pending['side'] == 'buy':
                fill = open_price * Decimal('1.002')
                cash = decimal(state['cash'])
                prior_equity = equity(state, market)
                day_start = decimal(state['day_start_equity'])
                peak = decimal(state['peak_equity'])
                order = {'symbol': sym, 'side':'buy', 'asset_class':'stock',
                         'qty':1,'order_type':'limit','limit_price':str(fill)}
                account = {'equity': str(prior_equity), 'day_start_equity': str(day_start),
                           'peak_equity':str(peak),'available_cash':str(cash),
                           'managed_allocation':'500', 'current_position_value':'0',
                           'freshness_verified':True, 'margin_used':0,'options_enabled':False}
                check = risk_policy.evaluate_shadow_order(order, account,
                                                          trading_enabled=False, emergency_stop=False)
                # A pending buy uses EXACTLY one share and must pass the 5% cap at fill.
                if check['status'] == 'SHADOW_ELIGIBLE' and sym not in state['positions']:
                    state['cash'] = money(cash - fill)
                    state['positions'][sym] = {'qty':'1', 'basis':money(fill), 'entry_date':m['bar_date']}
                    events.append({'action':'VIRTUAL_BUY_FILLED','symbol':sym,'price':money(fill),
                                   'date':m['bar_date']})
                    state['trade_ledger'] = (state.get('trade_ledger', []) + [
                        {'side': 'BUY', 'symbol': sym, 'bar_date': m['bar_date'], 'quantity': '1',
                         'fill_price': money(fill)}])[-100:]
                else:
                    events.append({'action':'VIRTUAL_BUY_BLOCKED','symbol':sym,
                                   'reason':check['reasons']})
            else:
                p = state['positions'].get(sym)
                if p:
                    fill = open_price * Decimal('.998')
                    state['cash'] = money(decimal(state['cash']) + decimal(p['qty']) * fill)
                    del state['positions'][sym]
                    realized = (fill - decimal(p['basis'])) * decimal(p['qty'])
                    state['trade_ledger'] = (state.get('trade_ledger', []) + [
                        {'side': 'SELL', 'symbol': sym, 'bar_date': m['bar_date'],
                         'quantity': p['qty'], 'fill_price': money(fill),
                         'realized_pnl': money(realized),
                         'basis_per_share': p['basis']}])[-100:]
                    events.append({'action':'VIRTUAL_SELL_FILLED','symbol':sym,'price':money(fill),
                                   'date':m['bar_date']})
            state['pending'] = None
            state['last_trade_day'] = m['bar_date']
        elif m and m['status'] == 'READY':
            events.append({'action':'PENDING_NEXT_BAR', 'symbol':sym, 'signal_date':pending['signal_date']})
        else:
            events.append({'action':'HOLD_PENDING','reason':'Pending symbol has no usable price'})

    current = equity(state, market)
    if current is None:
        return state, events
    state['peak_equity'] = money(max(decimal(state['peak_equity']), current))
    state['virtual_equity'] = money(current)

    if state['pending'] is not None:
        return state, events
    if state.get('last_trade_day') == today:
        return state, events
    # Plan one virtual sell first; fills the NEXT BAR, at a modeled spread/slippage.
    for sym, p in state['positions'].items():
        t = market[sym]
        entry = datetime.fromisoformat(p['entry_date']).date()
        age = (datetime.fromisoformat(t['bar_date']).date() - entry).days
        if t['bearish'] or decimal(t['last_close']) < decimal(p['basis']) * Decimal('.93') or age > 30:
            state['pending'] = {'symbol':sym, 'side':'sell','signal_date':t['bar_date']}
            events.append({'action':'VIRTUAL_SELL_QUEUED','symbol':sym,
                           'reason':'Downtrend, 7% price stop signal, or >30 calendar days'})
            return state, events
    # Virtual buy can be queued only with TWO documented model opinions + technical trend.
    if len(state['positions']) >= 5:
        return state, events
    for sym, t in market.items():
        if sym in state['positions'] or sym in {'VTI', 'BOTZ','GRID'} or not t['bullish']:
            continue
        ok, reason = safe_ai_for_virtual_buy(audit, sym)
        if not ok:
            continue
        price = decimal(t['last_close']) * Decimal('1.005')
        account = {'equity':str(current), 'day_start_equity':state['day_start_equity'],
                   'peak_equity':state['peak_equity'], 'available_cash':state['cash'],
                   'managed_allocation':'500','current_position_value':'0',
                   'freshness_verified':True,'margin_used':0,'options_enabled':False}
        proposed = {'symbol':sym,'side':'buy','asset_class':'stock','qty':1,
                    'order_type':'limit','limit_price':str(price)}
        check = risk_policy.evaluate_shadow_order(proposed, account,
                                                  trading_enabled=False, emergency_stop=False)
        if check['status'] == 'SHADOW_ELIGIBLE':
            state['pending'] = {'symbol':sym, 'side':'buy','signal_date':t['bar_date']}
            events.append({'action':'VIRTUAL_BUY_QUEUED','symbol':sym,'reason':reason + '; 5/20-day trend',
                           'estimated_notional':money(price), 'execution':'NEXT_BAR_OPEN_SIMULATION'})
            break
    return state, events


def market_data(now, state, api_key, audit=None):
    """Prioritize recent AI-audited tickers; always include holdings and VTI."""
    rotation = int(state.get('rotation', 0)) % len(WATCH)
    chosen, selection_mode = prioritize_research_candidates(audit or {}, state)
    print('V3 candidate selection: ' + selection_mode + '; candidates ' + ','.join(chosen))
    chosen += list(state['positions'])
    if state.get('pending'):
        chosen.append(state['pending']['symbol'])
    chosen.append('VTI')
    symbols = list(dict.fromkeys(chosen))[:8]
    results = {}
    errors = []
    for idx, sym in enumerate(symbols):
        if idx:
            # Massive's free personal plan may allow 5 requests/min.
            time.sleep(13)
        try:
            series = bars(sym, now.date(), api_key)
            t = technical(series, now.date())
            if t['status'] == 'READY':
                t['next_open'] = series[-1]['open']
            results[sym] = t
        except (HTTPError, URLError, ValueError, TimeoutError, KeyError) as exc:
            label = market_error_label(exc)
            errors.append(f'{sym}: {label}')
            print(f'Massive daily bars {sym}: {label}')
    if not any(r.get('status') == 'READY' for r in results.values()):
        # Retry the same symbol set after an outage; do not rotate past failed leads.
        return results, errors, rotation
    # Only advance the fallback watchlist rotation when that fallback was used.
    next_rotation = ((rotation + 3) % len(WATCH) if selection_mode.startswith('WATCHLIST_FALLBACK')
                     else rotation)
    return results, errors, next_rotation


def benchmark_mark(state, market):
    vti = market.get('VTI') or {}
    if vti.get('status') != 'READY':
        return {'status':'unavailable'}
    price = decimal(vti['last_close'])
    if price <= 0:
        return {'status':'unavailable'}
    if not state.get('vti_initial_close'):
        state['vti_initial_close'] = str(price)
        state['vti_initial_date'] = vti['bar_date']
    original = decimal(state['vti_initial_close'])
    if original <= 0:
        raise ValueError('Invalid VTI benchmark baseline')
    benchmark_value = CAPITAL * price / original
    sleeve = decimal(state.get('virtual_equity','500'))
    return {'status':'daily_price_comparison_only', 'since_date':state['vti_initial_date'],
            'vti_value_of_500':money(benchmark_value),
            'virtual_minus_vti_dollars':money(sleeve - benchmark_value),
            'note':'Daily adjusted bar closes; not a total-return or tax-adjusted benchmark'}


def performance_snapshot(state, market, benchmark, simulation_enabled):
    """Daily virtual equity observations, not backtested or real-trading results."""
    prices_ok = all(s in market and market[s].get('status') == 'READY'
                    for s in state.get('positions', {}))
    if not prices_ok:
        return {'status': 'UNAVAILABLE_HELD_PRICES', 'note': 'Missing marks; no performance observation'}
    bars_seen = [v.get('bar_date') for v in market.values() if v.get('status') == 'READY']
    if not bars_seen:
        return {'status': 'UNAVAILABLE_MARKET_DATA'}
    bar_day = max(bars_seen)
    if simulation_enabled:
        point = {'market_date': bar_day, 'virtual_equity': state.get('virtual_equity', '500.00'),
                 'benchmark_value': benchmark.get('vti_value_of_500') if benchmark.get('status')
                 == 'daily_price_comparison_only' else None}
        history = list(state.get('performance_history', []))
        # Re-running a Saturday/Sunday run over the same Friday price must NOT
        # inflate the number of independent trading sessions.
        if history and history[-1].get('market_date') == bar_day:
            history[-1] = point
        elif not history or history[-1].get('market_date', '') < bar_day:
            history.append(point)
        state['performance_history'] = history[-100:]
    history = state.get('performance_history', [])
    equity_now = decimal(state.get('virtual_equity', '500.00'))
    pnl = equity_now - CAPITAL
    peak = CAPITAL
    maximum_drawdown = Decimal('0')
    for entry in history:
        eq = decimal(entry['virtual_equity'])
        peak = max(peak, eq)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, (peak - eq) / peak * 100)
    trades = [x for x in state.get('trade_ledger', []) if x.get('side') == 'SELL']
    wins = sum(decimal(x.get('realized_pnl', '0')) > 0 for x in trades)
    realized = sum((decimal(x.get('realized_pnl', '0')) for x in trades), Decimal('0'))
    status = ('SIMULATION_DISABLED' if not simulation_enabled else
              'BASELINE_ONLY' if len(history) < 2 else 'OBSERVATIONS_IN_PROGRESS')
    return {'status': status, 'observed_sessions': len(history), 'most_recent_market_day': bar_day,
            'virtual_equity': money(equity_now), 'net_pnl_dollars': money(pnl),
            'net_return_percent': money(pnl / CAPITAL * 100),
            'max_observed_drawdown_percent': money(maximum_drawdown),
            'closed_virtual_trades': len(trades), 'profitable_closed_trades': wins,
            'realized_closed_pnl': money(realized),
            'benchmark': benchmark,
            'note': 'Virtual observations, not predictive. Closed trades reflect retained last 100 fills; excludes taxes, commissions and other real execution effects.'}


def render_email(now, broker, ai, market, events, state, errors, benchmark, decisions=None, performance=None):
    lines = ['V3 INVESTMENT PORTFOLIO & VIRTUAL PAPER REPORT',
             'As of UTC: ' + now.isoformat(timespec='minutes'),
             'No live orders. No Alpaca paper orders. All simulated fills are virtual.', '',
             'Alpaca paper account connection: ' + broker['status'],
             'Alpaca paper concentration warnings: ' + '; '.join(paper_warnings(broker)),
             'AI audit: ' + ai['status'],
             'Simulated $500 sleeve equity: $' + state.get('virtual_equity',state['cash']),
             'VTI daily-bar benchmark: ' + benchmark.get('vti_value_of_500','unavailable'),
             'Simulated cash: $' + state['cash'],
             'Simulated holdings: ' + (', '.join(state['positions']) or 'none'), '',
             'Events:']
    lines += [json.dumps(e, sort_keys=True) for e in events] or ['No new simulated signals.']
    lines += ['', 'Market data (daily bars, NOT live quotes):']
    for s,t in market.items():
        lines.append(s + ': ' + str(t.get('status')) + ', last close ' + str(t.get('last_close','unknown'))
                     + ', date ' + str(t.get('bar_date','unknown')))
    if decisions:
        lines += ['', 'V3.1 decision explanations (virtual only):']
        for sym, d in decisions.items():
            lines.append(f"{sym}: {d['verdict']} | {', '.join(d['reasons']) or 'NO_BLOCKING_RULE'}")
    if performance:
        lines += ['', 'Virtual performance: ' + str(performance.get('status')),
                  'Observed trading dates: ' + str(performance.get('observed_sessions', 0)),
                  'Virtual P/L: $' + str(performance.get('net_pnl_dollars', 'unavailable'))]
    if errors:
        lines += ['', 'Source errors: '+ ', '.join(errors)]
    lines += ['', 'AI model agreement is not proof of investment value or profitability.',
              'Robinhood data is not automatically connected to this GitHub workflow.',
              'No broker order requests were submitted.']
    return '\n'.join(lines)


def main():
    now = datetime.now(timezone.utc)
    repo = os.environ.get('GITHUB_REPOSITORY','')
    if not os.environ.get('GITHUB_TOKEN') or not repo or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise RuntimeError('GitHub environment missing or malformed')
    issue, state = load_state(repo)
    broker = paper_snapshot()
    print('Alpaca PAPER read-only sync: '+broker['status'])
    audit = ai_audit(repo, os.environ['GITHUB_TOKEN'], now)
    print('Recent 2+ model audit: '+audit['status'])
    shared = shared_research_context(repo, now)
    print('Shared AI research context: ' + shared['status'] + ' (research only; never trade approval)')
    key = os.environ.get('MASSIVE_API_KEY', '').strip()
    if not key:
        raise RuntimeError('MASSIVE_API_KEY not configured; no state changed')
    market, errors, next_rotation = market_data(now, state, key, audit=audit)
    state['rotation'] = next_rotation
    active = {s:t for s,t in market.items() if t.get('status') == 'READY'}
    if not active:
        print('::warning::No usable Massive daily bars. Virtual trading is suspended until market data validates.')
    # This is ONLY a virtual simulator; no broker POST occurs anywhere in this file.
    if is_true('V3_SIMULATE', default=False) and active:
        state, events = virtual_step(state, active, audit, now)
    else:
        if not active:
            events = [{'action':'SIMULATION_BLOCKED_NO_VALID_MARKET_DATA',
                       'reason':'Massive daily bars failed or are too old; no virtual trades'}]
        else:
            events = [{'action':'SIMULATION_OFF','reason':'Enable V3_SIMULATE after reviewing reports'}]
        state['virtual_equity'] = state.get('virtual_equity','500.00')
    benchmark = benchmark_mark(state, market)
    simulated = is_true('V3_SIMULATE', default=False)
    decisions = explain_virtual_decisions(market, audit, state, simulated, events, now=now)
    performance = performance_snapshot(state, market, benchmark, simulated)
    for symbol, decision in decisions.items():
        decision['shared_ai_context'] = shared.get('by_symbol', {}).get(symbol, {
            'status': 'NO_RECENT_SHARED_RESEARCH', 'eligible_to_authorize_trades': False})
    for symbol, decision in decisions.items():
        print('V3.1 ' + symbol + ': ' + decision['verdict'] +
              ' [' + ', '.join(decision['reasons']) + ']')
    state['history'] = (state.get('history', []) + [
        {'at':now.isoformat(), 'equity':state.get('virtual_equity','500.00'),'events':events}
    ])[-80:]
    report = {'as_of_utc':now.isoformat(),'environment':'PAPER_READ_ONLY_AND_VIRTUAL_ONLY',
              'alpaca_paper':broker,'alpaca_paper_warnings':paper_warnings(broker),
              'ai_audit':audit,'shared_ai_memory':shared,'daily_market':market,
              'market_errors':errors,
              'v31_decisions': decisions, 'v31_performance': performance,
              'v31_candidate_selection': {
                  'rule': ('RECENT_AI_AUDIT' if audit.get('status') == 'recent' and
                           any(s in WATCH and s not in ('VTI','BOTZ','GRID')
                               for s in (audit.get('by_symbol') or {}))
                           else 'WATCHLIST_FALLBACK_NO_AI_CANDIDATES'),
                  'researched_symbols': sorted((audit.get('by_symbol') or {}).keys()),
                  'priced_candidates': [s for s in market if s != 'VTI'],
                  'unscanned_research_symbols': sorted(set((audit.get('by_symbol') or {})) - set(market)),
                  'note': 'Only priced candidates evaluated. No recommendation for unscanned symbols.'},
              'market_data_status': ('ready' if active and not errors else
                                     'partial' if active else 'unavailable'),
              'virtual_state':state,'events':events,
              'benchmark':benchmark,
              'real_broker_orders_submitted':0}
    with open('v3-report.json','w',encoding='utf-8') as f:
        json.dump(report,f,indent=2,sort_keys=True)
    # Once-per-day email; more frequent source alerts still handled by existing workflows.
    send = is_true('V3_SEND_EMAIL', default=True) and state.get('last_report_day') != now.date().isoformat()
    if send:
        if not os.environ.get('RESEND_API_KEY') or not os.environ.get('ALERT_EMAIL_TO'):
            print('V3 email skipped (Resend missing); audit still available')
        else:
            base.send_email('V3 portfolio + virtual paper review',
                            render_email(now,broker,audit,market,events,state,errors,benchmark, decisions, performance))
            state['last_report_day'] = now.date().isoformat()
            print('V3 email submitted')
    save_state(repo, issue, state)
    print('V3 complete: ' + str(len(active)) + ' usable ticker bars; '
          + str(len(events)) + ' virtual events; broker orders 0')
    if errors:
        print('Partial daily price source failures: ' + str(len(errors)))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Never print credentials, account IDs, URLs or raw broker response bodies.
        print('V3 stopped safely: '+ (f'HTTP {exc.code}' if isinstance(exc,HTTPError)
                                      else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
