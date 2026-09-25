import asyncio
import json

from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.models.exchange_config import ExchangeConfig
from app.services.okx_client import decrypt_text


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ExchangeConfig).where(ExchangeConfig.name == "openclaw")
        )
        account = result.scalar_one()
    print(json.dumps({
        "name": account.name,
        "exchange": account.exchange,
        "api_key": decrypt_text(account.api_key),
        "api_secret": decrypt_text(account.api_secret),
        "api_passphrase": decrypt_text(account.api_passphrase or ""),
        "is_testnet": bool(account.is_testnet),
    }))


if __name__ == "__main__":
    asyncio.run(main())
