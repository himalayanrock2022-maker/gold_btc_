"""Research-only multi-timeframe signal engine. No order execution."""
from __future__ import annotations
import math, time
from typing import Any
import pandas as pd

TIMEFRAMES = ("M5", "M15", "H1", "H4")


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    required = {"time", "open", "high", "low", "close"}
    if df.empty or not required.issubset(df.columns):
        raise ValueError("Candle data missing required OHLC fields")
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    for col in ("open", "high", "low", "close", "tick_volume"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["time", "open", "high", "low", "close"]).sort_values("time")
    df = df.drop_duplicates("time").reset_index(drop=True)
    if len(df) < 220:
        raise ValueError(f"Need at least 220 closed candles; got {len(df)}")
    return df


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    close, high, low = x.close, x.high, x.low
    x["ema20"] = close.ewm(span=20, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    x["ema200"] = close.ewm(span=200, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    x["rsi"] = (100 - (100 / (1 + rs))).fillna(50)
    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    x["macd"] = fast - slow
    x["macd_signal"] = x.macd.ewm(span=9, adjust=False).mean()
    prev_close = close.shift(1)
    tr = pd.concat([(high-low), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    x["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_safe = x.atr.replace(0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_safe
    dx = 100 * (plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, float("nan"))
    x["adx"] = dx.ewm(alpha=1/14, adjust=False).mean().fillna(0)
    x["plus_di"], x["minus_di"] = plus_di.fillna(0), minus_di.fillna(0)
    if "tick_volume" not in x:
        x["tick_volume"] = 0
    x["vol_sma20"] = x.tick_volume.rolling(20).mean()
    return x


def _trend(row: pd.Series) -> str:
    if row.close > row.ema50 > row.ema200:
        return "BUY"
    if row.close < row.ema50 < row.ema200:
        return "SELL"
    return "NEUTRAL"


def make_signal(symbol: str, bundle: dict[str, list[dict]], tick: dict, cfg: dict) -> dict | None:
    """Return a scored signal only when independent timeframe filters agree."""
    fs = {tf: indicators(frame(bundle[tf])) for tf in TIMEFRAMES}
    rows = {tf: fs[tf].iloc[-1] for tf in TIMEFRAMES}
    # HTF direction must agree on H4 and H1.
    t4, t1 = _trend(rows["H4"]), _trend(rows["H1"])
    if t4 == "NEUTRAL" or t4 != t1:
        return None
    side = t4
    m15, m5 = rows["M15"], rows["M5"]
    # Reject stale quotes/candles rather than publishing outdated levels.
    if time.time() - float(tick.get("time", 0)) > 180:
        return None
    now_utc = pd.Timestamp.now(tz="UTC")
    if (now_utc - m15.time).total_seconds() > 20 * 60 or (now_utc - m5.time).total_seconds() > 8 * 60:
        return None
    points, reasons = 0, []
    def add(condition: bool, pts: int, reason: str):
        nonlocal points
        if condition:
            points += pts
            reasons.append(reason)
    add(True, 2, f"H4 trend {side}")
    add(True, 2, f"H1 trend {side}")
    add((m15.close > m15.ema20 if side == "BUY" else m15.close < m15.ema20), 1, "M15 EMA20 alignment")
    add((m15.macd > m15.macd_signal if side == "BUY" else m15.macd < m15.macd_signal), 1, "M15 MACD momentum")
    add((m5.macd > m5.macd_signal if side == "BUY" else m5.macd < m5.macd_signal), 1, "M5 MACD trigger")
    add((45 <= m15.rsi <= 68 if side == "BUY" else 32 <= m15.rsi <= 55), 1, "M15 RSI in momentum zone")
    add(m15.adx >= 18, 1, "M15 ADX trend strength")
    vol_ok = bool(m5.vol_sma20 and m5.tick_volume >= m5.vol_sma20)
    add(vol_ok, 1, "M5 tick volume above average")
    # Reject extended entries and oversized spreads.
    atr = float(m15.atr)
    if not math.isfinite(atr) or atr <= 0:
        return None
    price = float(tick["ask"] if side == "BUY" else tick["bid"])
    spread = float(tick["ask"]) - float(tick["bid"])
    if spread <= 0 or spread > atr * float(cfg.get("MAX_SPREAD_ATR", 0.18)):
        return None
    if abs(price - float(m15.ema20)) > atr * 1.5:
        return None
    minimum = int(cfg.get("MIN_SCORE", 8))
    if points < minimum:
        return None
    # ATR-based protective levels, normalized to broker digits in bridge.
    sl_dist = atr * float(cfg.get("SL_ATR", 1.5))
    if side == "BUY":
        sl, risk = price - sl_dist, sl_dist
        tps = [price + risk*r for r in (1.5, 2.5, 3.5)]
    else:
        sl, risk = price + sl_dist, sl_dist
        tps = [price - risk*r for r in (1.5, 2.5, 3.5)]
    digits = int(tick.get("digits", 2))
    rnd = lambda value: round(float(value), digits)
    return {
        "symbol": symbol, "side": side, "entry": rnd(price), "sl": rnd(sl),
        "tp1": rnd(tps[0]), "tp2": rnd(tps[1]), "tp3": rnd(tps[2]),
        "score": points, "max_score": 10,
        "score_label": f"{points}/10 confluence (not a win probability)",
        "rsi_m15": float(m15.rsi), "adx_m15": float(m15.adx),
        "atr_m15": rnd(atr), "spread": rnd(spread),
        "bar_time": str(m15.time), "reasons": reasons,
        "trend_h4": t4, "trend_h1": t1,
    }
