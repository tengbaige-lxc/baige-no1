"""免费币新闻抓取（CoinDesk / CoinTelegraph / Decrypt / Google News RSS）。

收编来源：服务器 /root/.openclaw/workspace/skills/crypto-news-free/scripts/fetch_news.py
（2026-07-17，OpenClaw 脱钩时收编进 app/vendor/，与派生时收编缠论/清算/宏观同一惯例）。
函数体除本注释与删除 CLI main() 外一行未改；纯标准库（urllib + xml.etree），无外部依赖。

新增 fetch_all()：复刻原 main() 的聚合语义（四源各抓 limit 条 → 按标题去重 →
超量时按来源轮转截断保证多样性），供 news 端点与 news_feedback 直接调用，
取代此前"subprocess 跑脚本再按 emoji 标记解析 stdout"的有损往返。
注意：所有函数都是同步网络 IO——调用方必须放 executor（Phase 2.5d 的约定）。
"""

"""
免费币新闻抓取器
抓取CoinDesk、CoinTelegraph、Decrypt RSS和Google News
"""

import sys
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime


def fetch_coindesk_rss(limit=10):
    """抓取CoinDesk RSS"""
    try:
        url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
            
        root = ET.fromstring(data)
        
        news_list = []
        channel = root.find('channel')
        if channel is not None:
            items = channel.findall('item')[:limit]
            for item in items:
                title = item.find('title')
                link = item.find('link')
                pub_date = item.find('pubDate')
                description = item.find('description')
                
                if title is not None and link is not None:
                    news_list.append({
                        'source': 'CoinDesk',
                        'title': title.text,
                        'url': link.text,
                        'time': pub_date.text if pub_date is not None else 'N/A',
                        'summary': description.text[:200] + '...' if description is not None and len(description.text) > 200 else (description.text if description is not None else '')
                    })
        
        return news_list
    except Exception as e:
        print(f"CoinDesk抓取失败: {e}", file=sys.stderr)
        return []


def fetch_cointelegraph_rss(limit=10):
    """抓取CoinTelegraph RSS"""
    try:
        url = "https://cointelegraph.com/rss"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
            
        root = ET.fromstring(data)
        
        news_list = []
        channel = root.find('channel')
        if channel is not None:
            items = channel.findall('item')[:limit]
            for item in items:
                title = item.find('title')
                link = item.find('link')
                pub_date = item.find('pubDate')
                
                if title is not None:
                    news_list.append({
                        'source': 'CoinTelegraph',
                        'title': title.text,
                        'url': link.text if link is not None else 'N/A',
                        'time': pub_date.text if pub_date is not None else 'N/A',
                        'summary': ''
                    })
        
        return news_list
    except Exception as e:
        print(f"CoinTelegraph抓取失败: {e}", file=sys.stderr)
        return []


def fetch_decrypt_rss(limit=10):
    """抓取Decrypt RSS"""
    try:
        url = "https://decrypt.co/rss"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
            
        root = ET.fromstring(data)
        
        news_list = []
        channel = root.find('channel')
        if channel is not None:
            items = channel.findall('item')[:limit]
            for item in items:
                title = item.find('title')
                link = item.find('link')
                pub_date = item.find('pubDate')
                description = item.find('description')
                
                if title is not None:
                    summary = ''
                    if description is not None and description.text:
                        summary = description.text[:200] + '...' if len(description.text) > 200 else description.text
                    
                    news_list.append({
                        'source': 'Decrypt',
                        'title': title.text,
                        'url': link.text if link is not None else 'N/A',
                        'time': pub_date.text if pub_date is not None else 'N/A',
                        'summary': summary
                    })
        
        return news_list
    except Exception as e:
        print(f"Decrypt抓取失败: {e}", file=sys.stderr)
        return []


def fetch_google_news_rss(limit=10):
    """抓取Google News Crypto RSS"""
    try:
        url = "https://news.google.com/rss/search?q=crypto+bitcoin+ethereum+when:1d&hl=en-US&gl=US&ceid=US:en"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
            
        root = ET.fromstring(data)
        
        news_list = []
        channel = root.find('channel')
        if channel is not None:
            items = channel.findall('item')[:limit]
            for item in items:
                title = item.find('title')
                link = item.find('link')
                pub_date = item.find('pubDate')
                source_elem = item.find('source')
                
                if title is not None:
                    source_name = source_elem.text if source_elem is not None else 'GoogleNews'
                    news_list.append({
                        'source': f'GoogleNews/{source_name}',
                        'title': title.text,
                        'url': link.text if link is not None else 'N/A',
                        'time': pub_date.text if pub_date is not None else 'N/A',
                        'summary': ''
                    })
        
        return news_list
    except Exception as e:
        print(f"GoogleNews抓取失败: {e}", file=sys.stderr)
        return []


def deduplicate_news(news_list):
    """按标题去重，保留第一个来源"""
    seen = set()
    result = []
    for news in news_list:
        title_lower = news['title'].lower().strip()
        if title_lower and title_lower not in seen:
            seen.add(title_lower)
            result.append(news)
    return result

def fetch_all(limit: int = 10) -> list:
    """四源聚合：各抓 limit 条 → 标题去重 → 超量时按来源轮转截断（保多样性）。

    与被收编脚本 main() 的聚合逻辑一致（不含币种过滤与打印）。
    """
    all_news = []
    all_news.extend(fetch_coindesk_rss(limit))
    all_news.extend(fetch_cointelegraph_rss(limit))
    all_news.extend(fetch_decrypt_rss(limit))
    all_news.extend(fetch_google_news_rss(limit))
    all_news = deduplicate_news(all_news)

    if len(all_news) > limit:
        from collections import defaultdict
        by_source = defaultdict(list)
        for news in all_news:
            by_source[news["source"].split("/")[0]].append(news)
        result = []
        while len(result) < limit and any(by_source[s] for s in by_source):
            for src_key in list(by_source.keys()):
                if by_source[src_key] and len(result) < limit:
                    result.append(by_source[src_key].pop(0))
        all_news = result
    return all_news
