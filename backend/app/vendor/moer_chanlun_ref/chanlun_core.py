"""
缠论核心算法模块（最小必要集）
支持：分型 -> 笔 -> 线段 -> 中枢 -> 背驰 -> 买卖点
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Literal
import math


@dataclass
class KLine:
    date: str
    open: float
    high: float
    low: float
    close: float
    vol: float = 0.0
    # 运行时标记
    merged: bool = False
    fenxing: Optional[Literal['top', 'bottom']] = None


@dataclass
class Bi:
    id: str
    direction: Literal['up', 'down']
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float


@dataclass
class Xianduan:
    id: str
    direction: Literal['up', 'down']
    bis: List[Bi] = field(default_factory=list)
    start_idx: int = 0
    end_idx: int = 0
    high: float = 0.0
    low: float = 0.0
    confirmed: bool = False


@dataclass
class Zhongshu:
    id: str
    ztype: Literal['zoushi', 'zhezhuan', 'fenlei']
    level: Literal['week', 'day', '30f', '5f']
    start_idx: int
    end_idx: int
    upper: float  # ZG
    lower: float  # ZD
    segment_ids: List[str] = field(default_factory=list)


@dataclass
class BuySignal:
    id: str
    stype: Literal[
        'jin1_std', 'jin1_ext',
        'jin2_approx',
        'jin3_approx',
        'shou1', 'shou2',
        'ma_bull_pullback',
        'low_rebound',
    ]
    date: str
    price: float
    confidence: float
    reason: str
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. K 线合并（包含关系处理）
# ---------------------------------------------------------------------------
def merge_klines(klines: List[KLine]) -> List[KLine]:
    """
    处理 K 线包含关系，向上/向下合并。
    返回合并后的新 K 线序列（不修改原序列）。
    """
    if not klines:
        return []
    result: List[KLine] = []
    pending = KLine(
        date=klines[0].date,
        open=klines[0].open,
        high=klines[0].high,
        low=klines[0].low,
        close=klines[0].close,
        vol=klines[0].vol,
    )
    trend: Optional[Literal['up', 'down']] = None

    for i in range(1, len(klines)):
        cur = klines[i]
        # 判断包含关系
        if (cur.high <= pending.high and cur.low >= pending.low) or \
           (pending.high <= cur.high and pending.low >= cur.low):
            # 存在包含关系，需要合并
            if trend is None:
                # 第一根和第二根，按趋势方向判断
                if cur.high > pending.high:
                    trend = 'up'
                else:
                    trend = 'down'
            # 合并
            if trend == 'up':
                new_high = max(pending.high, cur.high)
                new_low = max(pending.low, cur.low)
            else:
                new_high = min(pending.high, cur.high)
                new_low = min(pending.low, cur.low)
            pending = KLine(
                date=pending.date,
                open=pending.open,
                high=new_high,
                low=new_low,
                close=cur.close,
                vol=pending.vol + cur.vol,
                merged=True,
            )
        else:
            result.append(pending)
            # 更新趋势方向
            if cur.high > pending.high and cur.low > pending.low:
                trend = 'up'
            elif cur.high < pending.high and cur.low < pending.low:
                trend = 'down'
            pending = KLine(
                date=cur.date,
                open=cur.open,
                high=cur.high,
                low=cur.low,
                close=cur.close,
                vol=cur.vol,
            )
    result.append(pending)
    return result


# ---------------------------------------------------------------------------
# 2. 分型识别
# ---------------------------------------------------------------------------
def find_fenxing(klines: List[KLine]) -> List[KLine]:
    """
    在已合并的 K 线序列上标记顶分型/底分型。
    返回新的 K 线列表（带 fenxing 标记）。
    """
    out = [KLine(k.date, k.open, k.high, k.low, k.close, k.vol, k.merged) for k in klines]
    for i in range(1, len(out) - 1):
        prev_, cur_, next_ = out[i - 1], out[i], out[i + 1]
        if cur_.high > prev_.high and cur_.high > next_.high and \
           cur_.low > prev_.low and cur_.low > next_.low:
            cur_.fenxing = 'top'
        elif cur_.low < prev_.low and cur_.low < next_.low and \
             cur_.high < prev_.high and cur_.high < next_.high:
            cur_.fenxing = 'bottom'
    return out


# ---------------------------------------------------------------------------
# 3. 笔生成
# ---------------------------------------------------------------------------
def build_bi(klines: List[KLine]) -> List[Bi]:
    """
    从分型序列生成笔。
    规则：
    - 顶分型 + 底分型 = 向下笔
    - 底分型 + 顶分型 = 向上笔
    - 分型之间至少 1 根独立 K 线
    """
    # 提取分型索引
    fx_indices: List[Tuple[int, Literal['top', 'bottom']]] = []
    for idx, k in enumerate(klines):
        if k.fenxing:
            fx_indices.append((idx, k.fenxing))

    bis: List[Bi] = []
    i = 0
    while i < len(fx_indices) - 1:
        idx1, type1 = fx_indices[i]
        # 找下一个相反分型
        j = i + 1
        while j < len(fx_indices) and fx_indices[j][1] == type1:
            j += 1
        if j >= len(fx_indices):
            break
        idx2, type2 = fx_indices[j]

        # 检查独立 K 线
        independent = idx2 - idx1 - 1
        if independent >= 1:
            direction: Literal['up', 'down'] = 'up' if type1 == 'bottom' else 'down'
            price1 = klines[idx1].low if type1 == 'bottom' else klines[idx1].high
            price2 = klines[idx2].high if type2 == 'top' else klines[idx2].low
            bis.append(Bi(
                id=f"bi_{len(bis)}",
                direction=direction,
                start_idx=idx1,
                end_idx=idx2,
                start_price=price1,
                end_price=price2,
            ))
            i = j
        else:
            # 没有独立 K 线，跳过当前分型，继续
            i += 1
    return bis


# ---------------------------------------------------------------------------
# 4. 线段生成（简化版）
# ---------------------------------------------------------------------------
def build_xianduan(bis: List[Bi]) -> List[Xianduan]:
    """
    从笔序列生成线段。
    简化规则：
    - 线段至少由 3 笔构成
    - 当出现反向 3 笔结构时，确认前一线段终结
    """
    if not bis:
        return []

    xds: List[Xianduan] = []
    current_bis: List[Bi] = [bis[0]]

    for i in range(1, len(bis)):
        b = bis[i]
        if not current_bis:
            current_bis.append(b)
            continue

        same_dir = b.direction == current_bis[-1].direction
        if same_dir:
            current_bis.append(b)
        else:
            # 方向改变，检查是否满足线段终结条件
            # 当前线段至少 3 笔，且反向已有至少 1 笔
            if len(current_bis) >= 3:
                # 确认前一线段
                xd = _create_xianduan(current_bis, confirmed=True)
                xds.append(xd)
                current_bis = [b]
            else:
                # 当前线段不够 3 笔，反向笔破坏，合并到后续
                # 简化处理：如果不够 3 笔，则把之前的反向笔一起合并，重新定方向
                # 这里采用最简策略：如果current_bis只有1-2笔，被反向破坏后，
                # 尝试回溯前一线段（如果有的话）
                if xds:
                    # 把 current_bis 和当前反向笔一起作为新的候选
                    current_bis.append(b)
                else:
                    current_bis.append(b)

    # 处理末尾
    if current_bis:
        xd = _create_xianduan(current_bis, confirmed=False)
        xds.append(xd)

    # 后处理：如果最后一个线段是confirmed=False且前面有confirmed的，
    # 尝试用后面的笔补全（但这需要后面有笔，已经到末尾了，保持现状）
    return xds


def _create_xianduan(bis_list: List[Bi], confirmed: bool) -> Xianduan:
    direction = bis_list[0].direction
    highs = [b.end_price if b.direction == 'up' else b.start_price for b in bis_list]
    lows = [b.start_price if b.direction == 'up' else b.end_price for b in bis_list]
    return Xianduan(
        id=f"xd_{len(bis_list)}",
        direction=direction,
        bis=bis_list,
        start_idx=bis_list[0].start_idx,
        end_idx=bis_list[-1].end_idx,
        high=max(highs),
        low=min(lows),
        confirmed=confirmed,
    )


# ---------------------------------------------------------------------------
# 5. 中枢识别
# ---------------------------------------------------------------------------
def build_zhongshu(xds: List[Xianduan], level: Literal['day', '30f', '5f'] = 'day') -> List[Zhongshu]:
    """
    从线段序列中识别中枢。
    规则：连续 3 段向上/向下线段的价格重叠区间。
    摩尔中枢规则：新中枢必须与前面所有已确认中枢的价格区间无重叠，
    否则视为前面中枢的延伸/扩展，不生成独立新中枢。
    """
    if len(xds) < 3:
        return []
    zs_list: List[Zhongshu] = []
    i = 0
    while i <= len(xds) - 3:
        seg1, seg2, seg3 = xds[i], xds[i + 1], xds[i + 2]
        upper = min(seg1.high, seg2.high, seg3.high)
        lower = max(seg1.low, seg2.low, seg3.low)
        if upper > lower:
            # 摩尔中枢规则：新中枢区间必须不与任何已有中枢重叠
            overlaps_with_existing = False
            for existing_zs in zs_list:
                if upper > existing_zs.lower and lower < existing_zs.upper:
                    overlaps_with_existing = True
                    break
            if overlaps_with_existing:
                # 与已有中枢重叠，跳过当前起始位置，继续向后寻找
                i += 1
                continue

            # 中枢类型判定简化规则
            ztype = _classify_zhongshu_type(seg1, seg2, seg3)
            zs = Zhongshu(
                id=f"zs_{len(zs_list)}",
                ztype=ztype,
                level=level,
                start_idx=seg1.start_idx,
                end_idx=seg3.end_idx,
                upper=upper,
                lower=lower,
                segment_ids=[seg1.id, seg2.id, seg3.id],
            )
            zs_list.append(zs)
            # 继续向后延伸，若后续线段与当前中枢重叠，则延伸中枢
            j = i + 3
            while j < len(xds):
                seg = xds[j]
                if seg.low < zs.upper and seg.high > zs.lower:
                    zs.end_idx = seg.end_idx
                    zs.segment_ids.append(seg.id)
                    j += 1
                else:
                    break
            i = j
        else:
            i += 1
    return zs_list


def _classify_zhongshu_type(seg1: Xianduan, seg2: Xianduan, seg3: Xianduan) -> Literal['zoushi', 'zhezhuan', 'fenlei']:
    """
    简化中枢类型判定：
    - 若前一段方向与中枢第一段时间顺序一致，且 seg1 是明显的趋势延续段，则偏向走势中枢
    - 若 seg1 之前是明显的反向转折，则偏向转折中枢
    这里用最简规则：第一个线段如果是长段突破后的回调中枢，则为走势中枢；否则为转折中枢
    """
    # 简单启发式：如果 seg1 长度（price range）很大，视为趋势段中的走势中枢
    r1 = abs(seg1.high - seg1.low)
    r2 = abs(seg2.high - seg2.low)
    r3 = abs(seg3.high - seg3.low)
    avg_r = (r1 + r2 + r3) / 3.0
    if r1 > avg_r * 1.5:
        return 'zoushi'
    # 默认先返回转折中枢，后续可优化
    return 'zhezhuan'


# ---------------------------------------------------------------------------
# 6. 辅助：MA 计算
# ---------------------------------------------------------------------------
def calc_ma(values: List[float], period: int) -> List[float]:
    if not values or period <= 0:
        return [math.nan] * len(values)
    result: List[float] = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(math.nan)
        else:
            result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def build_ma34_zhongshu_items(klines: List[KLine], min_distance: int = 6) -> List[dict]:
    """
    Build display zhongshu items from standard 4-point alternating MA34 extrema.

    This mirrors the K-line page's auto zhongshu rule:
    - use MA34 values as the peak/trough line
    - find local extrema with a fixed left/right distance
    - the first valid 4-point alternating structure defines the first zhongshu
    - later valid 4-point alternating structures define later zhongshu items
    - compute the overlap zone from the two peaks and two troughs
    """
    if len(klines) < 34 + min_distance * 2:
        return []

    closes = [k.close for k in klines]
    ma34 = calc_ma(closes, 34)
    extremes = []

    for i in range(min_distance, len(ma34) - min_distance):
        if math.isnan(ma34[i]):
            continue
        is_peak = True
        is_trough = True
        for j in range(1, min_distance + 1):
            if ma34[i] <= ma34[i - j] or ma34[i] <= ma34[i + j]:
                is_peak = False
            if ma34[i] >= ma34[i - j] or ma34[i] >= ma34[i + j]:
                is_trough = False
        if is_peak:
            extremes.append({'index': i, 'date': klines[i].date, 'price': ma34[i], 'type': 'peak'})
        if is_trough:
            extremes.append({'index': i, 'date': klines[i].date, 'price': ma34[i], 'type': 'trough'})

    items = []
    i = 0
    while i <= len(extremes) - 4:
        group = extremes[i:i + 4]
        types = [item['type'] for item in group]
        if types == ['trough', 'peak', 'trough', 'peak']:
            peak_values = [group[1]['price'], group[3]['price']]
            trough_values = [group[0]['price'], group[2]['price']]
        elif types == ['peak', 'trough', 'peak', 'trough']:
            peak_values = [group[0]['price'], group[2]['price']]
            trough_values = [group[1]['price'], group[3]['price']]
        else:
            i += 1
            continue

        upper = min(peak_values)
        lower = max(trough_values)
        avg_price = (upper + lower) / 2 or 1
        if upper <= lower or (upper - lower) / avg_price < 0.02:
            i += 1
            continue

        items.append({
            'id': f"ma34_zs_{i}",
            'type': 'ma34_extreme',
            'level': 'day',
            'startIndex': group[0]['index'],
            'endIndex': group[3]['index'],
            'startDate': group[0]['date'],
            'endDate': group[3]['date'],
            'upper': upper,
            'lower': lower,
            'extremes': group,
        })
        # A confirmed 4-point structure uses three segments; the next independent
        # candidate may start from the fourth point.
        i += 3

    return items


# ---------------------------------------------------------------------------
# 7. 辅助：MACD 面积计算
# ---------------------------------------------------------------------------
def calc_macd_area(macd_vals: List[float], start_idx: int, end_idx: int) -> float:
    """计算某区间内 MACD hist 或 dif 的绝对面积和"""
    if start_idx < 0 or end_idx >= len(macd_vals) or start_idx > end_idx:
        return 0.0
    return sum(abs(macd_vals[i]) for i in range(start_idx, end_idx + 1))


def calc_macd(close: List[float]) -> Tuple[List[float], List[float], List[float]]:
    """返回 dif, dea, hist"""
    def _ema(data: List[float], period: int) -> List[float]:
        k = 2 / (period + 1)
        result: List[float] = []
        ema = data[0]
        for i, v in enumerate(data):
            if i == 0:
                ema = v
            else:
                ema = v * k + ema * (1 - k)
            result.append(ema)
        return result

    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(close))]
    dea = _ema(dif, 9)
    hist = [2 * (dif[i] - dea[i]) for i in range(len(close))]
    return dif, dea, hist


# ---------------------------------------------------------------------------
# 8. 买卖点判定
# ---------------------------------------------------------------------------
def detect_buy_signals(
    klines: List[KLine],
    ma5: List[float],
    ma34: List[float],
    ma170: List[float],
    big_yang_threshold: float = 0.05,
    ma34_break_ratio: float = 0.03,
    zhongshu_tolerance: float = 0.02,
) -> List[BuySignal]:
    """
    主入口：基于日 K 数据检测进攻买一、防守买一、防守买二（进攻买二近似版、进攻买三近似版）
    """
    signals: List[BuySignal] = []

    # 1. 缠论结构计算
    merged = merge_klines(klines)
    fx_klines = find_fenxing(merged)
    bis = build_bi(fx_klines)
    xds = build_xianduan(bis)
    zs_list = build_zhongshu(xds, level='day')

    # 2. MACD
    closes = [k.close for k in klines]
    dif, dea, hist = calc_macd(closes)

    # 3. 核心买点围绕标准 MA34 中枢计算，和图上自动中枢保持同一口径。
    signals.extend(_detect_ma34_standard_buys(
        klines, ma5, ma34, ma170, hist,
        big_yang_threshold=big_yang_threshold,
        zhongshu_tolerance=zhongshu_tolerance,
        ma34_break_ratio=ma34_break_ratio,
    ))

    # 4. 辅助观察信号，不参与默认核心扫描。
    signals.extend(_detect_ma_bull_pullback(klines, ma5, ma34, ma170))
    signals.extend(_detect_low_rebound(klines, ma5))

    # 去重：同一天同类型只保留一个
    seen = set()
    unique = []
    for s in signals:
        key = (s.date, s.stype)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return sorted(unique, key=lambda x: x.date)


def _detect_ma34_standard_buys(
    klines: List[KLine],
    ma5: List[float],
    ma34: List[float],
    ma170: List[float],
    hist: List[float],
    big_yang_threshold: float,
    zhongshu_tolerance: float,
    ma34_break_ratio: float,
) -> List[BuySignal]:
    """Detect core buy points from the standard MA34 4-extrema zhongshu."""
    signals: List[BuySignal] = []
    zs_items = build_ma34_zhongshu_items(klines)
    if not zs_items:
        return signals

    for zs in zs_items:
        upper = float(zs['upper'])
        lower = float(zs['lower'])
        end_idx = int(zs['endIndex'])
        scan_start = min(end_idx + 1, len(klines))
        if scan_start >= len(klines):
            continue

        first_break_idx: Optional[int] = None
        pullback_idx: Optional[int] = None
        recent_high_after_break = 0.0

        for idx in range(scan_start, len(klines)):
            k = klines[idx]
            prev_close = klines[idx - 1].close if idx > 0 else k.open
            ma34_v = ma34[idx] if idx < len(ma34) else math.nan
            ma170_v = ma170[idx] if idx < len(ma170) else math.nan
            yang_ratio = (k.close - k.open) / k.open if k.open else 0.0
            is_big_yang = k.close > k.open and yang_ratio >= big_yang_threshold
            ma34_up = idx >= 3 and not math.isnan(ma34_v) and not math.isnan(ma34[idx - 3]) and ma34_v > ma34[idx - 3]

            if first_break_idx is None:
                crossed_upper = prev_close <= upper * (1 + zhongshu_tolerance) and k.close > upper * (1 + zhongshu_tolerance)
                strong_above_upper = k.close > upper * (1 + zhongshu_tolerance) and (is_big_yang or ma34_up)
                if crossed_upper or strong_above_upper:
                    stype = 'jin1_ext' if ma34_up else 'jin1_std'
                    confidence = 0.84 if ma34_up else 0.74
                    if not math.isnan(ma170_v) and k.close >= ma170_v:
                        confidence += 0.04
                    signals.append(BuySignal(
                        id=f"{stype}_{zs['id']}_{idx}",
                        stype=stype,
                        date=k.date,
                        price=k.close,
                        confidence=min(confidence, 0.9),
                        reason=f"进攻买一({'扩展' if stype == 'jin1_ext' else '标准'}): 标准MA34中枢后放量/转强突破中枢上沿ZG={upper:.2f}",
                        meta={
                            'zhongshuId': zs['id'],
                            'upper': round(upper, 2),
                            'lower': round(lower, 2),
                            'ma34': round(ma34_v, 2) if not math.isnan(ma34_v) else None,
                            'ma170': round(ma170_v, 2) if not math.isnan(ma170_v) else None,
                        }
                    ))
                    first_break_idx = idx
                    recent_high_after_break = k.high
                continue

            recent_high_after_break = max(recent_high_after_break, k.high)
            near_upper = upper * (1 - zhongshu_tolerance) <= k.low <= upper * (1 + zhongshu_tolerance)
            near_ma34 = False
            valid_ma34 = True
            if not math.isnan(ma34_v):
                near_ma34 = ma34_v * (1 - zhongshu_tolerance) <= k.low <= ma34_v * (1 + zhongshu_tolerance)
                valid_ma34 = k.low >= ma34_v * (1 - ma34_break_ratio)

            if pullback_idx is None and idx > first_break_idx:
                held_upper = k.low >= upper * (1 - zhongshu_tolerance)
                closes_back_strong = k.close >= upper and k.close > k.open
                if (near_upper or near_ma34) and held_upper and valid_ma34 and closes_back_strong:
                    signals.append(BuySignal(
                        id=f"shou1_{zs['id']}_{idx}",
                        stype='shou1',
                        date=k.date,
                        price=k.close,
                        confidence=0.72,
                        reason=f"防守买一: 突破标准MA34中枢后回踩{'中枢上沿' if near_upper else 'MA34'}不破并收阳",
                        meta={
                            'zhongshuId': zs['id'],
                            'upper': round(upper, 2),
                            'lower': round(lower, 2),
                            'ma34': round(ma34_v, 2) if not math.isnan(ma34_v) else None,
                        }
                    ))
                    pullback_idx = idx
                continue

            if pullback_idx is not None and idx > pullback_idx:
                ma5_v = ma5[idx] if idx < len(ma5) else math.nan
                prev_ma5_v = ma5[idx - 1] if idx - 1 < len(ma5) else math.nan
                rebreak_ma5 = not math.isnan(ma5_v) and not math.isnan(prev_ma5_v) and k.close > ma5_v and klines[idx - 1].close <= prev_ma5_v
                rebreak_high = k.close > recent_high_after_break * 0.995
                if k.close > k.open and (rebreak_ma5 or rebreak_high):
                    signals.append(BuySignal(
                        id=f"jin2_approx_{zs['id']}_{idx}",
                        stype='jin2_approx',
                        date=k.date,
                        price=k.close,
                        confidence=0.66,
                        reason="进攻买二: 标准MA34中枢突破后回踩不破，再次站上MA5/前高",
                        meta={
                            'zhongshuId': zs['id'],
                            'pullbackIndex': pullback_idx,
                            'ma5': round(ma5_v, 2) if not math.isnan(ma5_v) else None,
                        }
                    ))
                    break

        # 离开中枢后的强加速，作为进攻买三近似信号。
        for idx in range(scan_start, len(klines)):
            k = klines[idx]
            yang_ratio = (k.close - k.open) / k.open if k.open else 0.0
            away_from_zs = k.low > upper * (1 + zhongshu_tolerance)
            if away_from_zs and k.close > k.open and yang_ratio >= big_yang_threshold:
                signals.append(BuySignal(
                    id=f"jin3_approx_{zs['id']}_{idx}",
                    stype='jin3_approx',
                    date=k.date,
                    price=k.close,
                    confidence=0.60,
                    reason="进攻买三: 标准MA34中枢上方不回中枢并大阳加速",
                    meta={'zhongshuId': zs['id'], 'upper': round(upper, 2)}
                ))
                break

        # 下破中枢后的防守买二：后段 MACD 绿柱面积缩小，并重新回到中枢。
        break_down_idx: Optional[int] = None
        for idx in range(scan_start, len(klines)):
            if klines[idx].close < lower * (1 - zhongshu_tolerance):
                break_down_idx = idx
                break
        if break_down_idx is not None:
            prev_start = max(0, end_idx - 13)
            prev_area = calc_macd_area(hist, prev_start, end_idx)
            for idx in range(break_down_idx + 1, min(break_down_idx + 13, len(klines))):
                cur_area = calc_macd_area(hist, break_down_idx, idx)
                back_to_zs = klines[idx].high >= lower and klines[idx].close > klines[idx].open
                if back_to_zs and prev_area > 0 and cur_area < prev_area * 0.8:
                    signals.append(BuySignal(
                        id=f"shou2_{zs['id']}_{idx}",
                        stype='shou2',
                        date=klines[idx].date,
                        price=klines[idx].close,
                        confidence=0.70,
                        reason=f"防守买二: 下破标准MA34中枢后MACD面积收敛并回到中枢, 面积比{cur_area / prev_area:.2f}",
                        meta={
                            'zhongshuId': zs['id'],
                            'macdAreaRatio': round(cur_area / prev_area, 2),
                            'lower': round(lower, 2),
                        }
                    ))
                    break

    return signals


def analyze_chanlun_structure(klines: List[KLine], level: Literal['day', '30f', '5f'] = 'day') -> dict:
    """Return serializable Chan structure for chart overlays."""
    merged = merge_klines(klines)
    fx_klines = find_fenxing(merged)
    bis = build_bi(fx_klines)
    xds = build_xianduan(bis)

    def kline_at(idx: int) -> Optional[KLine]:
        if 0 <= idx < len(fx_klines):
            return fx_klines[idx]
        return None

    fenxings = []
    for idx, k in enumerate(fx_klines):
        if not k.fenxing:
            continue
        fenxings.append({
            'index': idx,
            'date': k.date,
            'type': k.fenxing,
            'price': k.high if k.fenxing == 'top' else k.low,
        })

    bi_items = []
    for b in bis:
        start = kline_at(b.start_idx)
        end = kline_at(b.end_idx)
        if not start or not end:
            continue
        bi_items.append({
            'id': b.id,
            'direction': b.direction,
            'startIndex': b.start_idx,
            'endIndex': b.end_idx,
            'startDate': start.date,
            'endDate': end.date,
            'startPrice': b.start_price,
            'endPrice': b.end_price,
        })

    xd_items = []
    for xd in xds:
        start = kline_at(xd.start_idx)
        end = kline_at(xd.end_idx)
        if not start or not end:
            continue
        xd_items.append({
            'id': xd.id,
            'direction': xd.direction,
            'startIndex': xd.start_idx,
            'endIndex': xd.end_idx,
            'startDate': start.date,
            'endDate': end.date,
            'high': xd.high,
            'low': xd.low,
            'confirmed': xd.confirmed,
        })

    zs_items = build_ma34_zhongshu_items(klines)

    return {
        'mergedKlines': len(fx_klines),
        'fenxings': fenxings,
        'bis': bi_items,
        'xianduans': xd_items,
        'zhongshus': zs_items,
    }


# ---------------------------------------------------------------------------
# 8.1 进攻买一
# ---------------------------------------------------------------------------
def _detect_jin1(
    klines: List[KLine],
    xds: List[Xianduan],
    zs_list: List[Zhongshu],
    ma34: List[float],
    ma170: List[float],
    big_yang_threshold: float,
) -> List[BuySignal]:
    signals: List[BuySignal] = []
    if not klines or not xds:
        return signals

    # 找最近的日线中枢（转折或分类）
    candidate_zs = [z for z in zs_list if z.ztype in ('zhezhuan', 'fenlei')]
    if not candidate_zs:
        return signals

    latest_zs = candidate_zs[-1]
    zs_end = latest_zs.end_idx

    # 看多条件：zs 结束后出现向下线段终结，且价格站上 MA34
    # 在 zs 结束后的 xds 中找
    post_xds = [xd for xd in xds if xd.start_idx >= zs_end]
    if not post_xds:
        return signals

    # 找最后一个已确认的向下线段终结
    last_down_xd: Optional[Xianduan] = None
    for xd in reversed(post_xds):
        if xd.direction == 'down' and xd.confirmed:
            last_down_xd = xd
            break
    if not last_down_xd:
        return signals

    # 从 last_down_xd 终点开始，向前扫描几根 K 线，找大阳线
    start_scan = last_down_xd.end_idx
    end_scan = min(start_scan + 5, len(klines) - 1)

    for idx in range(start_scan, end_scan + 1):
        k = klines[idx]
        prev_close = klines[idx - 1].close if idx > 0 else k.open
        yang_ratio = (k.close - k.open) / k.open if k.open != 0 else 0
        is_big_yang = yang_ratio >= big_yang_threshold and k.close > k.open
        if not is_big_yang:
            continue

        # 做多条件：价格在中枢上方，且最好在 MA170 上方（或中枢上沿≥MA170）
        ma34_v = ma34[idx] if idx < len(ma34) else math.nan
        ma170_v = ma170[idx] if idx < len(ma170) else math.nan
        above_zs = k.close >= latest_zs.upper * (1 - 0.02)
        above_ma170 = not math.isnan(ma170_v) and k.close >= ma170_v * 0.98

        if above_zs and above_ma170:
            # MA34 转折看多（扩展版条件）
            ma34_up = False
            if idx >= 3 and not math.isnan(ma34_v):
                slope_now = ma34_v - ma34[idx - 3] if idx - 3 >= 0 else 0
                ma34_up = slope_now > 0

            stype = 'jin1_ext' if ma34_up else 'jin1_std'
            signals.append(BuySignal(
                id=f"{stype}_{idx}",
                stype=stype,
                date=k.date,
                price=k.close,
                confidence=0.85 if stype == 'jin1_ext' else 0.75,
                reason=f"进攻买一({'扩展' if stype=='jin1_ext' else '标准'}): 日线转折中枢后向下线段终结+大阳线突破, MA34={'向上' if ma34_up else '平/下'}",
                meta={
                    'zhongshuId': latest_zs.id,
                    'xianduanId': last_down_xd.id,
                    'ma34': round(ma34_v, 2) if not math.isnan(ma34_v) else None,
                    'ma170': round(ma170_v, 2) if not math.isnan(ma170_v) else None,
                }
            ))
            break  # 同一区间只取第一个
    return signals


# ---------------------------------------------------------------------------
# 8.2 防守买一
# ---------------------------------------------------------------------------
def _detect_shou1(
    klines: List[KLine],
    zs_list: List[Zhongshu],
    ma34: List[float],
    ma170: List[float],
    zhongshu_tolerance: float,
    ma34_break_ratio: float,
) -> List[BuySignal]:
    signals: List[BuySignal] = []
    if not klines or not zs_list:
        return signals

    for zs in zs_list:
        if zs.ztype not in ('zhezhuan', 'fenlei'):
            continue
        zs_end = zs.end_idx
        if zs_end >= len(klines) - 1:
            continue

        # 找中枢后的突破高点
        max_high_idx = zs_end
        max_high = klines[zs_end].high
        scan_end = min(zs_end + 10, len(klines) - 1)
        for idx in range(zs_end, scan_end + 1):
            if klines[idx].high > max_high:
                max_high = klines[idx].high
                max_high_idx = idx

        if max_high_idx == zs_end:
            continue

        # 从高点后找回踩
        for idx in range(max_high_idx + 1, min(max_high_idx + 6, len(klines))):
            k = klines[idx]
            low = k.low
            # 回踩中枢上沿
            near_upper = zs.upper * (1 - zhongshu_tolerance) <= low <= zs.upper * (1 + zhongshu_tolerance)
            # 或回踩 MA34
            ma34_v = ma34[idx] if idx < len(ma34) else math.nan
            near_ma34 = False
            if not math.isnan(ma34_v):
                near_ma34 = ma34_v * (1 - zhongshu_tolerance) <= low <= ma34_v * (1 + zhongshu_tolerance)

            # 不有效跌破 MA34
            valid_ma34 = not math.isnan(ma34_v) and low >= ma34_v * (1 - ma34_break_ratio)

            if (near_upper or near_ma34) and valid_ma34:
                # 企稳信号：阳线
                if k.close > k.open:
                    signals.append(BuySignal(
                        id=f"shou1_{idx}",
                        stype='shou1',
                        date=k.date,
                        price=k.close,
                        confidence=0.70,
                        reason=f"防守买一: 中枢突破后回踩{'中枢上沿' if near_upper else 'MA34'}企稳阳线",
                        meta={
                            'zhongshuId': zs.id,
                            'ma34': round(ma34_v, 2) if not math.isnan(ma34_v) else None,
                        }
                    ))
                    break
    return signals


# ---------------------------------------------------------------------------
# 8.3 防守买二
# ---------------------------------------------------------------------------
def _detect_shou2(
    klines: List[KLine],
    xds: List[Xianduan],
    zs_list: List[Zhongshu],
    dif: List[float],
    hist: List[float],
) -> List[BuySignal]:
    signals: List[BuySignal] = []
    if len(xds) < 4 or len(zs_list) < 2:
        return signals

    # 找连续的下跌中枢（至少两个）
    down_zs_groups: List[List[Zhongshu]] = []
    i = 0
    while i < len(zs_list) - 1:
        group = [zs_list[i]]
        j = i + 1
        while j < len(zs_list):
            # 连续的中枢，中间有向下线段连接
            group.append(zs_list[j])
            j += 1
            break  # 简化：只要两个相邻中枢即可
        if len(group) >= 2:
            down_zs_groups.append(group)
        i += 1

    for group in down_zs_groups:
        last_zs = group[-1]
        # 找最后一个中枢后的向下线段
        post_xds = [xd for xd in xds if xd.start_idx >= last_zs.end_idx and xd.direction == 'down']
        if not post_xds:
            continue
        last_down = post_xds[-1]
        if not last_down.confirmed:
            continue

        # 找前一个同向下跌线段（在最后一个中枢前或中枢后第一个）
        prev_down_candidates = [xd for xd in xds if xd.direction == 'down' and xd.end_idx < last_down.start_idx]
        if not prev_down_candidates:
            continue
        prev_down = prev_down_candidates[-1]

        # 价格新低
        price_new_low = last_down.low < prev_down.low
        if not price_new_low:
            continue

        # MACD 面积比较
        area_prev = calc_macd_area(hist, prev_down.start_idx, prev_down.end_idx)
        area_last = calc_macd_area(hist, last_down.start_idx, last_down.end_idx)
        beichi = area_last < area_prev * 0.95  # 后段面积明显缩小

        if not beichi:
            continue

        # 回到中枢确认：从 last_down 终点后，找向上结构的高点进入中枢区间
        confirm_idx = None
        for idx in range(last_down.end_idx, min(last_down.end_idx + 8, len(klines))):
            if klines[idx].high >= last_zs.lower and klines[idx].high <= last_zs.upper:
                confirm_idx = idx
                break
            if klines[idx].high > last_zs.upper:
                confirm_idx = idx
                break

        if confirm_idx is None:
            continue

        # 买点位置：背驰底分型确认点（last_down 终点后第一根阳线）
        buy_idx = None
        for idx in range(last_down.end_idx, min(last_down.end_idx + 5, len(klines))):
            if klines[idx].close > klines[idx].open:
                buy_idx = idx
                break
        if buy_idx is None:
            buy_idx = last_down.end_idx

        k = klines[buy_idx]
        has_san = len(group) >= 2  # 简化：两中枢视为有三类
        signals.append(BuySignal(
            id=f"shou2_{buy_idx}",
            stype='shou2',
            date=k.date,
            price=k.close,
            confidence=0.80 if has_san else 0.65,
            reason=f"防守买二: 趋势背驰{'(有三类)' if has_san else '(无三类)'}, MACD面积比{area_last/area_prev:.2f}, 已回中枢",
            meta={
                'zhongshuId': last_zs.id,
                'xianduanId': last_down.id,
                'macdAreaRatio': round(area_last / area_prev, 2) if area_prev else None,
                'hasSan': has_san,
            }
        ))
    return signals


# ---------------------------------------------------------------------------
# 8.4 进攻买二（日 K 近似版）
# ---------------------------------------------------------------------------
def _detect_jin2_approx(
    klines: List[KLine],
    xds: List[Xianduan],
    zs_list: List[Zhongshu],
    ma5: List[float],
    big_yang_threshold: float,
) -> List[BuySignal]:
    signals: List[BuySignal] = []
    if len(zs_list) < 2 or not xds:
        return signals

    # 找两个连续下跌中枢
    for i in range(len(zs_list) - 1):
        z1, z2 = zs_list[i], zs_list[i + 1]
        # 简化：检查中间是否有向下线段连接，且 z2 低点低于 z1 低点
        mid_xds = [xd for xd in xds if xd.start_idx >= z1.end_idx and xd.end_idx <= z2.start_idx]
        has_down_connect = any(xd.direction == 'down' for xd in mid_xds)
        if not has_down_connect:
            continue
        if z2.lower >= z1.lower:
            continue

        # z2 后找向下线段创新低（独立线段）
        post_xds = [xd for xd in xds if xd.start_idx >= z2.end_idx and xd.direction == 'down']
        if not post_xds:
            continue
        last_down = post_xds[-1]
        if not last_down.confirmed:
            continue

        # 底分型后突破 MA5 -> 回踩不破底分型低 -> 再突破 MA5
        bd_idx = last_down.end_idx  # 简化为线段终点作为底分型位置
        if bd_idx >= len(klines) - 3:
            continue

        # 找突破 MA5 的 K 线
        break_ma5_idx = None
        for idx in range(bd_idx + 1, min(bd_idx + 5, len(klines))):
            if not math.isnan(ma5[idx]) and klines[idx].close > ma5[idx]:
                break_ma5_idx = idx
                break
        if break_ma5_idx is None:
            continue

        # 回踩不破底分型低
        pullback_idx = None
        for idx in range(break_ma5_idx + 1, min(break_ma5_idx + 4, len(klines))):
            if klines[idx].low < klines[bd_idx].low:
                break  # 跌破，失效
            if not math.isnan(ma5[idx]) and klines[idx].close < ma5[idx]:
                pullback_idx = idx
                break
        if pullback_idx is None:
            continue

        # 再突破 MA5
        for idx in range(pullback_idx + 1, min(pullback_idx + 4, len(klines))):
            if not math.isnan(ma5[idx]) and klines[idx].close > ma5[idx]:
                k = klines[idx]
                signals.append(BuySignal(
                    id=f"jin2_approx_{idx}",
                    stype='jin2_approx',
                    date=k.date,
                    price=k.close,
                    confidence=0.65,
                    reason="进攻买二(日K近似): 两下跌中枢后底分型+突破MA5+回踩不破低+再突破MA5",
                    meta={
                        'zhongshuIds': [z1.id, z2.id],
                        'xianduanId': last_down.id,
                    }
                ))
                break
    return signals


# ---------------------------------------------------------------------------
# 8.5 进攻买三（日 K 近似版）
# ---------------------------------------------------------------------------
def _detect_jin3_approx(
    klines: List[KLine],
    xds: List[Xianduan],
    ma5: List[float],
    big_yang_threshold: float,
) -> List[BuySignal]:
    signals: List[BuySignal] = []
    if not xds:
        return signals

    # 找当前向上线段
    up_xds = [xd for xd in xds if xd.direction == 'up']
    if not up_xds:
        return signals
    last_up = up_xds[-1]
    if last_up.confirmed:
        return signals  # 已确认的向上线段结束，不再有买三

    # 在线段内部找加速突破前高 + 大阳线
    # 简化：找线段内最后一个明显中枢（用 3 笔重叠近似）
    if len(last_up.bis) < 4:
        return signals

    # 找线段内的局部高点作为前高
    local_highs = [b.end_price for b in last_up.bis if b.direction == 'up']
    if len(local_highs) < 2:
        return signals
    prev_high = local_highs[-2]

    end_idx = last_up.end_idx
    scan_start = max(end_idx - 5, last_up.start_idx)
    for idx in range(scan_start, end_idx + 1):
        k = klines[idx]
        yang_ratio = (k.close - k.open) / k.open if k.open != 0 else 0
        if yang_ratio >= big_yang_threshold and k.close > k.open and k.close > prev_high:
            # MACD 红柱放大检查（已在外部计算，这里简化）
            signals.append(BuySignal(
                id=f"jin3_approx_{idx}",
                stype='jin3_approx',
                date=k.date,
                price=k.close,
                confidence=0.60,
                reason="进攻买三(日K近似): 向上线段中加速突破前高+大阳线",
                meta={
                    'xianduanId': last_up.id,
                    'prevHigh': round(prev_high, 2),
                }
            ))
            break
    return signals


# ---------------------------------------------------------------------------
# 8.6 均线多头回踩（放宽条件）
# ---------------------------------------------------------------------------
def _detect_ma_bull_pullback(
    klines: List[KLine],
    ma5: List[float],
    ma34: List[float],
    ma170: List[float],
) -> List[BuySignal]:
    """MA5 > MA34 > MA170 的多头排列中，回踩 MA5/MA34 附近企稳收阳"""
    signals: List[BuySignal] = []
    start = 170
    for i in range(start, len(klines)):
        if math.isnan(ma5[i]) or math.isnan(ma34[i]) or math.isnan(ma170[i]):
            continue
        # 多头排列
        if not (ma5[i] > ma34[i] > ma170[i]):
            continue
        k = klines[i]
        if not (k.close > k.open):
            continue
        low = k.low
        # 回踩 MA5 或 MA34 附近（2% 容错）
        near_ma5 = abs(low - ma5[i]) / ma5[i] < 0.02
        near_ma34 = abs(low - ma34[i]) / ma34[i] < 0.02
        if near_ma5 or near_ma34:
            signals.append(BuySignal(
                id=f"ma_bull_pullback_{i}",
                stype='ma_bull_pullback',
                date=k.date,
                price=k.close,
                confidence=0.55,
                reason=f"均线多头回踩: MA5>MA34>MA170，回踩{'MA5' if near_ma5 else 'MA34'}企稳阳线",
                meta={
                    'ma5': round(ma5[i], 2),
                    'ma34': round(ma34[i], 2),
                }
            ))
    return signals


# ---------------------------------------------------------------------------
# 8.7 低点反弹（放宽条件）
# ---------------------------------------------------------------------------
def _detect_low_rebound(
    klines: List[KLine],
    ma5: List[float],
) -> List[BuySignal]:
    """近5日最低点后首次收阳且站上MA5"""
    signals: List[BuySignal] = []
    for i in range(5, len(klines)):
        k = klines[i]
        if not (k.close > k.open):
            continue
        if math.isnan(ma5[i]) or k.close < ma5[i]:
            continue
        # 前一日为近5日最低点
        prev_low = min(klines[j].low for j in range(i - 4, i))
        if klines[i - 1].low == prev_low and prev_low < k.close:
            signals.append(BuySignal(
                id=f"low_rebound_{i}",
                stype='low_rebound',
                date=k.date,
                price=k.close,
                confidence=0.50,
                reason="低点反弹: 前日为近5日最低点，当日收阳站上MA5",
                meta={
                    'prev_low': round(prev_low, 2),
                    'ma5': round(ma5[i], 2),
                }
            ))
    return signals
