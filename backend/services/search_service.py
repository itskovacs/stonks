"""
Ticker Search Service
=====================
Translates a free-text query into enriched ticker results.

Strategy
--------
1. yf.Search(query) — yfinance's official search, returns candidate tickers
   with metadata (symbol, name, exchange, quoteType) but no live pricing.
2. Parallel YFinanceFetcher.info() enrichment for the top N candidates —
   adds current price (value) and daily percentage change.
3. Bare-ticker fallback — if yf.Search returns nothing and the query looks
   like a valid ticker symbol (≤ 6 chars, alphanumeric + dots/hyphens),
   attempt a direct info() fetch as a single result.

The YFinanceFetcher has a 1h TTL cache for info() calls, so repeated search
results for tickers that have been viewed recently are served from cache.

Result ordering follows yfinance's relevance ranking. Candidates with no
usable price data (currentPrice == 0) are excluded — they are typically
delisted, unpriced, or unsupported instruments.

Supported quote types
---------------------
EQUITY, ETF, MUTUALFUND, INDEX — intentionally excludes OPTION, FUTURE,
CURRENCY, CRYPTOCURRENCY which are out of scope for this application.

Public API
----------
search_tickers(query, max_results)  → list[dict]   (sync, threadpool-safe)
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import yfinance as yf

from services.fetcher import YFinanceFetcher, _get_session

log = logging.getLogger(__name__)

# Quote types included in results.  OPTION, FUTURE, CURRENCY excluded.
_INCLUDED_TYPES = frozenset({"EQUITY", "ETF", "MUTUALFUND", "INDEX"})

# Maximum concurrent price-enrichment fetches per search call.
_MAX_ENRICH_WORKERS = 8

# A query that looks like a bare ticker: short, only ticker-legal characters.
# Broader than strict alphanumeric to cover BRK.B, BF-B, etc.
_TICKER_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")


def _looks_like_ticker(query: str) -> bool:
    q = query.upper()
    return 1 <= len(q) <= 6 and all(c in _TICKER_CHARS for c in q)


def _search_candidates(query: str, max_results: int) -> list[dict]:
    """
    Calls yf.Search and returns filtered quote candidates.
    yf.Search handles Yahoo Finance crumb/cookie authentication internally.
    Returns an empty list on any failure — callers always have the fallback.
    """
    try:
        results = yf.Search(
            query,
            max_results=max_results,
            news_count=0,
            lists_count=0,
            enable_fuzzy_query=False,
            session=_get_session(),
        )
        return [
            q for q in (results.quotes or [])
            if q.get("quoteType") in _INCLUDED_TYPES and q.get("symbol")
        ]
    except Exception as exc:
        log.debug("yf.Search failed for %r: %s", query, exc)
        return []


def _enrich_candidate(candidate: dict) -> dict | None:
    """
    Fetches live price data for one candidate via YFinanceFetcher.
    Returns None when no usable price is available (delisted, unsupported).

    The candidate dict comes from yf.Search.quotes and may include:
      symbol, shortname, longname, quoteType, exchange, currency
    """
    ticker = (candidate.get("symbol") or "").upper()
    if not ticker:
        return None
    try:
        f = YFinanceFetcher(ticker)
        info = f.info()

        current = f.get_float("currentPrice") or f.get_float("regularMarketPrice") or 0.0
        if current <= 0:
            # No usable price — instrument is likely delisted or unsupported
            return None

        prev       = f.get_float("previousClose") or f.get_float("regularMarketPreviousClose") or 0.0
        change_pct = round((current - prev) / prev * 100, 2) if prev else 0.0

        name = (
            info.get("shortName")
            or info.get("longName")
            or candidate.get("shortname")
            or candidate.get("longname")
            or ticker
        )

        return {
            "ticker":        ticker,
            "name":          name,
            "value":         round(current, 4),
            "change_1d_pct": change_pct,
            "currency":      info.get("currency") or candidate.get("currency") or "USD",
            "exchange":      info.get("exchange") or candidate.get("exchange"),
            "quote_type":    candidate.get("quoteType") or info.get("quoteType") or "EQUITY",
        }
    except Exception as exc:
        log.debug("Enrichment failed for %s: %s", ticker, exc)
        return None


def search_tickers(query: str, max_results: int = 8) -> list[dict]:
    """
    Searches for tickers matching the query and returns enriched results.

    Parameters
    ----------
    query       : free-text search string (e.g. "Apple", "MSFT", "S&P 500")
    max_results : maximum number of results to return (default 8, capped at 20)

    Returns
    -------
    List of dicts:
      { ticker, name, value, change_1d_pct, currency, exchange, quote_type }

    Result order follows yfinance's relevance ranking.
    Results with no usable price are excluded.
    """
    query = query.strip()
    if not query:
        return []

    max_results = min(max_results, 20)

    # Fetch more candidates than needed to absorb those with no price data
    candidates = _search_candidates(query, max_results=max_results + 4)

    # Fallback: bare ticker query, try direct if Search returned nothing
    if not candidates and _looks_like_ticker(query):
        log.debug("No Search results for %r — trying direct ticker fetch", query)
        candidates = [{"symbol": query.upper(), "quoteType": "EQUITY"}]

    if not candidates:
        return []

    # Parallel enrichment, preserving relevance order
    order = {c["symbol"].upper(): i for i, c in enumerate(candidates)}
    enriched: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=min(_MAX_ENRICH_WORKERS, len(candidates))) as executor:
        futures = {executor.submit(_enrich_candidate, c): c for c in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                enriched[result["ticker"]] = result

    # Re-sort by original relevance rank, then truncate
    return sorted(
        enriched.values(),
        key=lambda r: order.get(r["ticker"], 999),
    )[:max_results]
