"""
schemas.py
==========
All API request/response shapes (pure Pydantic, no SQLAlchemy).
Database models live in models/models.py.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# =============================================================================
# ENUMS
# =============================================================================


class TransactionType(StrEnum):
    BUY      = "BUY"
    SELL     = "SELL"
    DEPOSIT  = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    DIVIDEND = "DIVIDEND"


# =============================================================================
# AUTH
# =============================================================================


class Token(BaseModel):
    access_token: str
    refresh_token: str


class LoginRegisterModel(BaseModel):
    username: Annotated[
        str,
        StringConstraints(min_length=1, max_length=19, pattern=r"^[a-zA-Z0-9_-]+$"),
    ]
    password: str


# =============================================================================
# REQUESTS
# =============================================================================


class TickerRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)

    @field_validator("ticker")
    @classmethod
    def normalize(cls, v: str) -> str:
        return v.strip().upper()


class EnvelopeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()


class TransactionRequest(BaseModel):
    date: datetime | None = None
    type: TransactionType
    ticker: str | None = Field(default=None, max_length=20)
    shares: float | None = Field(default=None, ge=0)
    price: float = Field(ge=0)
    fees: float = Field(default=0.0, ge=0)
    envelope_name: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("type", mode="before")
    @classmethod
    def normalize_type(cls, v: object) -> object:
        return str(v).upper() if isinstance(v, str) else v

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else None

    @field_validator("envelope_name")
    @classmethod
    def strip_envelope(cls, v: str) -> str:
        return v.strip()


# =============================================================================
# STOCK REPORT — SUB-MODELS
# =============================================================================


class StockBar(BaseModel):
    ticker: str
    company_name: str
    sector: str
    industry: str
    current_price: float
    change_1d: float
    change_1d_pct: float
    change_ytd_pct: float
    market_cap: float | None
    volume: int | None
    avg_volume: int | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    beta: float | None
    currency: str
    rsi_14: float | None
    sma_50: float | None
    sma_200: float | None


class RiskGauge(BaseModel):
    score: float
    label: str
    components: dict[str, float]
    explanation: str


class KPI(BaseModel):
    key: str
    label: str
    value: float | None
    formatted: str
    unit: str


class PricePoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class ClosePricePoint(BaseModel):
    """Lightweight close-only price point for sparklines and watchlist charts."""
    date: str
    close: float


class ChartAnnotation(BaseModel):
    date: str
    label: str
    type: str


class PriceChart(BaseModel):
    prices: list[PricePoint]
    annotations: list[ChartAnnotation]


class PriceChartResponse(BaseModel):
    ticker: str
    period: str
    interval: str
    prices: list[PricePoint]
    annotations: list[ChartAnnotation]


class GridCell(BaseModel):
    key: str
    label: str
    value: float | None
    formatted: str
    status: str
    benchmark: str


class ValuationGrid(BaseModel):
    cells: list[GridCell]
    score: float


class HealthGrid(BaseModel):
    cells: list[GridCell]
    score: float


class GrowthGrid(BaseModel):
    cells: list[GridCell]
    score: float


class ScoreBar(BaseModel):
    label: str
    score: float
    weight: float
    weighted: float
    color: str


class ScoreBreakdown(BaseModel):
    bars: list[ScoreBar]
    total: float
    grade: str


class QuarterRow(BaseModel):
    period: str
    revenue: float | None
    revenue_growth_yoy: float | None
    net_income: float | None
    eps: float | None
    eps_surprise_pct: float | None
    gross_margin: float | None
    ebitda: float | None


class EarningsUpdate(BaseModel):
    last_report_date: str | None
    next_report_date: str | None
    last_eps_actual: float | None
    last_eps_estimate: float | None
    eps_surprise_pct: float | None
    revenue_actual: float | None
    revenue_estimate: float | None
    revenue_surprise_pct: float | None
    analyst_count: int | None
    forward_pe: float | None
    forward_eps: float | None


class StockSignals(BaseModel):
    signals: dict[str, str]
    raw_values: dict[str, float | dict]
    buy_pct: int
    hold_pct: int
    sell_pct: int
    summary: str


class CatalystRisk(BaseModel):
    type: str
    label: str
    detail: str
    severity: str


class SignalBar(BaseModel):
    label: str
    pct: float
    color: str


class AnalystBreakdown(BaseModel):
    strong_buy: int
    buy: int
    hold: int
    sell: int
    strong_sell: int
    total: int


class RatingVerdict(BaseModel):
    analyst_rating: str | None
    analyst_target_price: float | None
    upside_pct: float | None
    analyst_breakdown: AnalystBreakdown | None = None
    signal_bars: list[SignalBar]
    verdict: str
    confidence: str


class NewsItem(BaseModel):
    title: str
    source: str
    published: str
    url: str
    summary: str | None
    sentiment: str


class WacPosition(BaseModel):
    """WAC position for one envelope holding the viewed ticker."""
    envelope_name:  str
    envelope_color: str
    shares:         float
    avg_cost:       float
    cost_basis:     float


class InsiderActivity(BaseModel):
    """Buy/sell counts and share totals from yfinance insider_purchases (last 6 months)."""
    buy_count:   int | None
    sell_count:  int | None
    buy_shares:  int | None
    sell_shares: int | None
    net_shares:  int | None


class UserPosition(BaseModel):
    """A single transaction belonging to the viewed ticker, from the current user's ledger."""
    id: int
    date: datetime
    type: TransactionType
    shares: float
    price: float
    fees: float
    total: float
    envelope_name: str


