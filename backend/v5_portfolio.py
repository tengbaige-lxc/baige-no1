"""Public-data portfolio proposals. No account access or order execution."""
import math
from statistics import mean, median, pstdev

BAR_MS = 30 * 60 * 1000

# Explicit membership avoids classifying unrelated tickers such as ORCL/CRCL
# as oil exposure merely because their symbols contain "CL".
RISK_GROUPS = {
    'OIL_ENERGY': frozenset({
        'CL-USDT-SWAP',
        'USO-USDT-SWAP',
        'XLE-USDT-SWAP',
    }),
}

# These contracts rise when broad equity risk falls. Their order side must be
# inverted when calculating portfolio direction, concentration and hedging.
INVERSE_RISK_SYMBOLS = frozenset({
    'SOXS-USDT-SWAP',
    'SQQQ-USDT-SWAP',
    'UVXY-USDT-SWAP',
})

FACTOR_SYMBOLS = {
    'ENERGY': frozenset({'CL-USDT-SWAP', 'USO-USDT-SWAP', 'XLE-USDT-SWAP'}),
    'METALS': frozenset({
        'XAU-USDT-SWAP', 'GLD-USDT-SWAP', 'SLV-USDT-SWAP',
        'SIL-USDT-SWAP', 'GDX-USDT-SWAP',
    }),
    'RATES': frozenset({
        'TLT-USDT-SWAP', 'TMF-USDT-SWAP', 'IEF-USDT-SWAP', 'SHY-USDT-SWAP',
    }),
}


def risk_group_for_symbol(symbol):
    normalized = str(symbol or '').strip().upper()
    return next((name for name, members in RISK_GROUPS.items()
                 if normalized in members), None)


def economic_direction_for_symbol(symbol, contract_direction):
    direction = str(contract_direction or '').strip().upper()
    if direction not in {'LONG', 'SHORT'}:
        return direction
    if str(symbol or '').strip().upper() not in INVERSE_RISK_SYMBOLS:
        return direction
    return 'SHORT' if direction == 'LONG' else 'LONG'


def risk_factor_for_symbol(symbol, pool=None):
    normalized = str(symbol or '').strip().upper()
    if str(pool or '').strip().lower() == 'crypto':
        return 'CRYPTO'
    for factor, members in FACTOR_SYMBOLS.items():
        if normalized in members:
            return factor
    return 'EQUITY'


def _position_market(candidate):
    market = candidate['market']
    sign = 1 if candidate['direction'] == 'LONG' else -1
    return {**market, 'returns': [sign * value for value in market['returns']]}


def market_features(candles, now_ms):
    """Use only a contiguous, fresh history of 96 closed 30m returns."""
    closed_rows = sorted({int(c[0]): c for c in candles
                          if len(c) >= 9 and str(c[8]) == '1'
                          and int(c[0]) + BAR_MS <= now_ms}.values(),
                         key=lambda c: int(c[0]))
    rows = closed_rows[-97:]
    if len(rows) < 97:
        return None
    if now_ms - (int(rows[-1][0]) + BAR_MS) > BAR_MS:
        return None
    if any(int(b[0]) - int(a[0]) != BAR_MS for a, b in zip(rows, rows[1:])):
        return None
    closes = [float(c[4]) for c in rows]
    if not all(math.isfinite(c) and c > 0 for c in closes):
        return None
    returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    volatility = pstdev(returns)
    if volatility < 1e-6:
        return None
    liquidity_rows = closed_rows[-240:]
    quote_volume_trailing = 0.0
    for candle in liquidity_rows:
        try:
            quote_volume = float(candle[7])
        except (TypeError, ValueError):
            continue
        if math.isfinite(quote_volume):
            quote_volume_trailing += max(0.0, quote_volume)
    return {'returns': returns, 'return_timestamps': [int(c[0]) for c in rows[1:]],
            'volatility': volatility, 'return_24h': closes[-1] / closes[-49] - 1,
            'quote_volume_24h': quote_volume_trailing,
            'quote_volume_trailing': quote_volume_trailing,
            'liquidity_lookback_bars': len(liquidity_rows),
            'bar_ts': int(rows[-1][0]),
            'weak_LONG': closes[-1] < mean(closes[-34:]) and closes[-2] < mean(closes[-35:-1])
                         and mean(closes[-5:]) < mean(closes[-6:-1]),
            'weak_SHORT': closes[-1] > mean(closes[-34:]) and closes[-2] > mean(closes[-35:-1])
                          and mean(closes[-5:]) > mean(closes[-6:-1])}


