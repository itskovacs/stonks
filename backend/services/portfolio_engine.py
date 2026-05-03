"""
Portfolio History Engine
========================
Pure computation — zero database dependency.
All data is passed in by the caller (profile router).

Public API
----------
compute_envelope_overview(txs, envelopes, period) → chart + stats payload

Transaction types
-----------------
BUY     — increases share count; increases cost basis
SELL    — decreases share count; reduces cost basis proportionally (WAC)
DEPOSIT / WITHDRAW / DIVIDEND — cash events; ignored by the position engine

Chart equity value
------------------
value(day) = Σ  shares_i × close_i(day)

Cash is NOT included in chart values — it is tracked separately
via Envelope.cash_available in the database layer.

Pure price returns (stats)
--------------------------
best_day, worst_day, volatility are derived from price-only returns:
    pure_change(day) = Σ  pre_trade_shares × (close_today − close_yesterday)
This isolates market movements from cash flows that would otherwise
inflate or deflate the daily return figure.

Price fetching
--------------
_fetch_closes uses a ThreadPoolExecutor to fetch all tickers concurrently
rather than sequentially. Each thread gets its own curl_cffi Session
via the thread-local pool in fetcher.py.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from services.fetcher import YFinanceFetcher

log = logging.getLogger(__name__)

VALID_PERIODS = {"1w", "1mo", "3mo", "6mo", "ytd", "1y", "3y"}
PERIOD_DAYS   = {"1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "3y": 1095}

# Max concurrent yfinance fetches for portfolio overview.
# Matches the default asyncio threadpool size to avoid over-scheduling.
_MAX_FETCH_WORKERS = 20


# ─────────────────────────────────────────────────────────────────────────────
# Public: WAC position computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_ticker_wac(txs: list[dict]) -> list[dict]:
    """
    Computes WAC position per envelope for a single ticker.

    Caller passes only the transactions already filtered to that ticker —
    no extra DB query or price fetch is needed.

    Returns [{ envelope_name, envelope_color, shares, avg_cost, cost_basis }],
    one entry per envelope that still holds a non-zero position,
    ordered by envelope_name.
    """
    holdings: dict[str, dict] = {}

    for tx in sorted(txs, key=lambda x: x["date"]):
        if tx["type"] not in ("BUY", "SELL"):
            continue
        env = tx.get("envelope_name", "")
        if not env:
            continue

        shares = float(tx.get("shares") or 0.0)
        total  = float(tx.get("total")  or 0.0)

        if env not in holdings:
            holdings[env] = {
                "shares":    0.0,
                "cost_basis": 0.0,
                "color":     tx.get("envelope_color", "#e2e8f0"),
            }

        if tx["type"] == "BUY":
            holdings[env]["shares"]     += shares
            holdings[env]["cost_basis"] += total
        else:
            h = holdings[env]
            if h["shares"] > 1e-9:
                sell_ratio       = min(shares / h["shares"], 1.0)
                h["cost_basis"] -= h["cost_basis"] * sell_ratio
                h["shares"]      = max(h["shares"] - shares, 0.0)

    return [
        {
            "envelope_name":  env,
            "envelope_color": h["color"],
            "shares":         round(h["shares"], 8),
            "avg_cost":       round(h["cost_basis"] / h["shares"], 4),
            "cost_basis":     round(h["cost_basis"], 2),
        }
        for env, h in sorted(holdings.items())
        if h["shares"] > 1e-8
    ]


def compute_all_positions(txs: list[dict], envelopes: list[dict]) -> list[dict]:
    """
    Computes WAC positions across all envelopes in a single pass.
    Used to avoid N×full-iteration when there are multiple envelopes.

    Returns [{ ticker, envelope_name, shares, avg_cost, cost_basis }]
    """
    # Build per-envelope holdings in a single sorted iteration
    holdings: dict[str, dict[str, dict[str, float]]] = {
        env["name"]: {} for env in envelopes
    }

    for tx in sorted(txs, key=lambda x: x["date"]):
        if tx["type"] not in ("BUY", "SELL"):
            continue
        env_name = tx.get("envelope_name")
        if env_name not in holdings:
            continue
        ticker = (tx.get("ticker") or "").upper()
        if not ticker:
            continue

        shares = float(tx.get("shares") or 0.0)
        total  = float(tx.get("total")  or 0.0)
        h_env  = holdings[env_name]

        if ticker not in h_env:
            h_env[ticker] = {"shares": 0.0, "cost_basis": 0.0}

        if tx["type"] == "BUY":
            h_env[ticker]["cost_basis"] += total
            h_env[ticker]["shares"]     += shares
        else:
            h = h_env[ticker]
            if h["shares"] > 1e-9:
                sell_ratio      = min(shares / h["shares"], 1.0)
                h["cost_basis"] -= h["cost_basis"] * sell_ratio
                h["shares"]     = max(h["shares"] - shares, 0.0)

    result = []
    for env_name, h_env in holdings.items():
        for ticker, h in h_env.items():
            if h["shares"] > 1e-8:
                result.append({
                    "ticker":       ticker,
                    "envelope_name":env_name,
                    "shares":       round(h["shares"], 8),
                    "avg_cost":     round(h["cost_basis"] / h["shares"], 4),
                    "cost_basis":   round(h["cost_basis"], 2),
                })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _apply_tx(holdings: dict[str, float], tx: dict[str, Any]) -> None:
    """Applies a single BUY or SELL to an equity-holdings dict in-place."""
    if tx["type"] not in ("BUY", "SELL"):
        return
    ticker = (tx.get("ticker") or "").upper()
    shares = float(tx.get("shares") or 0.0)
    if tx["type"] == "BUY":
        holdings[ticker] = holdings.get(ticker, 0.0) + shares
    else:
        remaining = holdings.get(ticker, 0.0) - shares
        if remaining > 1e-9:
            holdings[ticker] = remaining
        else:
            holdings.pop(ticker, None)


def _envelope_value(
    holdings: dict[str, float],
    closes: pd.DataFrame,
    day: pd.Timestamp,
) -> float:
    """
    Mark-to-market equity value for one envelope on a given day.
    Uses the last known close on or before `day` to bridge weekends and gaps.
    """
    total = 0.0
    for ticker, shares in holdings.items():
        if shares < 1e-9 or closes.empty or ticker not in closes.columns:
            continue
        col_slice = closes[ticker].loc[:day].dropna()
        if col_slice.empty:
            continue
        total += shares * float(col_slice.iloc[-1])
    return total


def _pure_price_change(
    pre_trade_holdings: dict[str, dict[str, float]],
    envelope_names: list[str],
    closes: pd.DataFrame,
    day: pd.Timestamp,
    prev_day: pd.Timestamp,
) -> tuple[float, float]:
    """Returns (abs_price_change, prev_total) for pure-return daily stats."""
    prev_total  = sum(_envelope_value(pre_trade_holdings[n], closes, prev_day) for n in envelope_names)
    today_total = sum(_envelope_value(pre_trade_holdings[n], closes, day)      for n in envelope_names)
    return today_total - prev_total, prev_total


# ─────────────────────────────────────────────────────────────────────────────
# Parallel price fetching
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_one_ticker(ticker: str, start: str, end: str) -> tuple[str, pd.Series | None]:
    """Fetches closing prices for a single ticker. Designed for threadpool use."""
    try:
        hist = YFinanceFetcher(ticker).history(start=start, end=end)
        if hist.empty or "Close" not in hist.columns:
            log.warning("No Close data for %s", ticker)
            return ticker, None
        s = hist["Close"].copy()
        s.index = pd.to_datetime(s.index).normalize()
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
        s.name = ticker
        return ticker, s
    except Exception as exc:
        log.warning("Price fetch failed for %s: %s", ticker, exc)
        return ticker, None


def _fetch_closes(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """
    Fetches daily closing prices for all tickers concurrently.
    Returns a tz-naive, date-indexed DataFrame (columns = TICKER_UPPERCASE),
    forward-filled so that weekends and gaps are bridged.
    """
    if not tickers:
        return pd.DataFrame()

    frames: dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=min(_MAX_FETCH_WORKERS, len(tickers))) as executor:
        futures = {
            executor.submit(_fetch_one_ticker, t, start, end): t
            for t in tickers
        }
        for future in as_completed(futures):
            ticker, series = future.result()
            if series is not None:
                frames[ticker] = series

    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).bfill().ffill()


# ─────────────────────────────────────────────────────────────────────────────
# Stats
# ─────────────────────────────────────────────────────────────────────────────


def _compute_stats(
    daily_pure_changes: list[dict[str, Any]],
    txs: list[dict[str, Any]],
    envelopes: list[dict[str, Any]],
    chart_start_str: str,
    period_days: int,
) -> dict:
    in_period = [tx for tx in txs if tx["date"][:10] >= chart_start_str]

    net_deposits = round(
        sum(float(tx.get("total") or 0) for tx in in_period if tx["type"] == "DEPOSIT")
        - sum(float(tx.get("total") or 0) for tx in in_period if tx["type"] == "WITHDRAW"),
        2,
    )
    dividend_income = round(
        sum(float(tx.get("total") or 0) for tx in in_period if tx["type"] == "DIVIDEND"), 2
    )
    trades_count = sum(1 for tx in in_period if tx["type"] in ("BUY", "SELL"))

    # Single-pass position computation across all envelopes (not N separate calls)
    all_positions = compute_all_positions(txs, envelopes)
    tickers_held = len({p["ticker"] for p in all_positions})

    stats: dict[str, Any] = {
        "period_days":               period_days,
        "net_deposits":              net_deposits,
        "dividend_income":           dividend_income,
        "tickers_held":              tickers_held,
        "volatility_annualized_pct": None,
        "best_day":                  None,
        "worst_day":                 None,
        "trades_count":              trades_count,
    }

    if not daily_pure_changes:
        return stats

    daily_rets = [c["change_pct"] / 100.0 for c in daily_pure_changes]
    if len(daily_rets) > 1:
        vol = float(np.std(daily_rets, ddof=1)) * math.sqrt(252) * 100
        stats["volatility_annualized_pct"] = round(vol, 2)

    best  = max(daily_pure_changes, key=lambda c: c["change"])
    worst = min(daily_pure_changes, key=lambda c: c["change"])
    stats["best_day"]  = {"date": best["date"],  "change": best["change"],  "change_pct": best["change_pct"]}
    stats["worst_day"] = {"date": worst["date"], "change": worst["change"], "change_pct": worst["change_pct"]}

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Public: envelope overview
# ─────────────────────────────────────────────────────────────────────────────


def compute_envelope_overview(
    txs: list[dict],
    envelopes: list[dict],
    period: str = "3mo",
) -> dict:
    """
    Reconstructs daily mark-to-market equity values for the requested period.

    DEPOSIT / WITHDRAW / DIVIDEND do NOT contribute to chart equity values.
    Only BUY / SELL transactions mutate the holdings state tracked here.

    Non-trading-day transactions are applied on the next market session.
    """
    if period not in VALID_PERIODS:
        period = "3mo"

    today = date.today()
    end_str = str(today + timedelta(days=1))

    if period == "ytd":
        chart_start = date(today.year, 1, 1)
        period_days = (today - chart_start).days
    else:
        period_days = PERIOD_DAYS[period]
        chart_start = today - timedelta(days=period_days)
    chart_start_str = str(chart_start)

    empty_stats = {
        "period_days":               period_days,
        "net_deposits":              0.0,
        "dividend_income":           0.0,
        "tickers_held":              0,
        "volatility_annualized_pct": None,
        "best_day":                  None,
        "worst_day":                 None,
        "trades_count":              0,
    }

    if not txs:
        return {"period": period, "dates": [], "series": [], "events": [], "stats": empty_stats}

    sorted_txs    = sorted(txs, key=lambda x: x["date"])
    envelope_names = [env["name"] for env in envelopes]

    equity_tickers = list({
        tx["ticker"].upper()
        for tx in sorted_txs
        if tx.get("ticker") and tx["ticker"].strip() and tx["type"] in ("BUY", "SELL")
    })

    closes = _fetch_closes(equity_tickers, start=chart_start_str, end=end_str)

    # Replay all transactions BEFORE the window to build opening state
    state: dict[str, dict[str, float]] = {name: {} for name in envelope_names}
    for tx in sorted_txs:
        if tx["date"][:10] >= chart_start_str:
            break
        env_name = tx.get("envelope_name")
        if env_name in state:
            _apply_tx(state[env_name], tx)

    # In-window transactions: cash events before equity trades on same day
    in_window = sorted(
        [tx for tx in sorted_txs if tx["date"][:10] >= chart_start_str],
        key=lambda x: (x["date"][:10], 0 if x["type"] in ("DEPOSIT", "WITHDRAW", "DIVIDEND") else 1),
    )
    pending_idx = 0
    n_pending   = len(in_window)

    trading_days = closes.index if not closes.empty else pd.bdate_range(
        start=chart_start_str, end=str(today)
    )

    dates: list[str]                     = []
    series_data: dict[str, list[float]]  = {name: [] for name in envelope_names}
    events: list[dict[str, Any]]         = []
    daily_pure_changes: list[dict[str, Any]] = []
    prev_day: pd.Timestamp | None        = None

    for day in trading_days:
        day_str = str(day.date())

        pre_trade = {name: dict(state[name]) for name in envelope_names}

        while pending_idx < n_pending and in_window[pending_idx]["date"][:10] <= day_str:
            tx = in_window[pending_idx]
            env_name = tx.get("envelope_name")
            if env_name in state:
                _apply_tx(state[env_name], tx)
            events.append({
                "date":           day_str,
                "type":           tx["type"],
                "ticker":         (tx.get("ticker") or "").upper() or None,
                "envelope_name":  env_name,
                "amount":         round(float(tx.get("total") or 0.0), 2),
                "shares":         float(tx.get("shares") or 0.0),
            })
            pending_idx += 1

        dates.append(day_str)
        for name in envelope_names:
            series_data[name].append(round(_envelope_value(state[name], closes, day), 2))

        if prev_day is not None:
            pure_abs, prev_val = _pure_price_change(pre_trade, envelope_names, closes, day, prev_day)
            pure_pct = (pure_abs / prev_val * 100.0) if prev_val > 1e-9 else 0.0
            daily_pure_changes.append({
                "date":       day_str,
                "change":     round(pure_abs, 2),
                "change_pct": round(pure_pct, 2),
            })

        prev_day = day

    series = [
        {"name": env["name"], "color": env["color"], "values": series_data[env["name"]]}
        for env in envelopes
    ]
    stats = _compute_stats(daily_pure_changes, txs, envelopes, chart_start_str, period_days)

    return {"period": period, "dates": dates, "series": series, "events": events, "stats": stats}
