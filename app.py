"""Read-only Alpaca PAPER dashboard. No order routes or trading SDK."""
import hmac
import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
PAPER_BASE = 'https://paper-api.alpaca.markets'
DATA_BASE = 'https://data.alpaca.markets'
WATCHLIST = {
    'NXE': ('at_or_below', 8.50), 'IONQ': ('at_or_below', 40.00),
    'OKLO': ('range', 30.00, 32.00), 'ASTS': ('range', 52.00, 55.00),
    'VRT': ('range', 225.00, 240.00), 'RKLB': ('at_or_below', 67.00),
    'VTI': ('range', 365.00, 381.00), 'BOTZ': ('at_or_below', 33.00),
    'GRID': ('at_or_below', 170.00), 'CRSP': ('hold',),
    'POET': ('review_below', 7.00), 'SLDP': ('hold',),
    'TLRY': ('hold',), 'IGC': ('hold',), 'SERV': ('hold',),
}

PAGE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper Monitor | Read Only</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}body{background:#0c1220;color:#e8eef8;max-width:860px;margin:auto;padding:16px}h1{font-size:1.6rem;margin-bottom:4px}h2{font-size:1.1rem;margin-top:25px}.muted{color:#a9b8cc}.notice{background:#17243a;border-left:4px solid #7bc7ff;padding:12px;border-radius:6px;margin:14px 0}.panel{background:#141f30;border:1px solid #304058;border-radius:12px;padding:15px;margin:12px 0}label{display:block;margin-bottom:6px}input{background:#0b1526;color:#fff;border:1px solid #66778f;border-radius:7px;padding:12px;box-sizing:border-box;width:100%;font-size:16px}button{background:#80d5f9;color:#07111b;border:0;border-radius:8px;padding:11px 14px;font-weight:700;margin:10px 8px 0 0;cursor:pointer}button:disabled{opacity:.55}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.tile{background:#0d1828;padding:11px;border-radius:8px}.tile strong{display:block;font-size:1.2rem;margin-top:5px}.stock{padding:13px 0;border-bottom:1px solid #34425a}.stock:last-child{border:0}.stockhead{display:flex;justify-content:space-between;gap:10px}.symbol{font-weight:800}.price{font-weight:800}.state{font-size:.83rem;font-weight:700;letter-spacing:.04em;margin:5px 0}.ok{color:#91e2af}.review{color:#fbd07e}.dim{color:#a9b8cc}.error{color:#ffb1b1;white-space:pre-wrap}.small{font-size:.85rem}code{overflow-wrap:anywhere}</style></head><body>
<h1>Alpaca Paper Monitor</h1><div class="muted">Read-only backup / advisory dashboard</div>
<div class="notice"><strong>NO TRADING.</strong> This dashboard reads an Alpaca <strong>simulated paper account</strong>, NOT Robinhood Agentic account 3994. Claude must independently verify Robinhood holdings, open orders and buying power before any real execution. Market-data quotes can be delayed or stale.</div>
<div class="panel"><label for="token">Private dashboard access token</label><input id="token" type="password" autocomplete="off" placeholder="Enter your dashboard token"><div class="small muted">Token is kept only in this page's memory and sent as a request header. It is not saved in browser storage.</div><button id="accountBtn" onclick="loadAccount()">Check paper account</button><button id="watchBtn" onclick="loadWatchlist()">Check watchlist</button><div id="message" class="small" role="status" aria-live="polite"></div></div>
<h2>Paper account</h2><div id="account" class="panel muted">Press “Check paper account.”</div>
<h2>Watchlist review</h2><div id="watchlist" class="panel muted">Press “Check watchlist.” No automated orders or buy signals.</div>
<script>
const $=id=>document.getElementById(id);
function safe(v){return String(v===null||v===undefined?'—':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function usd(v){if(v===null||v===undefined||v==='')return 'Unavailable';let n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}):'Unavailable';}
function message(s,bad=false){$('message').className='small '+(bad?'error':'muted');$('message').textContent=s;}
async function get(path){const token=$('token').value;if(!token)throw Error('Enter your private dashboard token first.');let r=await fetch(path,{headers:{'X-Dashboard-Token':token},cache:'no-store'});let data;try{data=await r.json()}catch{throw Error('Server returned an invalid response (HTTP '+r.status+').')}if(!r.ok)throw Error((data.error||'Request failed')+' (HTTP '+r.status+')');return data;}
async function loadAccount(){let b=$('accountBtn');b.disabled=true;message('Checking paper account…');try{let a=await get('/account');$('account').innerHTML='<div class="grid">'+[['Status',a.status],['Paper portfolio',usd(a.portfolio_value)],['Paper cash',usd(a.cash)],['Paper buying power (may include simulated margin)',usd(a.buying_power)],['Account ending',a.account_number_last4],['Mode','PAPER ONLY']].map(x=>'<div class="tile">'+safe(x[0])+'<strong>'+safe(x[1])+'</strong></div>').join('')+'</div>';message('Paper account connected. No trades placed.')}catch(e){message(e.message,true)}finally{b.disabled=false}}
async function loadWatchlist(){let b=$('watchBtn');b.disabled=true;message('Fetching Alpaca market data…');try{let d=await get('/watchlist');let warning=d.warning?'<p class="review">'+safe(d.warning)+'</p>':'';let meta='<p class="small muted">Source: '+safe(d.feed||'IEX')+' · Checked: '+safe(d.checked_at||'—')+' · Prices are informational, not executable quotes.</p>';let rows=d.items.map(i=>{let cls=i.state==='IN RANGE'||i.state==='AT/BELOW'||i.state==='REVIEW LEVEL'?'ok':i.state==='UNAVAILABLE'?'error':'review';return '<div class="stock"><div class="stockhead"><span class="symbol">'+safe(i.symbol)+'</span><span class="price">'+usd(i.price)+'</span></div><div class="state '+cls+'">'+safe(i.state)+'</div><div class="small muted">'+safe(i.rule)+' · '+safe(i.source||'No quote')+' · '+safe(i.timestamp||'Timestamp unavailable')+'</div>'+(i.note?'<div class="small">'+safe(i.note)+'</div>':'')+'</div>'}).join('');$('watchlist').innerHTML=warning+meta+rows;message(d.available_count+' of '+d.items.length+' prices available. No trades placed.',d.available_count===0)}catch(e){message(e.message,true)}finally{b.disabled=false}}
</script></body></html>'''

@app.after_request
def secure_headers(response):
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    return response


def authorized():
    expected = os.environ.get('DASHBOARD_ACCESS_TOKEN', '')
    supplied = request.headers.get('X-Dashboard-Token', '')
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def api_headers():
    key = os.environ.get('ALPACA_PAPER_API_KEY', '')
    secret = os.environ.get('ALPACA_PAPER_API_SECRET', '')
    if not key or not secret:
        return None
    return {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}


def api_error(response, label):
    # Do not echo upstream bodies; they may contain identifying data.
    status = response.status_code
    if status in (401, 403):
        return f'{label}: Alpaca rejected credentials or market-data access (HTTP {status}).'
    if status == 429:
        return f'{label}: Alpaca rate limit reached (HTTP 429). Try later.'
    return f'{label}: Alpaca returned HTTP {status}.'


def request_json(url, headers, params=None):
    response = requests.get(url, headers=headers, params=params, timeout=15)
    if not response.ok:
        raise ValueError(api_error(response, 'Request'))
    try:
        result = response.json()
    except ValueError:
        raise ValueError('Alpaca returned invalid JSON.') from None
    if not isinstance(result, dict):
        raise ValueError('Alpaca returned an unexpected response format.')
    return result


def numeric(value):
    try:
        result = float(value)
        if result != result or result in (float('inf'), float('-inf')):
            return None
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def pick_price(snapshot):
    """Prefer latest trade; use latest minute bar or daily bar with explicit source labels."""
    if not isinstance(snapshot, dict):
        return None, None, None
    for field, key, label in (
        ('latestTrade', 'p', 'Latest trade'),
        ('minuteBar', 'c', 'Latest minute-bar close'),
        ('dailyBar', 'c', 'Daily-bar close'),
        ('prevDailyBar', 'c', 'Previous daily-bar close'),
    ):
        entry = snapshot.get(field)
        if isinstance(entry, dict):
            price = numeric(entry.get(key))
            if price is not None and price > 0:
                return price, entry.get('t'), label
    return None, None, None


def rule_for(symbol, price):
    rule = WATCHLIST[symbol]
    kind = rule[0]
    if kind == 'hold':
        notes = {
            'CRSP': 'No add before Nov 8, 2026 zugo-cel data; reassess thesis and <= $52.50 afterward.',
            'SLDP': 'Wait for meaningful commercialization milestone; then review <= $2.10.',
            'TLRY': 'Oct 8 earnings occurred; new thesis review and buy boundary required.',
            'IGC': 'No add before CALMA Phase 2 results.',
            'SERV': 'No add.',
        }
        return 'REVIEW ONLY', 'Hold / catalyst restriction', notes.get(symbol, 'No automatic buy signal.')
    if kind == 'range':
        low, high = rule[1:]
        state = 'UNAVAILABLE' if price is None else ('REVIEW LOW' if price < low else 'IN RANGE' if price <= high else 'ABOVE RANGE')
        return state, f'Review range ${low:g}–${high:g}', 'Review only; no automatic trade.'
    threshold = rule[1]
    if kind == 'review_below':
        state = 'UNAVAILABLE' if price is None else ('REVIEW LEVEL' if price <= threshold else 'ABOVE REVIEW LEVEL')
        note = 'Stronger POET review <= $6.50; not an automatic buy.' if symbol == 'POET' else 'Review only.'
        return state, f'Review at/below ${threshold:g}', note
    state = 'UNAVAILABLE' if price is None else ('AT/BELOW' if price <= threshold else 'ABOVE')
    note = 'Stronger review <= $36; assess financing/dilution.' if symbol == 'IONQ' else 'Review only; not an order.'
    return state, f'Review at/below ${threshold:g}', note


@app.get('/')
def home():
    return render_template_string(PAGE)


@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'mode': 'paper', 'order_submission': False})


@app.get('/account')
def account():
    if not authorized():
        return jsonify({'error': 'unauthorized'}), 401
    headers = api_headers()
    if headers is None:
        return jsonify({'error': 'Paper credentials not configured in Render.'}), 503
    try:
        data = request_json(PAPER_BASE + '/v2/account', headers)
    except (requests.RequestException, ValueError) as exc:
        app.logger.warning('Paper account lookup failed: %s', type(exc).__name__)
        return jsonify({'error': 'Paper account request failed; check credentials and Render logs.'}), 502
    return jsonify({'mode': 'PAPER ONLY', 'order_submission': False,
                    'status': data.get('status'), 'currency': data.get('currency'),
                    'account_number_last4': str(data.get('account_number') or '')[-4:],
                    'portfolio_value': data.get('portfolio_value'),
                    'cash': data.get('cash'), 'buying_power': data.get('buying_power')})


@app.get('/watchlist')
def watchlist():
    if not authorized():
        return jsonify({'error': 'unauthorized'}), 401
    headers = api_headers()
    if headers is None:
        return jsonify({'error': 'Paper credentials not configured in Render.'}), 503
    checked_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    try:
        data = request_json(DATA_BASE + '/v2/stocks/snapshots', headers,
                            params={'symbols': ','.join(WATCHLIST), 'feed': 'iex'})
    except requests.Timeout:
        return jsonify({'error': 'Alpaca market-data request timed out.'}), 504
    except requests.RequestException:
        return jsonify({'error': 'Could not connect to Alpaca market data.'}), 502
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 502

    snapshots = data.get('snapshots')
    if not isinstance(snapshots, dict):
        return jsonify({'error': 'Alpaca response did not contain a valid snapshots object.'}), 502
    items = []
    for symbol in WATCHLIST:
        price, timestamp, source = pick_price(snapshots.get(symbol))
        state, rule, note = rule_for(symbol, price)
        items.append({'symbol': symbol, 'price': price, 'timestamp': timestamp,
                      'source': source, 'state': state, 'rule': rule, 'note': note})
    available_count = sum(item['price'] is not None for item in items)
    warning = None
    if available_count == 0:
        warning = ('Alpaca returned no usable trade/bar prices for these symbols. '
                   'IEX data may be unavailable for this request. No live quote is implied.')
    elif available_count < len(items):
        warning = f'{len(items) - available_count} symbols have no usable IEX trade/bar price.'
    return jsonify({'mode': 'PAPER ONLY', 'order_submission': False, 'feed': 'IEX',
                    'checked_at': checked_at, 'available_count': available_count,
                    'warning': warning, 'items': items})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '10000')))