def correlation(a, b):
    ar = dict(zip(a['return_timestamps'], a['returns']))
    br = dict(zip(b['return_timestamps'], b['returns']))
    timestamps = sorted(ar.keys() & br.keys())
    if len(timestamps) < 64:
        return None
    x, y = [ar[t] for t in timestamps], [br[t] for t in timestamps]
    sx, sy = pstdev(x), pstdev(y)
    if sx <= 0 or sy <= 0:
        return None
    mx, my = mean(x), mean(y)
    return max(-1.0, min(1.0, mean((u - mx) * (v - my) for u, v in zip(x, y)) / sx / sy))


def build_portfolio(observations, *, now_ms, minimum=6.5, allocation_state=None,
                    max_same_side_correlation=.85):
    market = {r['symbol']: r['market'] for r in observations if r.get('market')}
    baseline = median([m['return_24h'] for m in market.values()]) if market else 0
    pools = {'LONG': [], 'SHORT': []}
    for row in observations:
        direction = row.get('direction')
        score = row.get('score')
        m = row.get('market')
        if direction not in pools or not row.get('eligible') or not m:
            continue
        if not isinstance(score, (int, float)) or not math.isfinite(score) or score < minimum:
            continue
        if not 0 <= now_ms - row.get('observed_ms', 0) <= 180000:
            continue
        if not 0 <= now_ms - (m['bar_ts'] + BAR_MS) <= BAR_MS:
            continue
        directional_strength = (m['return_24h'] - baseline) * (1 if direction == 'LONG' else -1)
        strength = max(-1.0, min(1.0, directional_strength / (m['volatility'] * math.sqrt(48))))
        risk_direction = economic_direction_for_symbol(row['symbol'], direction)
        risk_factor = risk_factor_for_symbol(row['symbol'], row.get('pool'))
        pools[risk_direction].append({
            **row,
            'risk_direction': risk_direction,
            'risk_factor': risk_factor,
            'quality': score + .5 * strength,
            'relative_strength': directional_strength,
        })
    for side in pools:
        pools[side].sort(key=lambda r: (-r['quality'], r['symbol']))
        # Bounded joint search rather than independently filling two top-four lists.
        pools[side] = pools[side][:12]

    selected = {'LONG': [], 'SHORT': []}
    used = set()
    used_risk_groups = {'LONG': set(), 'SHORT': set()}
    for side in ('LONG', 'SHORT'):
        for candidate in pools[side]:
            if candidate['symbol'] in used:
                continue
            risk_group = risk_group_for_symbol(candidate['symbol'])
            if risk_group and risk_group in used_risk_groups[side]:
                continue
            within = [correlation(_position_market(candidate), _position_market(old))
                      for old in selected[side]]
            if any(value is not None and value >= max_same_side_correlation for value in within):
                continue
            selected[side].append({**candidate, 'risk_group': risk_group})
            used.add(candidate['symbol'])
            if risk_group:
                used_risk_groups[side].add(risk_group)
            if len(selected[side]) >= 4:
                break

    if not any(selected.values()):
        return {'status': 'WAIT', 'reason': 'no_qualified_directional_candidate', 'legs': [],
                'pairs': [], 'unallocated_fraction': 1.0, 'risk_share': {'LONG': .5, 'SHORT': .5},
                'executable': False}
    if selected['LONG'] and selected['SHORT']:
        difference = (mean(r['quality'] for r in selected['LONG'])
                      - mean(r['quality'] for r in selected['SHORT']))
        desired_share = max(.35, min(.65, .5 + .06 * difference))
    else:
        desired_share = .65 if selected['LONG'] else .35
    allocation_state = allocation_state or {'bar_ts': 0, 'long_share': .5}
    previous_share = max(.35, min(.65, float(allocation_state['long_share'])))
    common_bar = min(r['market']['bar_ts'] for side in selected for r in selected[side])
    long_share = previous_share if common_bar <= allocation_state['bar_ts'] else max(
        previous_share - .10, min(previous_share + .10, desired_share))
    share = {'LONG': long_share, 'SHORT': 1 - long_share}
    raw_weights = []
    risk_budgets = {}
    for side in selected:
        count = len(selected[side])
        risk_budgets[side] = share[side] * count / 4
        if count:
            raw_weights.extend((row, risk_budgets[side] / count / row['market']['volatility'])
                               for row in selected[side])
    target_utilization = sum(risk_budgets.values())
    raw_total = sum(weight for _, weight in raw_weights)
    weights = [(row, weight / raw_total * target_utilization)
               for row, weight in raw_weights]
    concentration_scale = min(1.0, .20 / max(weight for _, weight in weights))
    weights = [(row, weight * concentration_scale) for row, weight in weights]
    utilization = sum(weight for _, weight in weights)
    legs = [{k: r[k] for k in ['symbol', 'direction', 'risk_direction', 'risk_factor',
                               'score', 'quality', 'relative_strength']}
            | {'volatility_30m': r['market']['volatility'], 'bar_ts': r['market']['bar_ts'],
               'risk_group': r.get('risk_group'),
               'trigger_score': r.get('trigger_score', r['score']),
               'revalidation_score': r.get('revalidation_score', r['score']),
               'notional_weight': weight * utilization,
               'strong_trend': bool(r.get('strong_trend')),
               'trend_4h_aligned': bool(r.get('trend_4h_aligned')),
               'structure_30m_aligned': bool(r.get('structure_30m_aligned')),
               'adx_4h_strong': bool(r.get('adx_4h_strong')),
               'adx_4h': float(r.get('adx_4h') or 0)}
            for r, weight in weights]
    return {'status': 'PROPOSAL', 'legs': legs, 'pairs': [], 'risk_share': share,
            'allocation_state': {'bar_ts': common_bar, 'long_share': long_share},
            'unallocated_fraction': 1 - utilization, 'executable': False,
            'risk_measure': 'sum_of_leg_notional_times_30m_volatility_proxy_not_portfolio_VaR',
            'reason': 'refresh_account_signals_and_native_stop_preflight_before_execution'}


