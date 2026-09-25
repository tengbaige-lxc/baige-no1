"""Pure candle-quality checks for entry calculations; no I/O or order routing."""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
import re
import time
from typing import Sequence


@dataclass(frozen=True)
class QualityPolicy:
    history_bars: int = 20
    min_positive_history: int = 12
    min_reference_move: float = 0.000001

    def __post_init__(self):
        if self.history_bars < 2 or not 1 <= self.min_positive_history <= self.history_bars - 1:
            raise ValueError('invalid_history_policy')
        if not math.isfinite(self.min_reference_move) or self.min_reference_move <= 0:
            raise ValueError('invalid_min_reference_move')


def partition_candles(rows: Sequence, *, now_ms: int, period_ms: int) -> dict:
    """Require both exchange confirmation and elapsed wall time for closed bars."""
    if period_ms <= 0:
        raise ValueError('invalid_period')
    unique = {}
    bad = 0
    conflicts = set()
    for row in rows:
        try:
            if len(row) < 9 or str(row[8]) not in ('0', '1'):
                raise ValueError('missing_confirmation')
            t = int(row[0])
            o, h, low, close, volume = map(float, row[1:6])
            if (not all(math.isfinite(v) for v in (o, h, low, close, volume))
                    or min(o, h, low, close) <= 0 or volume < 0
                    or low > min(o, close) or h < max(o, close) or low > h or t > now_ms):
                raise ValueError('invalid_candle')
            parsed = (t, o, h, low, close, volume, str(row[8]))
            if t in unique and unique[t] != parsed:
                conflicts.add(t)
            unique[t] = parsed
        except (TypeError, ValueError, OverflowError):
            bad += 1
    for t in conflicts:
        unique.pop(t, None)
    closed, intrabar, unsettled = [], [], []
    for t in sorted(unique):
        b = unique[t]
        if b[6] == '1' and t + period_ms <= now_ms:
            closed.append(b)
        elif b[6] == '0' and t <= now_ms < t + period_ms:
            intrabar.append(b)
        else:
            unsettled.append(b)
    return dict(closed=closed, intrabar=intrabar, unsettled=unsettled,
                rejected_rows=bad, conflicting_timestamps=len(conflicts))


def activity_quality(closed: Sequence, candidate: Sequence | None, *, period_ms: int,
                     now_ms: int, policy: QualityPolicy = QualityPolicy()) -> dict:
    """Missing/unreliable ratios remain null, never infinity or artificial zero."""
    result = dict(available=False, reasons=[], volume_ratio=None, speed_ratio=None,
                  median_volume=None, median_move=None, positive_volumes=0, positive_moves=0,
                  current_move=None, confirmed=False)
    if candidate is None:
        result['reasons'].append('no_candidate')
        return result
    history = [b for b in closed if b[0] < candidate[0]][-policy.history_bars:]
    result['confirmed'] = candidate[6] == '1' and candidate[0] + period_ms <= now_ms
    if len(history) < policy.history_bars:
        result['reasons'].append('insufficient_closed_history')
        return result
    if now_ms - (candidate[0] + period_ms) >= period_ms:
        result['reasons'].append('stale_candidate')
    window = history + [candidate]
    if any(b[0] - a[0] != period_ms for a, b in zip(window, window[1:])):
        result['reasons'].append('history_gap')
    volumes = [b[5] for b in history]
    moves = [abs(history[i][4]/history[i-1][4]-1) for i in range(1, len(history))]
    mv, ms = statistics.median(volumes), statistics.median(moves)
    result.update(median_volume=mv, median_move=ms,
                  positive_volumes=sum(v > 0 for v in volumes),
                  positive_moves=sum(m >= policy.min_reference_move for m in moves),
                  current_move=candidate[4]/history[-1][4]-1)
    if mv <= 0 or result['positive_volumes'] < policy.min_positive_history:
        result['reasons'].append('unreliable_volume_baseline')
    if ms < policy.min_reference_move or result['positive_moves'] < policy.min_positive_history:
        result['reasons'].append('unreliable_move_baseline')
    if not result['reasons']:
        vr, sr = candidate[5]/mv, abs(result['current_move'])/ms
        if math.isfinite(vr) and math.isfinite(sr):
            result.update(available=True, volume_ratio=vr, speed_ratio=sr)
        else:
            result['reasons'].append('nonfinite_ratio')
    return result