class StockReport(BaseModel):
    stock_bar: StockBar
    risk_gauge: RiskGauge
    kpi_strip: list[KPI]
    valuation_grid: ValuationGrid
    health_grid: HealthGrid
    growth_grid: GrowthGrid
    score_breakdown: ScoreBreakdown
    quarterly_trend: list[QuarterRow]
    earnings_update: EarningsUpdate
    catalysts_risks: list[CatalystRisk]
    rating_verdict: RatingVerdict
    news: list[NewsItem]
    user_positions:  list[UserPosition]  = Field(default_factory=list)
    wac_by_envelope: list[WacPosition]   = Field(default_factory=list)
    signals: StockSignals
    sector_weightings: dict[str, float]
    insider_activity: InsiderActivity
    in_watchlist: bool = False

# =============================================================================
# PROFILE / PORTFOLIO
# =============================================================================


class TransactionOut(BaseModel):
    """
    Serialized transaction for API responses.
    envelope_name is derived from the envelope relationship at read time
    (not stored in the Transaction table).
    """
    id: int
    user: str
    date: datetime
    type: TransactionType
    ticker: str | None
    shares: float
    price: float
    fees: float
    total: float
    envelope_name: str
    note: str | None = None


class EnvelopeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    color: str
    cash_available: float


# =============================================================================
# DASHBOARD
# =============================================================================


class WatchlistRow(BaseModel):
    ticker: str
    name: str
    current_price: float
    prev_close: float
    change_1d: float
    change_1d_pct: float
    sector: str | None
    currency: str
    history_7d: list[ClosePricePoint]


class PositionRow(BaseModel):
    ticker: str
    # Company or ETF full name sourced from yfinance shortName / longName.
    # None when yfinance returns no name for the ticker (e.g. delisted,
    # regional instruments, or a transient fetch error).
    name: str | None
    envelope_name: str
    currency: str
    shares: float
    avg_cost: float
    cost_basis: float
    current_price: float
    current_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    # Allocation as % of TOTAL portfolio value (equity + cash).
    # The sum of all allocation_pcts will be < 100% when cash is held,
    # which is correct — it shows how much of the portfolio is deployed.
    allocation_pct: float
    change_1d: float
    change_1d_pct: float


class EnvelopeSummary(BaseModel):
    id: int
    name: str
    color: str
    cash_available: float
    total_value: float


class DashboardTotals(BaseModel):
    total_value: float
    total_cash: float
    total_cost_basis: float
    total_pnl: float
    total_pnl_pct: float
    total_change_1d: float
    total_change_1d_pct: float
    # Net cash deposited (DEPOSIT minus WITHDRAW) per trailing window.
    # Keys: 30d, 90d, 180d, 1y, 5y. Does NOT include BUY capital.
    net_deposits: dict[str, float]
    # Total dividend income received in trailing 90 days.
    dividend_income_90d: float


class DashboardResponse(BaseModel):
    watchlist: list[WatchlistRow]
    positions: list[PositionRow]
    envelopes: list[EnvelopeSummary]
    transactions: list[TransactionOut]
    totals: DashboardTotals


# =============================================================================
# PORTFOLIO OVERVIEW
# =============================================================================


class EnvelopeEvent(BaseModel):
    date: str
    type: TransactionType
    ticker: str | None
    envelope_name: str
    amount: float
    shares: float


class EnvelopeSeriesLine(BaseModel):
    name: str
    color: str
    values: list[float]


class EnvelopeDayStats(BaseModel):
    date: str
    change: float
    change_pct: float


class EnvelopeStats(BaseModel):
    period_days: int
    net_deposits: float
    dividend_income: float
    tickers_held: int
    volatility_annualized_pct: float | None
    best_day: EnvelopeDayStats | None
    worst_day: EnvelopeDayStats | None
    trades_count: int


class EnvelopeOverviewResponse(BaseModel):
    period: str
    dates: list[str]
    series: list[EnvelopeSeriesLine]
    events: list[EnvelopeEvent]
    stats: EnvelopeStats


# =============================================================================
# TICKER SEARCH
# =============================================================================


class TickerSearchResult(BaseModel):
    """Enriched ticker result returned by the watchlist search endpoint."""
    ticker: str
    name: str
    # Current market price ("value" in the API contract)
    value: float
    # Daily price change as a percentage
    change_1d_pct: float
    currency: str
    exchange: str | None
    # Instrument category: EQUITY, ETF, MUTUALFUND, INDEX
    quote_type: str
