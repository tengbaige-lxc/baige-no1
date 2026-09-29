import asyncio
from typing import Any

from app.services.binance_client import binance_futures_client
from app.services.okx_client import decrypt_text, okx_manager


SUPPORTED_EXCHANGES = frozenset({"okx", "binance"})


def normalize_exchange(value: str) -> str:
    exchange = (value or "").strip().lower()
    if exchange not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"unsupported exchange: {value}; expected one of "
            f"{', '.join(sorted(SUPPORTED_EXCHANGES))}"
        )
    return exchange


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _base_summary(config: Any) -> dict:
    exchange = normalize_exchange(config.exchange)
    return {
        "id": config.id,
        "name": config.name,
        "exchange": exchange,
        "is_active": bool(config.is_active),
        "is_testnet": bool(config.is_testnet),
        "execution_supported": exchange == "okx",
    }


def build_okx_account_summary(
    config: Any,
    balances: list[dict],
    positions: list[dict],
) -> dict:
    usdt = next((item for item in balances if item.get("ccy") == "USDT"), {})
    open_positions = [item for item in positions if _as_float(item.get("pos")) != 0]
    long_count = 0
    short_count = 0
    for position in open_positions:
        side = (position.get("posSide") or "net").lower()
        size = _as_float(position.get("pos"))
        if side == "long" or (side == "net" and size > 0):
            long_count += 1
        else:
            short_count += 1

    return {
        **_base_summary(config),
        "connection_status": "connected",
        "position_mode": "unknown",
        "usdt_equity": round(_as_float(usdt.get("eq")), 4),
        "usdt_available": round(
            _as_float(usdt.get("availEq") or usdt.get("availBal")), 4
        ),
        "position_count": len(open_positions),
        "long_count": long_count,
        "short_count": short_count,
        "position_notional_usd": round(
            sum(abs(_as_float(item.get("notionalUsd"))) for item in open_positions), 2
        ),
        "position_margin_usd": round(
            sum(
                abs(_as_float(item.get("imr") or item.get("margin")))
                for item in open_positions
            ),
            2,
        ),
        "unrealized_pnl": round(
            sum(_as_float(item.get("upl")) for item in open_positions), 2
        ),
        "error": None,
    }


def build_binance_account_summary(
    config: Any,
    account: dict,
    position_mode: str,
) -> dict:
    positions = account.get("positions") or []
    usdt = next(
        (item for item in account.get("assets") or [] if item.get("asset") == "USDT"),
        {},
    )
    open_positions = [
        item for item in positions if _as_float(item.get("positionAmt")) != 0
    ]
    long_count = 0
    short_count = 0
    for position in open_positions:
        side = (position.get("positionSide") or "BOTH").upper()
        size = _as_float(position.get("positionAmt"))
        if side == "LONG" or (side == "BOTH" and size > 0):
            long_count += 1
        else:
            short_count += 1

    return {
        **_base_summary(config),
        "connection_status": "connected",
        "position_mode": position_mode,
        "usdt_equity": round(
            _as_float(usdt.get("marginBalance") or account.get("totalMarginBalance")),
            4,
        ),
        "usdt_available": round(
            _as_float(usdt.get("availableBalance") or account.get("availableBalance")),
            4,
        ),
        "position_count": len(open_positions),
        "long_count": long_count,
        "short_count": short_count,
        "position_notional_usd": round(
            sum(abs(_as_float(item.get("notional"))) for item in open_positions), 2
        ),
        "position_margin_usd": round(
            sum(
                abs(
                    _as_float(
                        item.get("initialMargin") or item.get("isolatedWallet")
                    )
                )
                for item in open_positions
            ),
            2,
        ),
        "unrealized_pnl": round(
            sum(
                _as_float(item.get("unrealizedProfit"))
                for item in open_positions
            ),
            2,
        ),
        "error": None,
    }


async def validate_exchange_credentials(
    exchange: str,
    *,
    api_key: str,
    api_secret: str,
    api_passphrase: str = "",
    is_testnet: bool = False,
) -> None:
    exchange = normalize_exchange(exchange)
    if exchange == "okx":
        if not api_passphrase:
            raise ValueError("OKX requires an API passphrase")
        await okx_manager.get_balance(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=api_passphrase,
            simulated=is_testnet,
        )
        return

    await binance_futures_client.validate_credentials(
        api_key,
        api_secret,
        testnet=is_testnet,
    )


async def load_account_summary(config: Any) -> dict:
    try:
        exchange = normalize_exchange(config.exchange)
        api_key = decrypt_text(config.api_key)
        api_secret = decrypt_text(config.api_secret)
        passphrase = decrypt_text(config.api_passphrase or "")

        if exchange == "okx":
            balances, positions = await asyncio.gather(
                okx_manager.get_balance(
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    simulated=bool(config.is_testnet),
                ),
                okx_manager.get_positions(
                    api_key,
                    api_secret,
                    passphrase,
                    simulated=bool(config.is_testnet),
                ),
            )
            return build_okx_account_summary(config, balances, positions)

        account, position_mode = await asyncio.gather(
            binance_futures_client.get_account(
                api_key,
                api_secret,
                testnet=bool(config.is_testnet),
            ),
            binance_futures_client.get_position_mode(
                api_key,
                api_secret,
                testnet=bool(config.is_testnet),
            ),
        )
        return build_binance_account_summary(config, account, position_mode)
    except Exception as exc:
        try:
            base = _base_summary(config)
        except ValueError:
            base = {
                "id": config.id,
                "name": config.name,
                "exchange": (config.exchange or "unknown").lower(),
                "is_active": bool(config.is_active),
                "is_testnet": bool(config.is_testnet),
                "execution_supported": False,
            }
        return {
            **base,
            "connection_status": "error",
            "position_mode": "unknown",
            "usdt_equity": 0.0,
            "usdt_available": 0.0,
            "position_count": 0,
            "long_count": 0,
            "short_count": 0,
            "position_notional_usd": 0.0,
            "position_margin_usd": 0.0,
            "unrealized_pnl": 0.0,
            "error": str(exc),
        }
