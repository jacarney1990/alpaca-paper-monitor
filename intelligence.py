"""Independent read-only news and SEC filing monitor. Never places orders."""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

API = 'https://api.github.com'
STATE_TITLE = 'Investment Intelligence State (automated, do not edit)'
SYMBOLS = {
    'NXE': 'NexGen Energy', 'IONQ': 'IonQ', 'OKLO': 'Oklo',
    'ASTS': 'AST SpaceMobile', 'VRT': 'Vertiv', 'RKLB': 'Rocket Lab',
    'VTI': 'Vanguard Total Stock Market ETF',
    'BOTZ': 'Global X Robotics Artificial Intelligence ETF',
    'GRID': 'First Trust Nasdaq Clean Edge Smart Grid',
    'CRSP': 'CRISPR Therapeutics', 'POET': 'POET Technologies',
    'SLDP': 'Solid Power', 'TLRY': 'Tilray Brands',
    'IGC': 'IGC Pharma', 'SERV': 'Serve Robotics',
}
HIGH_SIGNAL = re.compile(
    r'\b(earnings|guidance|offering|dilution|secondary|bankruptcy|going concern|'
    r'FDA|clinical trial|phase [123]|trial results|data readout|approval|rejection|'
    r'contract|launch delay|launch failure|investigation|class action|SEC filing|'
    r'8-K|10-Q|10-K|merger|acquisition|commercialization|financing|recall|'
    r'partnership|production|regulatory|liquidity|restructuring)\b', re.I
)
FORM_SIGNAL = {'8-K', '10-Q', '10-K', 'S-3', 'S-1', '424B5', '424B2', 'SC 13D', '4', '6-K', '20-F'}


def describe_error(exc):
    if isinstance(exc, HTTPError):
        return f'HTTP {exc.code}'
    if isinstance(exc, URLError):
        return f'network error ({type(exc.reason).__name__})'
    return type(exc).__name__


def request_json(url, headers=None, method='GET', payload=None, retries=2):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = Request(url, data=data, method=method, headers={
        'User-Agent': 'InvestmentIntelligence/1.1 contact via configured SEC_USER_AGENT',
        'Accept': 'application/json',
        **({'Content-Type': 'application/json'} if data is not None else {}),
        **(headers or {}),
    })
    for attempt in range(retries + 1):
        try:
            with urlopen(req, timeout=25) as resp:
                return json.load(resp)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except URLError:
            if attempt == retries:
                raise
        time.sleep(1 + attempt * 2)


def request_xml(url):
    req = Request(url, headers={'User-Agent': 'InvestmentIntelligence/1.1'})
    with urlopen(req, timeout=25) as resp:
        return ET.fromstring(resp.read(2_000_000))


def github(repo_path, method='GET', payload=None):
    return request_json(API + repo_path, method=method, payload=payload, headers={
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    })


def load_state(repo):
    issues = github(f'/repos/{repo}/issues?state=open&per_page=100')
    for issue in issues:
        if 'pull_request' not in issue and issue.get('title') == STATE_TITLE:
            state = json.loads(issue.get('body') or '{}')
            if not isinstance(state, dict):
                raise RuntimeError('State issue does not contain a JSON object')
            return issue['number'], state
    return None, {}


def save_state(repo, number, state):
    payload = {'body': json.dumps(state, indent=2, sort_keys=True)}
    if number is None:
        payload['title'] = STATE_TITLE
        github(f'/repos/{repo}/issues', 'POST', payload)
    else:
        github(f'/repos/{repo}/issues/{number}', 'PATCH', payload)


def published_recent(raw, now, days=3):
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return False
        return timedelta(minutes=-10) <= now - dt.astimezone(timezone.utc) <= timedelta(days=days)
    except (TypeError, ValueError, OverflowError):
        return False


def rss_leads(symbol, company, now):
    term = f'"{company}" {symbol} stock when:3d'
    url = 'https://news.google.com/rss/search?' + urlencode({
        'q': term, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en',
    })
    root = request_xml(url)
    leads = []
    for item in root.findall('./channel/item')[:15]:
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        if not (title and link.startswith('https://') and
                published_recent(item.findtext('pubDate'), now)):
            continue
        if not HIGH_SIGNAL.search(title):
            continue
        if symbol.lower() not in title.lower() and company.lower() not in title.lower():
            continue
        leads.append({'id': 'news:' + symbol + ':' + link, 'symbol': symbol,
                      'title': title[:240], 'url': link,
                      'kind': 'NEWS LEAD (verify source)'})
    return leads


