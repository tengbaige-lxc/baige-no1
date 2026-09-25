import asyncio
import os
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Optional
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.common import ResponseModel
from app.services.news_analyzer import news_analyzer, get_ttl_for_dimension
from app.db.base import AsyncSessionLocal
from app.models.news_factor import NewsFactor
from sqlalchemy import select, and_

router = APIRouter()

CACHE_FILE = os.path.join(os.path.dirname(__file__), "../../../../data/news_cache.json")
TRANSLATION_CACHE_FILE = os.path.join(os.path.dirname(__file__), "../../../../data/news_translation_cache.json")
CACHE_TTL = 300  # 5 minutes

class NewsItem(BaseModel):
    source: str
    title: str
    title_zh: Optional[str] = None
    url: str
    time: str
    summary: str
    summary_zh: Optional[str] = None

class NewsFactorItem(BaseModel):
    symbol: str
    direction: str
    strength: int
    dimension: str
    title: str
    source: str
    reason: str

class NewsResponse(BaseModel):
    items: List[NewsItem]
    updated_at: str
    count: int
    factors: Optional[List[NewsFactorItem]] = None


def _ensure_dir():
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)


def _load_translation_cache():
    try:
        if os.path.exists(TRANSLATION_CACHE_FILE):
            with open(TRANSLATION_CACHE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _save_translation_cache(cache: dict):
    try:
        with open(TRANSLATION_CACHE_FILE, "w") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text or "")


def _translate_to_zh(text: str, cache: dict) -> str:
    """Translate short news text to Simplified Chinese with a local cache."""
    text = (text or "").strip()
    if not text:
        return ""
    if _has_chinese(text):
        return text
    if text in cache:
        return cache[text]

    try:
        query = urllib.parse.urlencode({
            "client": "gtx",
            "sl": "auto",
            "tl": "zh-CN",
            "dt": "t",
            "q": text[:900],
        })
        url = f"https://translate.googleapis.com/translate_a/single?{query}"
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        translated = "".join(part[0] for part in payload[0] if part and part[0]).strip()
        cache[text] = translated or text
        return cache[text]
    except Exception:
        cache[text] = text
        return text


def _localize_news_items(items: List[dict]) -> List[dict]:
    cache = _load_translation_cache()
    changed = False
    localized = []
    for item in items:
        next_item = dict(item)
        title = next_item.get("title", "")
        summary = next_item.get("summary", "")
        if title and not next_item.get("title_zh"):
            before = len(cache)
            next_item["title_zh"] = _translate_to_zh(title, cache)
            changed = changed or len(cache) != before
        if summary and not next_item.get("summary_zh"):
            before = len(cache)
            next_item["summary_zh"] = _translate_to_zh(summary, cache)
            changed = changed or len(cache) != before
        localized.append(next_item)

    if changed:
        _save_translation_cache(cache)
    return localized


async def _analyze_and_store_news(items: List[dict]):
    """分析新闻并写入新闻因子表（去重：同title跳过）"""
    try:
        async with AsyncSessionLocal() as db:
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue
                # 去重检查
                existing = await db.execute(
                    select(NewsFactor).where(NewsFactor.title == title).limit(1)
                )
                if existing.scalar_one_or_none():
                    continue

                results = news_analyzer.analyze(
                    title=title,
                    summary=item.get("summary", ""),
                    source=item.get("source", ""),
                    url=item.get("url", ""),
                )
                for r in results:
                    ttl = get_ttl_for_dimension(r.dimension)
                    factor = NewsFactor(
                        symbol=r.symbol,
                        title=r.title,
                        source=r.source,
                        url=r.url,
                        direction=r.direction,
                        strength=r.strength,
                        dimension=r.dimension,
                        raw_summary=item.get("summary", ""),
                        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
                    )
                    db.add(factor)
            await db.commit()
    except Exception as e:
        print(f"新闻分析入库失败: {e}")


