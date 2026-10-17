"""V3.2 read-only operations supervisor for an autonomous *virtual* investment platform.

No broker connections, broker orders, strategy changes, or secret echoing.
Reads GitHub Actions run metadata + latest V3 report. Sends Resend email only
on detected anomalies (or an explicitly requested test message).
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import intelligence

API = 'https://api.github.com'
WORKFLOW = 'investment-v3.yml'
MAX_BYTES = 2_000_000
REQUIRED_ENV = 'PAPER_READ_ONLY_AND_VIRTUAL_ONLY'


class SupervisorError(RuntimeError):
    """Generic error that does not contain secrets or provider bodies."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_int(value):
    if isinstance(value, bool):
        raise SupervisorError('Invalid integer value')
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SupervisorError('Invalid integer value') from exc
    if value < 0:
        raise SupervisorError('Invalid integer value')
    return value


def utc_timestamp(value):
    if not isinstance(value, str):
        raise SupervisorError('Invalid timestamp')
    try:
        obj = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise SupervisorError('Invalid timestamp') from exc
    if obj.tzinfo is None:
        raise SupervisorError('Timestamp lacks timezone')
    return obj.astimezone(timezone.utc)


def nonnegative(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return result.is_finite() and result >= 0


def github_json(path, token):
    if not path.startswith('/repos/'):
        raise SupervisorError('GitHub endpoint rejected')
    req = Request(API + path, headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'InvestmentIntelligence-Ops/3.2',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    try:
        # Disable redirects, so repo credentials cannot be forwarded to an
        # artifact cloud URL or an unexpected third-party host.
        with build_opener(NoRedirect()).open(req, timeout=25) as resp:
            blob = resp.read(MAX_BYTES + 1)
            if len(blob) > MAX_BYTES:
                raise SupervisorError('Oversized GitHub response')
            return json.loads(blob)
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as exc:
        if isinstance(exc, HTTPError):
            raise SupervisorError(f'GitHub HTTP {exc.code}') from exc
        raise SupervisorError('GitHub request unavailable') from exc


def download_artifact(repo, artifact_id, token):
    path = '/repos/' + quote(repo, safe='/') + f'/actions/artifacts/{safe_int(artifact_id)}/zip'
    headers = {'Authorization': 'Bearer ' + token,
               'Accept': 'application/vnd.github+json',
               'User-Agent': 'InvestmentIntelligence-Ops/3.2'}
    try:
        build_opener(NoRedirect()).open(Request(API + path, headers=headers), timeout=25)
    except HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise SupervisorError(f'Artifact HTTP {exc.code}') from exc
        redirect = exc.headers.get('Location', '')
    else:
        raise SupervisorError('Artifact did not redirect')
    host = (urlparse(redirect).hostname or '').lower()
    parsed = urlparse(redirect)
    if parsed.scheme != 'https' or not (host.endswith('.actions.githubusercontent.com') or
                                      host.endswith('.blob.core.windows.net')):
        raise SupervisorError('Untrusted artifact download host')
    # No token headers to signed artifact download URL.
    try:
        with urlopen(Request(redirect), timeout=25) as resp:
            blob = resp.read(MAX_BYTES + 1)
        if len(blob) > MAX_BYTES:
            raise SupervisorError('Oversized artifact')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            if 'v3-report.json' not in archive.namelist():
                raise SupervisorError('Missing v3-report.json')
            info = archive.getinfo('v3-report.json')
            if info.file_size > MAX_BYTES:
                raise SupervisorError('Oversized report')
            data = json.loads(archive.read('v3-report.json'))
        if not isinstance(data, dict):
            raise SupervisorError('Report is not an object')
        return data
    except (HTTPError, URLError, TimeoutError, ValueError, zipfile.BadZipFile,
            KeyError, RuntimeError) as exc:
        if isinstance(exc, SupervisorError):
            raise
        raise SupervisorError('Artifact data unavailable') from exc


def find_run(runs, now, scheduled):
    """Select intended run. Scheduled monitoring does NOT accept manual runs.

    Watchdog runs Tue-Sat 01:37 UTC; expected run was Mon-Fri 22:23 UTC.
    A scheduled run may arrive late; a missing run is flagged, not hidden.
    """
    if scheduled:
        expected_day = (now - timedelta(days=1)).date()
        if expected_day.weekday() > 4:
            return None, 'NOT_A_SCHEDULED_MARKET_DAY'
        eligible = []
        for run in runs:
            if run.get('event') != 'schedule':
                continue
            try:
                if utc_timestamp(run.get('created_at')).date() == expected_day:
                    eligible.append(run)
            except SupervisorError:
                continue
        return (max(eligible, key=lambda r: r.get('created_at', '')), None) if eligible else (None, 'MISSING_SCHEDULED_RUN')
    # Manual preflight can inspect either a manual or scheduled completed run,
    # including a weekend test; age is still bounded.
    eligible = []
    for run in runs:
        try:
            created = utc_timestamp(run.get('created_at'))
            if timedelta(0) <= now - created <= timedelta(hours=72):
                eligible.append(run)
        except SupervisorError:
            pass
    return (max(eligible, key=lambda r: r.get('created_at', '')), None) if eligible else (None, 'NO_RECENT_RUN')


def inspect_report(report, now, expected_simulation=True):
    """Deterministic health check. Never convert model opinions into orders."""
    critical, warnings, notes = [], [], []
    if report.get('environment') != REQUIRED_ENV:
        critical.append('UNEXPECTED_EXECUTION_ENVIRONMENT')
    if report.get('real_broker_orders_submitted') != 0:
        critical.append('UNEXPECTED_BROKER_ORDER_COUNT')
    try:
        report_time = utc_timestamp(report.get('as_of_utc'))
        if not timedelta(0) <= now - report_time <= timedelta(hours=40):
            critical.append('STALE_OR_FUTURE_V3_REPORT')
    except SupervisorError:
        critical.append('BAD_REPORT_TIMESTAMP')
    broker = report.get('alpaca_paper') or {}
    if not isinstance(broker, dict) or broker.get('status') != 'read_only':
        critical.append('ALPACA_PAPER_READ_ONLY_UNAVAILABLE')
    audit = report.get('ai_audit') or {}
    if not isinstance(audit, dict) or audit.get('status') != 'recent':
        warnings.append('NO_RECENT_TWO_MODEL_AUDIT')
    market = report.get('daily_market') or {}
    if not isinstance(market, dict) or (report.get('market_data_status') == 'unavailable'):
        critical.append('NO_USABLE_MARKET_DATA')
    elif report.get('market_data_status') != 'ready':
        warnings.append('PARTIAL_MARKET_COVERAGE')
    if report.get('market_errors'):
        warnings.append('PRICE_SOURCE_ERRORS_PRESENT')
    if not isinstance(market, dict) or not isinstance(market.get('VTI'), dict) or market['VTI'].get('status') != 'READY':
        warnings.append('BENCHMARK_MARK_UNAVAILABLE')
    virtual = report.get('virtual_state') or {}
    if not isinstance(virtual, dict):
        critical.append('VIRTUAL_STATE_MISSING')
        virtual = {}
    for field in ('cash', 'virtual_equity', 'day_start_equity', 'peak_equity'):
        if not nonnegative(virtual.get(field)):
            critical.append('INVALID_VIRTUAL_' + field.upper())
    positions = virtual.get('positions') or {}
    if not isinstance(positions, dict):
        critical.append('INVALID_VIRTUAL_POSITIONS')
    elif not isinstance(market, dict) or any(
            s not in market or market[s].get('status') != 'READY' for s in positions):
        critical.append('MISSING_HELD_POSITION_MARK')
    decisions = report.get('v31_decisions')
    if not isinstance(decisions, dict):
        warnings.append('V31_DECISION_DETAILS_MISSING')
    perf = report.get('v31_performance') or {}
    if not isinstance(perf, dict) or not perf:
        critical.append('PERFORMANCE_REPORT_MISSING')
    elif expected_simulation and perf.get('status') == 'SIMULATION_DISABLED':
        critical.append('EXPECTED_SIMULATION_NOT_ENABLED')
    elif expected_simulation and perf.get('status') in ('UNAVAILABLE_MARKET_DATA', 'UNAVAILABLE_HELD_PRICES'):
        critical.append('SIMULATION_MARKS_UNAVAILABLE')
    elif expected_simulation and perf.get('status') not in ('BASELINE_ONLY', 'OBSERVATIONS_IN_PROGRESS'):
        warnings.append('UNKNOWN_PERFORMANCE_STATUS')
    if isinstance(perf, dict):
        sessions = perf.get('observed_sessions')
        if isinstance(sessions, int) and sessions < 10:
            notes.append('Less than 10 independent virtual sessions recorded; results preliminary')
    events = report.get('events', [])
    if isinstance(events, list) and any(isinstance(e, dict) and e.get('action') == 'SIMULATION_OFF' for e in events) and expected_simulation:
        critical.append('SIMULATION_OFF_EVENT')
    notes.append('Broker trading remains disabled; AI agreement is not a profit guarantee')
    return {'status': 'CRITICAL' if critical else 'WARNING' if warnings else 'HEALTHY',
            'critical': sorted(set(critical)), 'warnings': sorted(set(warnings)), 'notes': notes,
            'market_day': perf.get('most_recent_market_day') if isinstance(perf, dict) else None,
            'observed_sessions': perf.get('observed_sessions') if isinstance(perf, dict) else None,
            'virtual_equity': virtual.get('virtual_equity')}


def supervise(repo, token, now, scheduled, expected_simulation):
    encoded = quote(repo, safe='/')
    data = github_json(f'/repos/{encoded}/actions/workflows/{WORKFLOW}/runs?per_page=30', token)
    runs = data.get('workflow_runs', []) if isinstance(data, dict) else []
    run, reason = find_run(runs, now, scheduled)
    if not run:
        return {'status':'CRITICAL', 'critical':[reason], 'warnings': [], 'notes': []}
    run_id = safe_int(run.get('id'))
    conclusion = run.get('conclusion')
    if conclusion != 'success':
        return {'status':'CRITICAL', 'critical':['V3_RUN_NOT_SUCCESSFUL'],
                'warnings':[], 'notes':[f'Workflow run id: {run_id}',
                                        'Run status: {str(run.get("status", "unknown"))[:25]}']}
    metadata = github_json(f'/repos/{encoded}/actions/runs/{run_id}/artifacts?per_page=50', token)
    artifacts = metadata.get('artifacts', []) if isinstance(metadata, dict) else []
    named = [a for a in artifacts if a.get('name') == f'investment-v3-{run_id}' and not a.get('expired')]
    if not named:
        return {'status': 'CRITICAL', 'critical':['V3_AUDIT_ARTIFACT_MISSING'],
                'warnings': [], 'notes':[f'Workflow run id: {run_id}']}
    report = download_artifact(repo, named[0]['id'], token)
    result = inspect_report(report, now, expected_simulation)
    result['notes'].append(f'Workflow run id: {run_id}')
    return result


def send_alert(result, now, test=False):
    if not os.environ.get('RESEND_API_KEY') or not os.environ.get('ALERT_EMAIL_TO'):
        raise SupervisorError('Missing Resend notification configuration')
    lines = [f'V3.2 operations check: {result["status"]}',
             'Checked UTC: ' + now.isoformat(timespec='minutes'),
             'Scope: read-only workflow monitoring; no brokerage orders',
             'Critical: ' + (', '.join(result.get('critical', [])) or 'none'),
             'Warnings: ' + (', '.join(result.get('warnings', [])) or 'none'),
             'Market date: ' + str(result.get('market_day', 'unknown')),
             'Virtual sessions observed: ' + str(result.get('observed_sessions', 'unknown')),
             'Actions: https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '/actions',
             'This is a test notification.' if test else 'Review any failing workflow and its audit artifact.',
             'Real-money trading is not enabled.']
    intelligence.send_email('Investment V3.2 ' + ('TEST' if test else result['status'] + ' alert'), '\n'.join(lines))
    return True


def main():
    required = ('GITHUB_TOKEN', 'GITHUB_REPOSITORY')
    if any(not os.environ.get(x) for x in required):
        raise SupervisorError('Missing GitHub runner configuration')
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise SupervisorError('Repository name rejected')
    now = datetime.now(timezone.utc)
    scheduled = os.environ.get('GITHUB_EVENT_NAME') == 'schedule'
    expect = os.environ.get('OPS_EXPECT_SIMULATION', 'true').lower() == 'true'
    test_email = os.environ.get('OPS_TEST_EMAIL', 'false').lower() == 'true'
    try:
        result = supervise(repo, os.environ['GITHUB_TOKEN'], now, scheduled, expect)
    except (SupervisorError, KeyError, TypeError, ValueError, RuntimeError) as exc:
        reason = (str(exc) if isinstance(exc, SupervisorError) else type(exc).__name__)
        # Only sanitized fixed-category errors are created by helpers above.
        result = {'status':'CRITICAL', 'critical':['SUPERVISOR_READ_FAILED: ' + reason],
                  'warnings':[], 'notes': []}
    with open('ops-health.json','w',encoding='utf-8') as f:
        json.dump({'checked_utc':now.isoformat(), 'scheduled':scheduled, **result}, f, indent=2)
    print('V3.2 operations status: ' + result['status'])
    for category in result.get('critical', []):
        print('::error::' + category)
    for category in result.get('warnings', []):
        print('::warning::' + category)
    if test_email or (scheduled and result['status'] != 'HEALTHY'):
        send_alert(result, now, test=test_email)
        print('Operations notification submitted to Resend')
    elif not scheduled:
        print('Manual inspection only: alerts suppressed unless test email requested')
    else:
        print('Scheduled check healthy: no alert needed')
    if result['status'] == 'CRITICAL':
        raise SupervisorError('Health check critical; see ops-health.json')


if __name__ == '__main__':
    try:
        main()
    except (SupervisorError, HTTPError, URLError, TimeoutError) as exc:
        print('Operations supervisor failed safely: ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
