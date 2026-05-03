"""
Report Builder
==============
Assembles every section of a StockReport by orchestrating all services.

Public API
----------
build_report(ticker, user_positions)   → full StockReport dict
build_price_chart_for_period(...)      → PriceChart dict for period switching

Grid scoring
------------
Each grid (valuation / health / growth) has two outputs:
  cells  — display status (green / amber / red) for the UI grid
  score  — a 0–100 value computed from value-normalised per-metric anchors

The score is computed independently of the cell statuses using _norm_score(),
which linearly interpolates each raw metric value between a documented
"ideal" anchor (→ 100) and a "poor" anchor (→ 0).  Missing data → 50 (neutral).

This replaces the prior ternary approach (green=100/amber=50/red=0), which
produced misleading scores because all green cells were treated as equivalent
regardless of magnitude.

Score breakdown weights
-----------------------
Valuation  35% — is the stock trading at a reasonable price?
Health     35% — does the company have a strong balance sheet and returns?
Growth     30% — is revenue and earnings trending in the right direction?

Grade thresholds (A ≥ 75, B ≥ 60, C ≥ 45, D ≥ 30, F < 30) are calibrated
for the continuous scoring distribution — not for the prior ternary system.

D/E ratio display
-----------------
yfinance returns debtToEquity × 100 (as a percentage).  The health grid
divides by 100 for both value storage and display so that all ratio
comparisons are against the actual D/E (e.g. 1.5× instead of 150).
This is consistent with the risk engine.
"""

import logging
import math
from datetime import UTC, datetime

import pandas as pd

from services.fetcher import YFinanceFetcher
from services.news_service import fetch_news
from services.risk_engine import compute_risk
from services.technical import compute_signals

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_int(val: object) -> int | None:
    if val is None:
        return None
    try:
        if pd.isna(val):  # type: ignore[arg-type]
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ── Formatting helpers ────────────────────────────────────────────────────────


def _fmt_large(v: float | None) -> str:
    if v is None:
        return "N/A"
    av = abs(v)
    if av >= 1e12:
        return f"${v / 1e12:.2f}T"
    if av >= 1e9:
        return f"${v / 1e9:.2f}B"
    if av >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    return "N/A" if v is None else f"{v * 100:.1f}%"


def _fmt_ratio(v: float | None, decimals: int = 2) -> str:
    return "N/A" if v is None else f"{v:.{decimals}f}x"


