"""V3.3 Alpaca PAPER readiness preflight. Strictly read-only; never sends orders.

Reads the most recent V3.1 artifact plus the current Alpaca paper account.
Produces human-readable, deterministic reasons why a *future* paper order
is/ is not safe to consider. THIS PROGRAM HAS NO BROKER POST PATH.
"""
from __future__ import annotations
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from urllib.parse import quote

import ops_supervisor as ops
import v3_engine as v3

WATCH = set(v3.WATCH) - {'VTI', 'GRID', 'BOTZ'}
ALLOW_ONLY_ENVIRONMENT = 'PAPER_READ_ONLY_AND_VIRTUAL_ONLY'
ARTIFACT_NAME_PREFIX = 'investment-v3-'
MAX_REPORT_AGE_HOURS = 8
MAX_AI_AGE_HOURS = 48
CAPITAL = Decimal('500')
MAX_POSITION = Decimal('25')

class PreflightError(RuntimeError):
    pass


def checked_dt(raw):
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PreflightError('BAD_TIMESTAMP') from exc
    if dt.tzinfo is None:
        raise PreflightError('NAIVE_TIMESTAMP')
    return dt.astimezone(timezone.utc)


def elapsed_hours(when, now):
    delta = now - checked_dt(when)
    return delta.total_seconds() / 3600


def find_v3_report(repo, token, now, *, scheduled):
    """Require most recent completed run, not the last successful run.

    Never use yesterday's successful report to conceal a newer failed run.
    """
    prefix = '/repos/' + quote(repo, safe='/')
    response = ops.github_json(prefix + '/actions/workflows/investment-v3.yml/runs?per_page=15', token)
    runs = response.get('workflow_runs') if isinstance(response, dict) else None
    if not isinstance(runs, list) or not runs:
        raise PreflightError('NO_V3_RUNS')
    if scheduled:
        today = now.date().isoformat()
        candidates = [r for r in runs if r.get('event') == 'schedule'
                      and isinstance(r.get('created_at'), str)
                      and r['created_at'].startswith(today)]
        if not candidates:
            raise PreflightError('SCHEDULED_V3_RUN_MISSING_TODAY')
    else:
        candidates = [r for r in runs if isinstance(r.get('created_at'), str)
                      and 0 <= elapsed_hours(r['created_at'], now) <= 24]
        if not candidates:
            raise PreflightError('NO_RECENT_V3_RUN')
    latest = max(candidates, key=lambda r: r['created_at'])
    if latest.get('status') != 'completed' or latest.get('conclusion') != 'success':
        raise PreflightError('LATEST_V3_NOT_SUCCESSFUL')
    if latest.get('head_branch') not in (None, 'main'):
        raise PreflightError('UNEXPECTED_V3_BRANCH')
    run_id = ops.safe_int(latest.get('id'))
    artifact_metadata = ops.github_json(prefix+f'/actions/runs/{run_id}/artifacts?per_page=50', token)
    matches = [a for a in artifact_metadata.get('artifacts', [])
               if a.get('name') == ARTIFACT_NAME_PREFIX + str(run_id) and not a.get('expired')]
    if len(matches) != 1:
        raise PreflightError('LATEST_V3_ARTIFACT_MISSING_OR_AMBIGUOUS')
    return ops.download_artifact(repo, matches[0]['id'], token), run_id


