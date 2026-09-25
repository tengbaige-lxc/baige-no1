import asyncio
import json
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user_from_header_or_cookie
from app.models.user import User
from app.services.market_service import market_service

router = APIRouter()


async def market_stream(symbol: str):
    """SSE market data stream with graceful disconnect handling."""
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
        except asyncio.CancelledError:
            # Client disconnected, exit cleanly
            break
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'msg': str(e)})}\n\n"
        await asyncio.sleep(1)


@router.get("")
async def stream_market(
    symbol: str = Query(...),
    # Phase 3c（S8）：此前零鉴权——未登录者可白拿行情推送，且每个连接都在消耗
    # 引擎共享的 OKX 限流预算（A12）。EventSource 无法带 Authorization 头，
    # 故用"头优先、cookie 兜底"，陈旧 dist 前端亦无需重建。
    current_user: User = Depends(get_current_user_from_header_or_cookie),
):
    """Real-time market data SSE endpoint."""
    response = StreamingResponse(
        market_stream(symbol),
        media_type="text/event-stream",
    )
    # Prevent any caching/proxy buffering for SSE
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"  # Disable nginx buffering if present
    return response
