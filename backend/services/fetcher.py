"""
YFinance Fetcher
================
TTL-cached wrapper around yfinance 1.x (curl_cffi backend).

Thread safety
-------------
curl_cffi.Session is not thread-safe for concurrent use.  The dashboard
fetches dozens of tickers in parallel via asyncio.gather + run_in_threadpool.
Solution: threading.local() gives each worker thread its own curl_cffi.Session
and its own SQLite connection.  Neither object is ever shared across threads.

Cache implementation
--------------------
Bare sqlite3, NOT SQLModel.  Reasons:

  Performance:  sqlite3.connect() with a persistent thread-local connection
                costs ~0.004ms per query.  A fresh connect() costs ~0.190ms.
                With 30 tickers on the dashboard that is ~11ms of pure overhead
                per request, eliminated by connection reuse.

  Fit:          SQLModel adds ORM abstraction (relationships, validators,
                column mapping) that is irrelevant to a blob key-value store.
                The cache table has no FK constraints, no application-level
                schema coupling, and no type coercion needs beyond bytes.

  Isolation:    The cache DB is deliberately separate from the application DB.
                It can be deleted and recreated at any time with zero impact
                on user data.  It does not belong in the same schema.

Cache schema design
-------------------
  PRIMARY KEY (ticker, method) — one row per (ticker, API method / cache key)
  expiration  REAL             — UNIX timestamp; expiration check done IN SQL
                                 (avoids Python datetime parse on every read)
  type_tag    TEXT             — serialisation format: df | series | df_tuple
                                 | json | empty (see below)
  data        BLOB             — parquet / JSON bytes; NULL for 'empty' entries

Negative-result caching
-----------------------
When yfinance returns an empty Series, DataFrame, or None for a valid ticker
(e.g. no dividends, no split history, no recommendations), storing nothing
means every future request re-hits yfinance for the same empty response.
Solution: cache a sentinel row with type_tag='empty', data=NULL.
Callers interpret 'empty' as "we already asked; the answer is nothing."
The sentinel respects the same TTL as a real result.

TTL rationale
-------------
info / intraday history           1 h   — price-sensitive; stale after each candle
daily history (includes today)    4 h   — at most 1 stale candle per session
historical range (past end-date) 90 d   — static; past data does not change
calendar / earnings_dates         24 h  — next date shifts occasionally
analyst targets / recommendations 24 h  — weekly revision cadence
financials / balance / cashflow   90 d  — quarterly cadence
dividends / splits / earnings_history  90 d  — rare events / quarterly
"""

import io
import json
import logging
import math
import sqlite3
import threading
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import yfinance as yf
from curl_cffi import requests as curl_requests
from yfinance.exceptions import YFRateLimitError

from config import get_settings

log = logging.getLogger(__name__)

DB_CACHE = get_settings().YF_CACHE_PATH

# ── TTL registry (hours) ──────────────────────────────────────────────────────

_TTL: dict[str, int] = {
    "info":                  1,
    "dividends":             24 * 90,
    "splits":                24 * 90,
    "calendar":              24,
    "analyst_price_targets": 24,
    "earnings_dates":        24,
    "earnings_history":      24 * 90,
    "financials":            24 * 90,
    "balance_sheet":         24 * 90,
    "cashflow":              24 * 90,
    "recommendations":       24,
    "sector_weightings":     24,
    "insider_purchases":     24,
}

_TTL_HISTORY_SHORT    = 1    # intraday / very recent (1d, 5d period)
_TTL_HISTORY_DAILY    = 4    # daily bars including today
_TTL_HISTORY_HISTORIC = 24 * 90  # fixed past range — data never changes

# Minimum key count for a yfinance info dict to be considered complete enough to cache.
# Degraded / soft-rate-limited responses typically have 8–15 keys; real responses have 40+.
_MIN_INFO_KEYS = 25


def _ttl_history(period: str, end_date: str | None) -> int:
    """
    Chooses the TTL for a history fetch.

    - Intraday periods ('1d', '5d')         → 1 h
    - Range ending strictly before today    → 90 d  (historic, immutable)
    - Everything else (daily, including today) → 4 h
    """
    if period in ("1d", "5d"):
        return _TTL_HISTORY_SHORT
    if end_date is not None:
        try:
            end = date.fromisoformat(end_date)
            if end < date.today():
                return _TTL_HISTORY_HISTORIC
        except ValueError:
            pass
    return _TTL_HISTORY_DAILY


