"""V5 factor-aware portfolio scanner running in isolated shadow mode."""
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

from fastapi import FastAPI
from sqlalchemy import case, func, select
from app.db.base import Base, AsyncSessionLocal, engine as database_engine
import app.models  # Register the schema in the separate V5 database.
from app.models.exchange_config import ExchangeConfig
from app.models.trade_record import TradeRecord
from app.services.okx_client import decrypt_text, okx_manager
from app.services.strategy_engine import StrategyEngine
from v5_portfolio import build_portfolio, economic_direction_for_symbol, market_features
from v5_cross_sectional import build_cross_sectional_shadow
from v5_research_archive import archive_cross_sectional_scan, research_summary
from v5_execution import V5ExecutionManager
from v4_candidate_window import revalidation_allows, update_candidate_cache
from account_metrics import summarize_events

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('V5_STATE_DIR', ROOT / 'state')).resolve()
SCAN_DB = STATE / 'scan_history.sqlite'
status = {'mode': 'factor_aware_shadow', 'execution_enabled': False,
          'new_entries_enabled': False,
          'account_status': 'shadow_no_private_accounts', 'last_completed': None,
          'selected': {'LONG': [], 'SHORT': []}, 'scan_in_progress': False,
          'selected_by_pool': {}, 'portfolio_by_pool': {},
          'portfolio_mode': 'factor_isolated_dynamic_directional_shadow',
          'strategy_mandate': 'cross_sectional_long_short_transition',
          'cross_sectional_shadow_by_pool': {},
          'sizing_status': '20x_preview_factor_caps_60_70_3pct_leg_risk',
          'rotation_status': 'shadow_proposals_only_no_orders',
          'position_limit_status': 'combined_crypto_tradfi_max_14'}


def deny_private(*args, **kwargs):
    """Legacy guard kept for tests and any path that is not explicitly read-only."""
    raise RuntimeError('V5 private API access is disabled in shadow mode')


async def deny_trade(*args, **kwargs):
    raise RuntimeError('V5 execution is not armed')


def normalize_balance_details(rows):
    details = []
    for row in rows:
        nested = row.get('details')
        details.extend(nested if isinstance(nested, list) else [row])
    return details


def strength_metadata(factors, score, config):
    """Expose only verified higher-timeframe strength facts to sizing."""
    by_key = {factor.get('key'): factor for factor in factors if factor.get('key')}
    trend_4h_aligned = bool((by_key.get('trend_filter') or {}).get('triggered'))
    structure_30m_aligned = bool((by_key.get('trend_v3_30m') or {}).get('triggered'))
    adx_factor = by_key.get('trend_v3_adx_atr') or {}
    adx_4h_strong = bool(adx_factor.get('triggered'))
    adx_raw = adx_factor.get('raw') or {}
    try:
        adx_4h = float(adx_raw.get('adx') or 0)
    except (TypeError, ValueError):
        adx_4h = 0.0
    strong = (
        float(score or 0) >= float(config.get('strong_trend_min_score', 7.5))
        and trend_4h_aligned
        and structure_30m_aligned
        and adx_4h_strong
    )
    return {
        'trend_4h_aligned': trend_4h_aligned,
        'structure_30m_aligned': structure_30m_aligned,
        'adx_4h_strong': adx_4h_strong,
        'adx_4h': adx_4h,
        'strong_trend': strong,
    }


