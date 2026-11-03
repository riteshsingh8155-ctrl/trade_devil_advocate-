"""
smc_ict_core.py
================
SMC (Smart Money Concepts) / ICT style multi-timeframe analysis engine.

Top-down flow implemented:
    4H  -> overall directional BIAS (structure: HH/HL or LH/LL)
    1H  -> confirms bias + finds the higher-timeframe POI (unmitigated
           Order Block / Fair Value Gap) that price should react from
    15M -> zooms into the POI, looks for a liquidity sweep + CHoCH
           (change of character) in the direction of bias -> refined POI
    5M  -> looks for a fresh BOS (break of structure) confirming reversal
           at the refined POI -> tightest POI
    1M  -> entry trigger: price taps the 5M POI and prints a 1M
           CHoCH/BOS in the trade direction -> ENTRY signal generated

This is a decision-support / research framework, NOT a guaranteed
profitable system. Always backtest and paper-trade before using real
capital. Nothing here is financial advice.

DATA INPUT
----------
Each timeframe needs an OHLCV pandas DataFrame with columns:
    ['Open', 'High', 'Low', 'Close', 'Volume']  and a DatetimeIndex.

You can get this data from:
  * Your broker/TradingView CSV export (recommended, most reliable)
  * yfinance (if you have internet access where you run this script):
        import yfinance as yf
        df = yf.download("EURUSD=X", period="60d", interval="15m")
  * ccxt for crypto exchanges
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List, Literal

Bias = Literal["bullish", "bearish", "neutral"]


# ---------------------------------------------------------------------------
# 1. SWING / MARKET STRUCTURE
# ---------------------------------------------------------------------------

def find_swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Mark swing highs / swing lows using a fractal (left/right bars) method."""
    df = df.copy()
    highs = df["High"].values
    lows = df["Low"].values
    n = len(df)

    swing_high = np.zeros(n, dtype=bool)
    swing_low = np.zeros(n, dtype=bool)

    for i in range(left, n - right):
        window_h = highs[i - left:i + right + 1]
        window_l = lows[i - left:i + right + 1]
        if highs[i] == window_h.max() and np.argmax(window_h) == left:
            swing_high[i] = True
        if lows[i] == window_l.min() and np.argmin(window_l) == left:
            swing_low[i] = True

    df["swing_high"] = swing_high
    df["swing_low"] = swing_low
    return df


def market_structure_bias(df: pd.DataFrame, left: int = 3, right: int = 3) -> dict:
    """
    Determine directional bias from the last few swing points.
    Bullish  = higher highs & higher lows (HH/HL)
    Bearish  = lower highs & lower lows (LH/LL)
    Also flags the most recent BOS (break of structure) and
    CHoCH (change of character, i.e. a potential reversal).
    """
    d = find_swings(df, left, right)
    sh = d[d["swing_high"]][["High"]].tail(4)
    sl = d[d["swing_low"]][["Low"]].tail(4)

    bias: Bias = "neutral"
    if len(sh) >= 2 and len(sl) >= 2:
        highs_rising = sh["High"].iloc[-1] > sh["High"].iloc[-2]
        lows_rising = sl["Low"].iloc[-1] > sl["Low"].iloc[-2]
        highs_falling = sh["High"].iloc[-1] < sh["High"].iloc[-2]
        lows_falling = sl["Low"].iloc[-1] < sl["Low"].iloc[-2]

        if highs_rising and lows_rising:
            bias = "bullish"
        elif highs_falling and lows_falling:
            bias = "bearish"

    # BOS: close breaks beyond the last confirmed swing high/low
    last_close = df["Close"].iloc[-1]
    bos = None
    choch = None
    if len(sh):
        last_swing_high = sh["High"].iloc[-1]
        if last_close > last_swing_high:
            bos = "bullish"
            if bias == "bearish":
                choch = "bullish"  # reversal against prior bearish structure
    if len(sl):
        last_swing_low = sl["Low"].iloc[-1]
        if last_close < last_swing_low:
            bos = "bearish"
            if bias == "bullish":
                choch = "bearish"

    return {
        "bias": bias,
        "bos": bos,
        "choch": choch,
        "last_swing_high": float(sh["High"].iloc[-1]) if len(sh) else None,
        "last_swing_low": float(sl["Low"].iloc[-1]) if len(sl) else None,
    }


