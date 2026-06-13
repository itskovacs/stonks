"""
Earnings Calendar Service
=========================
Fetches market earnings announcements for ±2 days from today.

Uses the Nasdaq public earnings calendar API (no API key, one request per
calendar day, parallelised) to collect tickers, then enriches each with
sector, currency, and 5-day price history via the cached YFinanceFetcher.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta

from curl_cffi import requests as curl_requests

from services.fetcher import YFinanceFetcher

log = logging.getLogger(__name__)
_MAX_WORKERS = 2
_NASDAQ_EARN_URL = "https://api.nasdaq.com/api/calendar/earnings"


def _fetch_earnings_for_date(date_str: str) -> tuple[str, dict[str, str | None]]:
    """Returns (date_str, {ticker: company_name}) for all companies reporting on that date."""
    try:
        resp = curl_requests.get(
            _NASDAQ_EARN_URL,
            params={"date": date_str},
            impersonate="chrome",
            timeout=10,
        )
        if resp.status_code != 200:
            return date_str, {}
        rows = (resp.json().get("data") or {}).get("rows") or []
        out: dict[str, str | None] = {}
        for row in rows:
            ticker = str(row.get("symbol", "")).strip().upper()
            if not ticker:
                continue
            out[ticker] = row.get("name") or None
        return date_str, out
    except Exception as exc:
        log.warning("Nasdaq earn API failed for %s: %s", date_str, exc)
        return date_str, {}


def _enrich_ticker(ticker: str, earnings_date: str, company_name: str | None) -> dict | None:
    """Fetch info + 5-day price history for one ticker. Returns None on any error."""
    try:
        f = YFinanceFetcher(ticker)
        info = f.info()
        history_df = f.history(period="5d", interval="1d")

        history = []
        for ts, row in history_df.iterrows():
            history.append({
                "date":   ts.strftime("%Y-%m-%d"),
                "open":   round(float(row.get("Open",   0)), 4),
                "high":   round(float(row.get("High",   0)), 4),
                "low":    round(float(row.get("Low",    0)), 4),
                "close":  round(float(row.get("Close",  0)), 4),
                "volume": int(row.get("Volume", 0)),
            })

        return {
            "ticker":        ticker,
            "company_name":  company_name or info.get("shortName") or info.get("longName"),
            "earnings_date": earnings_date,
            "sector":        info.get("sector") or info.get("sectorKey"),
            "currency":      info.get("currency"),
            "history":       history,
        }
    except Exception as exc:
        log.warning("Earnings enrichment failed [%s]: %s", ticker, exc)
        return None


def get_earnings_calendar() -> dict:
    """
    Returns an EarningsCalendarResponse-compatible dict for the ±2 days window.
    Gracefully returns empty entries if upstream calendar fetches fail.
    """
    today = date.today()
    date_range = [
        (today + timedelta(days=d)).strftime("%Y-%m-%d")
        for d in range(-2, 3)
    ]

    # Step 1: collect {ticker -> (earnings_date, company_name)} across all dates in parallel
    seen: dict[str, tuple[str, str | None]] = {}
    with ThreadPoolExecutor(max_workers=len(date_range)) as pool:
        futures = {pool.submit(_fetch_earnings_for_date, d): d for d in date_range}
        for fut in as_completed(futures):
            day_str, tickers = fut.result()
            for ticker, company in tickers.items():
                if ticker not in seen:
                    seen[ticker] = (day_str, company)

    # Step 2: enrich each ticker with sector, currency, and 5-day history in parallel
    entries: list[dict] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_enrich_ticker, ticker, e_date, company): ticker
            for ticker, (e_date, company) in seen.items()
        }
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                entries.append(result)

    entries.sort(key=lambda e: (e["earnings_date"], e["ticker"]))

    return {
        "entries":      entries,
        "generated_at": datetime.now(UTC).isoformat(),
    }