async def refresh_account_status():
    """Refresh a deliberately narrow account view without exposing credentials."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ExchangeConfig).where(ExchangeConfig.is_active == True).order_by(ExchangeConfig.id)
        )
        accounts = list(result.scalars().all())
    if not accounts:
        status.update(account_status='awaiting_independent_api_key', account=None, accounts=[])
        return
    account_rows = []
    errors = []
    for account in accounts:
        key = decrypt_text(account.api_key)
        secret = decrypt_text(account.api_secret)
        passphrase = decrypt_text(account.api_passphrase or '')
        try:
            config = await okx_manager._private_json_request(
                key, secret, passphrase, 'GET', '/api/v5/account/config', retries=1,
            )
            okx_manager._raise_for_okx_error(config, '/api/v5/account/config')
            balances, positions = await asyncio.gather(
                okx_manager.get_balance(key, secret, passphrase, bool(account.is_testnet)),
                okx_manager.get_positions(key, secret, passphrase, bool(account.is_testnet)),
            )
            details = normalize_balance_details(balances)
            equity = sum(float(row.get('eqUsd') or row.get('eq') or 0) for row in details)
            available = sum(float(row.get('availEq') or row.get('availBal') or 0) for row in details)
            live_positions = [row for row in positions
                              if abs(float(row.get('pos') or 0)) > 0]
            live = sorted({str(row.get('instId')) for row in live_positions})
            long_count = 0
            short_count = 0
            economic_counts = {'LONG': 0, 'SHORT': 0}
            economic_margin = {'LONG': 0.0, 'SHORT': 0.0}
            economic_notional = {'LONG': 0.0, 'SHORT': 0.0}
            for position in live_positions:
                side = str(position.get('posSide') or 'net').lower()
                size = float(position.get('pos') or 0)
                contract_direction = 'LONG' if side == 'long' or (
                    side == 'net' and size > 0) else 'SHORT'
                if contract_direction == 'LONG':
                    long_count += 1
                else:
                    short_count += 1
                economic_direction = economic_direction_for_symbol(
                    position.get('instId'), contract_direction)
                economic_counts[economic_direction] += 1
                economic_margin[economic_direction] += abs(float(
                    position.get('imr') or position.get('margin') or 0))
                economic_notional[economic_direction] += abs(float(
                    position.get('notionalUsd') or 0))
            total_economic_margin = sum(economic_margin.values())
            account_config = (config.get('data') or [{}])[0]
            account_rows.append({
                'id': account.id,
                'name': account.name,
                'uid_suffix': str(account_config.get('uid') or '')[-6:],
                'main_uid_suffix': str(account_config.get('mainUid') or '')[-6:],
                'account_level': account_config.get('acctLv'),
                'position_mode': account_config.get('posMode'),
                'permissions': sorted(filter(None, str(account_config.get('perm') or '').split(','))),
                'equity_usd': round(equity, 4),
                'available_usd': round(available, 4),
                'live_position_count': len(live),
                'live_position_symbols': live,
                'long_count': long_count,
                'short_count': short_count,
                'economic_long_count': economic_counts['LONG'],
                'economic_short_count': economic_counts['SHORT'],
                'economic_long_margin_usd': round(economic_margin['LONG'], 2),
                'economic_short_margin_usd': round(economic_margin['SHORT'], 2),
                'economic_long_margin_share': round(
                    economic_margin['LONG'] / total_economic_margin, 4
                ) if total_economic_margin else 0.0,
                'economic_short_margin_share': round(
                    economic_margin['SHORT'] / total_economic_margin, 4
                ) if total_economic_margin else 0.0,
                'economic_net_notional_usd': round(
                    economic_notional['LONG'] - economic_notional['SHORT'], 2),
                'position_notional_usd': round(sum(
                    abs(float(row.get('notionalUsd') or 0)) for row in live_positions
                ), 2),
                'position_margin_usd': round(sum(
                    abs(float(row.get('imr') or row.get('margin') or 0))
                    for row in live_positions
                ), 2),
                'unrealized_pnl_usd': round(sum(
                    float(row.get('upl') or 0) for row in live_positions
                ), 2),
                'ready': equity > 0 and available > 0,
                'checked_at': datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            errors.append({'id': account.id, 'name': account.name,
                           'error': type(exc).__name__})
        finally:
            key = secret = passphrase = ''
    if account_rows and not errors:
        funded = sum(bool(row['ready']) for row in account_rows)
        account_status = ('verified_multi_account_live' if len(account_rows) > 1 and
                          funded == len(account_rows) else
                          'verified_funded_live' if funded else 'verified_zero_balance')
    elif account_rows:
        account_status = 'partial_verification_error'
    else:
        account_status = 'verification_error'
    status.update(account_status=account_status,
                  account=account_rows[0] if account_rows else None,
                  accounts=account_rows, account_errors=errors,
                  total_equity_usd=round(sum(row['equity_usd'] for row in account_rows), 4),
                  total_available_usd=round(sum(row['available_usd'] for row in account_rows), 4))


async def account_monitor_loop():
    while True:
        await refresh_account_status()
        await asyncio.sleep(60)


def persist(payload):
    with sqlite3.connect(SCAN_DB) as db:
        db.execute('CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY, created REAL NOT NULL, payload TEXT NOT NULL)')
        db.execute('INSERT INTO scans(created,payload) VALUES (?,?)', (time.time(), json.dumps(payload)))
        db.execute('DELETE FROM scans WHERE created < ?', (time.time() - 7 * 86400,))


def load_universes(config):
    path = config.get('universe_file')
    if not path:
        return {'crypto': list(config['symbols'])}
    payload = json.loads((ROOT / path).read_text())
    return {name: list(payload[name]) for name in ('crypto', 'tradfi')}


async def scan_universe(config, scanner, pool, symbols, allocation_state,
                        cross_sectional_state=None):
    started = time.monotonic()
    observations = []
    for symbol in symbols:
        try:
            ticker = await asyncio.wait_for(okx_manager.get_ticker(symbol), timeout=12)
            price = float(ticker.get('last') or 0)
            if not math.isfinite(price) or price <= 0:
                raise ValueError('invalid ticker')
            market = market_features(await asyncio.wait_for(
                okx_manager.get_candles(symbol, '30m', 240), timeout=12), time.time() * 1000)
            for direction, side in [('LONG', 'BUY'), ('SHORT', 'SELL')]:
                params = dict(config['params'])
                strategy = SimpleNamespace(id=1 if side == 'BUY' else 2, user_id=0,
                                           side=side, params=params, symbol=symbol,
                                           market_type='SWAP', strategy_type='white_dove')
                _, reason, factors = await asyncio.wait_for(
                    scanner._white_dove_v3_strategy(
                        strategy, price, symbol, None, False, score_only=True,
                    ), timeout=35,
                )
                raw = next((f.get('raw', {}) for f in factors
                            if f.get('key') == 'trend_v3_score'), {})
                observations.append({
                    'pool': pool, 'symbol': symbol, 'direction': direction,
                    'score': raw.get('score', 0),
                    'eligible': reason == 'rotation_score_only',
                    'reason': reason, 'price': price,
                    'observed_at': datetime.now(timezone.utc).isoformat(),
                    'observed_ms': time.time() * 1000, 'market': market,
                    'factor_keys': [f.get('key') for f in factors if f.get('key')],
                    **strength_metadata(factors, raw.get('score', 0), config),
                })
        except Exception as exc:
            observations.append({'pool': pool, 'symbol': symbol, 'eligible': False,
                                 'error': type(exc).__name__})
        await asyncio.sleep(.25)
    portfolio = build_portfolio(
        observations,
        now_ms=time.time() * 1000,
        minimum=config['minimum_score'],
        allocation_state=allocation_state,
        max_same_side_correlation=float(config.get('max_same_side_correlation', .85)),
    )
    cross_sectional_shadow = build_cross_sectional_shadow(
        observations,
        pool=pool,
        now_ms=time.time() * 1000,
        state=cross_sectional_state,
        config=config.get('cross_sectional_shadow') or {},
    )
    selected = {side: [leg for leg in portfolio['legs'] if leg['direction'] == side]
                for side in ('LONG', 'SHORT')}
    for row in observations:
        if row.get('market'):
            row['market'] = {k: v for k, v in row['market'].items()
                             if k not in {'returns', 'return_timestamps'}}
    return {
        'pool': pool, 'selected': selected, 'portfolio': portfolio,
        'allocation_state': portfolio.get('allocation_state', allocation_state),
        'cross_sectional_shadow': cross_sectional_shadow,
        'cross_sectional_state': cross_sectional_shadow.get('state', cross_sectional_state),
        'observations': observations,
        'errors': sum('error' in row for row in observations),
        'candidate_count': sum(row.get('eligible', False) for row in observations),
        'duration_seconds': round(time.monotonic() - started, 2),
    }


async def revalidate_candidate_pool(config, scanner, pool, cache, allocation_state):
    """Re-check cached triggers without requiring the transient 5m event to repeat."""
    observations = []
    for direction in ("LONG", "SHORT"):
        side = "BUY" if direction == "LONG" else "SELL"
        for cached in (cache.get(direction) or {}).values():
            symbol = cached["symbol"]
            try:
                ticker = await asyncio.wait_for(okx_manager.get_ticker(symbol), timeout=12)
                price = float(ticker.get("last") or 0)
                market = market_features(await asyncio.wait_for(
                    okx_manager.get_candles(symbol, "30m", 240), timeout=12), time.time() * 1000)
                strategy = SimpleNamespace(id=1 if side == "BUY" else 2, user_id=0,
                                           side=side, params=dict(config["params"]), symbol=symbol,
                                           market_type="SWAP", strategy_type="white_dove")
                _, reason, factors = await asyncio.wait_for(
                    scanner._white_dove_v3_strategy(
                        strategy, price, symbol, None, False, score_only=True,
                    ), timeout=35,
                )
                raw = next((factor.get("raw", {}) for factor in factors
                            if factor.get("key") == "trend_v3_score"), {})
                current_score = float(raw.get("score", 0) or 0)
                allowed = market is not None and revalidation_allows(
                    reason, current_score, max(config["minimum_score"],
                        config.get("candidate_revalidation_min_score", 4.5)),
                )
                observations.append({
                    "pool": pool,
                    "symbol": symbol,
                    "direction": direction,
                    "score": min(float(cached["score"]), current_score),
                    "trigger_score": float(cached["score"]),
                    "revalidation_score": current_score,
                    "eligible": allowed,
                    "reason": reason,
                    "price": price,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "observed_ms": time.time() * 1000,
                    "market": market,
                    "detected_at": cached["detected_at"],
                    **strength_metadata(factors, current_score, config),
                })
            except Exception as exc:
                observations.append({"pool": pool, "symbol": symbol, "direction": direction,
                                     "eligible": False, "error": type(exc).__name__})
            await asyncio.sleep(.25)
    portfolio = build_portfolio(
        observations,
        now_ms=time.time() * 1000,
        minimum=config["minimum_score"],
        allocation_state=allocation_state,
        max_same_side_correlation=float(config.get("max_same_side_correlation", .85)),
    )
    selected = {side: [leg for leg in portfolio["legs"] if leg["direction"] == side]
                for side in ("LONG", "SHORT")}
    return selected, portfolio, observations


async def scan_loop(config, scanner, universes, executor=None):
    allocation_states = {}
    cross_sectional_states = {}
    candidate_cache = {}
    if SCAN_DB.exists():
        with sqlite3.connect(SCAN_DB) as db:
            last = db.execute('SELECT payload FROM scans ORDER BY id DESC LIMIT 1').fetchone()
            if last:
                previous = json.loads(last[0])
                allocation_states = previous.get('allocation_states') or {}
                cross_sectional_states = previous.get('cross_sectional_states') or {}
                candidate_cache = previous.get('candidate_cache') or {}
    while True:
        started = time.monotonic()
        status['scan_in_progress'] = True
        try:
            results = {}
            for pool, symbols in universes.items():
                result = await scan_universe(
                    config, scanner, pool, symbols, allocation_states.get(pool),
                    cross_sectional_states.get(pool),
                )
                results[pool] = result
                allocation_states[pool] = result['allocation_state']
                cross_sectional_states[pool] = result['cross_sectional_state']
            candidate_cache = update_candidate_cache(
                candidate_cache,
                {pool: result["observations"] for pool, result in results.items()},
                now=time.time(),
                minimum_score=config["minimum_score"],
                ttl_seconds=int(config.get("candidate_window_seconds", 1800)),
            )
            window_results = {}
            for pool in universes:
                selected, portfolio, revalidated = await revalidate_candidate_pool(
                    config, scanner, pool, candidate_cache.get(pool) or {},
                    allocation_states.get(pool),
                )
                window_results[pool] = {
                    "selected": selected,
                    "portfolio": portfolio,
                    "revalidated": revalidated,
                }
                allocation_states[pool] = portfolio.get(
                    "allocation_state", allocation_states.get(pool),
                )
            completed = datetime.now(timezone.utc).isoformat()
            selected_by_pool = {pool: result['selected'] for pool, result in window_results.items()}
            portfolio_by_pool = {pool: result['portfolio'] for pool, result in window_results.items()}
            cross_sectional_shadow_by_pool = {
                pool: result['cross_sectional_shadow'] for pool, result in results.items()
            }
            selected = {side: [leg | {'pool': pool}
                               for pool, result in window_results.items()
                               for leg in result['selected'][side]]
                        for side in ('LONG', 'SHORT')}
            errors = sum(result['errors'] for result in results.values())
            candidates = sum(result['candidate_count'] for result in results.values())
            payload = {
                'completed': completed,
                'selected_by_pool': selected_by_pool,
                'portfolio_by_pool': portfolio_by_pool,
                'allocation_states': allocation_states,
                'cross_sectional_shadow_by_pool': cross_sectional_shadow_by_pool,
                'cross_sectional_states': cross_sectional_states,
                'candidate_cache': candidate_cache,
                'revalidated_by_pool': {pool: result['revalidated']
                                        for pool, result in window_results.items()},
                'observations_by_pool': {pool: result['observations']
                                         for pool, result in results.items()},
                'errors': errors,
                'duration_seconds': round(time.monotonic() - started, 2),
            }
            await asyncio.to_thread(persist, payload)
            try:
                status['cross_sectional_research_archive'] = await asyncio.to_thread(
                    archive_cross_sectional_scan, STATE, payload,
                )
                status['cross_sectional_research_summary'] = await asyncio.to_thread(
                    research_summary, STATE,
                )
            except Exception as exc:
                status['cross_sectional_research_archive_error'] = type(exc).__name__
                print('v5_cross_sectional_archive_error ' + type(exc).__name__, flush=True)
            status.update(last_completed=completed, selected=selected,
                          selected_by_pool=selected_by_pool,
                          portfolio_by_pool=portfolio_by_pool,
                          cross_sectional_shadow_by_pool=cross_sectional_shadow_by_pool,
                          candidate_window_seconds=config.get('candidate_window_seconds', 1800),
                          candidate_cache_counts={
                              pool: {side: len((candidate_cache.get(pool) or {}).get(side) or {})
                                     for side in ('LONG', 'SHORT')}
                              for pool in universes
                          },
                          errors=errors, candidate_count=candidates,
                          scan_seconds=payload['duration_seconds'],
                          pool_scan_seconds={pool: result['duration_seconds']
                                             for pool, result in results.items()})
            if executor is not None:
                if bool((config.get('cross_sectional_shadow') or {}).get(
                        'execution_enabled')):
                    execution = await executor.process_cross_sectional_scan(
                        completed, cross_sectional_shadow_by_pool)
                else:
                    execution = await executor.process_scan(completed, selected_by_pool)
                status['last_execution'] = execution
            print(json.dumps({
                'event': 'v5_scan_complete',
                'universe_sizes': {k: len(v) for k, v in universes.items()},
                'eligible': candidates, 'errors': errors,
                'selected': {pool: {side: [leg['symbol'] for leg in result['selected'][side]]
                                    for side in ('LONG', 'SHORT')}
                             for pool, result in window_results.items()},
                'cross_sectional_shadow': {
                    pool: {
                        'status': plan['status'],
                        'long': [leg['symbol'] for leg in plan['legs']
                                 if leg['direction'] == 'LONG'],
                        'short': [leg['symbol'] for leg in plan['legs']
                                  if leg['direction'] == 'SHORT'],
                    }
                    for pool, plan in cross_sectional_shadow_by_pool.items()
                },
            }, ensure_ascii=False), flush=True)
        except Exception as exc:
            status['last_error'] = type(exc).__name__
            print('v5_scan_error ' + type(exc).__name__, flush=True)
        finally:
            status['scan_in_progress'] = False
        await asyncio.sleep(max(30, config['scan_interval_seconds'] - (time.monotonic() - started)))


@asynccontextmanager
async def lifespan(app):
    STATE.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / 'config.json').read_text())
    universes = load_universes(config)
    execution_enabled = bool(config.get('execution_enabled'))
    cross_sectional_execution_enabled = execution_enabled and bool(
        (config.get('cross_sectional_shadow') or {}).get('execution_enabled'))
    new_entries_enabled = execution_enabled and int(
        config.get('max_new_legs_per_scan', 0)) > 0
    status.update(
        mode='factor_aware_live' if execution_enabled else 'factor_aware_shadow',
        execution_enabled=execution_enabled,
        new_entries_enabled=new_entries_enabled,
        account_status=('awaiting_account_verification' if execution_enabled
                        else 'shadow_no_private_accounts'),
        portfolio_mode=('monthly_liquidity_daily_cross_sectional_live'
                        if cross_sectional_execution_enabled else
                        'factor_isolated_dynamic_directional_live'
                        if execution_enabled
                        else 'factor_isolated_dynamic_directional_shadow'),
        strategy_mandate=('cross_sectional_long_short_live'
                          if cross_sectional_execution_enabled
                          else 'cross_sectional_long_short_transition'),
        cross_sectional_execution_enabled=cross_sectional_execution_enabled,
        sizing_status=('20x_live_factor_caps_60_70_3pct_leg_risk'
                       if execution_enabled
                       else '20x_preview_factor_caps_60_70_3pct_leg_risk'),
        rotation_status=('cross_sectional_targets_live_legacy_positions_exit_only'
                         if cross_sectional_execution_enabled else
                         'live_ledger_owned_positions_only' if execution_enabled
                         else 'shadow_proposals_only_no_orders'),
        position_limit_status=(
            f"combined_crypto_tradfi_max_{int(config.get('max_positions_total', 14))}"
        ),
    )
    assert config['minimum_score'] >= 6.5
    assert set(universes) == {'crypto', 'tradfi'}
    assert len(universes['crypto']) == 100
    assert all(values and len(values) == len(set(values)) for values in universes.values())
    assert not (set(universes['crypto']) & set(universes['tradfi']))
    async with database_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    scanner = StrategyEngine()
    scanner._execute_trade = deny_trade
    scanner._do_reduce = deny_trade
    scanner._calc_white_dove_margin_plan = deny_trade
    executor = None
    account_task = None
    risk_task = None
    if execution_enabled:
        executor_engine = StrategyEngine()
        executor = V5ExecutionManager(executor_engine, config, universes, STATE)
        await executor.bootstrap()
        account_task = asyncio.create_task(account_monitor_loop())
        risk_task = asyncio.create_task(executor.risk_loop())
    async def public_symbols():
        return [symbol for values in universes.values() for symbol in values]
    scanner._derivatives_market_data.start(public_symbols)
    task = asyncio.create_task(scan_loop(config, scanner, universes, executor=executor))
    status['universe_sizes'] = {name: len(values) for name, values in universes.items()}
    status['universe_size'] = sum(status['universe_sizes'].values())
    try:
        yield
    finally:
        task.cancel()
        if account_task is not None:
            account_task.cancel()
        if risk_task is not None:
            risk_task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if account_task is not None:
            with suppress(asyncio.CancelledError):
                await account_task
        if risk_task is not None:
            with suppress(asyncio.CancelledError):
                await risk_task
        scanner._derivatives_market_data.stop()
        if scanner._derivatives_market_data._task:
            with suppress(asyncio.CancelledError):
                await scanner._derivatives_market_data._task
        await okx_manager.aclose()
        await database_engine.dispose()


app = FastAPI(title='Baige V5 Shadow', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/health')
def health():
    return {'status': 'healthy', 'mode': status['mode'],
            'execution_enabled': status['execution_enabled'],
            'new_entries_enabled': status['new_entries_enabled'],
            'account_status': status['account_status'],
            'last_scan_completed': status['last_completed']}


@app.get('/status')
def get_status():
    return status


@app.get('/cross-sectional-research')
def cross_sectional_research():
    return research_summary(STATE)


@app.get('/account-performance')
async def account_performance(days: int = 30):
    """Read-only realized performance grouped by the isolated V5 account."""
    days = max(1, min(int(days), 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    close_time = case(
        (TradeRecord.strategy_tag == 'OKX同步', TradeRecord.created_at),
        else_=func.coalesce(TradeRecord.updated_at, TradeRecord.created_at),
    )
    async with AsyncSessionLocal() as db:
        configs = list((await db.execute(
            select(ExchangeConfig).order_by(ExchangeConfig.id)
        )).scalars().all())
        records = list((await db.execute(
            select(TradeRecord).where(
                TradeRecord.is_closed == True,
                TradeRecord.pnl_usdt.isnot(None),
                close_time >= since,
            ).order_by(close_time, TradeRecord.id)
        )).scalars().all())
    current = {int(row['id']): row for row in status.get('accounts') or []}
    rows = []
    for config in configs:
        account_records = [row for row in records if row.exchange_config_id == config.id]
        events = []
        for record in account_records:
            timestamp = record.created_at if record.strategy_tag == 'OKX同步' else (
                record.updated_at or record.created_at)
            events.append((timestamp, record.pnl_usdt, record.id))
        metrics = summarize_events(events)
        live = current.get(config.id, {})
        unrealized = float(live.get('unrealized_pnl_usd') or 0)
        rows.append({
            'id': f"baige-no5:{config.id}", 'name': config.name,
            'source': 'baige-no5', 'source_label': '白鸽五号',
            'equity': float(live.get('equity_usd') or 0),
            'available': float(live.get('available_usd') or 0),
            'position_count': int(live.get('live_position_count') or 0),
            'position_notional_usd': float(live.get('position_notional_usd') or 0),
            'position_margin_usd': float(live.get('position_margin_usd') or 0),
            'unrealized_pnl': round(unrealized, 2),
            'combined_pnl': round(metrics['realized_pnl'] + unrealized, 2),
            **metrics,
        })
    return {'period_days': days, 'accounts': rows}
