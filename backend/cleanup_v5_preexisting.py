import asyncio

from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.models.exchange_config import ExchangeConfig
from app.services.okx_client import decrypt_text, okx_manager


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ExchangeConfig).where(ExchangeConfig.name == "openclaw")
        )
        account = result.scalar_one()
    key = decrypt_text(account.api_key)
    secret = decrypt_text(account.api_secret)
    passphrase = decrypt_text(account.api_passphrase or "")
    pending = await okx_manager.get_pending_algo_stops(
        key, secret, passphrase, "SOXL-USDT-SWAP", bool(account.is_testnet)
    )
    v5_stops = [
        {"algoId": row["algoId"], "instId": row["instId"]}
        for row in pending
        if str(row.get("algoClOrdId") or "").startswith("bg2sl")
    ]
    results = await okx_manager.cancel_algo_orders(
        key, secret, passphrase, v5_stops, bool(account.is_testnet)
    )
    print({"matched": len(v5_stops), "cancel_results": results})
    await okx_manager.aclose()


if __name__ == "__main__":
    asyncio.run(main())
