import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os

from app.core.config import settings
from app.api.v1.api import api_router
from app.db.init_db import init_db
from app.services.strategy_engine import strategy_engine

# Path to frontend dist
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")



@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    strategy_engine.start()
    yield
    strategy_engine.stop()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
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
        # Assets with hash in filename can be cached forever
        if "/assets/" in scope.get("path", ""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

if os.path.isdir(FRONTEND_DIST):
    app.mount("/assets", CachedStaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="assets")


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/api")
async def api_root():
    return {"message": f"欢迎使用 {settings.PROJECT_NAME} API", "version": settings.VERSION}


# Serve index.html for all non-API routes (SPA fallback)
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # API routes should be handled before this
    if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi.json"):
        return {"detail": "Not Found"}
    index_file = os.path.join(FRONTEND_DIST, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": f"欢迎使用 {settings.PROJECT_NAME} API", "version": settings.VERSION}