def size_proposal(plan, *, equity, free_margin, margin_budget, leverage,
                  account_margin_limit, max_leverage):
    """Sizing preview only. Explicit account limits are mandatory, no defaults."""
    values = [equity, free_margin, margin_budget, leverage, account_margin_limit, max_leverage]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('explicit finite positive account budget and leverage are required')
    if account_margin_limit >= 1 or leverage > max_leverage:
        raise ValueError('invalid account reserve or leverage limit')
    if margin_budget > min(free_margin, equity * account_margin_limit):
        raise ValueError('margin budget exceeds verified account allowance')
    if plan['status'] != 'PROPOSAL':
        return []
    return [{**leg, 'margin_preview': margin_budget * leg['notional_weight'],
             'notional_preview': margin_budget * leverage * leg['notional_weight'],
             'executable': False} for leg in plan['legs']]


def rotation_proposal(holding, replacement, *, rank, weak, bar_ts, streak,
                      last_rotation_ms, now_ms, owned, account_verified):
    """Persisted caller state counts distinct closed bars, never repeated scans.

    These are proposals, not fills. Risk exits must run independently of this
    discretionary replacement gate and must not wait for a replacement.
    """
    state = dict(streak or {'bar_ts': None, 'count': 0})
    if state['bar_ts'] != bar_ts:
        consecutive = state['bar_ts'] is not None and bar_ts - state['bar_ts'] == BAR_MS
        state = {'bar_ts': bar_ts, 'count': (state['count'] if consecutive else 0) + 1 if rank > 6 and weak else 0}
    blocked = {'action': 'KEEP', 'streak': state, 'executable': False}
    if not owned or not account_verified or not replacement:
        return blocked
    if rank <= 6 or not weak:
        return blocked
    if not 0 <= now_ms - (bar_ts + BAR_MS) <= BAR_MS or state['count'] < 2:
        return blocked
    if now_ms - last_rotation_ms < 3600000:
        return blocked
    if holding['direction'] != replacement['direction'] or holding['symbol'] == replacement['symbol']:
        return blocked
    if not all(isinstance(r.get('score'), (float, int)) and math.isfinite(r['score']) for r in [holding, replacement]):
        return blocked
    if not replacement.get('eligible') or replacement['score'] < 6.5 or replacement['score'] - holding['score'] < 1.0:
        return blocked
    return {'action': 'PROPOSE_SAME_SIDE_REPLACEMENT', 'close_symbol': holding['symbol'],
            'candidate_symbol': replacement['symbol'], 'streak': state, 'executable': False,
            'sequence': ['verify_portfolio_exposure', 'reduce_old', 'verify_fill',
                         'refresh_candidates_balance_and_limits', 'open_new_with_native_stop']}
