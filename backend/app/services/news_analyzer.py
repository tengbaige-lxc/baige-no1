"""
新闻因子分析引擎
提取币种 → 判断方向 → 识别驱动维度 → 计算强度分
"""
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class NewsFactorResult:
    symbol: str           # 币种，如 BTC
    direction: str        # bullish / bearish / neutral
    strength: int         # -5 ~ +5
    dimension: str        # capital / demand / regulation / liquidity / narrative
    title: str
    source: str
    url: str
    reason: str           # 分析理由摘要


# ========== 规则库 ==========

COIN_ALIASES = {
    "BTC": ["bitcoin", "btc", "比特币"],
    "ETH": ["ethereum", "eth", "以太", "以太坊"],
    "SOL": ["solana", "sol", "索拉纳"],
    "XRP": ["ripple", "xrp", "瑞波"],
    "DOGE": ["dogecoin", "doge", "狗狗币"],
    "BNB": ["bnb", "binance coin", "币安币"],
    "ADA": ["cardano", "ada", "艾达"],
    "AVAX": ["avalanche", "avax", "雪崩"],
    "LINK": ["chainlink", "link", "预言机"],
    "ARB": ["arbitrum", "arb"],
    "OP": ["optimism", "op"],
    "SUI": ["sui"],
    "SEI": ["sei"],
    "TIA": ["celestia", "tia"],
    "PYTH": ["pyth", "pyth network"],
    "WLD": ["worldcoin", "wld"],
    "PEPE": ["pepe"],
    "SHIB": ["shib", "shiba"],
    "POL": ["polygon", "matic", "pol", "马蹄"],
    "DOT": ["polkadot", "dot", "波卡"],
    "ATOM": ["cosmos", "atom"],
    "NEAR": ["near"],
    "APT": ["aptos", "apt"],
    "STRK": ["starknet", "strk"],
}

DIMENSION_RULES = {
    "capital": {
        "keywords": [
            "融资", "投资", "fund", "funding", "invest",
            "ETF", "流入", "inflow", "锁仓", "stake", "staking",
            "质押", "回购", "buyback", "销毁", "burn",
            "空投", "airdrop", "拨款", "grant", "treasury",
        ],
        "weight": 1.2,
    },
    "demand": {
        "keywords": [
            "合作", "partner", "partnership", "集成", "integrate",
            "采用", "adopt", "adoption", "主网上线", "mainnet",
            "用户增长", "active address", "DAA", "MAA", "TVL",
            "交易量", "volume", "生态", "ecosystem", "dapp",
            "支付", "payment", "商户", "merchant",
        ],
        "weight": 1.0,
    },
    "regulation": {
        "keywords": [
            "SEC", "CFTC", "批准", "approve", "approval",
            "合规", "compliance", "牌照", "license",
            "立法", "legislation", "法案", "bill",
            "监管", "regulation",
            "禁令", "ban", "禁止", "prohibit",
            "下架", "delist", "诉讼", "sue", "lawsuit",
            "罚款", "fine", "调查", "investigation",
        ],
        "weight": 1.3,
    },
    "liquidity": {
        "keywords": [
            "上线", "list", "listing", "上币",
            "交易所", "exchange", "Binance", "Coinbase", "Upbit", "OKX",
            "做市商", "market maker", "MM",
            "流动性", "liquidity", "流动性挖矿", "LP",
            "合约上线", "永续合约", "perpetual", "future",
        ],
        "weight": 0.9,
    },
    "narrative": {
        "keywords": [
            "AI", "人工智能", "RWA", "真实资产",
            "DePIN", "去中心化物理", "模块化", "modular",
            "Layer2", "L2", "ZK", "零知识", "GameFi",
            "SocialFi", "DeFi", "ReFi", "意图", "intent",
            "restaking", "再质押", "BTCFi", "铭文", "inscription",
            "Meme", "文化", "社区", "叙事", "narrative",
        ],
        "weight": 0.7,
    },
}

SENTIMENT_POSITIVE = [
    "利好", "突破", "暴涨", "大涨", "反弹", "看涨", "bullish", "surge", "soar",
    "rally", "breakout", "approve", "批准", "通过", "合作", "上线", "上币", "list", "listing",
    "流入", "增持", "买入", "buy", "long", "多头", "强势", "创新高", "ATH",
    "里程碑", "milestone", "成功", "launch", "主网上线", "集成", "heats up",
]

SENTIMENT_NEGATIVE = [
    "利空", "暴跌", "大跌", "下跌", "看跌", "bearish", "crash", "plunge",
    "dump", "collapse", "decline", "禁止", "禁令", "下架", "调查", "诉讼",
    "罚款", "减持", "卖出", "sell", "short", "空头", "崩盘", "跌破",
    "失败", "延迟", "delay", "delayed", "取消", "cancel", "reject", "拒绝",
    "漏洞", "bug", "hack", "攻击", "exploit", "暂停", "suspend", "withdraw", "撤离",
]