# ---------------------------------------------------------------------------
# 2. ORDER BLOCKS
# ---------------------------------------------------------------------------

def find_order_blocks(df: pd.DataFrame, impulse_atr_mult: float = 1.5,
                       lookback: int = 100) -> pd.DataFrame:
    """
    Very common ICT-style definition:
      Bullish OB = last down (red) candle before a strong up impulse
                   that breaks structure.
      Bearish OB = last up (green) candle before a strong down impulse
                   that breaks structure.
    'Strong impulse' = candle range > impulse_atr_mult * ATR(14).
    Returns unmitigated (price hasn't traded back through) OBs only,
    most recent first.
    """
    d = df.tail(lookback).copy()
    d["range"] = d["High"] - d["Low"]
    d["atr"] = d["range"].rolling(14).mean()
    d["bullish_candle"] = d["Close"] > d["Open"]
    d["bearish_candle"] = d["Close"] < d["Open"]

    obs = []
    for i in range(1, len(d) - 1):
        row = d.iloc[i]
        prev = d.iloc[i - 1]
        if pd.isna(row["atr"]) or row["atr"] == 0:
            continue
        impulse_strength = row["range"] / row["atr"]

        # Bullish OB: prior candle bearish, this candle a strong bullish impulse
        if row["bullish_candle"] and prev["bearish_candle"] and impulse_strength >= impulse_atr_mult:
            ob_high = prev["High"]
            ob_low = prev["Low"]
            mitigated = (d["Low"].iloc[i + 1:] <= ob_high).any()
            obs.append({
                "type": "bullish", "index": d.index[i - 1],
                "high": ob_high, "low": ob_low, "mitigated": bool(mitigated)
            })

        # Bearish OB: prior candle bullish, this candle a strong bearish impulse
        if row["bearish_candle"] and prev["bullish_candle"] and impulse_strength >= impulse_atr_mult:
            ob_high = prev["High"]
            ob_low = prev["Low"]
            mitigated = (d["High"].iloc[i + 1:] >= ob_low).any()
            obs.append({
                "type": "bearish", "index": d.index[i - 1],
                "high": ob_high, "low": ob_low, "mitigated": bool(mitigated)
            })

    ob_df = pd.DataFrame(obs)
    if len(ob_df):
        ob_df = ob_df.sort_values("index", ascending=False).reset_index(drop=True)
    return ob_df


# ---------------------------------------------------------------------------
# 3. FAIR VALUE GAPS (imbalance)
# ---------------------------------------------------------------------------

def find_fvgs(df: pd.DataFrame, lookback: int = 100) -> pd.DataFrame:
    """
    3-candle imbalance:
      Bullish FVG: candle1.High < candle3.Low  (gap up)
      Bearish FVG: candle1.Low  > candle3.High (gap down)
    Returns unfilled gaps only, most recent first.
    """
    d = df.tail(lookback).copy()
    gaps = []
    for i in range(2, len(d)):
        c1, c3 = d.iloc[i - 2], d.iloc[i]
        if c1["High"] < c3["Low"]:
            filled = (d["Low"].iloc[i + 1:] <= c1["High"]).any()
            gaps.append({"type": "bullish", "index": d.index[i - 1],
                         "top": c3["Low"], "bottom": c1["High"], "filled": bool(filled)})
        if c1["Low"] > c3["High"]:
            filled = (d["High"].iloc[i + 1:] >= c1["Low"]).any()
            gaps.append({"type": "bearish", "index": d.index[i - 1],
                         "top": c1["Low"], "bottom": c3["High"], "filled": bool(filled)})
    fvg_df = pd.DataFrame(gaps)
    if len(fvg_df):
        fvg_df = fvg_df.sort_values("index", ascending=False).reset_index(drop=True)
    return fvg_df


