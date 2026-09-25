"""摩尔缠论结构引擎(复原版)—— 纯 MA 计算,不依赖 chan.py。角色① 状态骨架地基。

定位(docs/46 蓝图 v3 §5 / 任务书):**偏多、低滞后**结构引擎。
主输出 = (1) 每根 bar 的摩尔状态(段方向 / 当前中枢 ZG-ZD / MA170 多空环境)供角色① 当状态骨架;
        (2) 多头买点事件表(进一/二/三)。防一/防二 = 多头退出(只标不测,次要)。
卖方向不是本引擎重点(项目卖方向四轮全灭,docs/09)。

规则来源 = docs/45-W15 §8 跨模型对账:
  🔒 骨架层 LOCKED(直接实现):
    - MA5 / MA34 / MA170 = SMA 收盘,入场 TF 上算;**不做 K 线包含处理**,顶/底分型用原始 K
      (3 根:中间 bar 高点最高∧低点最高=顶分型;反之底分型);**弃笔**,直接本质线段。
    - 本质线段:MA5 金叉 MA34(上穿)圈定上涨段搜索区、死叉圈定下跌段;段端点=区内价格极值
      (上涨取最高价)由 顶/底分型 + MA5 拐头确认。
    - 中枢:MA34 重叠(≥3 段连续)定 ZG/ZD;3/5/7 段,9 段升级(两层级别)。
    - MA170 定多空环境(close>MA170 = 多头)。
    - 进一/二/三 = 原版一/二/三类结构(背驰底反转 / 回调不破 / 中枢突破回踩)+ 均线确认
      (进二回踩 MA34 不破、进三站上 ZG 且 >MA34)。做多方向。
  🔓 机制层 CONTESTED → parametrize(variants,别硬猜,§8 明列):
    - V_endpoint(段端点): A=取 MA5 极值那根 K 的价格极值 / B=金叉·死叉那根 + 顶/底分型。默认 A。
    - V_zhongshu(中枢边界): M=ZG/ZD 取 MA34 值重叠 / P=取段价格高低点重叠。默认 M。
    - V_beichi(进一背驰): ST=空间幅度÷K线数 / MA=MA5-MA34 面积。默认 ST。
    - C 的"生长"(上涨新顶实体下轨>前顶实体上轨)作段延续判据(use_growth,默认 True)。

PIT 铁律(docs/03 方法论;本引擎不用 chan.py 但沿用 PIT/滞后计量口径):
  - bar t 的一切(MA/分型/段/中枢/买点)只用 ≤t 的已收盘 K。分型第 3 根(center+1)才可确认;
    段端点须 顶/底分型第3根 + MA5 拐头确认后才 confirmed → 计量并输出 confirm_lag_bars(detect 滞后:
    名义端点→确认;这是摩尔 vs chan.py 的核心卖点=低滞后)。
  - 引擎为**单向因果单遍**处理(逐 bar step),故 增量喂 ≡ 全量批算(构造保证一致),供回放一致性验证。

接口:moer_engine(df_klines, tf, variants={...}) → dict{states, events, segs, zss, meta}
  states  = 每 bar 状态 DataFrame(seg_dir/seg_confirmed/confirm_lag_bars/zg/zd/regime_bull/bsp_type + 附列)
  events  = confirmed 买点事件表(进一/二/三;附防一/防二退出标记 is_exit,不测收益)

烟雾测试:.venv/bin/python scripts/moer_engine.py --smoke(合成手造数据;全过 exit 0)。
docs/44 教训:列名显式取数(勿多元解包依赖列序);H=1/胜率类荒谬统计量设烟雾。CST=UTC+8,存 UTC。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ 冻结常量
MA_WINDOWS = (5, 34, 170)            # MA5 / MA34 / MA170(SMA 收盘,docs/45 骨架层锁定三线)
MIN_SEG_BARS = 3                     # 段最小长度(防噪声微翻转;端点→begin 至少 3 bars)
TF_TD = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min",
         "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h", "1d": "1D"}

DEFAULT_VARIANTS = {
    "endpoint": "A",     # A(MA5 极值锚) | B(金叉/死叉那根 + 分型)
    "zhongshu": "M",     # M(MA34 值重叠) | P(段价格高低点重叠)
    "beichi": "ST",      # ST(空间幅度÷K线数) | MA(MA5-MA34 面积)
    "use_growth": True,  # C 生长判据作段延续 guard
}


def _resolve_variants(variants: dict | None) -> dict:
    v = dict(DEFAULT_VARIANTS)
    if variants:
        for k in variants:
            if k not in DEFAULT_VARIANTS:
                raise ValueError(f"未知 variant 键 {k!r};合法={list(DEFAULT_VARIANTS)}")
        v.update(variants)
    if v["endpoint"] not in ("A", "B"):
        raise ValueError("endpoint ∈ {A,B}")
    if v["zhongshu"] not in ("M", "P"):
        raise ValueError("zhongshu ∈ {M,P}")
    if v["beichi"] not in ("ST", "MA"):
        raise ValueError("beichi ∈ {ST,MA}")
    return v


# ------------------------------------------------------------------ MA / 分型(PIT 安全)
def compute_mas(close: np.ndarray) -> dict:
    """MA5/34/170 = rolling(N).mean();rolling[t] 仅用 close[t-N+1..t] ⊆ ≤t → 无前视(PIT)。"""
    s = pd.Series(close.astype(float))
    ma5 = s.rolling(MA_WINDOWS[0]).mean().to_numpy()
    ma34 = s.rolling(MA_WINDOWS[1]).mean().to_numpy()
    ma170 = s.rolling(MA_WINDOWS[2]).mean().to_numpy()
    return {"ma5": ma5, "ma34": ma34, "ma170": ma170}


def fractals(high: np.ndarray, low: np.ndarray):
    """原始 K 顶/底分型(不做包含处理,docs/45 §8)。center m 用 m-1,m,m+1;PIT 可确认于 m+1。

    顶分型:中间 bar 高点最高 ∧ 低点最高;底分型:中间 bar 高点最低 ∧ 低点最低。
    返回布尔数组 is_top / is_bot(下标=center m;两端 False)。
    """
    n = len(high)
    is_top = np.zeros(n, dtype=bool)
    is_bot = np.zeros(n, dtype=bool)
    if n >= 3:
        hp, hm, hn = high[:-2], high[1:-1], high[2:]
        lp, lm, ln = low[:-2], low[1:-1], low[2:]
        is_top[1:-1] = (hm > hp) & (hm > hn) & (lm > lp) & (lm > ln)
        is_bot[1:-1] = (hm < hp) & (hm < hn) & (lm < lp) & (lm < ln)
    return is_top, is_bot


# ------------------------------------------------------------------ 段 / 中枢 数据结构
@dataclass
class Segment:
    dir: str                 # UP | DOWN
    begin_idx: int
    end_idx: int
    begin_val: float
    end_val: float
    confirm_bar: int         # 端点确认所在 bar(顶/底分型第3根 + MA5 拐头)
    detect_lag: int          # confirm_bar − end_idx(名义端点→确认;低滞后卖点)
    rng_low: float           # 中枢重叠用 range 下界(V_zhongshu 决定 MA34 or 价)
    rng_high: float
    strength: float          # 背驰度量(V_beichi 决定)


@dataclass
class Zhongshu:
    zd: float
    zg: float
    seg_start: int           # confirmed segs 列表内起始下标
    seg_count: int
    confirm_bar: int         # 第3段确认 bar(中枢可知时刻)
    broke_up: bool = False   # 是否处于"已武装"的向上突破态(进三 breakout 状态)
    below: bool = True       # 是否曾回到 ZG 下(中枢内/下方)→ 允许把下次突破算作新的进三
    pending_b3: int = -1     # 突破后回踩底分型 center(待 MA5 拐头触发进三);-1=无
    last_b3_bar: int = -10**9


@dataclass
class _Forming:
    dir: str
    begin_idx: int
    begin_val: float
    ext_idx: int
    ext_val: float
    had_cross: bool = False  # 段内是否出现 MA5×MA34 同向排列(金叉/死叉"圈定")


# ------------------------------------------------------------------ 引擎主体
class MoerEngine:
    def __init__(self, df: pd.DataFrame, tf: str, variants: dict | None = None):
        self.tf = tf
        self.v = _resolve_variants(variants)
        self.df = df
        # 列名显式取数(docs/44:严禁多元解包依赖列序)
        self.o = df["open"].to_numpy(dtype=float)
        self.h = df["high"].to_numpy(dtype=float)
        self.l = df["low"].to_numpy(dtype=float)
        self.c = df["close"].to_numpy(dtype=float)
        self.n = len(df)
        self.open_time = df.index
        td = pd.Timedelta(TF_TD.get(tf, tf))
        self.close_time = df.index + td
        m = compute_mas(self.c)
        self.ma5, self.ma34, self.ma170 = m["ma5"], m["ma34"], m["ma170"]
        self.is_top, self.is_bot = fractals(self.h, self.l)
        # 状态
        self.segs: list[Segment] = []
        self.zss: list[Zhongshu] = []
        self.events: list[dict] = []
        self._forming: _Forming | None = None
        self._top_fx: list[int] = []      # 已完成顶分型 center(全局;用时按 begin 过滤)
        self._bot_fx: list[int] = []
        self._zs_search = 0               # 下一次找新中枢的 confirmed segs 起点
        self._pend_b2 = -1                # 进二待触发的回调底分型 center;-1=无
        self._last_b2_bar = -10**9

    # -------------------------------------------------- 段端点定位(V_endpoint)
    def _endpoint(self, direction: str, begin: int, t: int, ext_idx: int, ext_val: float):
        """给定 forming 段 [begin..t] 与运行极值,按 V_endpoint 定端点 (idx, val)。"""
        if self.v["endpoint"] == "A":
            # A:取 MA5 极值那根 K 的价格极值 —— MA 滞后于价,价顶在 MA5 顶之前 →
            #   在 [begin..MA5极值bar] 窗内取价格极值(上涨最高价 / 下跌最低价)。
            seg_ma5 = self.ma5[begin:t + 1]
            if np.all(np.isnan(seg_ma5)):
                return ext_idx, ext_val
            p = begin + int(np.nanargmax(seg_ma5) if direction == "UP" else np.nanargmin(seg_ma5))
            win_lo, win_hi = begin, max(begin, p)
            if direction == "UP":
                seg = self.h[win_lo:win_hi + 1]
                idx = win_lo + int(np.argmax(seg))
                return idx, float(self.h[idx])
            seg = self.l[win_lo:win_hi + 1]
            idx = win_lo + int(np.argmin(seg))
            return idx, float(self.l[idx])
        # B:金叉/死叉那根 + 顶/底分型 —— 取段内该向分型中价格极值者(无则回退运行极值)。
        fx = self._top_fx if direction == "UP" else self._bot_fx
        cand = [m for m in fx if begin < m <= t]
        if not cand:
            return ext_idx, ext_val
        if direction == "UP":
            idx = max(cand, key=lambda m: self.h[m])
            return idx, float(self.h[idx])
        idx = min(cand, key=lambda m: self.l[m])
        return idx, float(self.l[idx])

    # -------------------------------------------------- 段 range / 背驰强度
    def _seg_range(self, direction: str, begin: int, end: int):
        """V_zhongshu:M=MA34 值重叠 / P=段价格高低点重叠 → (low, high)。"""
        a, b = min(begin, end), max(begin, end)
        if self.v["zhongshu"] == "M":
            seg = self.ma34[a:b + 1]
            if np.all(np.isnan(seg)):
                return np.nan, np.nan
            return float(np.nanmin(seg)), float(np.nanmax(seg))
        return float(np.min(self.l[a:b + 1])), float(np.max(self.h[a:b + 1]))

    def _seg_strength(self, direction: str, begin: int, end: int, begin_val: float, end_val: float):
        """V_beichi:ST=|幅度|/K线数 / MA=Σ|MA5-MA34|(段内,同向面积)。"""
        span = max(1, abs(end - begin))
        if self.v["beichi"] == "ST":
            return abs(end_val - begin_val) / span
        a, b = min(begin, end), max(begin, end)
        diff = self.ma5[a:b + 1] - self.ma34[a:b + 1]
        area = np.nansum(np.abs(diff))
        return float(area)

    def _growth_ok(self, direction: str, new_idx: int, prev_idx: int) -> bool:
        """C 生长:上涨 新顶实体下轨 > 前顶实体上轨(下跌对称)。用作段延续 guard。"""
        nb_lo, nb_hi = min(self.o[new_idx], self.c[new_idx]), max(self.o[new_idx], self.c[new_idx])
        pb_lo, pb_hi = min(self.o[prev_idx], self.c[prev_idx]), max(self.o[prev_idx], self.c[prev_idx])
        if direction == "UP":
            return nb_lo > pb_hi
        return nb_hi < pb_lo

    # -------------------------------------------------- 段翻转(finalize + 起新段)
    def _finalize_and_flip(self, t: int):
        """在 bar t 确认 forming 段结束(端点冻结),起反向新段。返回 finalized Segment。"""
        f = self._forming
        end_idx, end_val = self._endpoint(f.dir, f.begin_idx, t, f.ext_idx, f.ext_val)
        rng_lo, rng_hi = self._seg_range(f.dir, f.begin_idx, end_idx)
        strength = self._seg_strength(f.dir, f.begin_idx, end_idx, f.begin_val, end_val)
        seg = Segment(dir=f.dir, begin_idx=f.begin_idx, end_idx=end_idx,
                      begin_val=f.begin_val, end_val=end_val, confirm_bar=t,
                      detect_lag=t - end_idx, rng_low=rng_lo, rng_high=rng_hi,
                      strength=strength)
        self.segs.append(seg)
        # 起反向新段(begin = 刚确认端点)
        new_dir = "DOWN" if f.dir == "UP" else "UP"
        if new_dir == "UP":
            sub = self.l[end_idx:t + 1]
            ei = end_idx + int(np.argmin(sub))
            ev = float(self.l[ei])
        else:
            sub = self.h[end_idx:t + 1]
            ei = end_idx + int(np.argmax(sub))
            ev = float(self.h[ei])
        self._forming = _Forming(dir=new_dir, begin_idx=end_idx, begin_val=end_val,
                                 ext_idx=ei, ext_val=ev, had_cross=False)
        return seg

    # -------------------------------------------------- 中枢维护(confirmed segs)
    def _update_zhongshu(self, t: int):
        """新 confirmed 段加入后,尝试成/延中枢(MA34 or 价重叠 ≥3 连续段;3/5/7,9 升级)。"""
        segs = self.segs
        # 延伸当前中枢:后续段仍与 [zd,zg] 相交 → seg_count++;否则该中枢完成,从其后找新中枢
        if self.zss:
            zs = self.zss[-1]
            nxt = zs.seg_start + zs.seg_count
            while nxt < len(segs):
                s = segs[nxt]
                if np.isnan(s.rng_low) or np.isnan(s.rng_high):
                    break
                if s.rng_high >= zs.zd and s.rng_low <= zs.zg:   # 相交=延伸
                    zs.seg_count += 1
                    nxt += 1
                else:
                    self._zs_search = nxt                        # 破坏中枢的段起,找新中枢
                    break
            else:
                return  # 段用尽,当前中枢继续挂着
        # 找新中枢:从 _zs_search 起,任意连续 3 段 range 重叠
        i = self._zs_search
        while i + 2 < len(segs):
            r = [segs[j] for j in (i, i + 1, i + 2)]
            if any(np.isnan(s.rng_low) or np.isnan(s.rng_high) for s in r):
                i += 1
                continue
            zd = max(s.rng_low for s in r)
            zg = min(s.rng_high for s in r)
            if zg > zd:
                self.zss.append(Zhongshu(zd=zd, zg=zg, seg_start=i, seg_count=3,
                                         confirm_bar=t))
                self._zs_search = i
                return
            i += 1

    # -------------------------------------------------- 买点(进一/二/三)+ 退出标记
    def _emit(self, bsp_type, is_buy, is_exit, nominal_idx, confirm_idx,
              seg_dir, regime_bull, zg, zd):
        entry_idx = confirm_idx + 1
        self.events.append({
            "bsp_type": bsp_type, "is_buy": bool(is_buy), "is_exit": bool(is_exit),
            "nominal_idx": int(nominal_idx),
            "nominal_time": self.open_time[nominal_idx],
            "nominal_price": float(self.c[nominal_idx]),
            "confirm_idx": int(confirm_idx),
            "conf_time": self.close_time[confirm_idx],       # PIT 可知时刻(确认 bar 收盘)
            "confirm_lag_bars": int(confirm_idx - nominal_idx),
            "entry_time": (self.open_time[entry_idx] if entry_idx < self.n else pd.NaT),
            "entry_price": (float(self.o[entry_idx]) if entry_idx < self.n else np.nan),
            "seg_dir": seg_dir,
            "regime_bull": (bool(regime_bull) if regime_bull is not None else None),
            "zg": (float(zg) if zg is not None and np.isfinite(zg) else np.nan),
            "zd": (float(zd) if zd is not None and np.isfinite(zd) else np.nan),
        })

    def _cur_zs(self) -> Zhongshu | None:
        return self.zss[-1] if self.zss else None

    def _regime(self, t: int):
        if np.isnan(self.ma170[t]):
            return None
        return bool(self.c[t] > self.ma170[t])

    def _try_buypoints(self, t: int, flipped_seg: Segment | None):
        """进一(背驰底反转,at DOWN→UP flip)/ 进二(回调不破,回踩 MA34)/ 进三(中枢突破回踩)。

        进二/进三 用 pending 态:先记回调底分型,待 MA5 拐头向上那根才触发(MA 滞后于价,
        单在分型第3根判 MA5 已拐会漏——B3 pullback 低点常先于 MA5 转折数根)。做多方向。
        """
        zs = self._cur_zs()
        reg = self._regime(t)
        zg = zs.zg if zs else None
        zd = zs.zd if zs else None
        f = self._forming
        turn_up = (t >= 1 and not np.isnan(self.ma5[t]) and not np.isnan(self.ma5[t - 1])
                   and self.ma5[t] > self.ma5[t - 1])

        # 进一:DOWN→UP 翻转(刚 finalize 的是 DOWN 段),该下跌段较前一下跌段背驰(力度减弱)。
        #   confirmed 段严格 UP/DOWN 交替 → 上一条同向段 = segs[-3](O(1))。
        if flipped_seg is not None and flipped_seg.dir == "DOWN" and len(self.segs) >= 3:
            prev = self.segs[-3]
            # 背驰 = 新低更低但力度更弱(V_beichi:ST 幅度/根数更小;MA 面积更小)
            if prev.dir == "DOWN" and flipped_seg.end_val < prev.end_val \
                    and flipped_seg.strength < prev.strength:
                self._emit("B1", True, False, flipped_seg.end_idx, t, "UP", reg, zg, zd)

        # 进二:UP forming 内,回调底分型不破段起低点、回踩 MA34 不破(low≥MA34);MA5 拐头向上触发
        if f is not None and f.dir == "UP":
            if t >= 2 and self.is_bot[t - 1]:
                m = t - 1
                if (m > f.begin_idx and self.l[m] > f.begin_val
                        and not np.isnan(self.ma34[m]) and self.l[m] >= self.ma34[m]):
                    self._pend_b2 = m
            if self._pend_b2 >= 0:
                if self.l[t] < f.begin_val:                       # 跌破段起低 → 回调失效
                    self._pend_b2 = -1
                elif (turn_up and not np.isnan(self.ma34[t]) and self.c[t] > self.ma34[t]
                        and t - self._last_b2_bar > 1):
                    self._last_b2_bar = t
                    self._emit("B2", True, False, self._pend_b2, t, "UP", reg, zg, zd)
                    self._pend_b2 = -1
        else:
            self._pend_b2 = -1                                    # 段非 UP → 清 pending

        # 进三:中枢向上突破后,回踩底分型 low≥ZG(不回中枢);MA5 拐头 + close 站上 ZG 且 >MA34 触发。
        #   一次突破只出一个进三:触发后消耗 broke_up,须先 close 回到 ZG 下(below)再突破才算下一个
        #   → 防持续上行中每个回踩都误报进三(否则数量爆炸)。
        if zs is not None:
            if self.c[t] <= zs.zg:
                zs.below = True                                  # 回到 ZG 下 → 允许下次突破算新进三
            elif self.c[t] > zs.zg and zs.below:
                zs.broke_up = True                               # 一次新的向上突破,武装
                zs.below = False
            if zs.broke_up and t >= 2 and self.is_bot[t - 1] and self.l[t - 1] >= zs.zg:
                zs.pending_b3 = t - 1
            if zs.pending_b3 >= 0:
                if self.l[t] < zs.zg:                             # 回落进中枢 → 突破回踩失效
                    zs.pending_b3 = -1
                elif (turn_up and self.c[t] > zs.zg and not np.isnan(self.ma34[t])
                        and self.c[t] > self.ma34[t] and t - zs.last_b3_bar > 1):
                    zs.last_b3_bar = t
                    self._emit("B3", True, False, zs.pending_b3, t, "UP", reg, zg, zd)
                    zs.pending_b3 = -1
                    zs.broke_up = False                          # 消耗:须回 ZG 下再突破才出下一个进三

        # 退出标记(防一/防二,只标不测收益):UP→DOWN 顶背驰=S1;DOWN forming 内反弹不过前高=S2
        if flipped_seg is not None and flipped_seg.dir == "UP" and len(self.segs) >= 3:
            prev = self.segs[-3]
            if prev.dir == "UP" and flipped_seg.strength < prev.strength \
                    and flipped_seg.end_val > prev.end_val:
                self._emit("S1", False, True, flipped_seg.end_idx, t, "DOWN", reg, zg, zd)
        if f is not None and f.dir == "DOWN" and t >= 2 and self.is_top[t - 1]:
            m = t - 1
            if m > f.begin_idx and self.h[m] < f.begin_val and self.ma5[t] < self.ma5[t - 1]:
                self._emit("S2", False, True, m, t, "DOWN", reg, zg, zd)

    # -------------------------------------------------- 单 bar 因果 step
    def _seed_forming(self, t: int):
        """MA34 首次可算时播种 forming 段(方向=sign(MA5-MA34))。"""
        d = "UP" if self.ma5[t] >= self.ma34[t] else "DOWN"
        if d == "UP":
            self._forming = _Forming(d, t, float(self.l[t]), t, float(self.h[t]))
        else:
            self._forming = _Forming(d, t, float(self.h[t]), t, float(self.l[t]))

    def _step(self, t: int) -> dict:
        # 分型完成:center = t-1(第3根=t 已收盘;PIT)
        if t >= 2:
            if self.is_top[t - 1]:
                self._top_fx.append(t - 1)
            if self.is_bot[t - 1]:
                self._bot_fx.append(t - 1)

        flipped = None
        if not np.isnan(self.ma34[t]):
            if self._forming is None:
                self._seed_forming(t)
            f = self._forming
            # 运行极值 + 段内排列("圈定")记账
            if f.dir == "UP":
                if self.h[t] >= f.ext_val:
                    f.ext_idx, f.ext_val = t, float(self.h[t])
                if not np.isnan(self.ma5[t]) and self.ma5[t] > self.ma34[t]:
                    f.had_cross = True
            else:
                if self.l[t] <= f.ext_val:
                    f.ext_idx, f.ext_val = t, float(self.l[t])
                if not np.isnan(self.ma5[t]) and self.ma5[t] < self.ma34[t]:
                    f.had_cross = True

            # 翻转判据(顶/底分型第3根 + MA5 拐头 + 已回撤 + 段内排列 + 最小段长 + 生长 guard)
            if self._flip_ready(t, f):
                flipped = self._finalize_and_flip(t)
                self._update_zhongshu(t)
            # 买点/退出(翻转当根 or 段内子结构);本根新增事件 = self.events[ev0:]
            ev0 = len(self.events)
            self._try_buypoints(t, flipped)
            bsp_now = [self.events[j]["bsp_type"] for j in range(ev0, len(self.events))]
        else:
            bsp_now = []

        return self._row(t, flipped, bsp_now)

    def _flip_ready(self, t: int, f: _Forming) -> bool:
        """翻转判据:顶/底分型第3根 + MA5 拐头 + 已回撤 + 段内被金叉/死叉"圈定" + 最小段长。

        C 生长 guard(use_growth):段仍在"生长"(相邻两同向分型呈 实体下轨>前实体上轨)且价尚未跌破
        前一摆动高/低点 → 判段延续、暂不翻(更稳、略增滞后);跌破前摆动点后释放。默认开。
        """
        if t < 1 or t - f.begin_idx < MIN_SEG_BARS:
            return False
        if np.isnan(self.ma5[t]) or np.isnan(self.ma5[t - 1]):
            return False
        if not f.had_cross:                                   # 未被金叉/死叉"圈定"→非真段
            return False
        # _top_fx/_bot_fx 升序(逐 bar append,center≤t-1)→ 段内"有分型"=末元素 > begin(O(1))
        if f.dir == "UP":
            turned = self.ma5[t] < self.ma5[t - 1]            # MA5 拐头向下
            pulled = self.h[t] < f.ext_val                    # 已从峰回撤
            has_fx = bool(self._top_fx) and self._top_fx[-1] > f.begin_idx
            if not (turned and pulled and has_fx):
                return False
            if self.v["use_growth"] and len(self._top_fx) >= 2 and self._top_fx[-2] > f.begin_idx:
                # 段延续:最近两顶仍"生长"且价未跌破前一摆动顶高 → 不翻(跌破后释放,不永久锁死真顶)
                if self._growth_ok("UP", self._top_fx[-1], self._top_fx[-2]) \
                        and self.h[t] > self.h[self._top_fx[-2]]:
                    return False
            return True
        turned = self.ma5[t] > self.ma5[t - 1]                # MA5 拐头向上
        pulled = self.l[t] > f.ext_val
        has_fx = bool(self._bot_fx) and self._bot_fx[-1] > f.begin_idx
        if not (turned and pulled and has_fx):
            return False
        if self.v["use_growth"] and len(self._bot_fx) >= 2 and self._bot_fx[-2] > f.begin_idx:
            if self._growth_ok("DOWN", self._bot_fx[-1], self._bot_fx[-2]) \
                    and self.l[t] < self.l[self._bot_fx[-2]]:
                return False
        return True

    def _row(self, t: int, flipped: Segment | None, bsp_now: list) -> dict:
        seg = self.segs[-1] if self.segs else None
        seg_dir = seg.dir if seg else "NONE"
        confirm_lag = seg.detect_lag if seg else -1
        zs = self._cur_zs()
        reg = self._regime(t)
        tip = self._forming
        bt = bsp_now                                         # 本 bar 确认的买/卖点(_step 传入)
        return {
            "bar_idx": t, "bar_open_time": self.open_time[t], "close_time": self.close_time[t],
            "close": float(self.c[t]),
            "ma5": self.ma5[t], "ma34": self.ma34[t], "ma170": self.ma170[t],
            "seg_dir": seg_dir, "seg_confirmed": seg is not None,
            "seg_begin_idx": (seg.begin_idx if seg else -1),
            "seg_end_idx": (seg.end_idx if seg else -1),
            "seg_begin_val": (seg.begin_val if seg else np.nan),
            "seg_end_val": (seg.end_val if seg else np.nan),
            "confirm_lag_bars": confirm_lag,
            "tip_dir": (tip.dir if tip else "NONE"),
            "zg": (zs.zg if zs else np.nan), "zd": (zs.zd if zs else np.nan),
            "zs_seg_count": (zs.seg_count if zs else 0),
            "zs_upgraded": (bool(zs.seg_count >= 9) if zs else False),
            "regime_bull": reg,
            "bsp_type": (",".join(bt) if bt else ""),
        }

    def run(self) -> dict:
        rows = [self._step(t) for t in range(self.n)]
        states = pd.DataFrame(rows)
        events = pd.DataFrame(self.events) if self.events else pd.DataFrame(
            columns=["bsp_type", "is_buy", "is_exit", "nominal_idx", "nominal_time",
                     "nominal_price", "confirm_idx", "conf_time", "confirm_lag_bars",
                     "entry_time", "entry_price", "seg_dir", "regime_bull", "zg", "zd"])
        segs = pd.DataFrame([vars(s) for s in self.segs]) if self.segs else pd.DataFrame()
        zss = pd.DataFrame([{k: v for k, v in vars(z).items()} for z in self.zss]) \
            if self.zss else pd.DataFrame()
        meta = {"tf": self.tf, "variants": self.v, "n_bars": self.n,
                "n_segs": len(self.segs), "n_zss": len(self.zss),
                "n_events": len(self.events),
                "detect_lag_median": (float(np.median([s.detect_lag for s in self.segs]))
                                      if self.segs else float("nan"))}
        return {"states": states, "events": events, "segs": segs, "zss": zss, "meta": meta}


def moer_engine(df_klines: pd.DataFrame, tf: str, variants: dict | None = None) -> dict:
    """摩尔缠论结构引擎入口。见模块 docstring。返回 dict{states, events, segs, zss, meta}。"""
    return MoerEngine(df_klines, tf, variants).run()


# ================================================================= 合成烟雾测试
def _mk_df(o, h, l, c, tf="4h"):
    """OHLC 数组 → 数据仓风格 DataFrame(UTC 索引,列名与 load_klines 一致)。"""
    n = len(c)
    idx = pd.date_range("2022-01-01", periods=n, freq=TF_TD.get(tf, tf), tz="UTC")
    return pd.DataFrame({"open": np.asarray(o, float), "high": np.asarray(h, float),
                         "low": np.asarray(l, float), "close": np.asarray(c, float)}, index=idx)


def _zz(anchors, leg=9, seed=0, noise=0.0015, wick=0.0012):
    """锚点折线 → OHLC。相邻锚点 linspace(leg 根)相连,每锚点=一个明确转折 → 稳定出分型。

    真实行情持续回撤,折线(zigzag)比单调 ramp 更贴近且跨 seed 稳健(单调强趋势段只在真转折
    那一根出分型,受噪声对齐影响极大 → 用折线让分型稳定成形)。open=前收盘 + 小噪声 + 影线。
    返回 (o, h, l, c)。
    """
    rng = np.random.default_rng(seed)
    path = [float(anchors[0])]
    for a, b in zip(anchors[:-1], anchors[1:]):
        path += list(np.linspace(a, b, leg))[1:]
    path = np.asarray(path, float)
    close = path * (1 + rng.normal(0, noise, len(path)))
    o = np.empty(len(close))
    o[0] = close[0]
    o[1:] = close[:-1]
    span = np.abs(rng.normal(0, wick, len(close)))
    h = np.maximum(o, close) * (1 + span)
    l = np.minimum(o, close) * (1 - span)
    return o, h, l, close


# 中枢震荡锚点(MA34 悬于带内,连续段 range 重叠 → 出中枢);复用于 测试2/5/3
_OSC = [108, 101, 109, 100, 110, 102, 108, 101, 110, 100, 109, 102]


def smoke() -> bool:
    print("[moer-smoke] ===== 合成手造数据烟雾/单元测试(不碰真实数据)=====", flush=True)
    checks: list[tuple[str, bool, str]] = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  — ' + detail) if detail else ''}", flush=True)

    # ---- 单元:分型(原始 K,不做包含处理);MA 手算比对 ----
    h = np.array([1., 3, 1])
    l = np.array([0., 2, 0])
    it, _ = fractals(h, l)
    chk("顶分型基元(中间 高最高∧低最高)", bool(it[1]))
    ib = fractals(np.array([3., 1, 3]), np.array([2., 0, 2]))[1]
    chk("底分型基元(中间 高最低∧低最低)", bool(ib[1]))
    # 不做包含处理:bar1(高12/低9)包含 bar2(高11/低10)(12>11∧9<10)。标准缠会先并 bar1+bar2
    #   再判分型;本引擎直接用原始 5K → center3(高14/低11,高>邻∧低>邻)= 顶分型,不受包含影响。
    hraw = np.array([10., 12, 11, 14, 9]); lraw = np.array([8., 9, 10, 11, 7])
    it_raw, _ = fractals(hraw, lraw)
    contained = (hraw[1] > hraw[2]) and (lraw[1] < lraw[2])          # bar2 ⊂ bar1(存在包含关系)
    chk("不做包含处理:含被包含 K,分型仍按原始 3K(center3=顶)", bool(it_raw[3]) and contained,
        f"is_top={list(np.flatnonzero(it_raw))} 存在包含={contained}")
    c = np.arange(1, 401, dtype=float)
    ma = compute_mas(c)
    manual = c[400 - 170:400].mean()
    chk("MA170 手算=rolling(收盘 SMA)", np.isclose(ma["ma170"][399], manual),
        f"{ma['ma170'][399]:.3f} vs {manual:.3f}")
    chk("MA warmup NaN(前 N-1 根不可算)", np.isnan(ma["ma5"][3]) and not np.isnan(ma["ma5"][4]))

    # ---- 测试1:上涨段 → MA5 金叉 + 新高 + 顶分型确认段翻转(端点≈真峰) ----
    a1 = [100, 92, 96, 86, 90, 80,                    # 下跌 zigzag 至 80
          88, 84, 98, 92, 112, 104, 124, 116, 132,    # 上涨 zigzag 至峰 132(MA5 金叉 MA34)
          120, 126, 108, 114, 96, 102, 90]            # 下跌 zigzag
    o1, h1, l1, c1 = _zz(a1, seed=1)
    r1 = moer_engine(_mk_df(o1, h1, l1, c1), "4h")
    segs1 = r1["segs"]
    up1 = segs1[segs1["dir"] == "UP"] if len(segs1) else segs1
    has_up = len(up1) > 0
    peak = float(up1["end_val"].max()) if has_up else 0.0
    top_near = has_up and abs(peak - 132) / 132 < 0.06
    chk("测试1:金叉后确认 UP 本质线段,端点≈真峰132", top_near,
        f"n_up={len(up1)} maxUPend={peak:.1f}" if has_up else "无 UP 段")
    dirs = list(segs1["dir"]) if len(segs1) else []
    flip_ud = any(dirs[i] == "UP" and dirs[i + 1] == "DOWN" for i in range(len(dirs) - 1))
    chk("测试1:段翻转 UP→DOWN(顶分型+MA5拐头确认)", flip_ud, f"dirs={dirs}")

    # ---- 测试4(嵌本1数据):confirm_lag ≥ 0 且有限;低滞后(摩尔卖点) ----
    lag_ok = bool((segs1["detect_lag"] >= 0).all() and np.isfinite(segs1["detect_lag"]).all())
    lag_med = float(segs1["detect_lag"].median())
    chk("测试4:段级 confirm_lag(detect) ≥ 0 且有限(全段)", lag_ok, f"detect_lag中位={lag_med:.1f}bars")
    chk("测试4b:低滞后(段级 detect_lag 中位 ≤ 40bars)", 0 <= lag_med <= 40, f"中位={lag_med:.1f}")
    st1 = r1["states"]
    stlag = st1[st1["seg_confirmed"]]["confirm_lag_bars"]
    chk("测试4c:状态表 confirm_lag_bars ≥ 0 有限", bool((stlag >= 0).all() and np.isfinite(stlag).all()))

    # ---- 测试2:中枢 3 段 MA34 重叠 → ZG/ZD ----
    a2 = [88, 94, 100] + _OSC * 2                     # 预热 + 窄幅震荡(MA34 悬带内,段 range 重叠)
    o2, h2, l2, c2 = _zz(a2, leg=8, seed=2)
    r2 = moer_engine(_mk_df(o2, h2, l2, c2), "4h")
    zdf = r2["zss"]
    has_zs = len(zdf) > 0
    zs_valid = bool(has_zs and (zdf["zg"] > zdf["zd"]).all() and (zdf["seg_count"] >= 3).all())
    st2 = r2["states"]
    band_bars = st2["zg"].notna() & st2["zd"].notna() & (st2["zg"] > st2["zd"])
    chk("测试2:≥3 段 MA34 重叠出中枢 ZG>ZD", zs_valid and bool(band_bars.any()),
        f"n_zs={len(zdf)} seg_count={list(zdf['seg_count'])[:3] if has_zs else []} "
        f"band_bars={int(band_bars.sum())}")
    if has_zs:
        first_band = int(np.flatnonzero(band_bars.to_numpy())[0])
        chk("测试2b:中枢 band 出现不早于第3段确认 bar(PIT)",
            first_band >= int(zdf.iloc[0]["confirm_bar"]),
            f"first_band_bar={first_band} confirm_bar={int(zdf.iloc[0]['confirm_bar'])}")

    # ---- 测试5:进三 = 中枢突破 → 回踩不破 ZG(且 close>MA34)触发 ----
    a5 = [88, 94, 100] + _OSC * 2 + [150, 122, 152, 142, 152]   # 震荡出中枢 → 突破150 → 回踩122(守 ZG上)→ 续涨
    o5, h5, l5, c5 = _zz(a5, leg=8, seed=5)
    r5 = moer_engine(_mk_df(o5, h5, l5, c5), "4h")
    ev5 = r5["events"]
    b3 = ev5[ev5["bsp_type"] == "B3"] if len(ev5) else ev5
    has_b3 = len(b3) > 0
    b3_ok = False
    if has_b3 and len(r5["zss"]):
        row = b3.iloc[0]
        # 触发时 名义回踩低点 ≥ ZG(不回中枢)、ZG 有限、方向做多
        b3_ok = bool(row["nominal_price"] >= row["zg"] and np.isfinite(row["zg"]) and row["is_buy"])
    chk("测试5:进三(中枢突破回踩不破 ZG,close>MA34)触发", has_b3 and b3_ok,
        (f"n_B3={len(b3)} nominal={b3.iloc[0]['nominal_price']:.1f} ZG={b3.iloc[0]['zg']:.1f}"
         if has_b3 else "无 B3"))

    # ---- 测试3:PIT —— 篡改未来 bar 不改过去任何状态/事件 ----
    o7, h7, l7, c7 = _zz(a5, leg=8, seed=7)
    df_a = _mk_df(o7, h7, l7, c7)
    k = 120
    o8, h8, l8, c8 = o7.copy(), h7.copy(), l7.copy(), c7.copy()
    for arr in (o8, h8, l8, c8):
        arr[k + 1:] = arr[k + 1:] * 3.0 + 5.0         # 篡改 t>k 的未来
    df_b = _mk_df(o8, h8, l8, c8)
    sa = moer_engine(df_a, "4h")["states"].iloc[:k + 1]
    sb = moer_engine(df_b, "4h")["states"].iloc[:k + 1]
    pit_cols = ["seg_dir", "seg_confirmed", "confirm_lag_bars", "zg", "zd",
                "regime_bull", "bsp_type", "ma5", "ma34", "ma170", "seg_end_idx"]
    pit_ok, bad = True, ""
    for col in pit_cols:
        a, b = sa[col].to_numpy(), sb[col].to_numpy()
        same = (np.array_equal(a, b, equal_nan=True) if a.dtype.kind == "f"
                else np.array_equal(a.astype(object), b.astype(object)))
        if not same:
            pit_ok, bad = False, col
            break
    chk("测试3:PIT — 改未来 bar 不改 ≤k 状态", pit_ok, f"首个不一致列={bad}" if not pit_ok else "全一致")
    ea = moer_engine(df_a, "4h")["events"]
    eb = moer_engine(df_b, "4h")["events"]
    ea = ea[ea["confirm_idx"] <= k] if len(ea) else ea
    eb = eb[eb["confirm_idx"] <= k] if len(eb) else eb
    ev_pit = (len(ea) == len(eb)) and (len(ea) == 0 or (
        list(ea["bsp_type"]) == list(eb["bsp_type"]) and
        list(ea["confirm_idx"]) == list(eb["confirm_idx"])))
    chk("测试3b:PIT — 改未来 bar 不改 ≤k 事件", ev_pit, f"events≤k a={len(ea)} b={len(eb)}")

    # ---- 荒谬统计量烟雾(docs/44):对称随机游走 UP/DOWN 段数应大致均衡,不一边倒 ----
    rng = np.random.default_rng(99)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1600)))
    ow = np.empty(len(walk)); ow[0] = walk[0]; ow[1:] = walk[:-1]
    sp = np.abs(rng.normal(0, 0.0015, len(walk)))
    rw = moer_engine(_mk_df(ow, np.maximum(ow, walk) * (1 + sp),
                            np.minimum(ow, walk) * (1 - sp), walk), "4h")
    sg = rw["segs"]
    frac_up = float((sg["dir"] == "UP").mean()) if len(sg) >= 6 else float("nan")
    chk("荒谬值烟雾:对称游走 UP 段占比∈[0.30,0.70](非一边倒)",
        len(sg) >= 6 and 0.30 <= frac_up <= 0.70, f"UP占比={frac_up:.2f} n_seg={len(sg)}")

    # ---- variants 8 组合全可运行 + 端点 A≠B 生效(机制层可切换) ----
    dfv = _mk_df(o5, h5, l5, c5)
    vruns = []
    for ep in ("A", "B"):
        for zr in ("M", "P"):
            for bc in ("ST", "MA"):
                rr = moer_engine(dfv, "4h", {"endpoint": ep, "zhongshu": zr, "beichi": bc})
                vruns.append((f"{ep}{zr}{bc}", len(rr["segs"]), len(rr["zss"])))
    chk("variants 8 组合全可运行且出段", all(ns > 0 for _, ns, _ in vruns),
        "; ".join(f"{k}:seg{s}" for k, s, _ in vruns[:4]))
    ra = moer_engine(dfv, "4h", {"endpoint": "A"})["segs"]
    rb = moer_engine(dfv, "4h", {"endpoint": "B"})["segs"]
    ep_diff = (len(ra) == len(rb) and len(ra) > 0
               and not np.array_equal(ra["end_idx"].to_numpy(), rb["end_idx"].to_numpy()))
    chk("端点变体 A≠B 生效(至少一段端点不同)", ep_diff,
        f"端点不同段数={int((ra['end_idx'].to_numpy() != rb['end_idx'].to_numpy()).sum()) if len(ra) == len(rb) and len(ra) else 'n/a'}")

    # ---- 未知 variant 键报错(防拼写静默) ----
    bad_key = False
    try:
        moer_engine(dfv, "4h", {"bogus": 1})
    except ValueError:
        bad_key = True
    chk("未知 variant 键抛 ValueError", bad_key)

    ok = all(cnd for _, cnd, _ in checks)
    npass = sum(cnd for _, cnd, _ in checks)
    print(f"\n[moer-smoke] {'✅ 全部通过' if ok else '❌ 有失败'} ({npass}/{len(checks)})", flush=True)
    return ok


# ----------------------------------------------------------------- CLI
def _demo_real():
    """可选:在真实 BTC 4h 上跑一遍打印摘要(不进证据链,仅供肉眼校验)。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from zen2c_data import load_klines  # noqa
    df = load_klines("BTCUSDT", "4h")
    r = moer_engine(df, "4h")
    m = r["meta"]
    print(f"[moer-demo] BTCUSDT 4h n={m['n_bars']} segs={m['n_segs']} zss={m['n_zss']} "
          f"events={m['n_events']} detect_lag中位={m['detect_lag_median']:.1f}bars", flush=True)
    ev = r["events"]
    if len(ev):
        print(ev["bsp_type"].value_counts().to_string(), flush=True)


def main():
    ap = argparse.ArgumentParser(description="摩尔缠论结构引擎(纯 MA;docs/45 §8)")
    ap.add_argument("--smoke", action="store_true", help="只跑合成手造数据烟雾/单元测试")
    ap.add_argument("--demo", action="store_true", help="在真实 BTC 4h 上跑摘要(肉眼校验,不进证据链)")
    args = ap.parse_args()
    if args.smoke:
        sys.exit(0 if smoke() else 1)
    if args.demo:
        _demo_real()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