SOURCE_TRUST = {
    "coindesk": 1.0,
    "cointelegraph": 1.0,
    "theblock": 1.0,
    "blockworks": 0.9,
    "decrypt": 0.9,
    "cryptonews": 0.7,
    "cryptopanic": 0.6,
    "twitter": 0.5,
    "x.com": 0.5,
    "default": 0.6,
}


class NewsAnalyzer:
    def __init__(self):
        self._build_coin_regex()

    def _build_coin_regex(self):
        self.coin_patterns: Dict[str, re.Pattern] = {}
        for symbol, aliases in COIN_ALIASES.items():
            parts = [re.escape(a) for a in aliases]
            pattern = re.compile(r"\b(" + "|".join(parts) + r")\b", re.IGNORECASE)
            self.coin_patterns[symbol] = pattern

    def extract_symbols(self, text: str) -> List[str]:
        found = []
        for symbol, pattern in self.coin_patterns.items():
            if pattern.search(text):
                found.append(symbol)
        priority = {"BTC": 0, "ETH": 1, "SOL": 2, "BNB": 3, "XRP": 4}
        found.sort(key=lambda s: priority.get(s, 99))
        return found

    def _detect_dimension(self, text: str) -> Tuple[str, float]:
        text_lower = text.lower()
        scores = {}
        for dim, rule in DIMENSION_RULES.items():
            score = sum(1 for kw in rule["keywords"] if kw.lower() in text_lower)
            if score > 0:
                scores[dim] = score * rule["weight"]
        if not scores:
            return "general", 0.5
        best_dim = max(scores, key=scores.get)
        return best_dim, DIMENSION_RULES[best_dim]["weight"]

    def _detect_sentiment(self, text: str) -> int:
        text_lower = text.lower()
        pos_score = sum(1 for w in SENTIMENT_POSITIVE if w.lower() in text_lower)
        neg_score = sum(1 for w in SENTIMENT_NEGATIVE if w.lower() in text_lower)
        return pos_score - neg_score

    def _source_trust(self, source: str) -> float:
        src_lower = source.lower()
        for key, val in SOURCE_TRUST.items():
            if key in src_lower:
                return val
        return SOURCE_TRUST["default"]

    def analyze(self, title: str, summary: str = "", source: str = "", url: str = "") -> List[NewsFactorResult]:
        full_text = f"{title} {summary}".strip()
        if not full_text:
            return []

        symbols = self.extract_symbols(full_text)
        if not symbols:
            dim, _ = self._detect_dimension(full_text)
            if dim in ("regulation", "capital"):
                symbols = ["BTC", "ETH"]
            else:
                return []

        sentiment_raw = self._detect_sentiment(full_text)
        dimension, dim_weight = self._detect_dimension(full_text)
        trust = self._source_trust(source)

        raw_strength = abs(sentiment_raw) * dim_weight * trust
        if raw_strength >= 3.5:
            strength = 5
        elif raw_strength >= 2.5:
            strength = 4
        elif raw_strength >= 1.5:
            strength = 3
        elif raw_strength >= 0.8:
            strength = 2
        else:
            strength = 1

        direction = "bullish" if sentiment_raw > 0 else ("bearish" if sentiment_raw < 0 else "neutral")
        if strength == 0:
            direction = "neutral"

        results = []
        for symbol in symbols:
            symbol_in_title = bool(self.coin_patterns[symbol].search(title))
            adjusted_strength = min(5, strength + (1 if symbol_in_title else 0))
            if direction == "neutral":
                adjusted_strength = 0

            results.append(NewsFactorResult(
                symbol=symbol,
                direction=direction,
                strength=adjusted_strength if direction == "bullish" else (-adjusted_strength if direction == "bearish" else 0),
                dimension=dimension,
                title=title,
                source=source,
                url=url,
                reason=f"{dimension}|sentiment={sentiment_raw}|trust={trust:.1f}",
            ))
        return results

    def batch_analyze(self, news_items: List[dict]) -> List[NewsFactorResult]:
        all_results = []
        for item in news_items:
            results = self.analyze(
                title=item.get("title", ""),
                summary=item.get("summary", ""),
                source=item.get("source", ""),
                url=item.get("url", ""),
            )
            all_results.extend(results)
        return all_results


news_analyzer = NewsAnalyzer()


def get_ttl_for_dimension(dimension: str) -> int:
    ttl_map = {
        "regulation": 72 * 3600,
        "capital": 48 * 3600,
        "demand": 36 * 3600,
        "liquidity": 24 * 3600,
        "narrative": 18 * 3600,
        "general": 24 * 3600,
    }
    return ttl_map.get(dimension, 24 * 3600)
