"""
Technical Analysis Engine
=========================
Evaluates 6 independent technical indicators and aggregates their votes
into a percentage-based BUY / HOLD / SELL breakdown.

Indicator set
-------------
RSI (14)            Momentum oscillator: oversold / overbought zones
MACD (12/26/9)      Trend direction + histogram acceleration filter
Bollinger %B        Price position within volatility bands
SMA 50/200          Macro trend regime (golden / death cross territory)
Stochastic %K/%D    Momentum crossover in oversold / overbought zones
Volume Trend        Volume surge confirming price direction (accumulation/distribution)

Removed from prior version
--------------------------
EMA 20       Nearly redundant with the SMA 50/200 regime signal.  Adds noise
             without independent information at this indicator count.
Williams %R  Highly correlated with RSI and Stochastic over the same lookback.
             Three overlapping momentum oscillators inflate the BUY/SELL%
             without increasing signal quality.
OBV trend    OBV vs its own 20-day MA generates excessive whipsaw. OBV's
             cumulative, scale-free nature also makes it non-comparable across
             tickers.  Replaced by Volume Trend, which is simpler and more
             informative.

Design invariants
-----------------
• Every indicator function returns (Signal | None, RawValue).
  Signal = None means insufficient data — the indicator is excluded from
  the aggregate entirely rather than defaulting to HOLD, so the vote
  percentages remain unbiased when fewer than 6 indicators have enough data.
• All return paths are explicit 2-tuples — no function ever returns a bare None.
• SMA 50/200 measures the persistent regime (SMA50 > SMA200 = bullish),
  not just the crossover day.  This is more useful educationally.
• Volume Trend: 5d avg volume vs 20d avg volume.  A surge (>1.5×) in volume
  confirms the prevailing price direction.  No surge → HOLD.
"""

from collections.abc import Callable

import numpy as np
import pandas as pd
import ta

# Signal type alias — kept as a plain str for JSON serialisability
Signal = str  # "BUY" | "HOLD" | "SELL"
RawValue = float | dict | None
IndicatorResult = tuple[Signal | None, RawValue]
IndicatorFn = Callable[[pd.DataFrame], IndicatorResult]


# ── Individual indicators ─────────────────────────────────────────────────────


def _rsi_signal(df: pd.DataFrame) -> IndicatorResult:
    """RSI (14): < 30 oversold → BUY, > 70 overbought → SELL."""
    if "Close" not in df.columns or len(df) < 15:
        return None, None
    rsi_val = ta.momentum.RSIIndicator(df["Close"], window=14).rsi().iloc[-1]
    if np.isnan(rsi_val):
        return None, None
    rsi = round(float(rsi_val), 2)
    if rsi < 30:
        return "BUY", rsi
    if rsi > 70:
        return "SELL", rsi
    return "HOLD", rsi


def _macd_signal(df: pd.DataFrame) -> IndicatorResult:
    """
    MACD (12/26/9):
    BUY  — MACD line above signal AND histogram expanding positively.
    SELL — MACD line below signal AND histogram expanding negatively.
    Both conditions required: position alone is insufficient; the histogram
    acceleration filter removes weak or stalling crossovers.
    """
    if "Close" not in df.columns or len(df) < 35:
        return None, None
    ind = ta.trend.MACD(df["Close"], window_slow=26, window_fast=12, window_sign=9)
    macd = ind.macd().iloc[-1]
    sig = ind.macd_signal().iloc[-1]
    hist = ind.macd_diff()
    if np.isnan(macd) or np.isnan(sig) or len(hist) < 2:
        return None, None
    hist_now = float(hist.iloc[-1])
    hist_prev = float(hist.iloc[-2])
    if macd > sig and hist_now > hist_prev:
        return "BUY", None
    if macd < sig and hist_now < hist_prev:
        return "SELL", None
    return "HOLD", None


def _bollinger_signal(df: pd.DataFrame) -> IndicatorResult:
    """
    Bollinger %B (20-day, 2σ):
    %B < 0 → price is below the lower band (oversold) → BUY
    %B > 1 → price is above the upper band (overbought) → SELL
    Using the band breach (not just proximity) makes this a higher-conviction signal.
    """
    if "Close" not in df.columns or len(df) < 20:
        return None, None
    pband = (
        ta.volatility.BollingerBands(df["Close"], window=20, window_dev=2)
        .bollinger_pband()
        .iloc[-1]
    )
    if np.isnan(pband):
        return None, None
    pb = round(float(pband), 3)
    if pband < 0.0:
        return "BUY", pb
    if pband > 1.0:
        return "SELL", pb
    return "HOLD", pb


def _sma_regime_signal(df: pd.DataFrame) -> IndicatorResult:
    """
    SMA 50/200 macro trend regime.
    SMA50 > SMA200 → bullish regime (golden cross territory) → BUY
    SMA50 < SMA200 → bearish regime (death cross territory) → SELL
    Requires at least 200 bars; returns (None, None) for shorter histories
    (e.g. recently listed securities).

    Raw value: {"sma_50": float, "sma_200": float} — used by the stock bar header.
    """
    if "Close" not in df.columns or len(df) < 200:
        return None, None
    close = df["Close"].ffill()
    sma50_val = close.rolling(50, min_periods=50).mean().iloc[-1]
    sma200_val = close.rolling(200, min_periods=200).mean().iloc[-1]
    if pd.isna(sma50_val) or pd.isna(sma200_val):
        return None, None
    val50 = round(float(sma50_val), 4)
    val200 = round(float(sma200_val), 4)
    raw: dict = {"sma_50": val50, "sma_200": val200}
    if val50 > val200:
        return "BUY", raw
    if val50 < val200:
        return "SELL", raw
    return "HOLD", raw