# ---------------------------------------------------------------------------
# 4. LIQUIDITY SWEEPS (stop hunts)
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(df: pd.DataFrame, left: int = 3, right: int = 3,
                            lookback: int = 30) -> Optional[dict]:
    """
    Looks for the most recent candle that wicks beyond a prior swing
    high/low but CLOSES back inside it -> classic stop-hunt / liquidity
    grab that often precedes a reversal.
    """
    d = find_swings(df, left, right).tail(lookback)
    sh = d[d["swing_high"]]
    sl = d[d["swing_low"]]

    for i in range(len(d) - 1, -1, -1):
        row = d.iloc[i]
        if len(sh) and row["High"] > sh["High"].max() and row["Close"] < sh["High"].max():
            return {"type": "sell_side_sweep_of_highs", "level": float(sh["High"].max()),
                    "time": d.index[i]}
        if len(sl) and row["Low"] < sl["Low"].min() and row["Close"] > sl["Low"].min():
            return {"type": "buy_side_sweep_of_lows", "level": float(sl["Low"].min()),
                    "time": d.index[i]}
    return None


# ---------------------------------------------------------------------------
# 5. PREMIUM / DISCOUNT (fib 50% of latest swing range)
# ---------------------------------------------------------------------------

def premium_discount_zone(df: pd.DataFrame, left: int = 3, right: int = 3) -> dict:
    d = find_swings(df, left, right)
    sh = d[d["swing_high"]].tail(1)
    sl = d[d["swing_low"]].tail(1)
    if not len(sh) or not len(sl):
        return {"zone": "unknown"}
    high = sh["High"].iloc[-1]
    low = sl["Low"].iloc[-1]
    mid = (high + low) / 2
    last_close = df["Close"].iloc[-1]
    zone = "premium" if last_close > mid else "discount"
    return {"zone": zone, "range_high": float(high), "range_low": float(low),
            "equilibrium": float(mid)}


# ---------------------------------------------------------------------------
# 6. FULL TOP-DOWN PIPELINE
# ---------------------------------------------------------------------------

@dataclass
class SMCSignal:
    direction: Optional[str] = None          # "BUY" / "SELL" / None
    confidence: int = 0                       # 0-5 confluence count
    entry_zone: Optional[tuple] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    notes: List[str] = field(default_factory=list)