def _fetch_news_from_script(limit=12):
    """抓取新闻（OpenClaw 脱钩后走收编的 app/vendor/news_fetch，2026-07-17）。

    此前是 subprocess 跑 OpenClaw workspace 里的脚本、再按 emoji 标记解析其
    stdout——路径在本仓库不存在，该功能本地从未真正工作过。现直接函数调用，
    item 形状不变（source/title/url/time/summary）。仍是同步网络 IO，
    调用方 _get_cached_news 已整体跑在 executor（2.5d）。
    """
    try:
        from app.vendor.news_fetch import fetch_all

        return fetch_all(limit)
    except Exception:
        return []


def _get_cached_news(limit=12):
    """获取缓存的新闻，过期则重新抓取"""
    _ensure_dir()
    
    # 检查缓存
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cache = json.load(f)
            if time.time() - cache.get("timestamp", 0) < CACHE_TTL:
                items = cache.get("items", [])[:limit]
                return _localize_news_items(items), cache.get("updated_at", "")
        except Exception:
            pass
    
    # 重新抓取
    items = _fetch_news_from_script(limit)
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({
                "items": items,
                "timestamp": time.time(),
                "updated_at": updated_at
            }, f, ensure_ascii=False)
    except Exception:
        pass
    
    return _localize_news_items(items), updated_at


@router.get("/latest", response_model=ResponseModel[NewsResponse])
async def get_latest_news(
    limit: int = 12,
    # Phase 3c（S8）：此前零鉴权，缓存过期时未登录者即可白拿一次重抓取。
    # 前端经 axios 全局带 Bearer 头，标准鉴权即可，src/dist 均不受影响。
    current_user: User = Depends(get_current_user),
):
    """获取最新币圈新闻（含分析因子）"""
    # _get_cached_news 内部全是同步阻塞（urllib 翻译每条 timeout=8s 最多 24 次 +
    # 收编后的 RSS 抓取），必须整体下沉 executor（Phase 2.5d / E14）——
    # 此前直接跑在事件循环上，打一次该端点能让引擎的软件止损轮询停摆 1-3 分钟。
    loop = asyncio.get_running_loop()
    items, updated_at = await loop.run_in_executor(None, _get_cached_news, limit)
    # 分析入库（纯规则，毫秒级，不显著阻塞响应）
    await _analyze_and_store_news(items)
    # 读取最近分析出的因子
    factors = []
    try:
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=6)
            result = await db.execute(
                select(NewsFactor).where(
                    NewsFactor.created_at > cutoff
                ).order_by(NewsFactor.created_at.desc()).limit(20)
            )
            rows = result.scalars().all()
            factors = [
                NewsFactorItem(
                    symbol=r.symbol,
                    direction=r.direction,
                    strength=r.strength,
                    dimension=r.dimension,
                    title=r.title,
                    source=r.source,
                    reason=f"{r.dimension}|{r.direction}|强度{r.strength}",
                )
                for r in rows
            ]
    except Exception:
        factors = []

    return ResponseModel(
        data=NewsResponse(
            items=[NewsItem(**item) for item in items],
            updated_at=updated_at,
            count=len(items),
            factors=factors,
        )
    )


@router.get("/factors", response_model=ResponseModel[List[NewsFactorItem]])
async def get_news_factors(
    symbol: Optional[str] = None,
    hours: int = 24,
    current_user: User = Depends(get_current_user),  # Phase 3c（S8）：同上补鉴权
):
    """获取新闻因子（可筛选币种）"""
    try:
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=hours)
            query = select(NewsFactor).where(
                and_(
                    NewsFactor.created_at > cutoff,
                    NewsFactor.expires_at > now,
                )
            ).order_by(NewsFactor.created_at.desc()).limit(50)
            if symbol:
                query = query.where(NewsFactor.symbol == symbol.upper())
            result = await db.execute(query)
            rows = result.scalars().all()
            factors = [
                NewsFactorItem(
                    symbol=r.symbol,
                    direction=r.direction,
                    strength=r.strength,
                    dimension=r.dimension,
                    title=r.title,
                    source=r.source,
                    reason=f"{r.dimension}|{r.direction}|强度{r.strength}",
                )
                for r in rows
            ]
            return ResponseModel(data=factors)
    except Exception as e:
        return ResponseModel(data=[], message=f"查询失败: {e}")