def _safe_float(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _safe_int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ── Grid cell status helpers ──────────────────────────────────────────────────

_OPS = {
    ">":  lambda a, b: a > b,
    "<":  lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
}


def _status(value: float | None, thresholds: dict) -> str:
    """
    Evaluates ordered threshold rules → first match wins.
    thresholds = {"green": (">", 10), "red": ("<", 0)}
    """
    if value is None:
        return "neutral"
    for color, (op, thresh) in thresholds.items():
        fn = _OPS.get(op)
        if fn and fn(value, thresh):
            return color
    return "amber"


def _cell(
    key: str,
    label: str,
    value: object,
    formatted: str,
    benchmark: str,
    thresholds: dict,
) -> dict:
    val = _safe_float(value)
    return {
        "key":       key,
        "label":     label,
        "value":     val,
        "formatted": formatted,
        "status":    _status(val, thresholds),
        "benchmark": benchmark,
    }


# ── Value-normalised scoring ──────────────────────────────────────────────────


def _norm_score(value: float | None, ideal: float, poor: float) -> float:
    """
    Maps a metric value linearly to [0, 100].
    ideal → 100 points, poor → 0 points.

    Works for both directions:
      Lower-is-better: ideal < poor  (e.g. P/E: ideal=12, poor=50)
      Higher-is-better: ideal > poor (e.g. ROE: ideal=0.20, poor=-0.05)

    Returns 50.0 for None (neutral — insufficient data).
    Returns 50.0 if ideal == poor (degenerate input, avoid division by zero).
    """
    if value is None or ideal == poor:
        return 50.0
    score = (value - poor) / (ideal - poor) * 100.0
    return max(0.0, min(100.0, score))


def _valuation_score(f: YFinanceFetcher) -> float:
    """
    Weighted average of five valuation metrics, each normalised to 0–100.

    Weights: P/E TTM (30%), Fwd P/E (25%), PEG (20%), P/B (15%), EV/EBITDA (10%).
    Loss-making companies (PE ≤ 0) receive 10/100 on the PE component because
    there is no earnings floor anchoring the valuation — not 0/100 because
    many growth companies pass through temporary unprofitability.
    """
    pe = f.get_float("trailingPE")
    pe_score = (
        _norm_score(pe, ideal=12.0, poor=50.0) if (pe is not None and pe > 0) else 10.0
    )

    fwd_pe = f.get_float("forwardPE")
    fwd_score = (
        _norm_score(fwd_pe, ideal=12.0, poor=40.0) if (fwd_pe is not None and fwd_pe > 0) else 10.0
    )

    peg = f.get_float("pegRatio")
    peg_score = (
        _norm_score(peg, ideal=0.75, poor=2.5) if (peg is not None and peg > 0) else 50.0
    )

    pb = f.get_float("priceToBook")
    pb_score = _norm_score(pb, ideal=1.5, poor=8.0) if pb is not None else 50.0

    ev_ebitda = f.get_float("enterpriseToEbitda")
    ev_score = (
        _norm_score(ev_ebitda, ideal=8.0, poor=25.0)
        if (ev_ebitda is not None and ev_ebitda > 0)
        else 50.0
    )

    return round(
        0.30 * pe_score
        + 0.25 * fwd_score
        + 0.20 * peg_score
        + 0.15 * pb_score
        + 0.10 * ev_score,
        1,
    )


def _health_score(f: YFinanceFetcher) -> float:
    """
    Weighted average of six financial health metrics, each normalised to 0–100.

    Weights: Net Margin (20%), ROE (20%), Gross Margin (15%), Current Ratio (15%),
             Quick Ratio (15%), ROA (15%).
    """
    cr = f.get_float("currentRatio")
    qr = f.get_float("quickRatio")
    roe = f.get_float("returnOnEquity")
    roa = f.get_float("returnOnAssets")
    gross_m = f.get_float("grossMargins")
    net_m = f.get_float("profitMargins")

    return round(
        0.20 * _norm_score(net_m, ideal=0.12, poor=-0.05)
        + 0.20 * _norm_score(roe, ideal=0.20, poor=-0.05)
        + 0.15 * _norm_score(gross_m, ideal=0.40, poor=0.05)
        + 0.15 * _norm_score(cr, ideal=2.0, poor=0.8)
        + 0.15 * _norm_score(qr, ideal=1.5, poor=0.5)
        + 0.15 * _norm_score(roa, ideal=0.08, poor=0.0),
        1,
    )


def _growth_score(f: YFinanceFetcher) -> float:
    """
    Weighted average of four growth metrics, each normalised to 0–100.

    Weights: Revenue Growth (35%), Earnings Growth (30%), Quarterly EPS Growth (20%),
             FCF positivity (15% — binary: positive FCF = 75, negative = 25, None = 50).

    FCF is treated as binary because FCF magnitude is not comparable across
    market caps without normalisation.  A positive vs negative FCF is the
    educationally meaningful signal here.
    """
    rev_g = f.get_float("revenueGrowth")
    earn_g = f.get_float("earningsGrowth")
    qtr_g = f.get_float("earningsQuarterlyGrowth")
    fcf = f.get_float("freeCashflow")

    fcf_score = 50.0 if fcf is None else (75.0 if fcf > 0 else 25.0)

    return round(
        0.35 * _norm_score(rev_g, ideal=0.15, poor=-0.05)
        + 0.30 * _norm_score(earn_g, ideal=0.15, poor=-0.10)
        + 0.20 * _norm_score(qtr_g, ideal=0.10, poor=-0.15)
        + 0.15 * fcf_score,
        1,
    )


# ── Section builders ──────────────────────────────────────────────────────────


def _build_stock_bar(f: YFinanceFetcher, hist: pd.DataFrame, signals: dict) -> dict:
    info = f.info()
    current = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    prev = _safe_float(info.get("previousClose") or info.get("regularMarketPreviousClose"))

    change_1d = (current - prev) if (current and prev) else None
    change_1d_pct = (change_1d / prev * 100) if (change_1d is not None and prev) else None

    ytd_pct = None
    if not hist.empty and "Close" in hist.columns:
        this_year = datetime.now(UTC).year
        year_slice = hist[hist.index.year == this_year]["Close"]
        if not year_slice.empty:
            first = float(year_slice.iloc[0])
            last = float(hist["Close"].iloc[-1])
            ytd_pct = (last - first) / first * 100 if first else None

    raw = signals.get("raw_values", {})
    rsi_14 = raw.get("RSI (14)")
    sma_raw = raw.get("SMA 50/200")
    sma_50 = sma_raw.get("sma_50") if isinstance(sma_raw, dict) else None
    sma_200 = sma_raw.get("sma_200") if isinstance(sma_raw, dict) else None

    return {
        "ticker":              f.ticker,
        "company_name":        info.get("longName") or info.get("shortName") or f.ticker,
        "sector":              info.get("sector", "Unknown"),
        "industry":            info.get("industry", "Unknown"),
        "current_price":       current or 0.0,
        "change_1d":           change_1d or 0.0,
        "change_1d_pct":       change_1d_pct or 0.0,
        "change_ytd_pct":      ytd_pct or 0.0,
        "market_cap":          _safe_float(info.get("marketCap")),
        "volume":              _safe_int(info.get("volume")),
        "avg_volume":          _safe_int(info.get("averageVolume")),
        "fifty_two_week_high": _safe_float(info.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low":  _safe_float(info.get("fiftyTwoWeekLow")),
        "beta":                _safe_float(info.get("beta")),
        "currency":            info.get("currency", "USD"),
        "rsi_14":              rsi_14,
        "sma_50":              sma_50,
        "sma_200":             sma_200,
    }


def _build_kpi_strip(f: YFinanceFetcher) -> list[dict]:
    def kpi(key: str, label: str, raw_val: object, formatted: str, unit: str) -> dict:
        return {
            "key":       key,
            "label":     label,
            "value":     _safe_float(raw_val),
            "formatted": formatted,
            "unit":      unit,
        }

    info = f.info()
    eps_raw = info.get("trailingEps")
    eps_fmt = f"${eps_raw:.2f}" if eps_raw is not None else "N/A"

    return [
        kpi("pe_trailing", "P/E (TTM)",     info.get("trailingPE"),               _fmt_ratio(info.get("trailingPE"), 1),               "x"),
        kpi("pe_forward",  "P/E (Fwd)",     info.get("forwardPE"),                _fmt_ratio(info.get("forwardPE"), 1),                "x"),
        kpi("ev_ebitda",   "EV/EBITDA",     info.get("enterpriseToEbitda"),       _fmt_ratio(info.get("enterpriseToEbitda"), 1),       "x"),
        kpi("price_book",  "P/B",           info.get("priceToBook"),              _fmt_ratio(info.get("priceToBook"), 2),              "x"),
        kpi("roe",         "ROE",           info.get("returnOnEquity"),           _fmt_pct(info.get("returnOnEquity")),           "%"),
        kpi("roa",         "ROA",           info.get("returnOnAssets"),           _fmt_pct(info.get("returnOnAssets")),           "%"),
        kpi("gross_margin","Gross Margin",  info.get("grossMargins"),             _fmt_pct(info.get("grossMargins")),             "%"),
        kpi("net_margin",  "Net Margin",    info.get("profitMargins"),            _fmt_pct(info.get("profitMargins")),            "%"),
        kpi("revenue_ttm", "Revenue (TTM)", info.get("totalRevenue"),             _fmt_large(info.get("totalRevenue")),           "$"),
        kpi("free_cash",   "Free Cash Flow",info.get("freeCashflow"),             _fmt_large(info.get("freeCashflow")),           "$"),
        kpi("div_yield",   "Div. Yield",    info.get("dividendYield"),            _fmt_pct(info.get("dividendYield")),            "%"),
        kpi("eps_ttm",     "EPS (TTM)",     info.get("trailingEps"),              eps_fmt,                                        "$"),
    ]


def _build_price_chart(f: YFinanceFetcher, hist: pd.DataFrame) -> dict:
    prices: list[dict] = []
    if not hist.empty:
        for dt, row in hist.iterrows():
            prices.append(
                {
                    "date":   str(dt.date()),
                    "open":   round(float(row.get("Open", 0)), 4),
                    "high":   round(float(row.get("High", 0)), 4),
                    "low":    round(float(row.get("Low", 0)), 4),
                    "close":  round(float(row.get("Close", 0)), 4),
                    "volume": int(row.get("Volume", 0)),
                }
            )

    annotations: list[dict] = []

    currency = f.info().get("currency", "")

    if not hist.empty and "High" in hist.columns and "Low" in hist.columns:
        hi_idx = hist["High"].idxmax()
        lo_idx = hist["Low"].idxmin()
        annotations += [
            {"date": str(hi_idx.date()), "label": f"High: {hist["High"][hi_idx]:.2f} {currency}", "type": "high"},
            {"date": str(lo_idx.date()), "label": f"Low: {hist["Low"][lo_idx]:.2f} {currency}",  "type": "low"},
        ]

    def _strip_tz(ts: object) -> pd.Timestamp:
        t = pd.Timestamp(ts)
        return t.tz_localize(None) if t.tzinfo is not None else t

    def _add_events(series: pd.Series, event_type: str, label_fmt: str) -> None:
        if series.empty or hist.empty:
            return
        for dt, val in series.items():
            d = _strip_tz(dt)
            if d >= hist.index.min():
                annotations.append(
                    {"date": str(d.date()), "label": label_fmt.format(val), "type": event_type}
                )

    _add_events(f.dividends(), "dividend", f"Div {{:.2f}} {currency}")
    _add_events(f.splits(),   "split",    "Split {:.0f}:1")

    try:
        ed = f.earnings_dates()
        if not ed.empty and not hist.empty:
            for dt_idx in list(ed.index[:4]):
                d = _strip_tz(dt_idx)
                if hist.index.min() <= d <= hist.index.max():
                    annotations.append({"date": str(d.date()), "label": "Earnings", "type": "earnings"})
    except Exception as exc:
        log.debug("Earnings date annotations skipped for %s: %s", f.ticker, exc)

    return {"prices": prices, "annotations": annotations}


def _build_valuation_grid(f: YFinanceFetcher) -> dict:
    """
    Display cells use status thresholds (green/amber/red) for the UI grid.
    The grid score is computed separately via _valuation_score() using
    value-normalised anchors — not from cell statuses.
    """
    info = f.info()
    cells = [
        _cell("pe_trailing", "P/E (TTM)",      info.get("trailingPE"),                        _fmt_ratio(info.get("trailingPE"), 1),                        "< 25 ideal",      {"green": ("<", 20),   "red": (">", 40)}),
        _cell("pe_forward",  "P/E (Forward)",  info.get("forwardPE"),                         _fmt_ratio(info.get("forwardPE"), 1),                         "< 20 ideal",      {"green": ("<", 18),   "red": (">", 35)}),
        _cell("peg",         "PEG Ratio",      info.get("pegRatio"),                          _fmt_ratio(info.get("pegRatio"), 2),                          "< 1 undervalued", {"green": ("<", 1),    "red": (">", 2)}),
        _cell("ps",          "P/S Ratio",      info.get("priceToSalesTrailing12Months"),      _fmt_ratio(info.get("priceToSalesTrailing12Months"), 1),      "< 3 fair",        {"green": ("<", 3),    "red": (">", 10)}),
        _cell("pb",          "P/B Ratio",      info.get("priceToBook"),                       _fmt_ratio(info.get("priceToBook"), 2),                       "< 3 fair",        {"green": ("<", 2),    "red": (">", 6)}),
        _cell("ev_ebitda",   "EV/EBITDA",      info.get("enterpriseToEbitda"),                _fmt_ratio(info.get("enterpriseToEbitda"), 1),                "< 12 ideal",      {"green": ("<", 12),   "red": (">", 20)}),
        _cell("ev_rev",      "EV/Revenue",     info.get("enterpriseToRevenue"),               _fmt_ratio(info.get("enterpriseToRevenue"), 1),               "< 5 fair",        {"green": ("<", 4),    "red": (">", 10)}),
        _cell("div_yield",   "Div. Yield",     info.get("dividendYield"),                     _fmt_pct(info.get("dividendYield")),                          "> 2% attractive", {"green": (">", 0.02), "red": ("<", 0)}),
    ]
    return {"cells": cells, "score": _valuation_score(f)}


def _build_health_grid(f: YFinanceFetcher) -> dict:
    """
    D/E ratio: yfinance returns debtToEquity × 100 (percentage scale).
    Divide by 100 for display and threshold comparison so that the actual
    D/E ratio is shown (e.g. 1.50× instead of 150.0).
    Thresholds remain: < 1× = green, > 2× = red.
    """
    info = f.info()

    de_raw = _safe_float(info.get("debtToEquity"))
    de_ratio = de_raw / 100.0 if de_raw is not None else None

    cells = [
        _cell("current_ratio", "Current Ratio",     info.get("currentRatio"),      _fmt_ratio(info.get("currentRatio"), 2),  "> 1.5 healthy",    {"green": (">", 1.5),  "red": ("<", 1.0)}),
        _cell("quick_ratio",   "Quick Ratio",       info.get("quickRatio"),        _fmt_ratio(info.get("quickRatio"), 2),    "> 1.0 healthy",    {"green": (">", 1.0),  "red": ("<", 0.5)}),
        _cell("de_ratio",      "D/E Ratio",         de_ratio,                      _fmt_ratio(de_ratio, 2),                  "< 1 low leverage", {"green": ("<", 1),    "red": (">", 2)}),
        _cell("interest_cov",  "Interest Coverage", info.get("interestCoverage"),  _fmt_ratio(info.get("interestCoverage"), 1), "> 3 safe",        {"green": (">", 3),    "red": ("<", 1.5)}),
        _cell("roe",           "ROE",               info.get("returnOnEquity"),    _fmt_pct(info.get("returnOnEquity")),     "> 15% strong",     {"green": (">", 0.15), "red": ("<", 0)}),
        _cell("roa",           "ROA",               info.get("returnOnAssets"),    _fmt_pct(info.get("returnOnAssets")),     "> 5% good",        {"green": (">", 0.05), "red": ("<", 0)}),
        _cell("gross_margin",  "Gross Margin",      info.get("grossMargins"),      _fmt_pct(info.get("grossMargins")),       "> 40% ideal",      {"green": (">", 0.40), "red": ("<", 0.10)}),
        _cell("net_margin",    "Net Margin",        info.get("profitMargins"),     _fmt_pct(info.get("profitMargins")),      "> 10% good",       {"green": (">", 0.10), "red": ("<", 0)}),
        _cell("op_cashflow",   "Op. Cash Flow",     info.get("operatingCashflow"), _fmt_large(info.get("operatingCashflow")), "> 0",             {"green": (">", 0),    "red": ("<", 0)}),
    ]
    return {"cells": cells, "score": _health_score(f)}


def _build_growth_grid(f: YFinanceFetcher) -> dict:
    """
    Note: earningsGrowth from yfinance is the trailing twelve-month rate.
    The cell is labelled "Earnings Growth (TTM)" to avoid implying it is
    a multi-year forward estimate.
    """
    info = f.info()
    rps = info.get("revenuePerShare")
    rps_fmt = f"${rps:.2f}" if rps is not None else "N/A"

    cells = [
        _cell("rev_growth",    "Revenue Growth (YoY)",       info.get("revenueGrowth"),             _fmt_pct(info.get("revenueGrowth")),           "> 10% strong", {"green": (">", 0.10), "red": ("<", 0)}),
        _cell("earn_growth",   "Earnings Growth (TTM)",      info.get("earningsGrowth"),            _fmt_pct(info.get("earningsGrowth")),          "> 10%",        {"green": (">", 0.10), "red": ("<", 0)}),
        _cell("earn_qtrly",    "Quarterly EPS Growth",       info.get("earningsQuarterlyGrowth"),   _fmt_pct(info.get("earningsQuarterlyGrowth")), "> 5%",         {"green": (">", 0.05), "red": ("<", 0)}),
        _cell("rev_per_share", "Revenue / Share",            rps,                                   rps_fmt,                                       "Higher → better", {}),
        _cell("free_cf",       "Free Cash Flow",             info.get("freeCashflow"),              _fmt_large(info.get("freeCashflow")),           "> 0",          {"green": (">", 0),    "red": ("<", 0)}),
    ]
    return {"cells": cells, "score": _growth_score(f)}


def _build_score_breakdown(val_score: float, health_score: float, growth_score: float) -> dict:
    """
    Combines three grid scores into a weighted composite.

    Weights:  Valuation 35%, Health 35%, Growth 30%.
    Grades:   A ≥ 75, B ≥ 60, C ≥ 45, D ≥ 30, F < 30.
    These thresholds are calibrated for the continuous 0–100 scoring system.
    A score of 75+ requires strong performance across most metrics; 45–60
    is mixed or sector-dependent.
    """
    bars = [
        {"label": "Valuation",        "score": val_score,    "weight": 0.35, "weighted": round(val_score    * 0.35, 2), "color": "#3B82F6"},
        {"label": "Financial Health", "score": health_score, "weight": 0.35, "weighted": round(health_score * 0.35, 2), "color": "#10B981"},
        {"label": "Growth",           "score": growth_score, "weight": 0.30, "weighted": round(growth_score * 0.30, 2), "color": "#F59E0B"},
    ]
    total = sum(b["weighted"] for b in bars)

    if total >= 75:
        grade = "A"
    elif total >= 60:
        grade = "B"
    elif total >= 45:
        grade = "C"
    elif total >= 30:
        grade = "D"
    else:
        grade = "F"

    return {"bars": bars, "total": round(total, 1), "grade": grade}


def _build_quarterly_trend(f: YFinanceFetcher) -> list[dict]:
    _, q_inc = f.financials()
    if q_inc is None or q_inc.empty:
        return []

    def _get(df: pd.DataFrame, key: str, col: object) -> float | None:
        if not df.empty and key in df.index and col in df.columns:
            return _safe_float(df.loc[key, col])
        return None

    cols = list(q_inc.columns[:8])
    rows: list[dict] = []

    for i, col in enumerate(cols):
        period = str(col.date()) if hasattr(col, "date") else str(col)[:10]
        revenue = _get(q_inc, "Total Revenue", col)
        gross = _get(q_inc, "Gross Profit", col)

        yoy_growth = None
        if i + 4 < len(cols):
            prev_rev = _get(q_inc, "Total Revenue", cols[i + 4])
            if revenue is not None and prev_rev and prev_rev != 0:
                yoy_growth = (revenue - prev_rev) / abs(prev_rev)

        rows.append(
            {
                "period":             period,
                "revenue":            revenue,
                "revenue_growth_yoy": yoy_growth,
                "net_income":         _get(q_inc, "Net Income", col),
                "eps":                _get(q_inc, "Basic EPS", col),
                "eps_surprise_pct":   None,
                "gross_margin":       (gross / revenue) if (gross is not None and revenue) else None,
                "ebitda":             _get(q_inc, "EBITDA", col),
            }
        )

    try:
        eh = f.earnings_history()
        if not eh.empty and "surprisePercent" in eh.columns:
            for row in rows:
                prefix = row["period"][:7]
                match = eh[eh.index.astype(str).str.startswith(prefix)]
                if not match.empty:
                    row["eps_surprise_pct"] = _safe_float(match["surprisePercent"].iloc[0])
    except Exception as exc:
        log.debug("EPS surprise enrichment skipped for %s: %s", f.ticker, exc)

    return rows


def _build_earnings_update(f: YFinanceFetcher) -> dict:
    info = f.info()
    cal = f.calendar()

    next_date = None
    try:
        if isinstance(cal, dict) and (nd := cal.get("Earnings Date")):
            next_date = str(pd.Timestamp(nd[0] if isinstance(nd, list) else nd).date())
    except Exception:
        pass

    eh = f.earnings_history()
    last_report_date = last_eps_actual = last_eps_est = surprise_pct = None

    if not eh.empty:
        last = eh.sort_index(ascending=False).iloc[0]
        last_report_date = (
            str(pd.Timestamp(last.name).date())
            if hasattr(last.name, "date")
            else str(last.name)[:10]
        )
        last_eps_actual = _safe_float(last.get("reportedEPS") or last.get("Reported EPS"))
        last_eps_est = _safe_float(last.get("epsEstimate") or last.get("EPS Estimate"))
        surprise_pct = _safe_float(last.get("surprisePercent") or last.get("Surprise(%)"))

    return {
        "last_report_date":       last_report_date,
        "next_report_date":       next_date,
        "last_eps_actual":        last_eps_actual,
        "last_eps_estimate":      last_eps_est,
        "eps_surprise_pct":       surprise_pct,
        "revenue_actual":         _safe_float(info.get("totalRevenue")),
        "revenue_estimate":       None,
        "revenue_surprise_pct":   None,
        "analyst_count":          _safe_int(info.get("numberOfAnalystOpinions")),
        "forward_pe":             _safe_float(info.get("forwardPE")),
        "forward_eps":            _safe_float(info.get("forwardEps")),
    }


def _build_catalysts_risks(f: YFinanceFetcher, signals: dict) -> list[dict]:
    info = f.info()
    items: list[dict] = []

    rev_growth = _safe_float(info.get("revenueGrowth"))
    if rev_growth is not None and rev_growth > 0.10:
        items.append({
            "type": "catalyst", "label": "Strong Revenue Growth",
            "detail": f"Revenue growing at {rev_growth * 100:.1f}% YoY — above 10% threshold.",
            "severity": "high",
        })

    fcf = _safe_float(info.get("freeCashflow"))
    if fcf is not None and fcf > 0:
        items.append({
            "type": "catalyst", "label": "Positive Free Cash Flow",
            "detail": f"FCF of {_fmt_large(fcf)} supports buybacks, dividends and R&D.",
            "severity": "medium",
        })

    roe = _safe_float(info.get("returnOnEquity"))
    if roe is not None and roe > 0.20:
        items.append({
            "type": "catalyst", "label": "High Return on Equity",
            "detail": f"ROE of {roe * 100:.1f}% indicates efficient capital use.",
            "severity": "medium",
        })

    if signals.get("buy_pct", 0) >= 50:
        items.append({
            "type": "catalyst", "label": "Bullish Technical Setup",
            "detail": f"{signals['buy_pct']}% of active technical indicators signal BUY.",
            "severity": "medium",
        })

    apt = f.analyst_price_targets()
    current = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    high_t = _safe_float(apt.get("high"))
    if high_t is not None and current and high_t > current * 1.15:
        upside = (high_t - current) / current * 100
        items.append({
            "type": "catalyst", "label": "High Analyst Target",
            "detail": f"Highest analyst target ${high_t:.2f} implies {upside:.0f}% upside.",
            "severity": "high",
        })

    # D/E: divide by 100 to convert yfinance percentage scale to actual ratio.
    de_raw = _safe_float(info.get("debtToEquity"))
    de = de_raw / 100.0 if de_raw is not None else None
    if de is not None and de > 2.0:
        items.append({
            "type": "risk", "label": "High Leverage",
            "detail": f"D/E ratio of {de:.2f}× raises refinancing risk in high-rate environments.",
            "severity": "high",
        })

    pe = _safe_float(info.get("trailingPE"))
    if pe is not None and pe > 40:
        items.append({
            "type": "risk", "label": "Elevated Valuation",
            "detail": f"P/E of {pe:.1f}× leaves little margin for earnings disappointment.",
            "severity": "medium",
        })

    cr = _safe_float(info.get("currentRatio"))
    if cr is not None and cr < 1.0:
        items.append({
            "type": "risk", "label": "Liquidity Concern",
            "detail": f"Current ratio of {cr:.2f} — current liabilities exceed current assets.",
            "severity": "high",
        })

    beta = _safe_float(info.get("beta"))
    if beta is not None and beta > 1.5:
        items.append({
            "type": "risk", "label": "High Market Sensitivity",
            "detail": f"Beta of {beta:.2f} — this stock moves ~{beta:.1f}× the market on average.",
            "severity": "medium",
        })

    if signals.get("sell_pct", 0) >= 50:
        items.append({
            "type": "risk", "label": "Bearish Technical Setup",
            "detail": f"{signals['sell_pct']}% of active technical indicators signal SELL.",
            "severity": "medium",
        })

    if not items:
        items.append({
            "type": "catalyst", "label": "No Major Signals Detected",
            "detail": "Insufficient data to identify specific catalysts or risks at this time.",
            "severity": "low",
        })

    return items


def _build_rating_verdict(f: YFinanceFetcher, signals: dict, total_score: float) -> dict:
    info = f.info()
    current = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    target = _safe_float(info.get("targetMeanPrice"))
    upside = ((target - current) / current * 100) if (target and current) else None

    rec = info.get("recommendationKey") or info.get("recommendation") or ""
    mean = _safe_float(info.get("recommendationMean"))

    if rec:
        analyst_rating = rec.replace("_", " ").title()
    elif mean is not None:
        if mean <= 1.5:   analyst_rating = "Strong Buy"
        elif mean <= 2.5: analyst_rating = "Buy"
        elif mean <= 3.5: analyst_rating = "Hold"
        elif mean <= 4.5: analyst_rating = "Sell"
        else:             analyst_rating = "Strong Sell"
    else:
        analyst_rating = "N/A"

    analyst_breakdown = None
    try:
        rec_df = f.recommendations()
        if not rec_df.empty:
            row = rec_df.iloc[0]
            strong_buy  = _safe_int(row.get("strongBuy", 0)) or 0
            buy         = _safe_int(row.get("buy", 0)) or 0
            hold        = _safe_int(row.get("hold", 0)) or 0
            sell        = _safe_int(row.get("sell", 0)) or 0
            strong_sell = _safe_int(row.get("strongSell", 0)) or 0
            total_a = strong_buy + buy + hold + sell + strong_sell
            if total_a > 0:
                analyst_breakdown = {
                    "strong_buy": strong_buy, "buy": buy, "hold": hold,
                    "sell": sell, "strong_sell": strong_sell, "total": total_a,
                }
    except Exception as exc:
        log.warning("Analyst breakdown parse error [%s]: %s", f.ticker, exc)

    signal_bars = [
        {"label": "BUY",  "pct": signals.get("buy_pct", 33),  "color": "#10B981"},
        {"label": "HOLD", "pct": signals.get("hold_pct", 34), "color": "#F59E0B"},
        {"label": "SELL", "pct": signals.get("sell_pct", 33), "color": "#EF4444"},
    ]

    if total_score >= 75 and upside is not None and upside > 15:
        verdict, confidence = (
            f"Strong fundamentals with {upside:.0f}% analyst upside. Consider for long positions.",
            "High",
        )
    elif total_score >= 60:
        verdict, confidence = "Solid fundamentals. Monitor for entry points aligned with technical signals.", "Medium"
    elif total_score >= 45:
        verdict, confidence = "Mixed signals. Caution warranted; wait for clearer catalysts before committing.", "Medium"
    else:
        verdict, confidence = "Weak fundamentals and/or elevated risk. High caution advised.", "Low"

    return {
        "analyst_rating":      analyst_rating,
        "analyst_target_price": target,
        "upside_pct":          upside,
        "analyst_breakdown":   analyst_breakdown,
        "signal_bars":         signal_bars,
        "verdict":             verdict,
        "confidence":          confidence,
    }


# ── Insider activity ─────────────────────────────────────────────────────────


def _build_insider_activity(f: YFinanceFetcher) -> dict:
    """
    Derives 6-month insider buy/sell counts and share totals from
    yfinance.insider_purchases (no HTTP calls — TTL-cached DataFrame).
    Returns None for each field when the data is unavailable (e.g. ETFs).
    """
    df = f.insider_purchases()
    empty = {"buy_count": None, "sell_count": None,
             "buy_shares": None, "sell_shares": None, "net_shares": None}
    if df.empty or df.shape[1] < 3:
        return empty

    label_col = df.columns[0]

    def _row(label: str) -> pd.Series | None:
        rows = df[df[label_col] == label]
        return rows.iloc[0] if not rows.empty else None

    buys  = _row("Purchases")
    sells = _row("Sales")
    net   = _row("Net Shares Purchased (Sold)")

    return {
        "buy_count":  _safe_int(buys["Trans"])   if buys  is not None else None,
        "sell_count": _safe_int(sells["Trans"])  if sells is not None else None,
        "buy_shares": _safe_int(buys["Shares"])  if buys  is not None else None,
        "sell_shares":_safe_int(sells["Shares"]) if sells is not None else None,
        "net_shares": _safe_int(net["Shares"])   if net   is not None else None,
    }


# ── Public entry points ───────────────────────────────────────────────────────


def build_report(ticker: str, user_positions: list | None = None) -> dict:
    """
    Builds the complete ticker page payload.

    user_positions (BUY/SELL/DIVIDEND transactions for this ticker) are injected
    by the router from the database layer so this function stays user-context-free.
    """
    f = YFinanceFetcher(ticker)
    hist = f.history(period="1y", interval="1d")

    val_grid    = _build_valuation_grid(f)
    health_grid = _build_health_grid(f)
    growth_grid = _build_growth_grid(f)
    score       = _build_score_breakdown(val_grid["score"], health_grid["score"], growth_grid["score"])
    signals     = compute_signals(hist)

    return {
        "stock_bar":        _build_stock_bar(f, hist, signals),
        "risk_gauge":       compute_risk(f, hist),
        "kpi_strip":        _build_kpi_strip(f),
        "valuation_grid":   val_grid,
        "health_grid":      health_grid,
        "growth_grid":      growth_grid,
        "score_breakdown":  score,
        "quarterly_trend":  _build_quarterly_trend(f),
        "earnings_update":  _build_earnings_update(f),
        "catalysts_risks":  _build_catalysts_risks(f, signals),
        "rating_verdict":   _build_rating_verdict(f, signals, score["total"]),
        "news":             fetch_news(ticker, limit=10),
        "user_positions":   user_positions or [],
        "signals":           signals,
        "sector_weightings": f.sector_weightings(),
        "insider_activity":  _build_insider_activity(f),
    }


def build_price_chart_for_period(ticker: str, yf_period: str, yf_interval: str) -> dict:
    """Thin wrapper used by the /chart endpoint."""
    f = YFinanceFetcher(ticker)
    hist = f.history(period=yf_period, interval=yf_interval)
    return _build_price_chart(f, hist)