def sec_filings(now):
    agent = os.environ.get('SEC_USER_AGENT', '').strip()
    if not agent:
        return [], 'SEC skipped: SEC_USER_AGENT is empty', 0
    headers = {'User-Agent': agent, 'Accept': 'application/json'}
    try:
        tickers = request_json('https://www.sec.gov/files/company_tickers.json', headers)
    except (HTTPError, URLError, ValueError) as exc:
        return [], f'SEC ticker directory unavailable: {describe_error(exc)}; news monitoring continues', 1
    by_ticker = {v['ticker'].upper(): str(v['cik_str']).zfill(10)
                 for v in tickers.values() if 'ticker' in v and 'cik_str' in v}
    filings, failures, checked = [], 0, 0
    for symbol in SYMBOLS:
        cik = by_ticker.get(symbol)
        if not cik:
            continue
        checked += 1
        try:
            data = request_json(f'https://data.sec.gov/submissions/CIK{cik}.json', headers)
            recent = data.get('filings', {}).get('recent', {})
            for form, date, accession, doc in list(zip(
                recent.get('form', []), recent.get('filingDate', []),
                recent.get('accessionNumber', []), recent.get('primaryDocument', [])
            ))[:35]:
                if form not in FORM_SIGNAL:
                    continue
                try:
                    age = now.date() - datetime.fromisoformat(date).date()
                except ValueError:
                    continue
                if not timedelta(0) <= age <= timedelta(days=3):
                    continue
                path = accession.replace('-', '')
                filing_url = (f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/'
                              f'{path}/{quote(doc, safe="")}')
                filings.append({'id': f'sec:{symbol}:{accession}:{form}',
                                'symbol': symbol,
                                'title': f'{symbol} filed SEC Form {form} ({date})',
                                'url': filing_url,
                                'kind': 'OFFICIAL SEC FILING (review contents)'})
        except (HTTPError, URLError, ValueError, KeyError) as exc:
            failures += 1
            print(f'SEC submission lookup for {symbol}: {describe_error(exc)}')
        time.sleep(0.15)
    return filings, f'SEC: checked {checked} companies; {failures} failed; {len(filings)} recent filings', failures


def send_email(subject, body):
    sender = os.environ.get('ALERT_EMAIL_FROM', '').strip() or 'Alpaca Monitor <onboarding@resend.dev>'
    result = request_json('https://api.resend.com/emails', method='POST', retries=0,
        headers={'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY']},
        payload={'from': sender, 'to': [os.environ['ALERT_EMAIL_TO']],
                 'subject': subject, 'text': body})
    if not result.get('id'):
        raise RuntimeError('Resend did not return an email ID')
    return result['id']


def compose(items, now):
    lines = ['Independent read-only investment intelligence monitor',
             f'Checked: {now.isoformat(timespec="minutes")}', '',
             'NEW leads and filings (review only, not trading instructions):', '']
    for item in items:
        lines.extend([f"{item['symbol']} | {item['kind']}", item['title'], item['url'], ''])
    lines.extend(['News headlines are unverified. Verify original sources before acting.',
                  'SEC filing presence alone is not a buy/sell signal.',
                  'No portfolio balances or price triggers were evaluated.',
                  'No orders were placed.'])
    return '\n'.join(lines)


def main():
    required = ['GITHUB_TOKEN', 'GITHUB_REPOSITORY', 'RESEND_API_KEY', 'ALERT_EMAIL_TO']
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError('Missing settings: ' + ', '.join(missing))
    now = datetime.now(timezone.utc)
    repo = os.environ['GITHUB_REPOSITORY']
    number, old = load_state(repo)
    seen = old.get('seen', {})
    if not isinstance(seen, dict):
        raise RuntimeError('Invalid seen-state')

    leads, news_failures = [], []
    for symbol, company in SYMBOLS.items():
        try:
            leads.extend(rss_leads(symbol, company, now))
        except (HTTPError, URLError, ET.ParseError, ValueError) as exc:
            news_failures.append(symbol)
            print(f'News lookup for {symbol}: {describe_error(exc)}')
    if len(news_failures) == len(SYMBOLS):
        raise RuntimeError('All news sources failed; state not updated')

    filings, sec_status, sec_failures = sec_filings(now)
    leads.extend(filings)
    print(sec_status)
    print(f'News: {len(SYMBOLS) - len(news_failures)}/{len(SYMBOLS)} ticker queries succeeded; '
          f'{len(leads)} total leads including filings')

    fresh = [item for item in leads if item['id'] not in seen]
    cutoff = (now - timedelta(days=14)).isoformat()
    updated = {k: v for k, v in seen.items() if isinstance(v, str) and v >= cutoff}
    for item in leads:
        updated[item['id']] = now.isoformat()
    updated = dict(list(updated.items())[-800:])

    if number is None:
        print(f'Baseline created: {len(leads)} leads; no initial email.')
    elif fresh:
        batch = fresh[:20]
        email_id = send_email('Investment intelligence: ' +
            ', '.join(dict.fromkeys(item['symbol'] for item in batch)), compose(batch, now))
        print(f'Sent intelligence email: {len(batch)} items; id present: {bool(email_id)}')
        for item in fresh[20:]:
            updated.pop(item['id'], None)
    else:
        print('No new high-signal news or SEC filings.')

    save_state(repo, number, {'seen': updated, 'last_success': now.isoformat(),
                              'last_sources_failed': len(news_failures) + sec_failures})
    if news_failures:
        print(f'Partial news failures: {len(news_failures)}')


if __name__ == '__main__':
    try:
        main()
    except (HTTPError, URLError, RuntimeError, ValueError, KeyError) as exc:
        print(f'Intelligence monitor failed safely: {describe_error(exc)}', file=sys.stderr)
        sys.exit(1)
