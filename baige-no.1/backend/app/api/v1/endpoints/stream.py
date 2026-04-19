import asyncio
import json
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.services.market_service import market_service

router = APIRouter()


async def market_stream(symbol: str):
    while True:
        try:
            ticker = await market_service.get_ticker(symbol)
            book = await market_service.get_orderbook(symbol)
            data = {
                "type": "market",
                "ticker": ticker,
                "orderbook": book,
            }
            yield f"data: {json.dumps(data)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'msg': str(e)})}\n\n"
        await asyncio.sleep(1)


@router.get("")
async def stream_market(symbol: str = Query(...)):
    return StreamingResponse(
        market_stream(symbol),
        media_type="text/event-stream",
    )
