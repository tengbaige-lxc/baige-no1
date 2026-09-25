# Baige V5

V5 separates portfolio risk into equity, crypto, energy, metals, and rates.
Opposite positions in different factors do not count as a hedge for each other.

V5 supports a reviewed shadow-to-live switch. The current small-live profile:

- scans public market data across the isolated crypto and TradFi pools;
- uses only its own encrypted account database and ledger;
- limits each scan to one new leg;
- uses 20x where the live instrument supports it;
- caps planned loss per new leg at 3% of account equity;
- counts pre-existing positions as risk but never manages them without V5 ledger ownership.

## 2026-09-18 mandate split

- Baige V4 remains the directional trend strategy.
- Baige V5 runs the cross-sectional long-short book for new entries. Positions
  opened by the previous directional selector remain exit-managed, but that
  selector can no longer submit new orders.
- Crypto and TradFi are ranked separately. TradFi positions are neutralized
  inside their economic risk factor; unrelated asset classes never count as a
  hedge for each other.
- The shadow universe is rebuilt from multi-day trailing quote liquidity once per UTC
  calendar month. Targets rebalance once per UTC day, hold equal long and short
  notional, and use inverse-volatility weights inside each factor.
- Crypto targets refresh at 00:00 and 12:00 UTC (08:00 and 20:00 Beijing).
  TradFi refreshes only at 00:00 UTC. Between those boundaries each target is
  frozen: scans may fill missing target legs but cannot replace the ranked
  list. Protective exits continue to run in real time.
- Inverse products such as SQQQ/SOXS/UVXY are excluded until a verified beta
  model can express their economic direction and leverage.
- Known leveraged ETPs are excluded for the same reason. Energy, metals, rates,
  equity and crypto must each be neutral inside their own economic factor.
- Every rebalance, signal observation, estimated 3bp-per-side cost and
  first available 24-hour outcome is retained for 400 days.
- Only an explicitly executable, dollar-neutral target can reach
  `V5ExecutionManager`. The live adapter rejects unbalanced targets, admits at
  most one new leg per scan, counts every exchange position against portfolio
  limits, and requires a verified native stop after each fill.
