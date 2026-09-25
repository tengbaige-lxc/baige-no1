"""Lightweight Elliott-wave signal engine without third-party dependencies.

Signal convention:
- 1 = bullish / long
- -1 = bearish / short
- 0 = neutral
"""


class SignalEngine:
    def __init__(self, swing_window: int = 8, fib_tolerance: float = 0.18, min_wave_bars: int = 4):
        self.swing_window = max(2, int(swing_window or 8))
        self.fib_tolerance = float(fib_tolerance or 0.18)
        self.min_wave_bars = max(1, int(min_wave_bars or 4))

    def _find_swings(self, highs: list[float], lows: list[float]) -> list[dict]:
        w = self.swing_window
        if len(highs) < w * 2 + 1:
            return []

        raw = []
        for i in range(w, len(highs) - w):
            hi_window = highs[i - w : i + w + 1]
            lo_window = lows[i - w : i + w + 1]
            is_high = highs[i] == max(hi_window)
            is_low = lows[i] == min(lo_window)
            if is_high and not is_low:
                raw.append({"pos": i, "price": float(highs[i]), "type": "H"})
            elif is_low and not is_high:
                raw.append({"pos": i, "price": float(lows[i]), "type": "L"})

        if len(raw) < 2:
            return raw

        zigzag = [raw[0]]
        for pt in raw[1:]:
            last = zigzag[-1]
            if pt["type"] == last["type"]:
                if pt["type"] == "H" and pt["price"] > last["price"]:
                    zigzag[-1] = pt
                elif pt["type"] == "L" and pt["price"] < last["price"]:
                    zigzag[-1] = pt
            else:
                zigzag.append(pt)
        return zigzag

    def _check_fib_ratios(self, w1: float, w2: float, w3: float, w4: float, w5: float) -> bool:
        tol = self.fib_tolerance
        if w1 <= 0 or w3 <= 0:
            return False
        r2 = w2 / w1
        r3 = w3 / w1
        r4 = w4 / w3
        return (
            0.5 - tol <= r2 <= 0.618 + tol
            and 1.0 - tol <= r3 <= 2.618 + tol
            and 0.236 - tol <= r4 <= 0.5 + tol
        )

    def _check_min_bars(self, swings: list[dict], start: int, count: int) -> bool:
        for i in range(start, start + count - 1):
            if abs(int(swings[i + 1]["pos"]) - int(swings[i]["pos"])) < self.min_wave_bars:
                return False
        return True

    def _find_impulse(self, swings: list[dict]) -> list[tuple[int, int]]:
        results = []
        for i in range(len(swings) - 5):
            types = [s["type"] for s in swings[i : i + 6]]

            if types == ["L", "H", "L", "H", "L", "H"]:
                x, p1, p2, p3, p4, p5 = swings[i : i + 6]
                wave1 = p1["price"] - x["price"]
                wave2 = p1["price"] - p2["price"]
                wave3 = p3["price"] - p2["price"]
                wave4 = p3["price"] - p4["price"]
                wave5 = p5["price"] - p4["price"]
                if wave1 <= 0 or wave3 <= 0 or wave5 <= 0:
                    continue
                if p2["price"] <= x["price"] or wave3 < wave1 and wave3 < wave5:
                    continue
                if p4["price"] <= p1["price"]:
                    continue
                if self._check_min_bars(swings, i, 6) and self._check_fib_ratios(wave1, wave2, wave3, wave4, wave5):
                    results.append((p5["pos"], -1))

            elif types == ["H", "L", "H", "L", "H", "L"]:
                x, p1, p2, p3, p4, p5 = swings[i : i + 6]
                wave1 = x["price"] - p1["price"]
                wave2 = p2["price"] - p1["price"]
                wave3 = p2["price"] - p3["price"]
                wave4 = p4["price"] - p3["price"]
                wave5 = p4["price"] - p5["price"]
                if wave1 <= 0 or wave3 <= 0 or wave5 <= 0:
                    continue
                if p2["price"] >= x["price"] or wave3 < wave1 and wave3 < wave5:
                    continue
                if p4["price"] >= p1["price"]:
                    continue
                if self._check_min_bars(swings, i, 6) and self._check_fib_ratios(wave1, wave2, wave3, wave4, wave5):
                    results.append((p5["pos"], 1))
        return results

    def _find_abc(self, swings: list[dict]) -> list[tuple[int, int]]:
        tol = self.fib_tolerance
        results = []
        for i in range(len(swings) - 3):
            types = [s["type"] for s in swings[i : i + 4]]

            if types == ["H", "L", "H", "L"]:
                start, pa, pb, pc = swings[i : i + 4]
                wave_a = start["price"] - pa["price"]
                wave_b = pb["price"] - pa["price"]
                wave_c = pb["price"] - pc["price"]
                if wave_a <= 0 or wave_b <= 0 or wave_c <= 0 or pb["price"] >= start["price"]:
                    continue
                r_b = wave_b / wave_a
                r_c = wave_c / wave_a
                if 0.382 - tol <= r_b <= 0.618 + tol and 0.618 - tol <= r_c <= 1.618 + tol:
                    if self._check_min_bars(swings, i, 4):
                        results.append((pc["pos"], 1))

            elif types == ["L", "H", "L", "H"]:
                start, pa, pb, pc = swings[i : i + 4]
                wave_a = pa["price"] - start["price"]
                wave_b = pa["price"] - pb["price"]
                wave_c = pc["price"] - pb["price"]
                if wave_a <= 0 or wave_b <= 0 or wave_c <= 0 or pb["price"] <= start["price"]:
                    continue
                r_b = wave_b / wave_a
                r_c = wave_c / wave_a
                if 0.382 - tol <= r_b <= 0.618 + tol and 0.618 - tol <= r_c <= 1.618 + tol:
                    if self._check_min_bars(swings, i, 4):
                        results.append((pc["pos"], -1))
        return results

    def generate(self, data_map: dict) -> dict:
        result = {}
        for code, rows in data_map.items():
            if hasattr(rows, "to_dict"):
                records = rows.reset_index().to_dict("records")
            else:
                records = list(rows or [])
            signals = [0 for _ in records]
            highs = [float(r["high"]) for r in records]
            lows = [float(r["low"]) for r in records]
            swings = self._find_swings(highs, lows)
            if len(swings) >= 6:
                for pos, direction in self._find_impulse(swings):
                    if 0 <= pos < len(signals):
                        signals[pos] = direction
            if len(swings) >= 4:
                for pos, direction in self._find_abc(swings):
                    if 0 <= pos < len(signals):
                        signals[pos] = direction
            result[code] = signals
        return result
