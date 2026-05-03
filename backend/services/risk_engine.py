"""
Risk Scoring Engine
===================
Produces a 0–100 composite risk score (higher = riskier) from six independent
sub-components, each grounded in quantitative finance methodology.

Sub-components (all normalised 0–100 via linear mapping):
1. Volatility   — Annualised σ of daily log-returns
2. Leverage     — Debt/Equity ratio + Interest Coverage
3. Valuation    — P/E (TTM) + P/B vs sector-aware fair-value bands
4. Liquidity    — Current Ratio + Quick Ratio
5. Earnings     — EPS miss frequency + trailing revenue growth
6. Market Beta  — Systematic β relative to the market

Missing data defaults to 50.0 (neutral) so that a single missing input
never drives the score to an extreme in either direction.

yfinance D/E scale note
-----------------------
yfinance returns `debtToEquity` multiplied by 100 (i.e. as a percentage).
For example, a D/E of 1.5× is returned as 150.0.  All risk engine code
divides by 100 before comparing against the thresholds in _SECTOR_THRESHOLDS,
which are expressed as actual ratios (e.g. de_risky=4.0 means 4.0× D/E).
The health grid in report_builder.py applies the same normalisation.
"""

import numpy as np
import pandas as pd

from services.fetcher import YFinanceFetcher

_SQRT_252: float = np.sqrt(252)

# ── Weight table (must sum to 1.0) ────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "volatility":  0.25,
    "leverage":    0.20,
    "valuation":   0.20,
    "liquidity":   0.15,
    "earnings":    0.10,
    "market_beta": 0.10,
}

# Sector-aware thresholds for leverage and valuation.
# de_safe / de_risky are actual D/E ratios (not the ×100 yfinance value).
_SECTOR_THRESHOLDS: dict[str, dict[str, float]] = {
    "Financial Services": {"de_safe": 5.0,  "de_risky": 15.0, "pe_safe": 12.0, "pe_risky": 25.0, "cr_safe": 1.1, "cr_risky": 0.5},
    "Utilities":          {"de_safe": 1.0,  "de_risky": 6.0,  "pe_safe": 15.0, "pe_risky": 35.0, "cr_safe": 1.0, "cr_risky": 0.5},
    "Real Estate":        {"de_safe": 1.0,  "de_risky": 8.0,  "pe_safe": 20.0, "pe_risky": 60.0, "cr_safe": 1.0, "cr_risky": 0.4},
    "Technology":         {"de_safe": 0.0,  "de_risky": 3.0,  "pe_safe": 25.0, "pe_risky": 80.0, "cr_safe": 2.0, "cr_risky": 0.8},
    "Healthcare":         {"de_safe": 0.0,  "de_risky": 3.0,  "pe_safe": 20.0, "pe_risky": 70.0, "cr_safe": 2.0, "cr_risky": 0.8},
    "_default":           {"de_safe": 0.0,  "de_risky": 4.0,  "pe_safe": 15.0, "pe_risky": 50.0, "cr_safe": 2.0, "cr_risky": 0.8},
}

_COMPONENT_LABELS: dict[str, str] = {
    "volatility":  "price volatility",
    "leverage":    "financial leverage",
    "valuation":   "valuation multiples",
    "liquidity":   "liquidity ratios",
    "earnings":    "earnings consistency",
    "market_beta": "market sensitivity (β)",
}


def _thresholds(fetcher: YFinanceFetcher) -> dict[str, float]:
    sector = fetcher.get("sector") or ""
    return _SECTOR_THRESHOLDS.get(str(sector), _SECTOR_THRESHOLDS["_default"])


