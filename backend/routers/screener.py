"""
Screener Router
===============
Technical snapshot for every ticker in the user's watchlist.

Each row costs two cached yfinance calls (info + 1-year history) plus one
call to compute_signals().  Cache TTLs: info = 1 h, history = 4 h.
Rows are fetched concurrently, capped at 5 in-flight threads.

Endpoints
---------
GET  /api/profile/screener
    Returns ScreenerRow[] for the user's watchlist.

GET  /api/profile/screener/{ticker}/sentiment
    Lazy: fetches live RSS news and returns a winsorized sentiment score.
    Only call on explicit user demand — 3 live HTTP requests per ticker.
"""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from sqlmodel import select

from deps import SessionDep, get_current_username
from models.models import Transaction, WatchlistItem
from models.schemas import ScreenerRow, ScreenerSentiment
from services.fetcher import YFinanceFetcher
from services.news_service import fetch_news_async
from services.technical import compute_signals

router = APIRouter(prefix="/profile/screener", tags=["Screener"])
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_screener_row(ticker: str, held_set: set[str]) -> dict:
    """Sync helper — runs in threadpool. Two cached yfinance calls."""
    f = YFinanceFetcher(ticker)

    current = f.get_float("currentPrice") or f.get_float("regularMarketPrice") or 0.0
    prev    = f.get_float("previousClose") or f.get_float("regularMarketPreviousClose") or 0.0
    change_1d_pct = round((current - prev) / prev * 100, 2) if prev else 0.0

    info     = f.info()
    name     = info.get("shortName") or info.get("longName") or ticker
    sector   = info.get("sector")
    currency = info.get("currency", "USD")

    hist = f.history(period="1y", interval="1d")

    change_5d_pct: float | None = None
    change_1m_pct: float | None = None
    if not hist.empty and "Close" in hist.columns:
        closes = hist["Close"]
        last = float(closes.iloc[-1])
        if len(closes) >= 7:
            p5 = float(closes.iloc[-6])
            change_5d_pct = round((last - p5) / p5 * 100, 2) if p5 else None
        if len(closes) >= 23:
            p1m = float(closes.iloc[-22])
            change_1m_pct = round((last - p1m) / p1m * 100, 2) if p1m else None

    sig_data = compute_signals(hist)
    signals  = sig_data["signals"]
    raw      = sig_data["raw_values"]

    rsi_14             = raw.get("RSI (14)")
    bollinger_b        = raw.get("Bollinger %B")
    sma_raw            = raw.get("SMA 50/200")
    sma_50             = sma_raw.get("sma_50")  if isinstance(sma_raw, dict) else None
    sma_200            = sma_raw.get("sma_200") if isinstance(sma_raw, dict) else None
    stochastic_k       = raw.get("Stochastic %K/%D")
    volume_trend_ratio = raw.get("Volume Trend")

    return {
        "ticker":               ticker,
        "name":                 name,
        "sector":               sector,
        "currency":             currency,
        "current_price":        round(current, 4),
        "change_1d_pct":        change_1d_pct,
        "change_5d_pct":        change_5d_pct,
        "change_1m_pct":        change_1m_pct,
        "rsi_14":               round(rsi_14, 2)             if rsi_14             is not None else None,
        "bollinger_b":          round(bollinger_b, 3)        if bollinger_b        is not None else None,
        "bollinger_signal":     signals.get("Bollinger %B"),
        "sma_50":               round(sma_50, 4)             if sma_50             is not None else None,
        "sma_200":              round(sma_200, 4)            if sma_200            is not None else None,
        "sma_signal":           signals.get("SMA 50/200"),
        "macd_signal":          signals.get("MACD (12/26/9)"),
        "stochastic_k":         round(stochastic_k, 2)       if stochastic_k       is not None else None,
        "stochastic_signal":    signals.get("Stochastic %K/%D"),
        "volume_trend_ratio":   round(volume_trend_ratio, 2) if volume_trend_ratio is not None else None,
        "volume_trend_signal":  signals.get("Volume Trend"),
        "buy_pct":              sig_data["buy_pct"],
        "hold_pct":             sig_data["hold_pct"],
        "sell_pct":             sig_data["sell_pct"],
        "is_held":              ticker in held_set,
    }


def _compute_sentiment_score(articles: list) -> float:
    if not articles:
        return 0.0
    pos   = sum(1 for a in articles if (a.get("sentiment") if isinstance(a, dict) else getattr(a, "sentiment", None)) == "positive")
    neg   = sum(1 for a in articles if (a.get("sentiment") if isinstance(a, dict) else getattr(a, "sentiment", None)) == "negative")
    total = len(articles)
    return round(max(-1.0, min(1.0, (pos - neg) / total)), 3)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[ScreenerRow], summary="Technical screener for user watchlist")
async def get_screener(
    session: SessionDep,
    current_user: Annotated[str, Depends(get_current_username)],
) -> list[dict]:
    watchlist = session.exec(
        select(WatchlistItem).where(WatchlistItem.user == current_user)
    ).all()
    tickers = [w.ticker for w in watchlist]
    if not tickers:
        return []

    # Determine held tickers: net positive share count across all envelopes.
    txs = session.exec(
        select(Transaction).where(Transaction.user == current_user)
    ).all()
    net_shares: dict[str, float] = {}
    for tx in txs:
        if not tx.ticker:
            continue
        tx_type = str(tx.type)
        if tx_type == "BUY":
            net_shares[tx.ticker] = net_shares.get(tx.ticker, 0.0) + tx.shares
        elif tx_type == "SELL":
            net_shares[tx.ticker] = net_shares.get(tx.ticker, 0.0) - tx.shares
    held_set = {t for t, s in net_shares.items() if s > 1e-6}

    semaphore = asyncio.Semaphore(5)

    async def _fetch(t: str) -> dict:
        async with semaphore:
            return await run_in_threadpool(_build_screener_row, t, held_set)

    results = await asyncio.gather(*[_fetch(t) for t in tickers], return_exceptions=True)

    rows: list[dict] = []
    for ticker, result in zip(tickers, results, strict=True):
        if isinstance(result, Exception):
            log.warning("Screener row failed for %s: %s", ticker, result)
        else:
            rows.append(result)
    return rows


@router.get(
    "/{ticker}/sentiment",
    response_model=ScreenerSentiment,
    summary="Lazy news sentiment score for a single ticker",
)
async def get_screener_sentiment(
    ticker: str,
    current_user: Annotated[str, Depends(get_current_username)],
) -> dict:
    ticker   = ticker.strip().upper()
    articles = await fetch_news_async(ticker, limit=30)
    score    = _compute_sentiment_score(articles)
    if score > 0.1:
        label = "positive"
    elif score < -0.1:
        label = "negative"
    else:
        label = "neutral"
    return {"ticker": ticker, "sentiment_score": score, "label": label, "article_count": len(articles)}