def _ttl(method: str) -> int:
    return _TTL.get(method, 4)


# ── Serialisation ─────────────────────────────────────────────────────────────


def _serialize(data: object) -> tuple[bytes, str]:
    if isinstance(data, pd.DataFrame):
        buf = io.BytesIO()
        data.to_parquet(buf, index=True, engine="pyarrow", compression="snappy")
        return buf.getvalue(), "df"

    if isinstance(data, pd.Series):
        buf = io.BytesIO()
        data.to_frame(name="_s_").to_parquet(buf, index=True, engine="pyarrow", compression="snappy")
        return buf.getvalue(), "series"

    if isinstance(data, tuple) and all(isinstance(x, pd.DataFrame) for x in data):
        parts: list[str] = []
        for df in data:
            b = io.BytesIO()
            df.to_parquet(b, index=True, engine="pyarrow", compression="snappy")
            parts.append(b.getvalue().hex())
        return json.dumps(parts).encode(), "df_tuple"

    return json.dumps(data, default=str).encode(), "json"


def _deserialize(blob: bytes | None, type_tag: str) -> object:
    match type_tag:
        case "empty":
            return None  # sentinel: caller converts to appropriate empty type
        case "df":
            return pd.read_parquet(io.BytesIO(blob))  # type: ignore[arg-type]
        case "series":
            df = pd.read_parquet(io.BytesIO(blob))  # type: ignore[arg-type]
            s = df["_s_"]
            s.name = None
            return s
        case "df_tuple":
            return tuple(
                pd.read_parquet(io.BytesIO(bytes.fromhex(h)))
                for h in json.loads(blob.decode())  # type: ignore[union-attr]
            )
        case "json":
            return json.loads(blob.decode())  # type: ignore[union-attr]
        case _:
            raise ValueError(f"Unknown cache type_tag: {type_tag!r}")


# ── Cache schema ──────────────────────────────────────────────────────────────


_INIT_SQL = """
CREATE TABLE IF NOT EXISTS yf_cache (
    ticker     TEXT NOT NULL,
    method     TEXT NOT NULL,
    expiration REAL NOT NULL,
    type_tag   TEXT NOT NULL DEFAULT 'json',
    data       BLOB,
    PRIMARY KEY (ticker, method)
);
CREATE INDEX IF NOT EXISTS idx_yf_cache_exp ON yf_cache (expiration);
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
"""


def _bootstrap_cache_db(path: str) -> None:
    """Creates the cache schema if it does not exist. Called once at import time."""
    with sqlite3.connect(path) as conn:
        conn.executescript(_INIT_SQL)


_bootstrap_cache_db(DB_CACHE)

# Entries expiring beyond this horizon are considered static historical data and
# are preserved on a user-triggered cache reset (financials, balance sheet, etc.
# carry a 90-day TTL; dynamic entries like info/recommendations are ≤ 24h).
_CACHE_RESET_HORIZON_H = 48


def clear_ticker_cache(ticker: str) -> None:
    """
    Deletes all dynamic cache entries for a ticker (TTL ≤ 24h).
    Long-lived historical data (financials, balance sheet, cashflow, dividends,
    splits — 90-day TTL) is intentionally preserved.
    """
    try:
        conn = _get_conn()
        cutoff = _exp_ts(_CACHE_RESET_HORIZON_H)
        conn.execute(
            "DELETE FROM yf_cache WHERE ticker = ? AND expiration < ?",
            (ticker, cutoff),
        )
        conn.commit()
        log.debug("cache CLEAR [%s] — dynamic entries deleted (static historical preserved)", ticker)
    except Exception as exc:
        log.warning("Cache clear error [%s]: %s", ticker, exc)


# ── Thread-local resource pool ────────────────────────────────────────────────

_thread_local = threading.local()


def _get_session() -> curl_requests.Session:
    """
    Returns the curl_cffi Session for the current thread.
    Creates one on first call; never shared across threads.
    """
    if not getattr(_thread_local, "session", None):
        _thread_local.session = curl_requests.Session(impersonate="chrome")
    return _thread_local.session


