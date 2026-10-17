"""Deterministic shadow/paper-order eligibility checks, not order execution.

All values must be supplied from a fresh, verified broker snapshot by a future adapter.
Fail closed whenever required account values are unknown. This module imports NO trading APIs.
"""
from decimal import Decimal, InvalidOperation

STARTING_MANAGED_CAPITAL = Decimal('500')
MAX_NEW_POSITION_FRACTION = Decimal('0.05')
MAX_DAILY_LOSS = Decimal('0.02')
MAX_DRAWDOWN = Decimal('0.10')


def amount(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Missing or invalid numeric input')
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Invalid numeric input') from exc
    if not number.is_finite():
        raise ValueError('Value must be finite')
    return number


def evaluate_shadow_order(order, account, *, trading_enabled=False, emergency_stop=True):
    """Returns a shadow analysis. Never calls a broker or submits an order."""
    errors = []
    if trading_enabled:
        errors.append('This module does not authorize or execute any real trades')
    if emergency_stop:
        errors.append('Emergency stop is ON')
    required = ('equity', 'day_start_equity', 'peak_equity', 'available_cash',
                'managed_allocation', 'current_position_value')
    if any(k not in account or account[k] is None for k in required):
        errors.append('Fresh account snapshot incomplete')
    if any(k not in order or order[k] is None for k in ('symbol', 'side', 'asset_class', 'qty', 'order_type', 'limit_price')):
        errors.append('Order fields incomplete')
    if errors:
        return {'status': 'BLOCK', 'reasons': errors, 'execution': 'DISABLED'}

    try:
        equity = amount(account['equity'])
        start = amount(account['day_start_equity'])
        peak = amount(account['peak_equity'])
        cash = amount(account['available_cash'])
        managed = amount(account['managed_allocation'])
        existing = amount(account['current_position_value'])
        price = amount(order['limit_price'])
        qty = amount(order['qty'])
    except ValueError:
        return {'status': 'BLOCK', 'reasons': ['Invalid or stale account/price inputs'], 'execution': 'DISABLED'}

    if any(x <= 0 for x in (equity, start, peak, managed, price, qty)) or any(x < 0 for x in (cash, existing)):
        errors.append('Invalid or nonpositive account/order amount')
    if managed > STARTING_MANAGED_CAPITAL:
        errors.append('Managed allocation is above $500 initial stage')
    if order['side'] != 'buy' or order['asset_class'] != 'stock':
        errors.append('Only stock buys are modeled in the initial shadow stage')
    if order['order_type'] != 'limit':
        errors.append('Only limit orders are modeled')
    if qty != qty.to_integral_value():
        errors.append('Whole-share quantities only')
    if account.get('margin_used', 0) != 0 or account.get('options_enabled', False):
        errors.append('Margin or options are not permitted')
    if not account.get('freshness_verified', False):
        errors.append('Broker data freshness must be independently verified')
    if equity <= 0 or start <= 0 or peak <= 0:
        errors.append('Missing reliable equity baseline')
    else:
        if (start - equity) / start >= MAX_DAILY_LOSS:
            errors.append('Daily loss halt of 2% reached')
        if (peak - equity) / peak >= MAX_DRAWDOWN:
            errors.append('Peak-equity drawdown halt of 10% reached')
    proposed = price * qty
    if proposed > cash:
        errors.append('Insufficient unleveraged available cash')
    if proposed + existing > managed * MAX_NEW_POSITION_FRACTION:
        errors.append('New position would exceed 5% of managed allocation')
    return {'status': 'BLOCK' if errors else 'SHADOW_ELIGIBLE', 'reasons': errors,
            'notional': str(proposed), 'execution': 'DISABLED'}
