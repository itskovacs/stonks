"""
Projection Engine
=================
Pure math — no database or network dependency.

Formulas
--------
Lump-sum compound growth (Future Value):
    FV_lump = PV × (1 + r_p)^p

Future Value of an ordinary annuity (end-of-period deposits):
    FVA = PMT × ((1 + r_p)^p − 1) / r_p    when r_p > 0
    FVA = PMT × p                             when r_p = 0  (avoids division by zero)

Total projected value:
    FV = FV_lump + FVA

Compounding frequency equals deposit frequency so that the periodic rate r_p
and the deposit interval are always aligned
(e.g. monthly deposits → r_p = annual_rate / 12).

Projection horizon is always 25 years.
"""

_PROJECTION_YEARS = 25

_PERIODS_PER_YEAR: dict[str, int] = {
    "monthly":   12,
    "quarterly":  4,
    "annually":   1,
}

# Year checkpoints surfaced as summary cards in the response.
# 25 is always the final year, so it is included explicitly.
_MILESTONE_YEARS = (1, 2, 5, 10, 20, 25)


def _fv_lump(pv: float, r_p: float, p: int) -> float:
    """Future value of a lump sum after p compounding periods."""
    return pv * (1.0 + r_p) ** p


def _fv_annuity(pmt: float, r_p: float, p: int) -> float:
    """Future value of an ordinary annuity after p periods."""
    if r_p == 0.0:
        return pmt * p
    return pmt * ((1.0 + r_p) ** p - 1.0) / r_p


def build_projection(
    initial_balance: float,
    initial_invested: float,
    deposit: float,
    annual_rate_pct: float,
    frequency: str = "monthly",
) -> dict:
    """
    Returns a Chart.js-compatible 25-year projection payload.

    `deposit` is the per-period amount matching `frequency`
    (e.g. a monthly amount when frequency="monthly").
    `annual_rate_pct` is the expected annual return in percent (7.0 → 7 %).
    """
    n   = _PERIODS_PER_YEAR[frequency]
    r_p = (annual_rate_pct / 100.0) / n

    labels:         list[str]   = []
    principal_data: list[float] = []
    interest_data:  list[float] = []

    for yr in range(_PROJECTION_YEARS + 1):
        p         = yr * n
        fv        = _fv_lump(initial_balance, r_p, p) + _fv_annuity(deposit, r_p, p)
        principal = round(initial_invested + deposit * p, 2)
        interest  = round(max(fv - principal, 0.0), 2)

        labels.append(f"Year {yr}")
        principal_data.append(principal)
        interest_data.append(interest)

    total_periods   = _PROJECTION_YEARS * n
    final_fv        = round(
        _fv_lump(initial_balance, r_p, total_periods)
        + _fv_annuity(deposit, r_p, total_periods),
        2,
    )
    total_deposited = round(initial_invested + deposit * total_periods, 2)

    milestones = []
    for my in _MILESTONE_YEARS:
        p         = my * n
        fv        = round(_fv_lump(initial_balance, r_p, p) + _fv_annuity(deposit, r_p, p), 2)
        deposited = round(initial_invested + deposit * p, 2)
        milestones.append({
            "year":             my,
            "total_value":      fv,
            "total_deposited":  deposited,
            "interest_earned":  round(fv - deposited, 2),
        })

    return {
        "chart_data": {
            "labels": labels,
            "datasets": [
                {"label": "Invested",  "data": principal_data},
                {"label": "Interest", "data": interest_data},
            ],
        },
        "milestones": milestones,
        "summary": {
            "final_value":          final_fv,
            "total_deposited":      total_deposited,
            "total_interest":       round(final_fv - total_deposited, 2),
            "effective_multiplier": round(final_fv / total_deposited, 2) if total_deposited else None,
        },
        "inputs": {
            "initial_balance":   initial_balance,
            "deposit":           deposit,
            "annual_rate_pct":   annual_rate_pct,
            "deposit_frequency": frequency,
        },
    }
