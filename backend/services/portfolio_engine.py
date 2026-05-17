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
DEPOSIT / WITHDRAW / DIVIDEND — cash events; update the running cash balance

Chart value
-----------
value(day) = cash_balance + Σ  shares_i × close_i(day)

Cash IS included in chart values so that unallocated proceeds after a SELL
do not create gaps. The running cash balance is reconstructed from all
five transaction types by _apply_tx.

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


def compute_sell_pnl(txs: list[dict]) -> dict[int, dict]:
    """
    Returns {tx_id: {"realized_pnl": float, "realized_pnl_pct": float}}
    for every SELL, using WAC at the moment of sale.
    O(n) — call once per dashboard load alongside compute_all_positions.
    """
    holdings: dict[str, dict[str, dict[str, float]]] = {}
    result: dict[int, dict] = {}

    for tx in sorted(txs, key=lambda x: x["date"]):
        if tx["type"] not in ("BUY", "SELL"):
            continue
        env_name = tx.get("envelope_name", "")
        ticker   = (tx.get("ticker") or "").upper()
        if not env_name or not ticker:
            continue

        shares = float(tx.get("shares") or 0.0)
        total  = float(tx.get("total")  or 0.0)

        if env_name not in holdings:
            holdings[env_name] = {}
        if ticker not in holdings[env_name]:
            holdings[env_name][ticker] = {"shares": 0.0, "cost_basis": 0.0}

        h = holdings[env_name][ticker]
        if tx["type"] == "BUY":
            h["shares"]     += shares
            h["cost_basis"] += total
        else:
            if h["shares"] > 1e-9 and tx.get("id") is not None:
                wac              = h["cost_basis"] / h["shares"]
                effective_shares = min(shares, h["shares"])
                cost_of_sale     = round(wac * effective_shares, 2)
                realized_pnl     = round(total - cost_of_sale, 2)
                realized_pnl_pct = round(realized_pnl / cost_of_sale * 100, 2) if cost_of_sale else 0.0
                result[tx["id"]] = {"realized_pnl": realized_pnl, "realized_pnl_pct": realized_pnl_pct}
            sell_ratio       = min(shares / h["shares"], 1.0) if h["shares"] > 1e-9 else 0.0
            h["cost_basis"] -= h["cost_basis"] * sell_ratio
            h["shares"]      = max(h["shares"] - shares, 0.0)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _apply_tx(holdings: dict[str, float], tx: dict[str, Any]) -> None:
    """Applies a single transaction to an envelope holdings dict in-place."""
    total  = float(tx.get("total") or 0.0)
    tx_type = tx["type"]
    if tx_type in ("DEPOSIT", "DIVIDEND"):
        holdings["__cash__"] = holdings.get("__cash__", 0.0) + total
    elif tx_type == "WITHDRAW":
        holdings["__cash__"] = holdings.get("__cash__", 0.0) - total
    elif tx_type == "BUY":
        ticker = (tx.get("ticker") or "").upper()
        shares = float(tx.get("shares") or 0.0)
        holdings[ticker]     = holdings.get(ticker, 0.0) + shares
        holdings["__cash__"] = holdings.get("__cash__", 0.0) - total
    elif tx_type == "SELL":
        ticker = (tx.get("ticker") or "").upper()
        shares = float(tx.get("shares") or 0.0)
        remaining = holdings.get(ticker, 0.0) - shares
        if remaining > 1e-9:
            holdings[ticker] = remaining
        else:
            holdings.pop(ticker, None)
        holdings["__cash__"] = holdings.get("__cash__", 0.0) + total



