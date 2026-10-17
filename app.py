import hmac
import os

import requests
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)
PAPER_BASE = "https://paper-api.alpaca.markets"

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alpaca Paper Monitor</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}
body{margin:0;background:#0b1220;color:#f1f5f9;padding:24px 16px}
main{max-width:560px;margin:24px auto}h1{font-size:1.65rem;margin:0 0 8px}
p{color:#a8b6ca;line-height:1.5}.panel{background:#172236;border:1px solid #31415a;border-radius:16px;padding:20px;margin-top:18px}
.flags{display:flex;flex-wrap:wrap;gap:8px}.flag{font-size:.78rem;font-weight:700;border:1px solid #2f6f58;background:#133d31;color:#b7f7d4;padding:7px 10px;border-radius:999px}
label{display:block;margin:0 0 8px;font-weight:600}input{box-sizing:border-box;width:100%;background:#0b1220;border:1px solid #64748b;border-radius:10px;color:white;padding:14px;font-size:1rem}
button{background:#7dd3fc;color:#08101c;border:0;border-radius:10px;padding:13px 18px;font-weight:800;margin-top:12px;width:100%;font-size:1rem;cursor:pointer}
button:disabled{opacity:.55;cursor:wait}.error{color:#fecaca}.good{color:#bbf7d0}.metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px}
.metric{background:#0b1220;border-radius:10px;padding:12px;overflow-wrap:anywhere}.metric small{display:block;color:#94a3b8;margin-bottom:6px}.metric strong{font-size:1.12rem}
footer{font-size:.85rem;color:#94a3b8;margin-top:20px}
</style></head><body><main>
<h1>Alpaca Paper Monitor</h1><p>Private, read-only account check. No orders can be placed through this app.</p>
<div class="flags"><span class="flag">PAPER ONLY</span><span class="flag">LIVE TRADING DISABLED</span><span class="flag">ORDERS DISABLED</span></div>
<section class="panel"><form id="check-form" autocomplete="off">
<label for="token">Dashboard access token</label>
<input id="token" type="password" autocomplete="off" spellcheck="false" required placeholder="Enter your private token">
<button id="check-button" type="submit">Check paper account</button></form>
<p id="message" role="status" aria-live="polite">Your token is sent in a request header, never in the URL.</p>
<div id="result" hidden><div class="metrics" id="metrics"></div></div></section>
<footer>Balances are from Alpaca's paper environment, not your Robinhood account. Do not share your access token or API credentials.</footer>
</main><script>
const form=document.getElementById('check-form');
const button=document.getElementById('check-button');
const message=document.getElementById('message');
const result=document.getElementById('result');
const metrics=document.getElementById('metrics');
const money=(v,c)=>{const n=Number(v);return v===null||v===undefined||v===''||!Number.isFinite(n)?'Unavailable':new Intl.NumberFormat('en-US',{style:'currency',currency:c==='USD'?'USD':'USD'}).format(n)};
form.addEventListener('submit',async(e)=>{
 e.preventDefault();const token=document.getElementById('token').value;
 button.disabled=true;message.className='';message.textContent='Checking paper account…';result.hidden=true;metrics.replaceChildren();
 try{
  const response=await fetch('/account',{method:'GET',headers:{'X-Dashboard-Token':token},cache:'no-store',credentials:'omit'});
  const data=await response.json();
  if(!response.ok){message.className='error';message.textContent=response.status===401?'Access denied. Check your dashboard token.':(data.error||'Account check failed.');return;}
  message.className='good';message.textContent='Connected to Alpaca paper account.';
  const values=[['Account status',data.status||'Unavailable'],['Account ending',data.account_number_last4||'Unavailable'],['Portfolio value',money(data.portfolio_value,data.currency)],['Cash',money(data.cash,data.currency)],['Buying power',money(data.buying_power,data.currency)],['Currency',data.currency||'Unavailable']];
  for(const [name,value] of values){const item=document.createElement('div');item.className='metric';const label=document.createElement('small');label.textContent=name;const text=document.createElement('strong');text.textContent=value;item.append(label,text);metrics.append(item)}
  result.hidden=false;
 }catch(err){message.className='error';message.textContent='Network error. Try again after Render finishes waking up.'}
 finally{button.disabled=false;document.getElementById('token').value=''}
});
</script></body></html>"""

@app.after_request
def secure_headers(response):
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    return response

@app.get('/')
def home():
    return render_template_string(PAGE)

@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'mode': 'paper', 'order_submission': False})

@app.get('/account')
def account():
    expected = os.environ.get('DASHBOARD_ACCESS_TOKEN', '')
    supplied = request.headers.get('X-Dashboard-Token', '')
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        return jsonify({'error': 'unauthorized'}), 401
    key = os.environ.get('ALPACA_PAPER_API_KEY', '')
    secret = os.environ.get('ALPACA_PAPER_API_SECRET', '')
    if not key or not secret:
        return jsonify({'error': 'paper credentials not configured'}), 503
    try:
        response = requests.get(
            PAPER_BASE + '/v2/account',
            headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return jsonify({
            'mode': 'PAPER ONLY', 'order_submission': False,
            'status': data.get('status'), 'currency': data.get('currency'),
            'account_number_last4': str(data.get('account_number') or '')[-4:],
            'portfolio_value': data.get('portfolio_value'),
            'cash': data.get('cash'),
            'buying_power': data.get('buying_power'),
        })
    except (requests.RequestException, ValueError):
        return jsonify({'error': 'paper account check failed; verify paper credentials in Render'}), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '10000')))
