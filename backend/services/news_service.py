"""
News Service
============
Aggregates headlines from free RSS feeds — no API key required.

Sources
-------
- Yahoo Finance RSS  (ticker-specific)
- Google News RSS    (ticker-specific)
- Seeking Alpha RSS  (public feed)

All three feeds are fetched concurrently in the async path.
A rule-based sentiment tagger classifies each headline as
positive / negative / neutral.

Public API
----------
fetch_news_async(ticker, limit)  → async, for FastAPI routes  (preferred)
fetch_news(ticker, limit)        → sync, for threadpool callers / tests
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import feedparser
from fastapi.concurrency import run_in_threadpool

from services.fetcher import YFinanceFetcher

log = logging.getLogger(__name__)


# ── Sentiment lexicon ─────────────────────────────────────────────────────────
_POSITIVE = frozenset(
    {
        "beat", "beats", "surpasses", "record", "profit", "gain", "growth",
        "rise", "rises", "rally", "rallies", "upgrade", "upgraded", "buy",
        "strong", "bullish", "outperform", "positive", "boost", "boosted",
        "opportunity", "rebound", "soar", "soars", "exceed", "exceeds",
        "breakthrough", "expansion", "innovative", "upbeat", "dividend",
        "raise", "raised", "accelerate", "momentum", "partnership",
        "recovery", "upside", "optimistic", "robust", "surge", "surges",
    }
)

_NEGATIVE = frozenset(
    {
        "miss", "misses", "disappoints", "decline", "declines", "loss",
        "fall", "falls", "cut", "cuts", "downgrade", "downgraded", "sell",
        "weak", "bearish", "underperform", "negative", "risk", "risks",
        "lawsuit", "recall", "layoff", "layoffs", "debt", "bankruptcy",
        "drop", "drops", "crash", "crashes", "warning", "concern",
        "reduce", "reduced", "slowdown", "contraction", "halt",
        "investigation", "probe", "fraud", "default", "volatile", "uncertainty",
    }
)

_WORD_RE = re.compile(r"\b\w+\b")
_HTML_RE = re.compile(r"<[^>]+>")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sentiment(title: str) -> str:
    words = set(_WORD_RE.findall(title.lower()))
    pos = len(words & _POSITIVE)
    neg = len(words & _NEGATIVE)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _parse_date(entry: feedparser.FeedParserDict) -> str:
    """
    Parses RFC-2822 / ISO timestamps from a feed entry.
    Falls back to the current UTC time if no parseable date is found.
    """
    for attr in ("published", "updated"):
        if raw := getattr(entry, attr, None):
            try:
                dt = parsedate_to_datetime(raw)
                return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return raw
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_feed(url: str, source_label: str, limit: int = 8) -> list[dict]:
    """
    Blocking RSS fetch + parse.  Always runs inside a threadpool — feedparser
    has no async support.
    """
    results: list[dict] = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:limit]:
            title = getattr(entry, "title", "").strip()
            if not title:
                continue
            raw_summary = getattr(entry, "summary", "") or ""
            summary = _HTML_RE.sub("", raw_summary).strip()[:300] or None
            results.append(
                {
                    "title": title,
                    "source": source_label,
                    "published": _parse_date(entry),
                    "url": getattr(entry, "link", ""),
                    "summary": summary,
                    "sentiment": _sentiment(title),
                }
            )
    except Exception as exc:
        log.warning("Feed parse error [%s / %s]: %s", source_label, url, exc)
    return results


def _build_feed_list(search_ticker: str) -> list[tuple[str, str, int]]:
    """Returns [(url, source_label, per_feed_limit), ...]."""
    enc = quote(search_ticker, safe="")
    return [
        (
            f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={enc}&region=US&lang=en-US",
            "Yahoo Finance",
            8,
        ),
        (
            f"https://news.google.com/rss/search?q={enc}+stock+OR+earnings&hl=en-US&gl=US&ceid=US:en",
            "Google News",
            8,
        ),
        (
            f"https://seekingalpha.com/api/sa/combined/{enc}.xml",
            "Seeking Alpha",
            5,
        ),
    ]


def _deduplicate_and_sort(items: list[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda x: x["published"], reverse=True)
    return unique[:limit]


def _resolve_ticker(ticker: str) -> str:
    """
    Maps a foreign exchange ticker to its US equivalent for RSS lookups.

    Resolution order (cheapest first):
    1. No '.' in ticker → already a plain US symbol, return as-is.
    2. yfinance underlyingSymbol → directly set for some cross-listed securities.
    3. Fallback → strip the exchange suffix (e.g. PHI1.F → PHI1).
    """
    if "." not in ticker:
        return ticker

    info = YFinanceFetcher(ticker).info()

    underlying = info.get("underlyingSymbol", "")
    if underlying and "." not in underlying:
        log.info("Auto-alias: %s → %s (underlyingSymbol)", ticker, underlying)
        return underlying

    base = ticker.split(".")[0]
    log.debug("Suffix stripped: %s → %s", ticker, base)
    return base


# ── Public API ────────────────────────────────────────────────────────────────


async def fetch_news_async(ticker: str, limit: int = 20) -> list[dict]:
    """
    Async news fetch — preferred entry point for FastAPI routes.
    All three feeds are fetched concurrently via asyncio.gather.
    """
    search_ticker = _resolve_ticker(ticker.upper())
    feeds = _build_feed_list(search_ticker)

    results = await asyncio.gather(
        *[run_in_threadpool(_parse_feed, url, label, feed_limit) for url, label, feed_limit in feeds],
        return_exceptions=True,
    )

    all_items: list[dict] = []
    for res in results:
        if not isinstance(res, Exception):
            all_items.extend(res)
        else:
            log.warning("Feed task error: %s", res)

    return _deduplicate_and_sort(all_items, limit)


def fetch_news(ticker: str, limit: int = 20) -> list[dict]:
    """
    Sync version — for threadpool callers (e.g. report_builder) and tests.
    Fetches feeds sequentially.
    """
    search_ticker = _resolve_ticker(ticker.upper())
    all_items: list[dict] = []
    for url, label, feed_limit in _build_feed_list(search_ticker):
        all_items.extend(_parse_feed(url, label, feed_limit))
    return _deduplicate_and_sort(all_items, limit)
