"""Read-only scheduled Alpaca advisory alerts. Never submits orders."""
import json
import os
import sys
from datetime import datetime, timezone, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

API = 'https://api.github.com'
STATE_TITLE = 'Alpaca Monitor State (automated, do not edit)'
BASE = os.environ.get('MONITOR_URL', 'https://alpaca-paper-monitor-zgv9.onrender.com').rstrip('/')


def call(url, method='GET', headers=None, payload=None, timeout=85):
    body = json.dumps(payload).encode() if payload is not None else None
    hdr = {'Accept': 'application/json', 'User-Agent': 'alpaca-readonly-alerts/1.0', **(headers or {})}
    if body is not None:
        hdr['Content-Type'] = 'application/json'
    req = Request(url, data=body, headers=hdr, method=method)
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


def github(path, method='GET', payload=None):
    return call(API + path, method, {
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }, payload, timeout=25)


def find_state(repo):
    issues = github(f'/repos/{repo}/issues?state=open&per_page=100')
    for issue in issues:
        if 'pull_request' not in issue and issue.get('title') == STATE_TITLE:
            try:
                return issue['number'], json.loads(issue.get('body') or '{}')
            except (ValueError, TypeError):
                raise RuntimeError('Monitor state issue contains invalid JSON. Fix before sending alerts.')
    return None, {}


def save_state(repo, number, state):
    body = json.dumps(state, indent=2, sort_keys=True)
    if number is None:
        github(f'/repos/{repo}/issues', 'POST', {'title': STATE_TITLE, 'body': body})
    else:
        github(f'/repos/{repo}/issues/{number}', 'PATCH', {'body': body})


def fresh(ts, now):
    if not isinstance(ts, str):
        return False
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return False
        age = (now - dt.astimezone(timezone.utc)).total_seconds()
        return -300 <= age <= 3 * 3600
    except ValueError:
        return False


def classify(item):
    symbol = item.get('symbol')
    state = item.get('state')
    price = item.get('price')
    if not isinstance(price, (float, int)) or price <= 0:
        return None
    if state in ('AT/BELOW', 'IN RANGE', 'REVIEW LEVEL'):
        if symbol == 'IONQ' and price <= 36:
            return 'STRONG REVIEW'
        if symbol == 'POET' and price <= 6.5:
            return 'STRONG REVIEW'
        return 'PRICE REVIEW'
    if state == 'REVIEW LOW':
        return 'REVIEW LOW, NOT BUY RANGE'
    return None


def market_session(now):
    ny = now.astimezone(ZoneInfo('America/New_York'))
    return ny.weekday() < 5 and time(9, 30) <= ny.time() < time(16, 0)


def main():
    required = ['DASHBOARD_ACCESS_TOKEN', 'RESEND_API_KEY', 'ALERT_EMAIL_TO', 'GITHUB_TOKEN', 'GITHUB_REPOSITORY']
    absent = [key for key in required if not os.environ.get(key)]
    if absent:
        raise RuntimeError('Missing configuration: ' + ', '.join(absent))
    now = datetime.now(timezone.utc)
    if not market_session(now):
        print('Outside US regular market session; no alerts.')
        return

    data = call(BASE + '/watchlist', headers={'X-Dashboard-Token': os.environ['DASHBOARD_ACCESS_TOKEN']})
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise RuntimeError('Unexpected dashboard watchlist response')
    if data.get('available_count', 0) == 0:
        raise RuntimeError('No available prices; refusing to alert on stale or missing data')

    repo = os.environ['GITHUB_REPOSITORY']
    number, old = find_state(repo)
    if not isinstance(old, dict):
        raise RuntimeError('Unexpected saved monitor state')
    new = dict(old)
    alerts = []
    for item in data['items']:
        symbol = item.get('symbol')
        if not isinstance(symbol, str):
            continue
        # Don't clear state on stale/missing data, so outages don't cause repeat alerts.
        if not fresh(item.get('timestamp'), now):
            continue
        classification = classify(item)
        previous = old.get(symbol)
        if classification is None:
            new.pop(symbol, None)
        else:
            new[symbol] = classification
            if previous != classification:
                alerts.append((symbol, item['price'], classification, item.get('source', 'IEX'), item['timestamp']))

    if alerts:
        lines = [f'{sym}: ${price:.2f} | {level} | {source} | quote {ts}' for sym, price, level, source, ts in alerts]
        body = ('Alpaca IEX read-only advisory monitor detected NEW review states:\n\n'
                + '\n'.join(lines)
                + '\n\nREVIEW ONLY. Not an instruction to buy. REVIEW LOW is outside the approved buy range. '
                'Claude must re-read Robinhood Agentic account 3994 buying power, holdings, and open orders immediately before any execution. '
                'Keep at least 40% of Agentic buying power in cash, no margin, one new whole-share limit order at a time, no duplicate orders. '
                'Alpaca paper balances are simulated and are NOT Robinhood balances. No orders were placed.\n\n'
                'Dashboard: ' + BASE)
        call('https://api.resend.com/emails', 'POST', {
            'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'],
        }, {
            'from': 'Alpaca Monitor <onboarding@resend.dev>',
            'to': [os.environ['ALERT_EMAIL_TO']],
            'subject': 'Alpaca advisory: ' + ', '.join(a[0] for a in alerts),
            'text': body,
        }, timeout=25)
        print(f'Sent one advisory email covering {len(alerts)} new states.')
    else:
        print('No new fresh threshold crossings.')
    if new != old or number is None:
        save_state(repo, number, new)
        print('Monitor state saved in private GitHub issue.')


if __name__ == '__main__':
    try:
        main()
    except (HTTPError, URLError, RuntimeError, ValueError, KeyError) as exc:
        # Do not print URLs, request bodies, or credentials to CI logs.
        print(f'Monitor failed safely: {type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
