"""
新闻因子反馈系统：对比新闻预测方向和实际币价走势，持续学习优化。
"""
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, and_, text
from app.db.base import AsyncSessionLocal
from app.models.news_factor import NewsFactor
from app.models.news_feedback import NewsFactorFeedback
from app.services.okx_client import okx_manager


async def check_news_accuracy(lookback_hours: int = 4):
    """
    检查 N 小时前的新闻预测是否准确。
    流程：
    1. 找出 lookback_hours 之前发布、且还没被检查过的新闻
    2. 获取新闻发布时的价格（近似）
    3. 获取现在（或 lookback_hours 后）的价格
    4. 计算收益率，对比预测方向
    5. 写入反馈表
    """
    try:
        async with AsyncSessionLocal() as db:
            now = datetime.now(timezone.utc)
            window_start = now - timedelta(hours=lookback_hours + 1)
            window_end = now - timedelta(hours=lookback_hours - 1)

            # 取待检查的新闻（排除已检查的）
            result = await db.execute(
                select(NewsFactor).where(
                    and_(
                        NewsFactor.created_at > window_start,
                        NewsFactor.created_at < window_end,
                        NewsFactor.direction.in_(["bullish", "bearish"]),
                    )
                ).limit(50)
            )
            news_list = result.scalars().all()
            if not news_list:
                return 0, 0

            checked = 0
            correct = 0
            for news in news_list:
                # 去重：如果已经检查过就跳过
                dup = await db.execute(
                    select(NewsFactorFeedback).where(
                        NewsFactorFeedback.news_factor_id == news.id
                    ).limit(1)
                )
                if dup.scalar_one_or_none():
                    continue

                symbol = f"{news.symbol}-USDT-SWAP"
                try:
                    # 获取 4H K 线，找最接近新闻发布时间的收盘价
                    klines = await okx_manager.get_candles(symbol, "4H", 10)
                    if len(klines) < 2:
                        continue

                    # 找新闻发布时间附近的价格
                    news_ts = news.created_at.timestamp() * 1000
                    price_at = None
                    price_after = None
                    for i, k in enumerate(klines):
                        candle_ts = int(k[0])
                        if candle_ts <= news_ts:
                            price_at = float(k[4])
                        if candle_ts <= news_ts + lookback_hours * 3600 * 1000:
                            price_after = float(k[4])

                    if price_at is None or price_after is None or price_at == 0:
                        continue

                    ret = (price_after - price_at) / price_at
                    is_correct = (news.direction == "bullish" and ret > 0) or (
                        news.direction == "bearish" and ret < 0
                    )

                    fb = NewsFactorFeedback(
                        news_factor_id=news.id,
                        symbol=news.symbol,
                        predicted_direction=news.direction,
                        predicted_strength=news.strength,
                        price_at_news=price_at,
                        price_after_4h=price_after,
                        return_4h=ret,
                        is_correct_4h=is_correct,
                    )
                    db.add(fb)
                    checked += 1
                    if is_correct:
                        correct += 1

                except Exception as e:
                    print(f"[news-feedback] 检查 {symbol} 失败: {e}")
                    continue

            await db.commit()

            if checked > 0:
                acc = correct / checked * 100
                print(
                    f"📊 新闻因子回测({lookback_hours}h): 检查{checked}条, 正确{correct}条, 准确率{acc:.1f}%"
                )
            return checked, correct

    except Exception as e:
        print(f"[news-feedback] 回测任务异常: {e}")
        return 0, 0


async def auto_fetch_and_analyze_news():
    """
    自动抓取免费币新闻(CoinDesk/CoinTelegraph/Decrypt)，分析后写入 news_factors 表。
    每小时执行一次。
    """
    try:
        # OpenClaw 脱钩（2026-07-17）：抓取模块已收编进 app/vendor/news_fetch，
        # 不再从 /root/.openclaw/workspace 注入 sys.path——那条路径在本仓库不存在，
        # 此前本函数在本地是 ImportError 静默降级、从未真正工作过。
        from app.vendor.news_fetch import (
            fetch_coindesk_rss,
            fetch_cointelegraph_rss,
            fetch_decrypt_rss,
            fetch_google_news_rss,
            deduplicate_news,
        )

        # 抓取多个源。四个 fetch_*_rss 都是同步网络 IO，而本函数是被
        # strategy_engine._run_loop 直接 await 的（每 ~1h 一次）——此前这几行会
        # 卡住整个事件循环，期间软件止损轮询与离场检查全部停摆（Phase 2.5d / E14）。
        # 丢进线程池，_run_loop 得以继续按节拍推进。
        def _fetch_all_blocking():
            news = []
            news.extend(fetch_coindesk_rss(10))
            news.extend(fetch_cointelegraph_rss(10))
            news.extend(fetch_decrypt_rss(10))
            news.extend(fetch_google_news_rss(10))
            return deduplicate_news(news)

        loop = asyncio.get_running_loop()
        all_news = await loop.run_in_executor(None, _fetch_all_blocking)

        if not all_news:
            print("[news-feedback] 未抓取到新闻")
            return 0

        from app.services.news_analyzer import news_analyzer, get_ttl_for_dimension
        from app.services.price_context import get_price_position, adjust_news_by_price, get_zone_label

        inserted = 0
        async with AsyncSessionLocal() as db:
            for item in all_news[:20]:
                title = item.get("title", "")
                if not title:
                    continue

                results = news_analyzer.analyze(
                    title=title,
                    summary=item.get("summary", ""),
                    source=item.get("source", "news"),
                    url=item.get("url", ""),
                )
                for r in results:
                    # 去重
                    dup = await db.execute(
                        select(NewsFactor).where(NewsFactor.title == title).limit(1)
                    )
                    if dup.scalar_one_or_none():
                        continue

                    # 获取价格位置并修正
                    symbol_swap = f"{r.symbol}-USDT-SWAP"
                    position, current, high, low, trend = await get_price_position(symbol_swap)
                    adj_direction, adj_strength, adj_note = adjust_news_by_price(
                        r.direction, r.strength, position
                    )

                    # 构建原始摘要
                    raw = f"{r.reason}|price_pos={position:.0%}|zone={get_zone_label(position)}"
                    if adj_note:
                        raw += f"|{adj_note}"

                    nf = NewsFactor(
                        symbol=r.symbol,
                        title=title[:500],
                        source=r.source,
                        url=r.url,
                        direction=adj_direction,
                        strength=adj_strength,
                        dimension=r.dimension,
                        raw_summary=raw,
                        expires_at=datetime.now(timezone.utc)
                        + timedelta(seconds=get_ttl_for_dimension(r.dimension)),
                    )
                    db.add(nf)
                    inserted += 1
                    if adj_note:
                        print(f"📰 [{r.symbol}] {title[:50]}... → 原{r.direction}/{r.strength} 修正为 {adj_direction}/{adj_strength} ({adj_note})")

            await db.commit()
            if inserted > 0:
                print(f"📰 自动抓取新闻: 写入 {inserted} 条因子")
            return inserted

    except Exception as e:
        print(f"[news-feedback] 抓取任务异常: {e}")
        import traceback
        traceback.print_exc()
        return 0
