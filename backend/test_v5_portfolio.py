import math
import unittest
from statistics import pstdev

from v5_portfolio import (
    BAR_MS,
    build_portfolio,
    economic_direction_for_symbol,
    market_features,
    risk_factor_for_symbol,
    risk_group_for_symbol,
    rotation_proposal,
    size_proposal,
)


NOW = 200 * BAR_MS


def candidate(i, side, score=7, scale=1):
    returns = [scale * (.002 * math.sin(t * .4) + .003 * math.sin(t * (.12 + i * .037))) for t in range(96)]
    return {'symbol': f'S{i}', 'direction': side, 'score': score, 'eligible': True,
            'observed_ms': NOW, 'market': {'returns': returns,
                'return_timestamps': list(range(104 * BAR_MS, 200 * BAR_MS, BAR_MS)),
                'bar_ts': 199 * BAR_MS, 'return_24h': .01 if side == 'LONG' else -.01,
                'volatility': pstdev(returns)}}


def book():
    return [candidate(i, 'LONG' if i < 5 else 'SHORT') for i in range(1, 9)]


class PortfolioTests(unittest.TestCase):
    def test_at_most_four_each_no_duplicate_symbols(self):
        plan = build_portfolio(book(), now_ms=NOW)
        self.assertEqual(plan['status'], 'PROPOSAL')
        for side in ['LONG', 'SHORT']:
            self.assertLessEqual(sum(r['direction'] == side for r in plan['legs']), 4)
        self.assertEqual(len({r['symbol'] for r in plan['legs']}), len(plan['legs']))

    def test_risk_proxy_shares_and_cap(self):
        plan = build_portfolio(book(), now_ms=NOW)
        risks = {s: sum(r['notional_weight'] * r['volatility_30m'] for r in plan['legs'] if r['direction'] == s)
                 for s in ['LONG', 'SHORT']}
        self.assertAlmostEqual(risks['LONG'] / sum(risks.values()), plan['risk_share']['LONG'])
        self.assertTrue(all(r['notional_weight'] <= .20000001 for r in plan['legs']))
        self.assertAlmostEqual(sum(r['notional_weight'] for r in plan['legs']) + plan['unallocated_fraction'], 1)

    def test_tilt_bounded_and_only_new_bar(self):
        rows = book()
        for row in rows:
            if row['direction'] == 'LONG':
                row['score'] = 20
        first = build_portfolio(rows, now_ms=NOW)
        self.assertLessEqual(first['risk_share']['LONG'], .65)
        second = build_portfolio(rows, now_ms=NOW, allocation_state=first['allocation_state'])
        self.assertEqual(second['risk_share'], first['risk_share'])
        later = build_portfolio(rows, now_ms=NOW, allocation_state={'bar_ts': 198 * BAR_MS, 'long_share': .59})
        self.assertLessEqual(later['risk_share']['LONG'], .65)

    def test_one_side_only_leaves_cash(self):
        plan = build_portfolio([candidate(1, 'LONG')], now_ms=NOW)
        self.assertEqual(plan['status'], 'PROPOSAL')
        self.assertEqual(len(plan['legs']), 1)
        self.assertGreater(plan['unallocated_fraction'], .8)

    def test_one_pair_does_not_consume_eight_leg_budget(self):
        rows = [candidate(1, 'LONG'), candidate(2, 'SHORT')]
        plan = build_portfolio(rows, now_ms=NOW)
        self.assertEqual(len(plan['legs']), 2)
        self.assertGreaterEqual(plan['unallocated_fraction'], .75)

    def test_stale_signal_and_failed_gate_cannot_enter(self):
        rows = book()
        for row in rows:
            row['observed_ms'] -= 180001
        self.assertEqual(build_portfolio(rows, now_ms=NOW)['status'], 'WAIT')
        for row in rows:
            row['observed_ms'] = NOW
            row['eligible'] = False
        self.assertEqual(build_portfolio(rows, now_ms=NOW)['status'], 'WAIT')

    def test_anticorrelated_pair_is_not_assumed_to_hedge(self):
        a, b = candidate(1, 'LONG'), candidate(2, 'SHORT')
        b['market']['returns'] = [-r for r in a['market']['returns']]
        self.assertEqual(build_portfolio([a, b], now_ms=NOW)['status'], 'PROPOSAL')

    def test_missing_correlation_history_not_neutral(self):
        a, b = candidate(1, 'LONG'), candidate(2, 'SHORT')
        b['market']['return_timestamps'] = list(range(96))
        self.assertEqual(build_portfolio([a, b], now_ms=NOW)['status'], 'PROPOSAL')

    def test_same_side_high_correlation_keeps_only_stronger_candidate(self):
        stronger, weaker = candidate(1, 'LONG', score=8), candidate(2, 'LONG', score=7)
        weaker['market']['returns'] = list(stronger['market']['returns'])
        weaker['market']['return_timestamps'] = list(stronger['market']['return_timestamps'])
        plan = build_portfolio([weaker, stronger], now_ms=NOW, max_same_side_correlation=.85)
        longs = [row for row in plan['legs'] if row['direction'] == 'LONG']
        self.assertEqual([row['symbol'] for row in longs], ['S1'])

    def test_same_side_oil_group_keeps_best_even_without_correlation_history(self):
        stronger, weaker = candidate(1, 'LONG', score=8), candidate(2, 'LONG', score=7)
        stronger['symbol'] = 'CL-USDT-SWAP'
        weaker['symbol'] = 'USO-USDT-SWAP'
        weaker['market']['return_timestamps'] = list(range(96))
        plan = build_portfolio([weaker, stronger], now_ms=NOW)
        longs = [row for row in plan['legs'] if row['direction'] == 'LONG']
        self.assertEqual([row['symbol'] for row in longs], ['CL-USDT-SWAP'])
        self.assertEqual(longs[0]['risk_group'], 'OIL_ENERGY')

    def test_oil_group_mapping_is_explicit(self):
        for symbol in ('CL-USDT-SWAP', 'USO-USDT-SWAP', 'XLE-USDT-SWAP'):
            self.assertEqual(risk_group_for_symbol(symbol), 'OIL_ENERGY')
        self.assertIsNone(risk_group_for_symbol('ORCL-USDT-SWAP'))
        self.assertIsNone(risk_group_for_symbol('CRCL-USDT-SWAP'))

    def test_risk_factors_are_asset_class_specific(self):
        self.assertEqual(risk_factor_for_symbol('BTC-USDT-SWAP', 'crypto'), 'CRYPTO')
        self.assertEqual(risk_factor_for_symbol('NVDA-USDT-SWAP', 'tradfi'), 'EQUITY')
        self.assertEqual(risk_factor_for_symbol('USO-USDT-SWAP', 'tradfi'), 'ENERGY')
        self.assertEqual(risk_factor_for_symbol('XAU-USDT-SWAP', 'tradfi'), 'METALS')
        self.assertEqual(risk_factor_for_symbol('TLT-USDT-SWAP', 'tradfi'), 'RATES')

    def test_portfolio_legs_expose_risk_factor(self):
        crypto = candidate(1, 'LONG')
        crypto['symbol'] = 'BTC-USDT-SWAP'
        crypto['pool'] = 'crypto'
        plan = build_portfolio([crypto], now_ms=NOW)
        self.assertEqual(plan['legs'][0]['risk_factor'], 'CRYPTO')

    def test_inverse_products_use_economic_direction(self):
        for symbol in ('SQQQ-USDT-SWAP', 'SOXS-USDT-SWAP', 'UVXY-USDT-SWAP'):
            self.assertEqual(economic_direction_for_symbol(symbol, 'LONG'), 'SHORT')
            self.assertEqual(economic_direction_for_symbol(symbol, 'SHORT'), 'LONG')
        self.assertEqual(economic_direction_for_symbol('NVDA-USDT-SWAP', 'LONG'), 'LONG')
        self.assertEqual(economic_direction_for_symbol('ORCL-USDT-SWAP', 'SHORT'), 'SHORT')

    def test_inverse_long_and_equity_short_share_economic_short_budget(self):
        inverse = candidate(1, 'LONG', score=8)
        inverse['symbol'] = 'SQQQ-USDT-SWAP'
        equity_short = candidate(8, 'SHORT', score=7)
        equity_short['symbol'] = 'NVDA-USDT-SWAP'
        plan = build_portfolio([inverse, equity_short], now_ms=NOW)
        self.assertEqual(plan['status'], 'PROPOSAL')
        self.assertTrue(plan['legs'])
        self.assertEqual({row['risk_direction'] for row in plan['legs']}, {'SHORT'})
        self.assertGreater(plan['risk_share']['SHORT'], plan['risk_share']['LONG'])

    def test_flat_candles_rejected(self):
        rows = [[t * BAR_MS, 100, 101, 99, 100, 10, 0, 0, '1'] for t in range(103, 200)]
        self.assertIsNone(market_features(rows, NOW))

    def test_closed_contiguous_fresh_market_only(self):
        rows = [[t * BAR_MS, 100, 102, 98, 100 + math.sin(t), 10, 0, 0, '1'] for t in range(103, 200)]
        valid = market_features(rows, NOW)
        self.assertEqual(len(valid['returns']), 96)
        self.assertIsNone(market_features(rows[:-1], NOW))
        self.assertIsNone(market_features(rows, NOW + 2 * BAR_MS))
        rows[-1][8] = '0'
        self.assertIsNone(market_features(rows, NOW))

    def test_size_requires_explicit_budget_and_never_executable(self):
        plan = build_portfolio(book(), now_ms=NOW)
        args = dict(equity=1000, free_margin=900, margin_budget=100,
                    leverage=2, account_margin_limit=.2, max_leverage=2)
        sized = size_proposal(plan, **args)
        self.assertLessEqual(sum(r['margin_preview'] for r in sized), 100.000001)
        self.assertTrue(all(not r['executable'] for r in sized))
        for field, value in [('leverage', None), ('margin_budget', 500), ('leverage', 20), ('equity', float('nan'))]:
            with self.assertRaises(ValueError):
                size_proposal(plan, **(args | {field: value}))

    def test_rotation_requires_distinct_closed_bars_and_verified_ownership(self):
        holding = {'symbol': 'OLD', 'direction': 'LONG', 'score': 5}
        replacement = {'symbol': 'NEW', 'direction': 'LONG', 'score': 7, 'eligible': True}
        args = dict(rank=7, weak=True, last_rotation_ms=0, now_ms=NOW, owned=True, account_verified=True)
        first = rotation_proposal(holding, replacement, bar_ts=198 * BAR_MS, streak=None, **args)
        duplicate = rotation_proposal(holding, replacement, bar_ts=198 * BAR_MS, streak=first['streak'], **args)
        self.assertEqual(duplicate['streak']['count'], 1)
        second = rotation_proposal(holding, replacement, bar_ts=199 * BAR_MS, streak=first['streak'], **args)
        self.assertEqual(second['action'], 'PROPOSE_SAME_SIDE_REPLACEMENT')
        for overrides in [{'owned': False}, {'account_verified': False}, {'rank': 5}, {'weak': False}, {'last_rotation_ms': NOW - 1000}]:
            result = rotation_proposal(holding, replacement, bar_ts=199 * BAR_MS, streak=first['streak'], **(args | overrides))
            self.assertEqual(result['action'], 'KEEP')


if __name__ == '__main__':
    unittest.main()