def _stochastic_signal(df: pd.DataFrame) -> IndicatorResult:
    """
    Stochastic %K/%D (14, 3):
    BUY  — %K < 20 (oversold zone) AND %K crossing above %D from below.
    SELL — %K > 80 (overbought zone) AND %K crossing below %D from above.
    Zone condition + crossover condition: reduces false signals from either alone.
    """
    if not {"High", "Low", "Close"}.issubset(df.columns) or len(df) < 17:
        return None, None
    stoch = ta.momentum.StochasticOscillator(
        df["High"], df["Low"], df["Close"], window=14, smooth_window=3
    )
    k_series = stoch.stoch()
    d_series = stoch.stoch_signal()
    if len(k_series) < 2 or len(d_series) < 2:
        return None, None
    k_now, k_prev = float(k_series.iloc[-1]), float(k_series.iloc[-2])
    d_now, d_prev = float(d_series.iloc[-1]), float(d_series.iloc[-2])
    if any(np.isnan(v) for v in (k_now, k_prev, d_now, d_prev)):
        return None, None
    if k_now < 20 and k_now > d_now and k_prev <= d_prev:
        return "BUY", round(k_now, 2)
    if k_now > 80 and k_now < d_now and k_prev >= d_prev:
        return "SELL", round(k_now, 2)
    return "HOLD", round(k_now, 2)


def _volume_trend_signal(df: pd.DataFrame) -> IndicatorResult:
    """
    Volume Trend (5-day average vs 20-day average volume ratio):
    A volume surge (5d avg > 1.5× the 20d avg) adds conviction to the price direction:
      Rising price + surge → accumulation → BUY
      Falling price + surge → distribution → SELL
    No surge (ratio ≤ 1.5) → HOLD — volume is not confirming a directional move.

    Price direction is measured as the 5-day price change, which smooths out
    intraday noise while remaining responsive.

    Raw value: the volume ratio (float).
    """
    if not {"Volume", "Close"}.issubset(df.columns) or len(df) < 20:
        return None, None
    vol_5d = float(df["Volume"].iloc[-5:].mean())
    vol_20d = float(df["Volume"].iloc[-20:].mean())
    if vol_20d < 1:
        return None, None
    ratio = round(vol_5d / vol_20d, 2)
    price_rising = float(df["Close"].iloc[-1]) > float(df["Close"].iloc[-5])
    if ratio > 1.5 and price_rising:
        return "BUY", ratio
    if ratio > 1.5 and not price_rising:
        return "SELL", ratio
    return "HOLD", ratio


# ── Indicator registry ────────────────────────────────────────────────────────

INDICATORS: dict[str, IndicatorFn] = {
    "RSI (14)": _rsi_signal,
    "MACD (12/26/9)": _macd_signal,
    "Bollinger %B": _bollinger_signal,
    "SMA 50/200": _sma_regime_signal,
    "Stochastic %K/%D": _stochastic_signal,
    "Volume Trend": _volume_trend_signal,
}


# ── Public entry point ────────────────────────────────────────────────────────


def compute_signals(df: pd.DataFrame) -> dict:
    """
    Evaluates all registered indicators and aggregates their votes.

    Parameters
    ----------
    df : pd.DataFrame — OHLCV history (yfinance format)

    Returns
    -------
    {
        "signals":    dict[str, str],           # per-indicator BUY | HOLD | SELL
        "raw_values": dict[str, float | dict],  # raw values where available
        "buy_pct":    int,
        "hold_pct":   int,
        "sell_pct":   int,
        "summary":    str
    }
    """
    if df.empty:
        return {
            "signals": {},
            "raw_values": {},
            "buy_pct": 0,
            "hold_pct": 100,
            "sell_pct": 0,
            "summary": "Insufficient data for technical analysis.",
        }

    signals: dict[str, str] = {}
    raw_values: dict[str, float | dict] = {}
    counts: dict[str, int] = {"BUY": 0, "HOLD": 0, "SELL": 0}

    for name, fn in INDICATORS.items():
        try:
            sig, raw = fn(df)
        except Exception:
            continue  # indicator failure is non-fatal; it is simply excluded
        if sig is not None:
            signals[name] = sig
            counts[sig] += 1
            if raw is not None:
                raw_values[name] = raw

    total = sum(counts.values()) or 1
    buy_pct = round(counts["BUY"] / total * 100)
    hold_pct = round(counts["HOLD"] / total * 100)
    sell_pct = 100 - buy_pct - hold_pct

    # Tie → HOLD (cautious default)
    dominant = max(counts, key=lambda k: counts[k])
    if sum(1 for v in counts.values() if v == counts[dominant]) > 1:
        dominant = "HOLD"

    summary = (
        f"{total} indicator(s) active — "
        f"{buy_pct}% BUY · {hold_pct}% HOLD · {sell_pct}% SELL. "
        f"Majority: {dominant}. "
        "(Informational only — not investment advice.)"
    )

    return {
        "signals": signals,
        "raw_values": raw_values,
        "buy_pct": buy_pct,
        "hold_pct": hold_pct,
        "sell_pct": sell_pct,
        "summary": summary,
    }
