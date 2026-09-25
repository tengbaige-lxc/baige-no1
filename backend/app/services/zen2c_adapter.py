from __future__ import annotations

from typing import Any

import pandas as pd

try:
    from app.services.zen2c_moer_engine import moer_engine
except ModuleNotFoundError:  # Standalone adapter tests.
    from zen2c_moer_engine import moer_engine


def _to_frame(klines: list[Any]) -> pd.DataFrame:
    rows = []
    for item in klines or []:
        if len(item) < 5:
            continue
        rows.append(
            {
                "timestamp": int(float(item[0])),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
            }
        )
    rows.sort(key=lambda row: row["timestamp"])
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    frame = pd.DataFrame(rows).drop_duplicates("timestamp", keep="last")
    frame.index = pd.to_datetime(frame.pop("timestamp"), unit="ms", utc=True)
    return frame


def evaluate_zen2c_structure(
    klines: list[Any],
    timeframe: str,
    *,
    variants: dict[str, Any] | None = None,
    recent_event_bars: int = 3,
) -> dict[str, Any]:
    """Return a JSON-friendly, point-in-time Zen2c structure snapshot."""
    frame = _to_frame(klines)
    if len(frame) < 170:
        return {
            "available": False,
            "reason": f"need at least 170 closed bars, got {len(frame)}",
            "timeframe": timeframe,
        }

    result = moer_engine(frame, timeframe, variants)
    latest = result["states"].iloc[-1]
    last_bar = int(latest["bar_idx"])
    events = result["events"]
    recent = []
    if not events.empty:
        selected = events[events["confirm_idx"] >= last_bar - max(0, recent_event_bars)]
        for _, event in selected.iterrows():
            recent.append(
                {
                    "type": str(event["bsp_type"]),
                    "is_buy": bool(event["is_buy"]),
                    "is_exit": bool(event["is_exit"]),
                    "confirm_idx": int(event["confirm_idx"]),
                    "confirm_lag_bars": int(event["confirm_lag_bars"]),
                }
            )

    def finite_or_none(value):
        return None if pd.isna(value) else float(value)

    return {
        "available": True,
        "timeframe": timeframe,
        "bar_idx": last_bar,
        "close": float(latest["close"]),
        "ma5": finite_or_none(latest["ma5"]),
        "ma34": finite_or_none(latest["ma34"]),
        "ma170": finite_or_none(latest["ma170"]),
        "regime_bull": bool(latest["regime_bull"]),
        "seg_dir": str(latest["seg_dir"]),
        "tip_dir": str(latest["tip_dir"]),
        "zg": finite_or_none(latest["zg"]),
        "zd": finite_or_none(latest["zd"]),
        "zs_seg_count": int(latest["zs_seg_count"]),
        "zs_upgraded": bool(latest["zs_upgraded"]),
        "current_events": tuple(
            part for part in str(latest["bsp_type"]).split(",") if part
        ),
        "recent_events": recent,
        "meta": {
            "n_bars": int(result["meta"]["n_bars"]),
            "n_segs": int(result["meta"]["n_segs"]),
            "n_zss": int(result["meta"]["n_zss"]),
            "n_events": int(result["meta"]["n_events"]),
            "variants": dict(result["meta"]["variants"]),
        },
    }
