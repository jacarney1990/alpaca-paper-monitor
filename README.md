# Alpaca Paper Read-Only Monitor

This starter app is **read-only**. It does not implement any order submission, modification, or cancellation endpoints. It connects exclusively to the Alpaca paper-trading API.

## Render
- Web Service, Python 3, Free instance for initial testing only
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Environment secrets: `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET`, `DASHBOARD_ACCESS_TOKEN` (a separately generated long random secret)
- Test public `/health` first; it returns no sensitive data.
- `/account` requires header `X-Dashboard-Token` with your private access token; it only returns status, currency, and last 4 digits of the paper account number. Never put tokens in a URL.

The application does not use Claude, Robinhood, or a live brokerage endpoint. Do not put credentials in GitHub, source files, or chat. A Render free web service sleeps when idle and is not suitable for unattended production trading.