def analyze(df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame,
            df_5m: pd.DataFrame, df_1m: pd.DataFrame) -> SMCSignal:

    sig = SMCSignal()

    # --- Step 1: 4H bias -------------------------------------------------
    ms_4h = market_structure_bias(df_4h)
    bias = ms_4h["bias"]
    sig.notes.append(f"4H structure bias: {bias} (BOS={ms_4h['bos']}, CHoCH={ms_4h['choch']})")
    if bias == "neutral":
        sig.notes.append("No clear 4H bias -> standing aside.")
        return sig

    # --- Step 2: 1H confirmation + HTF POI --------------------------------
    ms_1h = market_structure_bias(df_1h)
    pd_1h = premium_discount_zone(df_1h)
    sig.notes.append(f"1H structure: {ms_1h['bias']}, price is in {pd_1h.get('zone')} zone")

    aligned_1h = (bias == "bullish" and ms_1h["bias"] in ("bullish", "neutral")) or \
                 (bias == "bearish" and ms_1h["bias"] in ("bearish", "neutral"))
    if not aligned_1h:
        sig.notes.append("1H not aligned with 4H bias -> wait / no trade.")
        return sig
    sig.confidence += 1

    want_ob_type = "bullish" if bias == "bullish" else "bearish"
    obs_1h = find_order_blocks(df_1h)
    poi_1h = None
    if len(obs_1h):
        candidates = obs_1h[(obs_1h["type"] == want_ob_type) & (~obs_1h["mitigated"])]
        if len(candidates):
            poi_1h = candidates.iloc[0]
            sig.confidence += 1
            sig.notes.append(f"1H unmitigated {want_ob_type} order block found: "
                              f"{poi_1h['low']:.5f} - {poi_1h['high']:.5f}")
    if poi_1h is None:
        sig.notes.append("No fresh 1H order block in bias direction -> no high-quality POI yet.")
        return sig

    # --- Step 3: 15M liquidity sweep + CHoCH into the POI -----------------
    sweep_15m = detect_liquidity_sweep(df_15m)
    ms_15m = market_structure_bias(df_15m)
    if sweep_15m:
        sig.notes.append(f"15M liquidity sweep detected: {sweep_15m['type']} at {sweep_15m['level']:.5f}")
        sig.confidence += 1
    want_choch = "bullish" if bias == "bullish" else "bearish"
    if ms_15m["choch"] == want_choch:
        sig.notes.append(f"15M CHoCH confirms potential reversal in {want_choch} direction")
        sig.confidence += 1
    else:
        sig.notes.append("15M CHoCH not yet confirmed -> POI valid but wait for reaction.")

    obs_15m = find_order_blocks(df_15m)
    poi_15m = None
    if len(obs_15m):
        cands = obs_15m[(obs_15m["type"] == want_ob_type) & (~obs_15m["mitigated"])]
        if len(cands):
            poi_15m = cands.iloc[0]

    refined_poi = poi_15m if poi_15m is not None else poi_1h

    # --- Step 4: 5M BOS confirming reversal at refined POI -----------------
    ms_5m = market_structure_bias(df_5m)
    fvgs_5m = find_fvgs(df_5m)
    bos_ok = ms_5m["bos"] == want_choch
    if bos_ok:
        sig.notes.append(f"5M BOS confirms {want_choch} momentum")
        sig.confidence += 1

    tightest_poi_high = refined_poi["high"]
    tightest_poi_low = refined_poi["low"]

    # --- Step 5: 1M entry trigger ------------------------------------------
    last_price = df_1m["Close"].iloc[-1]
    in_zone = tightest_poi_low <= last_price <= tightest_poi_high
    ms_1m = market_structure_bias(df_1m)
    trigger = ms_1m["choch"] == want_choch or ms_1m["bos"] == want_choch

    if in_zone and trigger:
        sig.direction = "BUY" if bias == "bullish" else "SELL"
        sig.entry_zone = (float(tightest_poi_low), float(tightest_poi_high))
        if bias == "bullish":
            sig.stop_loss = float(tightest_poi_low) * 0.999
            risk = last_price - sig.stop_loss
            sig.take_profit = float(last_price + risk * 2)  # 1:2 default RR
        else:
            sig.stop_loss = float(tightest_poi_high) * 1.001
            risk = sig.stop_loss - last_price
            sig.take_profit = float(last_price - risk * 2)
        sig.confidence += 1
        sig.notes.append(f"1M entry trigger confirmed inside POI at {last_price:.5f} -> {sig.direction} signal")
    else:
        sig.notes.append(
            f"Price not yet reacting inside POI ({tightest_poi_low:.5f}-{tightest_poi_high:.5f}) "
            f"with a 1M CHoCH/BOS trigger -> WAIT, no entry yet."
        )

    return sig


def print_report(sig: SMCSignal):
    print("=" * 60)
    print(" SMC / ICT MULTI-TIMEFRAME ANALYSIS REPORT")
    print("=" * 60)
    for n in sig.notes:
        print(" -", n)
    print("-" * 60)
    print(f" Confluence score : {sig.confidence} / 6")
    if sig.direction:
        print(f" SIGNAL           : {sig.direction}")
        print(f" Entry zone       : {sig.entry_zone[0]:.5f} - {sig.entry_zone[1]:.5f}")
        print(f" Stop loss        : {sig.stop_loss:.5f}")
        print(f" Take profit (1:2): {sig.take_profit:.5f}")
    else:
        print(" SIGNAL           : NO TRADE (criteria not met)")
    print("=" * 60)
    print(" NOTE: Research/decision-support tool only. Not financial advice.")
    print(" Always backtest + paper trade before risking real capital.")