def assess_signal_quality(rows: Sequence, *, now_ms: int, period_ms: int,
                          policy: QualityPolicy = QualityPolicy()) -> dict:
    parts = partition_candles(rows, now_ms=now_ms, period_ms=period_ms)
    closed = parts['closed']
    confirmed = activity_quality(closed, closed[-1] if closed else None,
                                 period_ms=period_ms, now_ms=now_ms, policy=policy)
    fast = activity_quality(closed, parts['intrabar'][-1] if parts['intrabar'] else None,
                            period_ms=period_ms, now_ms=now_ms, policy=policy)
    return dict(confirmed_activity=confirmed, intrabar_activity=fast,
                closed_count=len(closed), intrabar_count=len(parts['intrabar']),
                unsettled_count=len(parts['unsettled']), rejected_rows=parts['rejected_rows'],
                conflicting_timestamps=parts['conflicting_timestamps'],
                last_closed_ts=closed[-1][0] if closed else None,
                structure_candle_source='confirmed_only', mode='quality_assessment')


def confirmed_candles(rows, bar, *, now_ms=None):
    match = re.fullmatch(r'(\d+)([mHDW])(?:utc)?', bar)
    if not match:
        raise ValueError('unsupported_candle_interval')
    period = int(match[1])*{'m': 60000, 'H': 3600000, 'D': 86400000, 'W': 604800000}[match[2]]
    now = int(time.time()*1000) if now_ms is None else now_ms
    parts = partition_candles(rows, now_ms=now, period_ms=period)
    allowed = {b[0] for b in parts['closed']}
    original = {}
    for row in rows:
        try:
            if int(row[0]) in allowed:
                original[int(row[0])] = list(row)
        except (ValueError, TypeError, IndexError):
            continue
    return [original[t] for t in sorted(original)]


def closed_ma_cross(closed, direction, *, now_ms, period_ms):
    if len(closed) < 35 or now_ms-(closed[-1][0]+period_ms) >= period_ms:
        return False
    window = closed[-35:]
    if any(b[0]-a[0] != period_ms for a, b in zip(window, window[1:])):
        return False
    c = [b[4] for b in window]
    old5, old34 = sum(c[-6:-1])/5, sum(c[:-1])/34
    new5, new34 = sum(c[-5:])/5, sum(c[-34:])/34
    if str(getattr(direction, 'value', direction)) == 'LONG':
        return old5 <= old34 and new5 > new34
    return old5 >= old34 and new5 < new34


def entry_timing(rows, direction, *, now_ms, min_volume_ratio, min_speed_ratio,
                 require_volume_speed=False):
    parts = partition_candles(rows, now_ms=now_ms, period_ms=300000)
    quality = assess_signal_quality(rows, now_ms=now_ms, period_ms=300000)
    quality['mode'] = 'entry_quality_v1'
    sign = 1 if str(getattr(direction, 'value', direction)) == 'LONG' else -1
    cross = closed_ma_cross(parts['closed'], direction, now_ms=now_ms, period_ms=300000)
    selected = None
    for source in ('confirmed', 'intrabar'):
        activity = quality[source+'_activity']
        if (activity['available'] and sign*activity['current_move'] > 0
                and activity['volume_ratio'] >= min_volume_ratio
                and activity['speed_ratio'] >= min_speed_ratio):
            selected = (source, activity)
            break
    volume_speed = selected is not None
    source = selected[0] if selected else 'confirmed'
    candidates = parts['intrabar'] if source == 'intrabar' else parts['closed']
    return dict(trigger=volume_speed or (cross and not require_volume_speed),
                volume_speed=volume_speed, cross=cross, source=source,
                volume_ratio=selected[1]['volume_ratio'] if selected else 0.,
                speed_ratio=selected[1]['speed_ratio'] if selected else 0.,
                current_move=selected[1]['current_move'] if selected else quality['confirmed_activity']['current_move'] or 0.,
                candle=candidates[-1] if candidates else None, quality=quality)
