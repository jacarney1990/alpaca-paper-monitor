"""Independent, read-only news/SEC/catalyst monitor. No brokerage order methods."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import xml.etree.ElementTree as ET

API = 'https://api.github.com'
STATE_TITLE = 'Investment Intelligence State (automated, do not edit)'
SYMBOLS = {
 'NXE':'NexGen Energy', 'IONQ':'IonQ', 'OKLO':'Oklo', 'ASTS':'AST SpaceMobile',
 'VRT':'Vertiv', 'RKLB':'Rocket Lab', 'VTI':'Vanguard Total Stock Market ETF',
 'BOTZ':'Global X Robotics Artificial Intelligence ETF', 'GRID':'First Trust Nasdaq Clean Edge Smart Grid',
 'CRSP':'CRISPR Therapeutics', 'POET':'POET Technologies', 'SLDP':'Solid Power',
 'TLRY':'Tilray Brands', 'IGC':'IGC Pharma', 'SERV':'Serve Robotics',
}
# Review only. Headline matching is a lead for human verification, not a confirmed event.
HIGH_SIGNAL = re.compile(r'\b(earnings|guidance|offering|dilution|secondary|bankruptcy|going concern|'
    r'FDA|clinical trial|phase [123]|trial results|data readout|approval|rejection|'
    r'contract|launch delay|launch failure|investigation|class action|SEC filing|'
    r'8-K|10-Q|10-K|merger|acquisition|commercialization|financing|recall|'
    r'partnership|production|regulatory|liquidity|restructuring)\b', re.I)
FORM_SIGNAL = {'8-K', '10-Q', '10-K', 'S-3', 'S-1', '424B5', '424B2', 'SC 13D', '4', '6-K', '20-F'}


def get_json(url, headers=None):
    req = Request(url, headers={'User-Agent':'alpaca-intelligence-monitor/1.0',
                                'Accept':'application/json', **(headers or {})})
    with urlopen(req, timeout=25) as resp:
        return json.load(resp)


def get_xml(url):
    req = Request(url, headers={'User-Agent':'alpaca-intelligence-monitor/1.0'})
    with urlopen(req, timeout=25) as resp:
        data = resp.read(2_000_000)
    return ET.fromstring(data)


def github(path, method='GET', payload=None):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = Request(API + path, data=data, method=method, headers={
        'Authorization':'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28',
        'User-Agent':'alpaca-intelligence-monitor/1.0',
        **({'Content-Type':'application/json'} if data is not None else {})})
    with urlopen(req, timeout=25) as resp:
        return json.load(resp)


def load_state(repo):
    issues = github(f'/repos/{repo}/issues?state=open&per_page=100')
    for issue in issues:
        if 'pull_request' not in issue and issue.get('title') == STATE_TITLE:
            state = json.loads(issue.get('body') or '{}')
            if not isinstance(state, dict):
                raise RuntimeError('State issue is not a JSON object')
            return issue['number'], state
    return None, {}


def save_state(repo, number, state):
    body = json.dumps(state, indent=2, sort_keys=True)
    if number is None:
        github(f'/repos/{repo}/issues', 'POST', {'title':STATE_TITLE, 'body':body})
    else:
        github(f'/repos/{repo}/issues/{number}', 'PATCH', {'body':body})


def published_recent(raw, now, days=3):
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            return False
        return timedelta(minutes=-10) <= now - dt.astimezone(timezone.utc) <= timedelta(days=days)
    except (TypeError, ValueError, OverflowError):
        return False


def rss_leads(symbol, company, now):
    # Restrict search to company name + stock symbol. Google News RSS is a discovery source,
    # NOT a verified primary-source announcement.
    term = f'"{company}" {symbol} stock'
    url = 'https://news.google.com/rss/search?' + urlencode({'q':term+' when:3d', 'hl':'en-US','gl':'US','ceid':'US:en'})
    root = get_xml(url)
    leads = []
    for item in root.findall('./channel/item')[:15]:
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        date = item.findtext('pubDate')
        if not (title and link.startswith('https://') and published_recent(date, now)):
            continue
        if not HIGH_SIGNAL.search(title):
            continue
        # Require company name or ticker to reduce irrelevant hits.
        if symbol.lower() not in title.lower() and company.lower() not in title.lower():
            continue
        leads.append({'id':'news:'+symbol+':'+link, 'symbol':symbol,
                      'title':title[:240], 'url':link, 'kind':'NEWS LEAD (verify source)'})
    return leads


def sec_filings(now):
    """Optional primary-source SEC submissions; gracefully skipped without SEC_USER_AGENT."""
    agent = os.environ.get('SEC_USER_AGENT','').strip()
    if not agent:
        return [], 'SEC lookup skipped: set SEC_USER_AGENT for primary-source filings.'
    headers = {'User-Agent':agent, 'Accept':'application/json'}
    tickers = get_json('https://www.sec.gov/files/company_tickers.json', headers)
    by_ticker = {v['ticker'].upper():str(v['cik_str']).zfill(10) for v in tickers.values()}
    filings = []
    for symbol in SYMBOLS:
        cik = by_ticker.get(symbol)
        if not cik:
            continue
        try:
            data = get_json(f'https://data.sec.gov/submissions/CIK{cik}.json', headers)
            recent = data.get('filings',{}).get('recent',{})
            forms = recent.get('form',[])
            dates = recent.get('filingDate',[])
            accessions = recent.get('accessionNumber',[])
            docs = recent.get('primaryDocument',[])
            for form, date, accession, doc in list(zip(forms, dates, accessions, docs))[:35]:
                if form not in FORM_SIGNAL:
                    continue
                try:
                    age = now.date() - datetime.fromisoformat(date).date()
                except ValueError:
                    continue
                if not timedelta(days=0) <= age <= timedelta(days=3):
                    continue
                path = accession.replace('-','')
                url = f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{path}/{quote(doc, safe="")}'
                filings.append({'id':f'sec:{symbol}:{accession}:{form}', 'symbol':symbol,
                    'title':f'{symbol} filed SEC Form {form} ({date})', 'url':url,
                    'kind':'OFFICIAL SEC FILING (read contents before conclusions)'})
        except (HTTPError, URLError, ValueError, KeyError) as exc:
            print(f'SEC lookup failed for {symbol}: {type(exc).__name__}')
    return filings, None


def send_email(subject, body):
    payload = json.dumps({'from':os.environ.get('ALERT_EMAIL_FROM','Alpaca Monitor <onboarding@resend.dev>'),
                          'to':[os.environ['ALERT_EMAIL_TO']], 'subject':subject, 'text':body}).encode()
    req = Request('https://api.resend.com/emails', data=payload, method='POST', headers={
        'Authorization':'Bearer '+os.environ['RESEND_API_KEY'],
        'Content-Type':'application/json', 'User-Agent':'alpaca-intelligence-monitor/1.0'})
    with urlopen(req, timeout=25) as resp:
        result = json.load(resp)
    if not result.get('id'):
        raise RuntimeError('Resend did not return an email ID')
    return result['id']


def compose(items, now):
    lines = ['Independent read-only investment intelligence monitor',
             f'Checked: {now.isoformat(timespec="minutes")}', '',
             'NEW leads and filings (review, not trading instructions):', '']
    for item in items:
        lines.extend([f"{item['symbol']} | {item['kind']}", item['title'], item['url'], ''])
    lines.extend(['News headlines are unverified leads. Open the original source and verify before acting.',
      'SEC form presence does not establish whether a filing is favorable or unfavorable.',
      'Price thresholds, portfolio balances and trade execution are NOT evaluated in this email.',
      'No orders were placed.'])
    return '\n'.join(lines)


def main():
    required = ['GITHUB_TOKEN','GITHUB_REPOSITORY','RESEND_API_KEY','ALERT_EMAIL_TO']
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError('Missing settings: '+', '.join(missing))
    now = datetime.now(timezone.utc)
    repo = os.environ['GITHUB_REPOSITORY']
    number, old = load_state(repo)
    seen = old.get('seen', {})
    if not isinstance(seen, dict):
        raise RuntimeError('Invalid seen-state')
    leads, failures = [], []
    for symbol, company in SYMBOLS.items():
        try:
            leads.extend(rss_leads(symbol, company, now))
        except (HTTPError, URLError, ET.ParseError, ValueError) as exc:
            failures.append(f'{symbol}: {type(exc).__name__}')
    try:
        filings, sec_note = sec_filings(now)
        leads.extend(filings)
        if sec_note:
            print(sec_note)
    except (HTTPError, URLError, ValueError) as exc:
        failures.append(f'SEC: {type(exc).__name__}')
    if len(failures) >= len(SYMBOLS):
        raise RuntimeError('All news sources failed; refusing to update state')
    # First run creates a baseline, preventing a flood of old headlines.
    fresh_items = [x for x in leads if x['id'] not in seen]
    first_run = number is None
    # Retain 14 days of state and avoid growing the GitHub issue indefinitely.
    cutoff = (now - timedelta(days=14)).isoformat()
    updated = {k:v for k,v in seen.items() if isinstance(v,str) and v >= cutoff}
    for item in leads:
        updated[item['id']] = now.isoformat()
    updated = dict(list(updated.items())[-800:])
    if first_run:
        print(f'Baseline created: {len(leads)} current leads. No initial email.')
    elif fresh_items:
        # Cap message volume, keep remainder unseen for a later run rather than silently discarding.
        batch = fresh_items[:20]
        email_id = send_email('Investment intelligence: '+', '.join(dict.fromkeys(x['symbol'] for x in batch)), compose(batch, now))
        print(f'Sent intelligence email ({len(batch)} items; id present: {bool(email_id)}).')
        # Only mark successfully emailed new items; avoid losing backlog >20.
        for item in fresh_items[20:]:
            updated.pop(item['id'], None)
    else:
        print('No new high-signal news or SEC filings.')
    save_state(repo, number, {'seen':updated, 'last_success':now.isoformat(),
                              'last_sources_failed':len(failures)})
    if failures:
        print(f'Partial source failures: {len(failures)}. See logs; no secrets included.')

if __name__ == '__main__':
    try:
        main()
    except (HTTPError, URLError, RuntimeError, ValueError, KeyError) as exc:
        print(f'Intelligence monitor failed safely: {type(exc).__name__}', file=sys.stderr)
        sys.exit(1)
