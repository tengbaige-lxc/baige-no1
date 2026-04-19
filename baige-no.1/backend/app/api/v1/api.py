from fastapi import APIRouter
from app.api.v1.endpoints import login, users, roles, menus, logs, messages, dashboard, exchange, market, trade, strategy, trade_records, positions, performance, risk_config, signals, reduce_short, backtest, stream

api_router = APIRouter()

api_router.include_router(login.router, tags=["认证"])
api_router.include_router(users.router, prefix="/users", tags=["用户管理"])
api_router.include_router(roles.router, prefix="/roles", tags=["角色管理"])
api_router.include_router(menus.router, prefix="/menus", tags=["菜单管理"])
api_router.include_router(logs.router, prefix="/logs", tags=["操作日志"])
api_router.include_router(messages.router, prefix="/messages", tags=["消息中心"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["仪表盘"])
api_router.include_router(exchange.router, prefix="/exchange", tags=["交易所配置"])
api_router.include_router(market.router, prefix="/market", tags=["行情数据"])
api_router.include_router(trade.router, prefix="/trade", tags=["交易操作"])
api_router.include_router(strategy.router, prefix="/strategy", tags=["策略交易"])
api_router.include_router(trade_records.router, prefix="/trade-records", tags=["交易记录"])
api_router.include_router(positions.router, prefix="/positions", tags=["持仓监控"])
api_router.include_router(performance.router, prefix="/performance", tags=["绩效分析"])
api_router.include_router(risk_config.router, prefix="/risk-config", tags=["风控配置"])
api_router.include_router(signals.router, prefix="/signals", tags=["信号监控"])
api_router.include_router(reduce_short.router, prefix="/reduce-short", tags=["减仓做空"])
api_router.include_router(backtest.router, prefix="/backtest", tags=["回测系统"])
api_router.include_router(stream.router, prefix="/stream", tags=["实时推送"])
