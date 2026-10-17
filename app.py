import os
import secrets
import hmac
from flask import Flask, Response, jsonify, request
import requests

app = Flask(__name__)
PAPER_BASE = 'https://paper-api.alpaca.markets'

@app.after_request
def secure_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response

@app.get('/')
def home():
    return jsonify({'mode': 'PAPER ONLY', 'live_trading': 'DISABLED', 'order_submission': 'DISABLED', 'message': 'Read-only setup. /health checks deployment; /account requires your private access token.'})

@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'mode': 'paper', 'order_submission': False})

@app.get('/account')
def account():
    access_token = os.environ.get('DASHBOARD_ACCESS_TOKEN', '')
    supplied = request.headers.get('X-Dashboard-Token', '')
    if not access_token or not supplied or not hmac.compare_digest(supplied, access_token):
        return jsonify({'error': 'unauthorized'}), 401
    key = os.environ.get('ALPACA_PAPER_API_KEY', '')
    secret = os.environ.get('ALPACA_PAPER_API_SECRET', '')
    if not key or not secret:
        return jsonify({'error': 'paper credentials not configured'}), 503
    try:
        result = requests.get(PAPER_BASE + '/v2/account', headers={
            'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret
        }, timeout=10)
        result.raise_for_status()
        payload = result.json()
        return jsonify({'mode': 'PAPER ONLY', 'order_submission': False,
                        'status': payload.get('status'), 'currency': payload.get('currency'),
                        'account_number_last4': str(payload.get('account_number', ''))[-4:]})
    except (requests.RequestException, ValueError):
        return jsonify({'error': 'paper account check failed; inspect configuration without exposing secrets'}), 502

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '10000')))
