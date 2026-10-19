"""Shared, fail-closed Gemini API request budget for investment research.

Applies only when GEMINI_DAILY_REQUEST_CAP is configured in GitHub Actions.
Tracks attempted API requests including retries. Stores no keys or portfolios.
Both consuming workflows MUST use the same GitHub Actions concurrency group.
This is a local ceiling, NOT a provider-quota oracle: other clients may use quota.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import intelligence as base

ISSUE_TITLE = 'Gemini API Research Budget (automated; do not edit)'
PACIFIC = ZoneInfo('America/Los_Angeles')
MIN_INTERVAL_SECONDS = 15
_LOCK = threading.Lock()


class GeminiBudgetError(RuntimeError):
    """Sanitized fail-closed budget status, no keys or provider content."""


def check_cap(raw):
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        raise GeminiBudgetError('GEMINI_DAILY_CAP_INVALID') from None
    if str(cap) != str(raw).strip() or not 1 <= cap <= 19:
        raise GeminiBudgetError('GEMINI_DAILY_CAP_INVALID')
    return cap


def next_reservation(body, now, cap):
    """Pure quota state transition. Callers serialize across jobs & threads."""
    if now.tzinfo is None:
        raise GeminiBudgetError('CLOCK_NOT_TIMEZONE_AWARE')
    now = now.astimezone(timezone.utc)
    local_day = now.astimezone(PACIFIC).date().isoformat()
    if body is None:
        body = {'schema': 1, 'date_pt': local_day, 'used': 0, 'last_attempt_utc': None}
    if not isinstance(body, dict) or body.get('schema') != 1:
        raise GeminiBudgetError('BUDGET_STATE_INVALID')
    stored_day = body.get('date_pt')
    if not isinstance(stored_day, str) or not re.fullmatch(r'\d{4}-\d\d-\d\d', stored_day):
        raise GeminiBudgetError('BUDGET_STATE_INVALID')
    count = body.get('used')
    if type(count) is not int or count < 0 or count > 10000 or stored_day > local_day:
        raise GeminiBudgetError('BUDGET_STATE_INVALID')
    if stored_day != local_day:
        count = 0
        last = None
    else:
        last = body.get('last_attempt_utc')
    if count >= cap:
        raise GeminiBudgetError('GEMINI_RESEARCH_DAILY_BUDGET_EXHAUSTED')
    wait = 0.0
    if last:
        if not isinstance(last, str):
            raise GeminiBudgetError('BUDGET_STATE_INVALID')
        try:
            earlier = datetime.fromisoformat(last.replace('Z', '+00:00'))
            if earlier.tzinfo is None:
                raise ValueError('naive')
            elapsed = (now - earlier.astimezone(timezone.utc)).total_seconds()
        except (ValueError, OverflowError, TypeError):
            raise GeminiBudgetError('BUDGET_STATE_INVALID') from None
        if elapsed < -5:
            raise GeminiBudgetError('BUDGET_CLOCK_SKEW')
        wait = max(0.0, MIN_INTERVAL_SECONDS - elapsed)
    next_time = now.timestamp() + wait
    updated = datetime.fromtimestamp(next_time, timezone.utc)
    return wait, {'schema': 1, 'date_pt': local_day, 'used': count + 1,
                  'last_attempt_utc': updated.isoformat(timespec='seconds')}



def has_remaining_capacity():
    """Read-only local quota preflight; never reserves or sends model requests.

    Returns False only when the shared Pacific-day cap is exhausted. Any
    malformed/inaccessible budget state fails closed before other providers
    incur unnecessary calls. This is not a provider quota guarantee.
    """
    cap = check_cap(os.environ.get('GEMINI_DAILY_REQUEST_CAP'))
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise GeminiBudgetError('BUDGET_REPO_INVALID')
    if not os.environ.get('GITHUB_TOKEN'):
        raise GeminiBudgetError('BUDGET_GITHUB_TOKEN_MISSING')
    with _LOCK:
        try:
            issues = base.github(f'/repos/{repo}/issues?state=all&per_page=100')
            found = [x for x in issues if 'pull_request' not in x
                     and x.get('title') == ISSUE_TITLE]
            if len(found) > 1:
                raise GeminiBudgetError('BUDGET_DUPLICATE_ISSUES')
            body = json.loads(found[0].get('body') or '') if found else None
            try:
                next_reservation(body, datetime.now(timezone.utc), cap)
            except GeminiBudgetError as exc:
                if str(exc) == 'GEMINI_RESEARCH_DAILY_BUDGET_EXHAUSTED':
                    return False
                raise
            return True
        except GeminiBudgetError:
            raise
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise GeminiBudgetError('BUDGET_STATE_OR_GITHUB_UNAVAILABLE') from exc


def reserve():
    """Reserve one attempt in the PRIVATE GitHub issue before calling Gemini."""
    cap = check_cap(os.environ.get('GEMINI_DAILY_REQUEST_CAP'))
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise GeminiBudgetError('BUDGET_REPO_INVALID')
    if not os.environ.get('GITHUB_TOKEN'):
        raise GeminiBudgetError('BUDGET_GITHUB_TOKEN_MISSING')
    with _LOCK:
        try:
            issues = base.github(f'/repos/{repo}/issues?state=all&per_page=100')
            found = [x for x in issues if 'pull_request' not in x and x.get('title') == ISSUE_TITLE]
            if len(found) > 1:
                raise GeminiBudgetError('BUDGET_DUPLICATE_ISSUES')
            if found:
                issue_number = found[0]['number']
                payload = json.loads(found[0].get('body') or '')
            else:
                issue_number = None
                payload = None
            wait, state = next_reservation(payload, datetime.now(timezone.utc), cap)
            # First enforce RPM spacing, then persist the reserved request before HTTP.
            # Holding this process lock prevents overlapping Gemini workers within one job.
            if wait:
                time.sleep(wait)
            # Recheck after wait to avoid state dating mismatches at Pacific midnight.
            # In practice waits are < 15 s; the once-a-day reset happens on next call.
            saved = {'body': json.dumps(state, sort_keys=True, separators=(',', ':'))}
            if issue_number is None:
                saved['title'] = ISSUE_TITLE
                base.github(f'/repos/{repo}/issues', 'POST', saved)
            else:
                base.github(f'/repos/{repo}/issues/{issue_number}', 'PATCH', saved)
            print(f'Gemini shared request budget: {state["used"]}/{cap} attempts reserved for {state["date_pt"]} PT')
        except GeminiBudgetError:
            raise
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise GeminiBudgetError('BUDGET_STATE_OR_GITHUB_UNAVAILABLE') from exc