def evaluate(report, broker, now, *, scheduled):
    """Evaluate readiness only; no order creation or transmission."""
    blocks = []
    warnings = []
    notices = []
    candidates = []

    if report.get('environment') != ALLOW_ONLY_ENVIRONMENT:
        blocks.append('UNEXPECTED_V3_EXECUTION_ENVIRONMENT')
    if report.get('real_broker_orders_submitted') != 0:
        blocks.append('BROKER_ORDER_COUNT_MISMATCH')
    try:
        age = elapsed_hours(report['as_of_utc'], now)
        if not 0 <= age <= MAX_REPORT_AGE_HOURS:
            blocks.append('V3_REPORT_STALE_OR_FUTURE')
    except (KeyError, PreflightError):
        blocks.append('BAD_V3_REPORT_TIMESTAMP')

    if broker.get('status') != 'read_only' or broker.get('environment') != 'ALPACA_PAPER_ONLY':
        blocks.append('ALPACA_PAPER_SNAPSHOT_NOT_VERIFIED')
    if broker.get('positions') or broker.get('open_orders'):
        blocks.append('PAPER_ACCOUNT_NOT_EMPTY_OR_HAS_OPEN_ORDERS')
    if not isinstance(broker.get('positions'), list) or not isinstance(broker.get('open_orders'), list):
        blocks.append('INCOMPLETE_BROKER_ACCOUNT_SNAPSHOT')
    try:
        cash, account_equity = v3.decimal(broker['cash']), v3.decimal(broker['equity'])
        if cash < CAPITAL or account_equity < CAPITAL:
            blocks.append('ALPACA_PAPER_ACCOUNT_BELOW_500')
    except (ValueError, KeyError, TypeError):
        blocks.append('UNKNOWN_PAPER_CASH_OR_EQUITY')

    account_in_report = report.get('alpaca_paper') or {}
    if not isinstance(account_in_report, dict) or account_in_report.get('status') != 'read_only':
        blocks.append('V3_PAPER_READ_ONLY_CONNECTION_UNAVAILABLE')
    elif (set(p.get('symbol') for p in account_in_report.get('positions', []) if isinstance(p, dict))
          != set(p.get('symbol') for p in (broker.get('positions') or []) if isinstance(p, dict))):
        blocks.append('BROKER_POSITION_RECONCILIATION_MISMATCH')

    audit = report.get('ai_audit') or {}
    if audit.get('status') != 'recent':
        blocks.append('TWO_MODEL_AI_AUDIT_NOT_RECENT')
    try:
        if not 0 <= elapsed_hours(audit['as_of'], now) <= MAX_AI_AGE_HOURS:
            blocks.append('AI_AUDIT_TOO_OLD')
    except (KeyError, PreflightError):
        blocks.append('BAD_AI_AUDIT_TIMESTAMP')

    if report.get('market_data_status') != 'ready' or report.get('market_errors'):
        blocks.append('INCOMPLETE_VALID_MARKET_DATA')
    virtual_state = report.get('virtual_state') or {}
    if virtual_state.get('pending'):
        notices.append('VIRTUAL_ORDER_PENDING_NOT_EXECUTABLE_BROKER_ORDER')
    performance = report.get('v31_performance') or {}
    sessions = performance.get('observed_sessions')
    if not isinstance(sessions, int) or sessions < 20:
        blocks.append('FEWER_THAN_20_VIRTUAL_MARKET_SESSIONS')
    # Strictly use V3's virtual equity / loss baselines, never Alpaca's $100K default.
    try:
        equity = v3.decimal(virtual_state['virtual_equity'])
        day = v3.decimal(virtual_state['day_start_equity'])
        peak = v3.decimal(virtual_state['peak_equity'])
        if day <= 0 or peak <= 0 or equity <= 0:
            blocks.append('BAD_VIRTUAL_EQUITY_BASELINES')
        else:
            if (day - equity)/day >= Decimal('.02'):
                blocks.append('DAILY_TWO_PERCENT_LOSS_LIMIT_REACHED')
            if (peak - equity)/peak >= Decimal('.10'):
                blocks.append('TEN_PERCENT_DRAWDOWN_LIMIT_REACHED')
    except (ValueError, TypeError, KeyError):
        blocks.append('INVALID_VIRTUAL_EQUITY_BASELINES')

    if now.weekday() >= 5:
        blocks.append('WEEKEND_NO_US_EQUITY_SESSION')
    daily_market = report.get('daily_market') or {}
    decisions = report.get('v31_decisions') or {}
    if not isinstance(decisions, dict) or not isinstance(daily_market, dict):
        blocks.append('V31_CANDIDATES_UNAVAILABLE')
        decisions, daily_market = {}, {}
    for sym in sorted(decisions):
        reasons = []
        if sym not in WATCH:
            continue
        decision = decisions[sym]
        market = daily_market.get(sym) or {}
        if not isinstance(decision, dict) or not isinstance(market, dict):
            reasons.append('MALFORMED_CANDIDATE')
            decision, market = {}, {}
        allowed_verdict = decision.get('verdict') == 'VIRTUAL_ENTRY_ELIGIBLE_NOT_EXECUTED'
        if not allowed_verdict:
            reasons.append('NO_ELIGIBLE_V31_ENTRY_SIGNAL')
        if decision.get('reasons'):
            reasons.append('EXISTING_V31_ENTRY_BLOCKERS')
        quality = decision.get('ai_research_quality') or {}
        if quality.get('status') != 'MODEL_AGREEMENT_REVIEW_ONLY':
            reasons.append('AI_MODELS_NOT_IN_QUALIFYING_AGREEMENT')
        # Prior AI review explicitly says evidence was not independently verified;
        # that is an absolute paper-order block, not something to waive on consensus.
        if quality.get('evidence_verification') != 'INDEPENDENTLY_VERIFIED':
            reasons.append('ORIGINAL_NEWS_AND_FILINGS_NOT_INDEPENDENTLY_VERIFIED')
        if market.get('status') != 'READY' or not market.get('bullish'):
            reasons.append('NOT_VALIDATED_BULLISH_DAILY_MARKET_DATA')
        try:
            close = v3.decimal(market['last_close'])
            if close <= 0:
                raise ValueError('nonpositive')
            if close * Decimal('1.005') > MAX_POSITION:
                reasons.append('ONE_WHOLE_SHARE_EXCEEDS_25_USD_CAP')
        except (ValueError, KeyError, TypeError):
            reasons.append('CANDIDATE_PRICE_UNKNOWN')
        # Never use a previous close as an executable quote.
        reasons.append('FRESH_TRADABLE_LIMIT_QUOTE_NOT_YET_IMPLEMENTED')
        candidates.append({'symbol':sym,'status':'NOT_READY_FOR_PAPER_ORDER',
                           'verdict':decision.get('verdict','UNKNOWN'),
                           'reasons':sorted(set(reasons)),
                           'market_bar_date':market.get('bar_date')})

    if not candidates:
        notices.append('NO_ELIGIBLE_AI_SCANNED_CANDIDATES')
    if scheduled and not now.weekday() < 5:
        blocks.append('UNEXPECTED_WEEKEND_SCHEDULE')
    blocks = sorted(set(blocks))
    warnings = sorted(set(warnings))
    return {'status':'BLOCKED' if blocks else 'MONITORING',
            'scope':'ALPACA_PAPER_READ_ONLY_PREFLIGHT',
            'execution_enabled': False,
            'broker_orders_submitted':0,
            'blocks':blocks,
            'warnings':warnings,
            'notes':notices,
            'candidates':candidates,
            'observed_virtual_sessions':sessions,
            'as_of_utc':now.isoformat()}