def _get_conn() -> sqlite3.Connection:
    """
    Returns the SQLite connection for the current thread.
    Creates one on first call; never shared across threads.
    Connection is set to WAL + NORMAL sync — safe for concurrent readers/writers.
    """
    if not getattr(_thread_local, "conn", None):
        conn = sqlite3.connect(DB_CACHE, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _thread_local.conn = conn
    return _thread_local.conn


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_ts() -> float:
    """Current time as a UNIX timestamp for direct SQL comparison."""
    return datetime.now(UTC).timestamp()


def _exp_ts(hours: int) -> float:
    """Expiration timestamp = now + hours."""
    return (datetime.now(UTC) + timedelta(hours=hours)).timestamp()


def safe_val(val: object, default: object = None) -> object:
    if val is None:
        return default
    try:
        if math.isnan(float(val)):  # type: ignore[arg-type]
            return default
    except (TypeError, ValueError):
        pass
    return val


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def _empty_series() -> pd.Series:
    return pd.Series(dtype=float)


def _is_empty_data(data: object) -> bool:
    """True when yfinance returned a meaningful empty result (not a fetch error)."""
    if data is None:
        return True
    if isinstance(data, pd.DataFrame):
        return data.empty
    if isinstance(data, pd.Series):
        return data.empty
    if isinstance(data, tuple):
        # Partial tuples (one DF empty, one non-empty) ARE meaningful — cache them.
        # Only skip caching if every element is empty.
        return all(isinstance(x, pd.DataFrame) and x.empty for x in data)
    if isinstance(data, dict):
        return len(data) == 0
    return False


# ── Fetcher ───────────────────────────────────────────────────────────────────


class YFinanceFetcher:
    """
    TTL-cached wrapper around yfinance 1.x.

    Every public method follows the same contract:
      1. Check thread-local SQLite for a live (not expired) entry.
         - type_tag='empty'  → yfinance previously returned nothing; return empty type.
         - type_tag=<other>  → deserialise and return.
      2. On miss: fetch from yfinance.
         - Got data          → serialise and store with method-appropriate TTL.
         - Got empty/None    → store sentinel (type_tag='empty') with same TTL.
         - Exception         → return empty type; do NOT cache (allows retry).

    The in-process _info_cache avoids even the SQLite hit for repeated info()
    calls within a single request (e.g. report_builder calls info() 9×).
    """

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker.upper()
        self.yf = yf.Ticker(self.ticker, session=_get_session())
        self._info_cache: dict | None = None

    # ── Cache primitives ──────────────────────────────────────────────────────

    def _get_cached(self, method: str) -> tuple[bool, object]:
        """
        Returns (found, value).
        found=True  means the cache has a live entry (value may be None for sentinels).
        found=False means the entry is absent or expired.
        """
        try:
            conn = _get_conn()
            now = _now_ts()
            row = conn.execute(
                "SELECT type_tag, data, expiration FROM yf_cache "
                "WHERE ticker = ? AND method = ? AND expiration > ?",
                (self.ticker, method, now),
            ).fetchone()
            if row is None:
                log.debug("cache MISS  [%s/%s]", self.ticker, method)
                return False, None
            type_tag, blob, expiration = row
            remaining_h = (expiration - now) / 3600
            log.debug("cache HIT   [%s/%s] — %.1fh remaining", self.ticker, method, remaining_h)
            return True, _deserialize(bytes(blob) if blob is not None else None, type_tag)
        except Exception as exc:
            log.warning("Cache read error [%s/%s]: %s", self.ticker, method, exc)
            return False, None

    def _set_cache(self, method: str, data: object, expire_hours: int) -> None:
        """
        Stores data in the cache with the given TTL.
        Empty data is stored as a sentinel (type_tag='empty', data=NULL) so that
        we never re-fetch the same empty result until the TTL expires.
        Fetch errors must NOT call this method — they should not be cached.
        """
        try:
            conn = _get_conn()
            if _is_empty_data(data):
                conn.execute(
                    "INSERT OR REPLACE INTO yf_cache (ticker, method, expiration, type_tag, data) "
                    "VALUES (?, ?, ?, 'empty', NULL)",
                    (self.ticker, method, _exp_ts(expire_hours)),
                )
                log.debug("cache WRITE [%s/%s] — sentinel (empty), ttl %dh", self.ticker, method, expire_hours)
            else:
                blob, type_tag = _serialize(data)
                conn.execute(
                    "INSERT OR REPLACE INTO yf_cache (ticker, method, expiration, type_tag, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (self.ticker, method, _exp_ts(expire_hours), type_tag, blob),
                )
                log.debug("cache WRITE [%s/%s] — ttl %dh", self.ticker, method, expire_hours)
            conn.commit()
        except Exception as exc:
            log.warning("Cache write error [%s/%s]: %s", self.ticker, method, exc)

    # ── Data methods ──────────────────────────────────────────────────────────

    def info(self) -> dict:
        if self._info_cache is not None:
            return self._info_cache
        found, cached = self._get_cached("info")
        if found:
            self._info_cache = cached or {}
            return self._info_cache
        try:
            raw = self.yf.info
            result = dict(raw) if raw else {}
            if len(result) >= _MIN_INFO_KEYS:
                self._set_cache("info", result, expire_hours=_ttl("info"))
            else:
                log.warning("Partial info response [%s] — %d keys, skipping cache", self.ticker, len(result))
            self._info_cache = result
        except YFRateLimitError:
            log.warning("Rate limited fetching info [%s]", self.ticker)
            self._info_cache = {}
        except Exception as exc:
            log.warning("yfinance info error [%s]: %s", self.ticker, exc)
            self._info_cache = {}
        return self._info_cache

    def history(
        self,
        period: str = "1y",
        interval: str = "1d",
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        if start:
            cache_key = f"history_range_{start}_{end or date.today().isoformat()}_{interval}"
            ttl = _ttl_history(period="", end_date=end)
        else:
            cache_key = f"history_{period}_{interval}"
            ttl = _ttl_history(period=period, end_date=None)

        found, cached = self._get_cached(cache_key)
        if found:
            return cached if cached is not None else _empty_df()

        try:
            kwargs: dict = {"interval": interval, "auto_adjust": True}
            if start:
                kwargs |= {"start": start, "end": end}
            else:
                kwargs["period"] = period
            df: pd.DataFrame | None = self.yf.history(**kwargs)
            if df is None or df.empty:
                self._set_cache(cache_key, None, expire_hours=ttl)
                return _empty_df()
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            self._set_cache(cache_key, df, expire_hours=ttl)
            return df
        except YFRateLimitError:
            log.warning("Rate limited fetching history [%s]", self.ticker)
            return _empty_df()
        except Exception as exc:
            log.warning("yfinance history error [%s]: %s", self.ticker, exc)
            return _empty_df()

    def dividends(self) -> pd.Series:
        found, cached = self._get_cached("dividends")
        if found:
            return cached if cached is not None else _empty_series()
        try:
            res: pd.Series = self.yf.dividends
            self._set_cache("dividends", res, expire_hours=_ttl("dividends"))
            return res if res is not None else _empty_series()
        except Exception as exc:
            log.warning("yfinance dividends error [%s]: %s", self.ticker, exc)
            return _empty_series()

    def splits(self) -> pd.Series:
        found, cached = self._get_cached("splits")
        if found:
            return cached if cached is not None else _empty_series()
        try:
            res: pd.Series = self.yf.splits
            self._set_cache("splits", res, expire_hours=_ttl("splits"))
            return res if res is not None else _empty_series()
        except Exception as exc:
            log.warning("yfinance splits error [%s]: %s", self.ticker, exc)
            return _empty_series()

    def calendar(self) -> dict:
        found, cached = self._get_cached("calendar")
        if found:
            return cached if cached is not None else {}
        try:
            res: dict = self.yf.calendar or {}
            self._set_cache("calendar", res, expire_hours=_ttl("calendar"))
            return res
        except Exception as exc:
            log.warning("yfinance calendar error [%s]: %s", self.ticker, exc)
            return {}

    def analyst_price_targets(self) -> dict:
        found, cached = self._get_cached("analyst_price_targets")
        if found:
            return cached if cached is not None else {}
        try:
            apt = self.yf.analyst_price_targets
            res = apt if isinstance(apt, dict) else {}
            self._set_cache("analyst_price_targets", res, expire_hours=_ttl("analyst_price_targets"))
            return res
        except Exception as exc:
            log.warning("yfinance analyst_price_targets error [%s]: %s", self.ticker, exc)
            return {}

    def earnings_dates(self) -> pd.DataFrame:
        found, cached = self._get_cached("earnings_dates")
        if found:
            return cached if cached is not None else _empty_df()
        try:
            res = self.yf.earnings_dates
            self._set_cache("earnings_dates", res, expire_hours=_ttl("earnings_dates"))
            return res if res is not None else _empty_df()
        except Exception as exc:
            log.warning("yfinance earnings_dates error [%s]: %s", self.ticker, exc)
            return _empty_df()

    def earnings_history(self) -> pd.DataFrame:
        found, cached = self._get_cached("earnings_history")
        if found:
            return cached if cached is not None else _empty_df()
        try:
            res = self.yf.earnings_history
            self._set_cache("earnings_history", res, expire_hours=_ttl("earnings_history"))
            return res if res is not None else _empty_df()
        except Exception as exc:
            log.warning("yfinance earnings_history error [%s]: %s", self.ticker, exc)
            return _empty_df()

    def financials(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        found, cached = self._get_cached("financials")
        if found:
            return cached if cached is not None else (_empty_df(), _empty_df())  # type: ignore[return-value]
        try:
            ann = self.yf.income_stmt
            qrt = self.yf.quarterly_income_stmt
            res = (
                ann if isinstance(ann, pd.DataFrame) else _empty_df(),
                qrt if isinstance(qrt, pd.DataFrame) else _empty_df(),
            )
            self._set_cache("financials", res, expire_hours=_ttl("financials"))
            return res
        except Exception as exc:
            log.warning("yfinance financials error [%s]: %s", self.ticker, exc)
            return _empty_df(), _empty_df()

    def balance_sheet(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        found, cached = self._get_cached("balance_sheet")
        if found:
            return cached if cached is not None else (_empty_df(), _empty_df())  # type: ignore[return-value]
        try:
            ann = self.yf.balance_sheet
            qrt = self.yf.quarterly_balance_sheet
            res = (
                ann if isinstance(ann, pd.DataFrame) else _empty_df(),
                qrt if isinstance(qrt, pd.DataFrame) else _empty_df(),
            )
            self._set_cache("balance_sheet", res, expire_hours=_ttl("balance_sheet"))
            return res
        except Exception as exc:
            log.warning("yfinance balance_sheet error [%s]: %s", self.ticker, exc)
            return _empty_df(), _empty_df()

    def cashflow(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        found, cached = self._get_cached("cashflow")
        if found:
            return cached if cached is not None else (_empty_df(), _empty_df())  # type: ignore[return-value]
        try:
            ann = self.yf.cash_flow
            qrt = self.yf.quarterly_cash_flow
            res = (
                ann if isinstance(ann, pd.DataFrame) else _empty_df(),
                qrt if isinstance(qrt, pd.DataFrame) else _empty_df(),
            )
            self._set_cache("cashflow", res, expire_hours=_ttl("cashflow"))
            return res
        except Exception as exc:
            log.warning("yfinance cashflow error [%s]: %s", self.ticker, exc)
            return _empty_df(), _empty_df()

    def recommendations(self) -> pd.DataFrame:
        found, cached = self._get_cached("recommendations")
        if found:
            return cached if cached is not None else _empty_df()
        try:
            res = self.yf.recommendations
            self._set_cache("recommendations", res, expire_hours=_ttl("recommendations"))
            return res if res is not None else _empty_df()
        except Exception as exc:
            log.warning("yfinance recommendations error [%s]: %s", self.ticker, exc)
            return _empty_df()

    def insider_purchases(self) -> pd.DataFrame:
        found, cached = self._get_cached("insider_purchases")
        if found:
            return cached if isinstance(cached, pd.DataFrame) else _empty_df()

        try:
            res = self.yf.insider_purchases
            data = res if (res is not None and not res.empty) else _empty_df()
        except Exception as exc:
            log.warning("insider_purchases error [%s]: %s", self.ticker, exc)
            return _empty_df()

        self._set_cache("insider_purchases", data, expire_hours=_ttl("insider_purchases"))
        return data

    def sector_weightings(self) -> dict:
        found, cached = self._get_cached("sector_weightings")
        if found:
            return cached if isinstance(cached, dict) else {}

        result: dict = {}
        try:
            info = self.info()
            if info.get("quoteType") == "ETF":
                raw = self.yf.funds_data.sector_weightings
                if raw:
                    result = dict(sorted(raw.items(), key=lambda x: x[1], reverse=True))
        except Exception as exc:
            log.warning("sector_weightings error [%s]: %s", self.ticker, exc)

        self._set_cache("sector_weightings", result, expire_hours=_ttl("sector_weightings"))
        return result

    # ── Convenience accessors ─────────────────────────────────────────────────

    def get(self, key: str, default: object = None) -> object:
        return safe_val(self.info().get(key), default)

    def get_float(self, key: str) -> float | None:
        v = self.info().get(key)
        try:
            f = float(v) if v is not None else None
            return None if (f is not None and math.isnan(f)) else f
        except (TypeError, ValueError):
            return None

    def get_int(self, key: str) -> int | None:
        v = self.info().get(key)
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