# ── Maths helpers ─────────────────────────────────────────────────────────────


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamps v to [lo, hi].  Returns 50.0 if v is NaN."""
    if np.isnan(v):
        return 50.0
    return max(lo, min(hi, v))


def _norm_linear(value: float, safe_val: float, risky_val: float) -> float:
    """
    Linear normalisation: safe_val → 0 (low risk), risky_val → 100 (high risk).
    Works for both ascending risk (D/E — higher is riskier) and
    descending risk (Current Ratio — lower is riskier).
    Result is clamped to [0, 100].
    """
    if risky_val == safe_val:
        return 0.0
    return _clamp(((value - safe_val) / (risky_val - safe_val)) * 100.0)


# ── Sub-component scorers ─────────────────────────────────────────────────────


def _volatility_risk(hist: pd.DataFrame) -> float:
    """
    Annualised σ from daily log-returns (252-day convention).
    σ < 15% → 0 (low risk)   σ > 60% → 100 (extreme risk).
    """
    if hist.empty or "Close" not in hist.columns:
        return 50.0
    log_ret = np.log1p(hist["Close"].pct_change()).dropna()
    if len(log_ret) < 20:
        return 50.0
    sigma_annual = float(log_ret.std(ddof=1)) * _SQRT_252 * 100
    return _norm_linear(sigma_annual, safe_val=15.0, risky_val=60.0)


def _leverage_risk(fetcher: YFinanceFetcher, t: dict[str, float]) -> float:
    """
    D/E ratio  : 0 → safe, > risky_threshold → very risky  (weight 60%)
    Int. Cover.: > 5× → safe, < 1× → very risky             (weight 40%)

    yfinance returns debtToEquity × 100; divide by 100 to get the actual ratio.
    """
    de_raw = fetcher.get_float("debtToEquity")
    ic = fetcher.get_float("interestCoverage")

    de = de_raw / 100.0 if de_raw is not None else None
    de_score = _norm_linear(de, t["de_safe"], t["de_risky"]) if (de is not None and de >= 0) else 50.0
    ic_score = _norm_linear(ic, safe_val=5.0, risky_val=1.0) if ic is not None else 50.0

    return _clamp(0.6 * de_score + 0.4 * ic_score)


def _valuation_risk(fetcher: YFinanceFetcher, t: dict[str, float]) -> float:
    """
    P/E TTM: fair→ safe, high → risky.  Loss-making (PE ≤ 0) → 80 risk score
             because there is no earnings floor anchoring the valuation.  (weight 60%)
    P/B    : 1 → safe, > 10 → risky.                                     (weight 40%)
    """
    pe = fetcher.get_float("trailingPE")
    pb = fetcher.get_float("priceToBook")

    if pe is not None:
        if pe > 0:
            pe_score = _norm_linear(pe, t["pe_safe"], t["pe_risky"])
        else:
            # Loss-making company: no P/E anchor → elevated valuation risk.
            pe_score = 80.0
    else:
        pe_score = 50.0

    pb_score = _norm_linear(pb, safe_val=1.0, risky_val=10.0) if (pb is not None and pb > 0) else 50.0

    return _clamp(0.6 * pe_score + 0.4 * pb_score)


def _liquidity_risk(fetcher: YFinanceFetcher, t: dict[str, float]) -> float:
    """
    Current Ratio: > cr_safe → safe, < cr_risky → very risky (weight 55%)
    Quick Ratio  : > 1.5 → safe, < 0.5 → very risky          (weight 45%)
    """
    cr = fetcher.get_float("currentRatio")
    qr = fetcher.get_float("quickRatio")
    cr_score = _norm_linear(cr, t["cr_safe"], t["cr_risky"]) if cr is not None else 50.0
    qr_score = _norm_linear(qr, safe_val=1.5, risky_val=0.5) if qr is not None else 50.0
    return _clamp(0.55 * cr_score + 0.45 * qr_score)


def _earnings_risk(fetcher: YFinanceFetcher) -> float:
    """
    EPS miss ratio   : frequency of negative earnings surprises (weight 60%)
    Revenue growth   : > +15% YoY → low risk, < −10% YoY → high risk (weight 40%)
    """
    hist = fetcher.earnings_history()
    miss_ratio = 0.5  # neutral default when history unavailable

    surprise_col = next(
        (c for c in hist.columns if "surprise" in c.lower()),
        None,
    )
    if not hist.empty and surprise_col:
        surprises = hist[surprise_col].dropna()
        if not surprises.empty:
            miss_ratio = float((surprises < 0).sum()) / len(surprises)

    rev_growth = fetcher.get_float("revenueGrowth")
    rg_score = _norm_linear(rev_growth, safe_val=0.15, risky_val=-0.10) if rev_growth is not None else 50.0

    return _clamp(0.6 * (miss_ratio * 100.0) + 0.4 * rg_score)


def _beta_risk(fetcher: YFinanceFetcher) -> float:
    """
    β < 0.8 → low systematic risk → 0
    β > 2.0 → high systematic risk → 100
    β < 0   → counter-cyclical / inverse ETF: treated as moderate (40) rather
              than neutral (50) because negative-beta assets still carry
              significant idiosyncratic and structural risks.
    """
    beta = fetcher.get_float("beta")
    if beta is None:
        return 50.0
    if beta < 0:
        return 40.0
    return _norm_linear(beta, safe_val=0.8, risky_val=2.0)


# ── Formatting helpers ────────────────────────────────────────────────────────


def _label(score: float) -> str:
    if score < 25:
        return "LOW"
    if score < 50:
        return "MODERATE"
    if score < 75:
        return "HIGH"
    return "VERY HIGH"


def _explanation(score: float, components: dict[str, float]) -> str:
    worst = max(components, key=lambda k: components[k])
    best = min(components, key=lambda k: components[k])
    return (
        f"Composite risk score: {score:.1f}/100. "
        f"Primary risk driver: {_COMPONENT_LABELS[worst]} ({components[worst]:.0f}/100). "
        f"Most favourable factor: {_COMPONENT_LABELS[best]} ({components[best]:.0f}/100)."
    )


# ── Public entry point ────────────────────────────────────────────────────────


def compute_risk(fetcher: YFinanceFetcher, hist: pd.DataFrame) -> dict:
    """
    Computes the composite risk score for a ticker.

    Parameters
    ----------
    fetcher : YFinanceFetcher   — cached data accessor
    hist    : pd.DataFrame      — OHLCV history (at least 20 rows for volatility)

    Returns
    -------
    {
        "score":       float,            # 0–100 (higher = riskier)
        "label":       str,              # LOW | MODERATE | HIGH | VERY HIGH
        "components":  dict[str, float], # individual sub-scores
        "explanation": str
    }
    """
    t = _thresholds(fetcher)
    components: dict[str, float] = {
        "volatility":  _volatility_risk(hist),
        "leverage":    _leverage_risk(fetcher, t),
        "valuation":   _valuation_risk(fetcher, t),
        "liquidity":   _liquidity_risk(fetcher, t),
        "earnings":    _earnings_risk(fetcher),
        "market_beta": _beta_risk(fetcher),
    }
    score = _clamp(sum(components[k] * WEIGHTS[k] for k in components))

    return {
        "score":       round(score, 1),
        "label":       _label(score),
        "components":  {k: round(v, 1) for k, v in components.items()},
        "explanation": _explanation(score, components),
    }
