import os
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager

from app.core.config import settings
from app.api.deps import get_current_active_superuser
from app.api.v1.api import api_router
from app.db.init_db import init_db
from app.services.strategy_engine import strategy_engine
from app.services.okx_client import okx_manager

# Path to frontend dist
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")



@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    strategy_engine.start()
    # OpenClaw 脱钩（2026-07-17）：openclaw_trading_journal 已删除；
    # 其承载的 60s 周期对账节拍由 strategy_engine 主循环接管（RECONCILE_EVERY_LOOPS）
    yield
    await strategy_engine.aclose()
    await okx_manager.aclose()


def _openapi_kwargs(environment: str) -> dict:
    """Phase 3e：生产环境关闭交互式 API 文档与 schema 暴露面。

    /docs 把全部 88 个端点的形状（含鉴权方式、参数结构）直接摆给扫描器——
    开发环境是便利，生产环境是攻击面测绘图。
    """
    if (environment or "").strip().lower() in {"production", "prod"}:
        return {"openapi_url": None, "docs_url": None, "redoc_url": None}
    return {"openapi_url": f"{settings.API_V1_STR}/openapi.json",
            "docs_url": "/docs", "redoc_url": "/redoc"}


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan,
    **_openapi_kwargs(settings.ENVIRONMENT),
)

# GZip compression (must be before CORS)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_STR)


# Mount static files with long-term cache headers
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        # Assets with hash in filename can be cached long-term
        if "/assets/" in scope.get("path", ""):
            response.headers["Cache-Control"] = "public, max-age=86400"
        return response

if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", CachedStaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/health/engine")
async def engine_health(current_user=Depends(get_current_active_superuser)):
    """引擎健康快照（Phase 2.5f）：最后心跳/各段成败/最后成功下单/兜底止损成功率/告警投递。

    需要超管鉴权——这里暴露"机器人是否在跑、最近是否下单、兜底止损是否挂得上"，
    对交易系统而言是敏感运行状态，不做成公开端点（也避免给 Phase 3 再添一处零鉴权面）。
    心跳 stale 即代表 _run_loop 已死或被卡住。
    """
    return strategy_engine.health_snapshot()


@app.get("/api")
async def api_root():
    return {"message": f"欢迎使用 {settings.PROJECT_NAME} API", "version": settings.VERSION}


# Serve index.html for all non-API routes (SPA fallback)
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # API routes should be handled before this.
    # Phase 3e：此前这里返回 dict → FastAPI 包成 200，"Not Found" 冒充成功响应，
    # 探测器/客户端都会把不存在的 API 路径当成存在。
    # "redoc" 必须在名单里：production 下 redoc_url=None 不注册路由，请求会落到
    # 本兜底——不拦截就会 200 返回 SPA 页（冒烟实测抓到），与 /docs 的 404 不一致。
    if full_path.startswith(("api/", "docs", "redoc", "openapi.json")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    index_file = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(index_file):
        response = FileResponse(index_file)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    return {"message": f"欢迎使用 {settings.PROJECT_NAME} API", "version": settings.VERSION}