def _envelope_value(
    holdings: dict[str, float],
    closes: pd.DataFrame,
    day: pd.Timestamp,
) -> float:
    """
    Mark-to-market equity value for one envelope on a given day.
    Uses the last known close on or before `day` to bridge weekends and gaps.
    """
    total = holdings.get("__cash__", 0.0)
    for ticker, shares in holdings.items():
        if ticker == "__cash__" or shares < 1e-9 or closes.empty or ticker not in closes.columns:
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
    """Returns (abs_price_change, prev_equity) for pure-return daily stats."""
    prev_total  = sum(_envelope_value(pre_trade_holdings[n], closes, prev_day) for n in envelope_names)
    today_total = sum(_envelope_value(pre_trade_holdings[n], closes, day)      for n in envelope_names)
    cash_total  = sum(pre_trade_holdings[n].get("__cash__", 0.0) for n in envelope_names)
    return today_total - prev_total, prev_total - cash_total


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
    benchmark_ticker: str = "XWD.TO",
    envelope_filter: list[str] | None = None,
) -> dict:
    """
    Reconstructs daily mark-to-market equity values for the requested period.

    All five transaction types mutate the per-envelope state: BUY/SELL update
    share counts; DEPOSIT/DIVIDEND/WITHDRAW update the cash balance. Chart
    values include both equity and cash so that post-SELL periods show
    unallocated proceeds rather than a gap.

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
        return {"period": period, "dates": [], "series": [], "events": [], "stats": empty_stats,
                "benchmark_pct": [], "portfolio_pct": [], "benchmark_ticker": benchmark_ticker}

    sorted_txs    = sorted(txs, key=lambda x: x["date"])
    envelope_names = [
        env["name"] for env in envelopes
        if envelope_filter is None or env["name"] in envelope_filter
    ]
    envelopes = [env for env in envelopes if env["name"] in envelope_names]

    # Pre-filter capital transactions to the selected envelopes so that the
    # benchmark mirror and cumulative_capital are scoped identically to the
    # portfolio series (avoids numerator/denominator mismatch when a subset
    # of envelopes is selected).
    _INFLOW  = {"DEPOSIT", "DIVIDEND"}
    _OUTFLOW = {"WITHDRAW"}
    _capital_types = _INFLOW | _OUTFLOW
    scoped_capital_txs = [
        tx for tx in sorted_txs
        if tx["type"] in _capital_types
        and (envelope_filter is None or tx.get("envelope_name") in envelope_filter)
    ]

    equity_tickers = list({
        tx["ticker"].upper()
        for tx in sorted_txs
        if tx.get("ticker") and tx["ticker"].strip() and tx["type"] in ("BUY", "SELL")
    })

    # Fetch equity tickers and the benchmark in one parallel pool.
    # The benchmark is fetched from the selected envelopes' first-ever capital
    # transaction so the pre-window mirror replay has prices for all deposits.
    _BENCHMARK = benchmark_ticker
    first_capital_str = next(
        (tx["date"][:10] for tx in scoped_capital_txs),
        chart_start_str,
    )
    all_fetch_tickers = equity_tickers + [_BENCHMARK]
    equity_frames: dict[str, pd.Series] = {}
    wpea_col: pd.Series | None = None

    with ThreadPoolExecutor(max_workers=min(_MAX_FETCH_WORKERS, len(all_fetch_tickers))) as executor:
        futures = {
            executor.submit(
                _fetch_one_ticker, t,
                first_capital_str if t == _BENCHMARK else chart_start_str,
                end_str,
            ): t
            for t in all_fetch_tickers
        }
        for future in as_completed(futures):
            ticker, series = future.result()
            if series is not None:
                if ticker == _BENCHMARK:
                    wpea_col = series
                else:
                    equity_frames[ticker] = series

    # Retry equity tickers that failed in the parallel fetch.  Transient errors
    # (connection reset, rate-limit inside the threadpool) are not cached by
    # YFinanceFetcher, so a sequential second attempt usually recovers them.
    missing_equity = [t for t in equity_tickers if t not in equity_frames]
    if missing_equity:
        log.warning("Price fetch failed for %s in parallel pool — retrying sequentially", missing_equity)
        for t in missing_equity:
            _, series = _fetch_one_ticker(t, chart_start_str, end_str)
            if series is not None:
                equity_frames[t] = series
            else:
                log.warning(
                    "No price data for %s after retry — held positions will be valued at $0 in the chart",
                    t,
                )

    closes = pd.DataFrame(equity_frames).bfill().ffill() if equity_frames else pd.DataFrame()

    # If the user holds XWD.TO as an equity position, inject it into closes
    # aligned to the existing trading-day index so the Euronext calendar does
    # not pollute trading_days with Paris-only market days.
    if wpea_col is not None and _BENCHMARK in equity_tickers and not closes.empty:
        closes[_BENCHMARK] = wpea_col.reindex(closes.index, method="ffill").bfill()

    # Replay all transactions BEFORE the window to build opening state.
    # sorted_txs is already in full-datetime (insertion) order — slicing it
    # directly is a faithful replay of the ORM ledger.  No priority override
    # is needed: floors have been removed from _apply_tx so same-day ordering
    # no longer affects the end-of-day cash balance.
    state: dict[str, dict[str, float]] = {name: {} for name in envelope_names}
    pre_window = [tx for tx in sorted_txs if tx["date"][:10] < chart_start_str]
    for tx in pre_window:
        env_name = tx.get("envelope_name")
        if env_name in state:
            _apply_tx(state[env_name], tx)

    # In-window transactions in insertion order; same rationale as pre_window.
    in_window = [tx for tx in sorted_txs if tx["date"][:10] >= chart_start_str]
    pending_idx = 0
    n_pending   = len(in_window)

    trading_days = closes.index if not closes.empty else pd.bdate_range(
        start=chart_start_str, end=str(today)
    )
    # Extend to today when market data has not yet settled (intraday / after-hours).
    # Transactions entered today become visible immediately; _envelope_value bridges
    # the missing close via .loc[:day], returning the last known price.
    # weekday() < 5 is a calendar heuristic consistent with the rest of this file —
    # exchange holidays will produce a zero-change today entry, which is harmless.
    today_ts = pd.Timestamp(today)
    if not closes.empty and today.weekday() < 5 and closes.index[-1] < today_ts:
        trading_days = trading_days.union(pd.DatetimeIndex([today_ts]))

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
    scoped_txs = (
        [tx for tx in txs if tx.get("envelope_name") in envelope_filter]
        if envelope_filter is not None else txs
    )
    stats = _compute_stats(daily_pure_changes, scoped_txs, envelopes, chart_start_str, period_days)

    # Benchmark: cash-flow mirror comparison vs XWD.TO.
    # For every DEPOSIT/DIVIDEND the user recorded, the mirror hypothetically buys
    # that same amount of XWD.TO at the day's price.  WITHDRAW reduces the mirror
    # proportionally.  Both portfolio_pct and benchmark_pct are then expressed as
    # % return on net capital deployed (capital_in = DEPOSIT + DIVIDEND - WITHDRAW),
    # giving a true apples-to-apples comparison for a DCA investor.
    benchmark_pct: list[float | None] = []
    portfolio_pct: list[float | None] = []

    if wpea_col is not None and dates:
        total_values = [
            sum(series_data[n][i] for n in envelope_names)
            for i in range(len(dates))
        ]

        # Step 1 — replay pre-window capital flows to build opening mirror state
        mirror_shares: float     = 0.0
        cumulative_capital: float = 0.0

        for tx in scoped_capital_txs:
            if tx["date"][:10] >= chart_start_str:
                break
            amount = float(tx.get("total") or 0.0)
            wpea_at_tx = wpea_col.loc[:pd.Timestamp(tx["date"][:10])].dropna()
            if wpea_at_tx.empty:
                continue
            price = float(wpea_at_tx.iloc[-1])
            if tx["type"] in _INFLOW:
                mirror_shares      += amount / price
                cumulative_capital += amount
            else:
                mirror_shares      = max(mirror_shares - amount / price, 0.0)
                cumulative_capital = max(cumulative_capital - amount, 0.0)

        # Step 2 — walk chart window, snapshotting mirror state at each trading day
        in_window_capital = [
            tx for tx in scoped_capital_txs
            if tx["date"][:10] >= chart_start_str
        ]
        cf_idx = 0

        mirror_shares_per_day: list[float] = []
        capital_per_day: list[float]       = []

        for day_str in dates:
            while cf_idx < len(in_window_capital) and in_window_capital[cf_idx]["date"][:10] <= day_str:
                tx     = in_window_capital[cf_idx]
                amount = float(tx.get("total") or 0.0)
                wpea_at_tx = wpea_col.loc[:pd.Timestamp(tx["date"][:10])].dropna()
                if not wpea_at_tx.empty:
                    price = float(wpea_at_tx.iloc[-1])
                    if tx["type"] in _INFLOW:
                        mirror_shares      += amount / price
                        cumulative_capital += amount
                    else:
                        mirror_shares      = max(mirror_shares - amount / price, 0.0)
                        cumulative_capital = max(cumulative_capital - amount, 0.0)
                cf_idx += 1
            mirror_shares_per_day.append(mirror_shares)
            capital_per_day.append(cumulative_capital)

        # Step 3 — per-day percentages: both divided by the same capital_in denominator
        for i, day_str in enumerate(dates):
            cap = capital_per_day[i]
            if cap < 1e-2:
                benchmark_pct.append(None)
                portfolio_pct.append(None)
                continue
            wpea_at_day = wpea_col.loc[:pd.Timestamp(day_str)].dropna()
            if wpea_at_day.empty:
                benchmark_pct.append(None)
                portfolio_pct.append(None)
                continue
            mirror_val = mirror_shares_per_day[i] * float(wpea_at_day.iloc[-1])
            benchmark_pct.append(round((mirror_val      / cap - 1) * 100, 4))
            portfolio_pct.append(round((total_values[i] / cap - 1) * 100, 4))

    return {"period": period, "dates": dates, "series": series, "events": events, "stats": stats,
            "benchmark_pct": benchmark_pct, "portfolio_pct": portfolio_pct,
            "benchmark_ticker": benchmark_ticker}
