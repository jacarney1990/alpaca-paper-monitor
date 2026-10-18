"""V4.3 isolated Alpaca PAPER pilot. Shadow by default; never accesses a live broker.

Zero trades are preferable to an unverified signal. The current V3 research does not
assert independently verified primary-source facts, so live paper submission should
remain BLOCKED until that evidence and forward performance genuinely exist.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import intelligence as base
import paper_bridge
import v3_engine as v3

PAPER_BASE = 'https://paper-api.alpaca.markets'   # FIXED. Never configurable.
PAPER_DATA = 'https://data.alpaca.markets'        # FIXED.
MAX_RESPONSE = 1_000_000
CAPITAL = Decimal('500')
MAX_ORDER = Decimal('25')
MAX_QUOTE_SECONDS = 45
MAX_REPORT_HOURS = 24
NY = ZoneInfo('America/New_York')
LEDGER_TITLE = 'V4.3 Alpaca PAPER Pilot State (automated; private; do not edit)'
REPORT_PATH = 'paper-pilot-report.json'
SUPPORTED_MODE = {'shadow', 'submit'}


class PilotBlock(RuntimeError):
    """Machine-readable, non-secret order disqualification."""


def d(value):
    try:
        x = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise PilotBlock('INVALID_DECIMAL') from None
    if not x.is_finite():
        raise PilotBlock('NONFINITE_DECIMAL')
    return x


def utc_time(value):
    try:
        t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise PilotBlock('INVALID_TIMESTAMP') from None
    if t.tzinfo is None:
        raise PilotBlock('NAIVE_TIMESTAMP')
    return t.astimezone(timezone.utc)


def checked_http(url, headers, *, payload=None):
    # Block any URL except the two pinned PAPER hosts; no live API can be reached.
    if not (url.startswith(PAPER_BASE + '/v2/') or
            url.startswith(PAPER_DATA + '/v2/stocks/')):
        raise PilotBlock('NON_PAPER_API_HOST_BLOCKED')
    raw = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=raw, method='POST' if raw is not None else 'GET',
                      headers={'Accept': 'application/json', 'User-Agent': 'V43PaperPilot/1.0',
                               **({'Content-Type': 'application/json'} if raw else {}), **headers})
    try:
        with urlopen(request, timeout=12) as response:
            if response.status not in (200, 201):
                raise PilotBlock('BAD_BROKER_RESPONSE')
            blob = response.read(MAX_RESPONSE + 1)
            if len(blob) > MAX_RESPONSE:
                raise PilotBlock('BROKER_RESPONSE_TOO_LARGE')
            return json.loads(blob)
    except HTTPError as exc:
        raise PilotBlock('PAPER_HTTP_' + str(exc.code)) from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise PilotBlock('PAPER_TRANSPORT_OR_FORMAT_ERROR') from None


def paper_headers():
    key = os.getenv('ALPACA_PAPER_API_KEY', '').strip()
    secret = os.getenv('ALPACA_PAPER_API_SECRET', '').strip()
    if not key or not secret:
        raise PilotBlock('PAPER_KEYS_MISSING')
    return {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}


def get_broker_snapshot(headers):
    account = checked_http(PAPER_BASE + '/v2/account', headers)
    positions = checked_http(PAPER_BASE + '/v2/positions', headers)
    orders = checked_http(PAPER_BASE + '/v2/orders?status=open&limit=100', headers)
    clock = checked_http(PAPER_BASE + '/v2/clock', headers)
    if not isinstance(account, dict) or not isinstance(positions, list) or not isinstance(orders, list) or not isinstance(clock, dict):
        raise PilotBlock('BROKER_SNAPSHOT_INVALID')
    return {'account': account, 'positions': positions, 'orders': orders, 'clock': clock}


def verify_broker(broker, now):
    account, positions, orders, clock = (broker[x] for x in ('account','positions','orders','clock'))
    if account.get('status') != 'ACTIVE' or account.get('trading_blocked') or account.get('account_blocked'):
        raise PilotBlock('PAPER_ACCOUNT_NOT_ACTIVE')
    if account.get('pattern_day_trader') and account.get('daytrade_count', 0):
        raise PilotBlock('PAPER_DAYTRADING_RESTRICTION_UNCHECKED')
    if positions or orders:
        raise PilotBlock('PAPER_ACCOUNT_NOT_DEDICATED_AND_EMPTY')
    if d(account.get('cash')) < CAPITAL or d(account.get('equity')) < CAPITAL:
        raise PilotBlock('PAPER_CAPITAL_INSUFFICIENT')
    if clock.get('is_open') is not True or now.weekday() >= 5:
        raise PilotBlock('US_STOCK_MARKET_CLOSED')
    # Guard near the market open/close to reduce auction and illiquidity exposure.
    market_now = now.astimezone(NY)
    if not (market_now.hour > 9 or (market_now.hour == 9 and market_now.minute >= 45)) or market_now.hour >= 15 or (market_now.hour == 14 and market_now.minute >= 45):
        raise PilotBlock('OUTSIDE_PILOT_TRADING_WINDOW')


def verify_report(report, now):
    if report.get('environment') != 'PAPER_READ_ONLY_AND_VIRTUAL_ONLY':
        raise PilotBlock('UNTRUSTED_V3_ENVIRONMENT')
    if report.get('real_broker_orders_submitted') != 0:
        raise PilotBlock('V3_BROKER_ORDER_COUNT_BAD')
    delta = (now - utc_time(report.get('as_of_utc'))).total_seconds()
    if delta < 0 or delta > MAX_REPORT_HOURS * 3600:
        raise PilotBlock('V3_REPORT_NOT_FRESH')
    if report.get('market_data_status') != 'ready' or report.get('market_errors'):
        raise PilotBlock('V3_MARKET_DATA_UNVERIFIED')
    if (report.get('ai_audit') or {}).get('status') != 'recent':
        raise PilotBlock('AI_RESEARCH_NOT_RECENT')
    perf = report.get('v31_performance') or {}
    if type(perf.get('observed_sessions')) is not int or perf['observed_sessions'] < 20:
        raise PilotBlock('INSUFFICIENT_VIRTUAL_SESSIONS')
    if type(perf.get('closed_virtual_trades')) is not int or perf['closed_virtual_trades'] < 5:
        raise PilotBlock('INSUFFICIENT_VIRTUAL_CLOSED_TRADES')
    if d(perf.get('max_observed_drawdown_percent')) >= Decimal('10'):
        raise PilotBlock('VIRTUAL_DRAWDOWN_HALT')
    state = report.get('virtual_state') or {}
    if state.get('pending'):
        raise PilotBlock('VIRTUAL_ORDER_PENDING')
    equity, start, peak = (d(state.get(k)) for k in ('virtual_equity', 'day_start_equity', 'peak_equity'))
    if equity <= 0 or start <= 0 or peak <= 0:
        raise PilotBlock('VIRTUAL_EQUITY_INVALID')
    if (start - equity) / start >= Decimal('.02') or (peak - equity) / peak >= Decimal('.10'):
        raise PilotBlock('VIRTUAL_RISK_HALT')


def candidate_symbols(report):
    decisions = report.get('v31_decisions') or {}
    market = report.get('daily_market') or {}
    if not isinstance(decisions, dict) or not isinstance(market, dict):
        raise PilotBlock('V3_CANDIDATES_INVALID')
    candidates = []
    for symbol, decision in sorted(decisions.items()):
        if (not re.fullmatch(r'[A-Z]{1,5}', symbol) or symbol in {'VTI','BOTZ','GRID'}
                or symbol not in v3.WATCH or not isinstance(decision, dict)):
            continue
        if decision.get('verdict') != 'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED' or decision.get('reasons'):
            continue
        quality = decision.get('ai_research_quality') or {}
        if quality.get('status') != 'MODEL_AGREEMENT_REVIEW_ONLY' or quality.get('classification') not in ('opportunity','watch'):
            continue
        # Existing V3 evidence verification is NOT_VERIFIED. Do not bypass this.
        if quality.get('evidence_verification') != 'INDEPENDENTLY_VERIFIED':
            continue
        context = decision.get('shared_ai_context') or {}
        models = set((context.get('team_views') or {}).keys()) if isinstance(context.get('team_views'), dict) else set(context.get('team_views') or [])
        if not {'OpenAI', 'Gemini', 'Groq'}.issubset(models):
            continue
        if context.get('status') not in ('RECENT','recent'):
            continue
        m = market.get(symbol) or {}
        if m.get('status') != 'READY' or m.get('bullish') is not True:
            continue
        candidates.append(symbol)
    return candidates


def executable_quote(symbol, headers, now):
    asset = checked_http(PAPER_BASE + '/v2/assets/' + quote(symbol, safe=''), headers)
    if not isinstance(asset, dict) or not asset.get('tradable') or asset.get('status') != 'active' or asset.get('class') not in ('us_equity', 'us_stock'):
        raise PilotBlock('ASSET_NOT_ACTIVE_TRADABLE_US_EQUITY')
    result = checked_http(PAPER_DATA + '/v2/stocks/' + quote(symbol, safe='') + '/quotes/latest?feed=iex', headers)
    if not isinstance(result, dict) or not isinstance(result.get('quote'), dict):
        raise PilotBlock('QUOTE_INVALID')
    q = result['quote']
    bid, ask = d(q.get('bp')), d(q.get('ap'))
    if bid <= 0 or ask <= bid:
        raise PilotBlock('QUOTE_BID_ASK_INVALID')
    age = (now - utc_time(q.get('t'))).total_seconds()
    if age < 0 or age > MAX_QUOTE_SECONDS:
        raise PilotBlock('QUOTE_STALE')
    if (ask - bid) / ask > Decimal('.008'):
        raise PilotBlock('QUOTE_SPREAD_TOO_WIDE')
    # Whole share DAY LIMIT; conservative 30 bps buffer, rounded up to a cent.
    limit_price = (ask * Decimal('1.003')).quantize(Decimal('.01'), rounding=ROUND_UP)
    if limit_price <= 0 or limit_price > MAX_ORDER:
        raise PilotBlock('WHOLE_SHARE_EXCEEDS_25_USD_CAP')
    return limit_price


def order_id(now, symbol):
    return 'v43p-' + now.astimezone(NY).strftime('%Y%m%d') + '-' + symbol + '-buy'


def build_plan(report, broker, now, quote_for):
    verify_report(report, now)
    verify_broker(broker, now)
    candidates = candidate_symbols(report)
    if not candidates:
        raise PilotBlock('NO_VERIFIED_THREE_AI_ENTRY_SIGNAL')
    if len(candidates) != 1:
        raise PilotBlock('AMBIGUOUS_MULTIPLE_CANDIDATES')
    symbol = candidates[0]
    price = quote_for(symbol)
    if d(price) > MAX_ORDER:
        raise PilotBlock('PILOT_POSITION_LIMIT')
    return {'symbol':symbol,'side':'buy','type':'limit','qty':'1',
            'time_in_force':'day','extended_hours':False,
            'limit_price':str(price),'client_order_id':order_id(now,symbol)}


def load_ledger(repo):
    issues = base.github(f'/repos/{repo}/issues?state=all&per_page=100')
    entries = [x for x in issues if x.get('title') == LEDGER_TITLE and 'pull_request' not in x]
    if len(entries) > 1:
        raise PilotBlock('PILOT_LEDGER_AMBIGUOUS')
    if not entries:
        return None, {'schema':1,'orders':[]}
    try:
        body = json.loads(entries[0].get('body') or '{}')
        if body.get('schema') != 1 or not isinstance(body.get('orders'), list):
            raise ValueError()
    except (ValueError, AttributeError):
        raise PilotBlock('PILOT_LEDGER_INVALID') from None
    return entries[0]['number'], body


def save_ledger(repo, issue_no, ledger):
    record = {'body': json.dumps(ledger, sort_keys=True, separators=(',', ':'))}
    if issue_no is None:
        record['title'] = LEDGER_TITLE
        result = base.github(f'/repos/{repo}/issues', 'POST', record)
        if not isinstance(result, dict) or not isinstance(result.get('number'), int):
            raise PilotBlock('PILOT_LEDGER_CREATE_UNCONFIRMED')
        return result['number']
    base.github(f'/repos/{repo}/issues/{issue_no}', 'PATCH', record)
    return issue_no


def submit_once(repo, plan, now, headers):
    # Persistent reservation BEFORE POST means ambiguous failures are never retried.
    issue_no, ledger = load_ledger(repo)
    orders = ledger['orders']
    if orders:  # Single-account pilot, deliberately just one lifetime entry.
        raise PilotBlock('PAPER_PILOT_ALREADY_HAS_ORDER_OR_UNRESOLVED_INTENT')
    request_id = plan['client_order_id']
    # A prior broker order of the same ID must never be duplicated. HTTP 404 is
    # the only allowed absence; any outage or other response fails closed.
    try:
        existing = checked_http(PAPER_BASE + '/v2/orders:by_client_order_id?' +
                                urlencode({'client_order_id':request_id}), headers)
    except PilotBlock as exc:
        if str(exc) != 'PAPER_HTTP_404':
            raise
    else:
        raise PilotBlock('DUPLICATE_CLIENT_ORDER_ID_ALREADY_EXISTS')
    record = {'client_order_id':request_id,'symbol':plan['symbol'],
              'state':'RESERVED', 'at_utc':now.isoformat(), 'paper_only':True}
    orders.append(record)
    save_ledger(repo, issue_no, ledger)
    # There is no retry, market-order fallback, or live URL path.
    try:
        response = checked_http(PAPER_BASE + '/v2/orders', headers, payload=plan)
    except PilotBlock:
        # Keep RESERVED until investigated; do not retry on the next schedule.
        raise PilotBlock('PAPER_POST_RESULT_UNKNOWN_RECONCILE_MANUALLY') from None
    if (not isinstance(response, dict) or response.get('client_order_id') != request_id
            or response.get('symbol') != plan['symbol'] or response.get('side') != 'buy'
            or not response.get('id')):
        raise PilotBlock('PAPER_POST_RESPONSE_UNCONFIRMED_RECONCILE_MANUALLY')
    record.update({'state':'SUBMITTED', 'broker_order_id':str(response['id']),
                   'broker_status':str(response.get('status','unknown'))[:35]})
    save_ledger(repo, issue_no, ledger)
    return {'client_order_id':request_id,'state':'SUBMITTED',
            'broker_status':record['broker_status']}


def main():
    now = datetime.now(timezone.utc)
    output = {'version':'V4.3','as_of_utc':now.isoformat(), 'mode':'shadow',
              'status':'BLOCKED', 'paper_orders_submitted':0,'real_money_orders':0,
              'paper_api_only':True, 'reason':'NOT_EVALUATED'}
    try:
        mode = os.getenv('PAPER_PILOT_MODE', 'shadow').lower().strip()
        if mode not in SUPPORTED_MODE:
            raise PilotBlock('UNKNOWN_PILOT_MODE')
        output['mode'] = mode
        if mode == 'submit' and os.getenv('PAPER_PILOT_APPROVED','').strip() != 'true':
            raise PilotBlock('PAPER_PILOT_SUBMISSION_NOT_APPROVED')
        token, repo = os.getenv('GITHUB_TOKEN'), os.getenv('GITHUB_REPOSITORY','')
        if not token or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise PilotBlock('GITHUB_CONFIGURATION_MISSING')
        report, run_id = paper_bridge.find_v3_report(repo, token, now, scheduled=False)
        output['source_v3_run_id'] = run_id
        headers = paper_headers()
        broker = get_broker_snapshot(headers)
        plan = build_plan(report, broker, now,
                          lambda s: executable_quote(s, headers, now))
        output.update({'status':'ELIGIBLE_SHADOW_PLAN','reason':'ALL_PAPER_GATES_PASSED',
                       'symbol':plan['symbol'], 'estimated_order_limit':plan['limit_price']})
        if mode == 'submit':
            outcome = submit_once(repo, plan, now, headers)
            output.update({'status':'PAPER_SUBMITTED','paper_orders_submitted':1, 'order':outcome})
    except PilotBlock as exc:
        output['reason'] = str(exc)
    except (paper_bridge.PreflightError, HTTPError, URLError, ValueError, KeyError, TimeoutError):
        output['reason'] = 'PRECONDITION_OR_EXTERNAL_SOURCE_FAILURE'
    except Exception:
        # GitHub transport/library exceptions cannot bypass the fail-closed state.
        output['reason'] = 'UNEXPECTED_FAIL_CLOSED'
    finally:
        with open(REPORT_PATH,'w',encoding='utf-8') as f:
            json.dump(output, f, indent=2, sort_keys=True)
    print('V4.3 Alpaca PAPER: ' + output['status'] + '; reason=' + output['reason'] +
          '; paper orders=' + str(output['paper_orders_submitted']) + '; live orders=0')
    # Blocked eligibility is an expected safety outcome, not a broken workflow.
    # Unknown broker POST requires human reconciliation and should turn the job red.
    if output['reason'].startswith('PAPER_POST_'):
        sys.exit(1)


if __name__ == '__main__':
    main()