def main():
    now = datetime.now(timezone.utc)
    output = {'status':'BLOCKED','scope':'ALPACA_PAPER_READ_ONLY_PREFLIGHT',
              'execution_enabled':False,'broker_orders_submitted':0,
              'blocks':['PREFLIGHT_NOT_COMPLETED'],'as_of_utc':now.isoformat()}
    try:
        repo = os.environ['GITHUB_REPOSITORY']
        token = os.environ['GITHUB_TOKEN']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not token:
            raise PreflightError('MISSING_OR_MALFORMED_GITHUB_ENVIRONMENT')
        report, run_id = find_v3_report(repo,token,now,scheduled=os.environ.get('GITHUB_EVENT_NAME')=='schedule')
        broker = v3.paper_snapshot()  # GET only
        output = evaluate(report, broker, now, scheduled=os.environ.get('GITHUB_EVENT_NAME')=='schedule')
        output['source_v3_run_id'] = run_id
    except (PreflightError, ops.SupervisorError, ValueError, KeyError, TypeError,
            OSError, TimeoutError, RuntimeError) as exc:
        error = str(exc) if isinstance(exc, PreflightError) else type(exc).__name__
        output['blocks'] = [error]
    finally:
        with open('paper-readiness.json','w',encoding='utf-8') as f:
            json.dump(output,f,indent=2,sort_keys=True)
    print('V3.3 paper broker readiness: '+output['status'])
    print('Alpaca PAPER execution: disabled')
    print('Paper brokerage orders: 0')
    for x in output['blocks']:
        print('::warning::'+x)
    for c in output.get('candidates', []):
        print(c['symbol']+': '+c['status']+'; '+','.join(c['reasons']))
    # Infrastructure failures should be evident in Actions rather than falsely green.
    if output['blocks'] and any(x in {'NO_V3_RUNS','SCHEDULED_V3_RUN_MISSING_TODAY',
               'NO_RECENT_V3_RUN','LATEST_V3_NOT_SUCCESSFUL',
               'LATEST_V3_ARTIFACT_MISSING_OR_AMBIGUOUS'} for x in output['blocks']):
        raise PreflightError('V3_PARENT_WORKFLOW_UNAVAILABLE')


if __name__ == '__main__':
    try:
        main()
    except PreflightError:
        sys.exit(1)
