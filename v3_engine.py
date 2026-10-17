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
    if status != 'OK':
        hint = massive_response_hint(status, data)
        raise MassiveDataError(f'MASSIVE_STATUS_{status}_{hint}')
    found = data.get('results', [])
    if not isinstance(found, list):
        raise MassiveDataError('INVALID_RESULTS_ARRAY')
    if not found:
        return []  # No trading-day data, or empty timeframe; not a parse failure.
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
        out.append({'date': day, 'close': str(price), 'open': str(opening)})
    dates = [x['date'] for x in out]
    if len(set(dates)) != len(dates):
        raise MassiveDataError('DUPLICATE_MARKET_DATES')
    return sorted(out, key=lambda b: b['date'])


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
                                              'reviewer_count': len(r.get('reviews') or {})}
                                  for r in audit['reports'] if r.get('symbol') in WATCH}}
        return {'status': 'unavailable', 'reason': 'No recent successful AI Team report artifact'}
    except (HTTPError, URLError, ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
        return {'status': 'unavailable', 'reason': 'AI audit retrieval: '
                + (f'HTTP {exc.code}' if isinstance(exc, HTTPError) else type(exc).__name__)}


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
            'last_report_day': None, 'rotation': 0, 'history': []}


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
                else:
                    events.append({'action':'VIRTUAL_BUY_BLOCKED','symbol':sym,
                                   'reason':check['reasons']})
            else:
                p = state['positions'].get(sym)
                if p:
                    fill = open_price * Decimal('.998')
                    state['cash'] = money(decimal(state['cash']) + decimal(p['qty']) * fill)
                    del state['positions'][sym]
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


def market_data(now, state, api_key):
    """3 rotating watched symbols + all active/pending virtual positions, plus VTI benchmark."""
    rotation = int(state.get('rotation', 0)) % len(WATCH)
    chosen = [WATCH[(rotation+i) % len(WATCH)] for i in range(3)]
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
    return results, errors, (rotation + 3) % len(WATCH)


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


def render_email(now, broker, ai, market, events, state, errors, benchmark):
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
    key = os.environ.get('MASSIVE_API_KEY', '').strip()
    if not key:
        raise RuntimeError('MASSIVE_API_KEY not configured; no state changed')
    market, errors, next_rotation = market_data(now, state, key)
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
    state['history'] = (state.get('history', []) + [
        {'at':now.isoformat(), 'equity':state.get('virtual_equity','500.00'),'events':events}
    ])[-80:]
    report = {'as_of_utc':now.isoformat(),'environment':'PAPER_READ_ONLY_AND_VIRTUAL_ONLY',
              'alpaca_paper':broker,'alpaca_paper_warnings':paper_warnings(broker),
              'ai_audit':audit,'daily_market':market,
              'market_errors':errors,
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
                            render_email(now,broker,audit,market,events,state,errors,benchmark))
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
