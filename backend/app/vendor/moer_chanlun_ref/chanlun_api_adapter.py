"""
Minimal Flask adapter for chanlun_core.py.

Usage:
    from flask import Flask
    from chanlun_api_adapter import chanlun_bp

    app = Flask(__name__)
    app.register_blueprint(chanlun_bp, url_prefix="/api")

POST /api/chanlun/analyze
Body:
{
  "klines": [
    {"trade_date": "20260421", "open": 10, "high": 11, "low": 9.8, "close": 10.8, "vol": 1000}
  ],
  "bigYangThreshold": 0.05
}
"""

from __future__ import annotations

from __future__ import annotations

from .chanlun_core import (
    KLine,
    analyze_chanlun_structure,
    calc_ma,
    detect_buy_signals,
)


# Flask 为可选依赖
try:
    from flask import Blueprint, jsonify, request
    _HAS_FLASK = True
except ImportError:
    _HAS_FLASK = False
    Blueprint = None  # type: ignore
    jsonify = None    # type: ignore
    request = None    # type: ignore


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_date(value) -> str:
    text = str(value or "").strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return text.replace("-", "")
    return text


def parse_klines(rows) -> list[KLine]:
    klines: list[KLine] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        date = _normalize_date(row.get("trade_date") or row.get("date"))
        if not date:
            continue
        klines.append(
            KLine(
                date=date,
                open=_to_float(row.get("open")),
                high=_to_float(row.get("high")),
                low=_to_float(row.get("low")),
                close=_to_float(row.get("close")),
                vol=_to_float(row.get("vol") or row.get("volume")),
            )
        )
    return klines


def analyze_klines(rows, big_yang_threshold=0.05) -> dict:
    klines = parse_klines(rows)
    if not klines:
        return {"signals": [], "structure": None}

    klines.sort(key=lambda item: item.date)
    closes = [k.close for k in klines]
    ma5 = calc_ma(closes, 5)
    ma34 = calc_ma(closes, 34)
    ma170 = calc_ma(closes, 170)
    signals = detect_buy_signals(
        klines,
        ma5,
        ma34,
        ma170,
        big_yang_threshold=float(big_yang_threshold or 0.05),
    )
    structure = analyze_chanlun_structure(klines, level="day")

    return {
        "signals": [
            {
                "id": signal.id,
                "stype": signal.stype,
                "date": signal.date,
                "price": round(signal.price, 2),
                "confidence": round(signal.confidence, 2),
                "reason": signal.reason,
                "meta": signal.meta,
            }
            for signal in signals
        ],
        "structure": structure,
    }


if _HAS_FLASK:
    chanlun_bp = Blueprint("chanlun", __name__)

    @chanlun_bp.post("/chanlun/analyze")
    def api_chanlun_analyze():
        data = request.get_json(silent=True) or {}
        result = analyze_klines(
            data.get("klines") or [],
            big_yang_threshold=data.get("bigYangThreshold", 0.05),
        )
        return jsonify({"success": True, "data": result})
else:
    chanlun_bp = None
