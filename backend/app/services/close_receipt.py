"""Conservative whole-position receipt matching; never allocate mixed PnL."""
import math


def match_complete_close(record, opening_order: dict, history: list[dict]) -> dict | None:
    """A single filled opening lot, fully closed without adds or partial lots."""
    try:
        def number(value):
            value = float(value)
            if not math.isfinite(value):
                raise ValueError('non-finite exchange number')
            return value

        symbol, direction = record.symbol, record.direction.upper()
        size, price = number(record.position_size), number(record.entry_price)
        expected_side = 'buy' if direction == 'LONG' else 'sell'
        if size <= 0 or price <= 0 or direction not in {'LONG', 'SHORT'}:
            return None
        if (opening_order.get('ordId') != record.external_id
                or opening_order.get('instId') != symbol
                or opening_order.get('state') != 'filled'
                or opening_order.get('side') != expected_side
                or opening_order.get('posSide') != direction.lower()
                or not str(opening_order.get('clOrdId', '')).startswith('bg2')
                or not math.isclose(number(opening_order['accFillSz']), size, rel_tol=1e-8)
                or not math.isclose(number(opening_order['avgPx']), price, rel_tol=1e-8)):
            return None
        opened = int(opening_order['cTime'])
        matches = []
        for row in history:
            if row.get('instId') != symbol or row.get('direction') != direction.lower():
                continue
            if abs(int(row['cTime']) - opened) > 1000:
                continue
            if not row.get('posId') or int(row['uTime']) <= int(row['cTime']):
                continue
            # closeTotalPos > size reveals re-adds even if final net size matches.
            if not math.isclose(number(row['closeTotalPos']), size, rel_tol=1e-8):
                continue
            if not math.isclose(number(row['openAvgPx']), price, rel_tol=1e-8):
                continue
            exit_price = number(row['closeAvgPx'])
            ct_val = number(record.ct_val)
            if ct_val <= 0 or exit_price <= 0:
                continue
            gross = number(row['pnl'])
            expected = (exit_price - price) * size * ct_val * (1 if direction == 'LONG' else -1)
            if not math.isclose(gross, expected, rel_tol=1e-5, abs_tol=1e-6):
                continue
            fee = number(row['fee'])
            funding = number(row['fundingFee'])
            penalty = number(row.get('liqPenalty') or 0)
            net = number(row['realizedPnl'])
            if not math.isclose(net, gross + fee + funding + penalty, rel_tol=1e-5, abs_tol=1e-6):
                continue
            matches.append(dict(pos_id=row['posId'], opened_ms=int(row['cTime']),
                                closed_ms=int(row['uTime']), exit_price=exit_price,
                                gross_pnl=gross, net_pnl=net, fee=fee,
                                funding_fee=funding, liquidation_penalty=penalty,
                                source='OKX positions-history', close_actor='unknown'))
        return matches[0] if len(matches) == 1 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
