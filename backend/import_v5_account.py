import asyncio
import json
import sys

from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.models.exchange_config import ExchangeConfig
from app.services.okx_client import encrypt_text


async def main():
    payload = json.load(sys.stdin)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ExchangeConfig).where(ExchangeConfig.name == payload["name"])
        )
        account = result.scalar_one_or_none()
        values = {
            "exchange": payload["exchange"],
            "api_key": encrypt_text(payload["api_key"]),
            "api_secret": encrypt_text(payload["api_secret"]),
            "api_passphrase": encrypt_text(payload["api_passphrase"]),
            "is_testnet": bool(payload["is_testnet"]),
            "is_active": True,
        }
        if account is None:
            account = ExchangeConfig(name=payload["name"], **values)
            db.add(account)
        else:
            for key, value in values.items():
                setattr(account, key, value)
        await db.commit()
        await db.refresh(account)
        print(json.dumps({"id": account.id, "name": account.name,
                          "is_active": bool(account.is_active)}))


if __name__ == "__main__":
    asyncio.run(main())
