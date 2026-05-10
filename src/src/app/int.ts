// =============================================================================
// int.ts — TypeScript interfaces for the STONKS backend API
// Generated from schemas.py — 1:1 mapping, no fields omitted or hallucinated.
// snake_case preserved to match JSON keys returned by FastAPI.
// =============================================================================

// ---------------------------------------------------------------------------
// ENUMS
// ---------------------------------------------------------------------------

export type SignalValue = 'BUY' | 'HOLD' | 'SELL';
export type TransactionType = 'BUY' | 'SELL' | 'DEPOSIT' | 'WITHDRAW' | 'DIVIDEND';
export type DepositPeriod = '30d' | '90d' | '180d' | '1y' | '5y';

// ---------------------------------------------------------------------------
// AUTH
// ---------------------------------------------------------------------------

export interface Token {
    access_token: string;
    refresh_token: string;
}

export interface LoginRegisterRequest {
    username: string; // 1–19 chars, [a-zA-Z0-9_-]
    password: string;
}

export interface RefreshTokenRequest {
    refresh_token: string;
}

export interface AuthParams {
    register_enabled: boolean;
}

// ---------------------------------------------------------------------------
// REQUESTS (sent to the API — match TransactionRequest / EnvelopeRequest)
// ---------------------------------------------------------------------------

export interface TickerRequest {
    ticker: string;
}

export interface EnvelopeRequest {
    name: string;
    color?: string | null;
}

export interface TransactionRequest {
    date?: string | null; // ISO-8601 datetime string or null → defaults to now
    type: TransactionType;
    ticker?: string | null;
    shares?: number | null;
    price: number;
    fees?: number; // defaults to 0
    envelope_name: string;
    note?: string | null;
}

export interface UserSettingsRequest {
    currency?: string | null; // ISO 4217, e.g. "USD" — null to leave unchanged
    apprise_url?: string | null; // comma-separated Apprise URLs — null to leave unchanged
}

export interface UserSettingsOut {
    currency: string | null;
    apprise_url: string | null;
}

export interface AlertRequest {
    ticker: string; // normalized to uppercase by the API
    target_price: number; // must be > 0
    trigger_above: boolean;
}

export interface AlertUpdateRequest {
    target_price?: number; // at least one of these two must be provided
    trigger_above?: boolean;
}

export interface AlertOut {
    id: number;
    ticker: string;
    target_price: number;
    trigger_above: boolean;
    is_armed: boolean;
    last_triggered: string | null; // YYYY-MM-DD date string or null
}

// ---------------------------------------------------------------------------
// GENERIC MUTATION RESPONSES
// Returned by: watchlist/add, watchlist/remove, envelopes/add,
//              PUT envelopes/{id}, DELETE envelopes/{id},
//              POST transactions
// ---------------------------------------------------------------------------

export interface MutationResponse {
    status: 'success';
    message?: string;
    /** Present on POST /transactions — the newly created transaction */
    transaction?: TransactionOut;
    /** Present on watchlist/add and watchlist/remove */
    watchlist?: string[];
    ticker?: WatchlistRow;
}

// ---------------------------------------------------------------------------
// STOCK REPORT — sub-models
// ---------------------------------------------------------------------------

export interface StockBar {
    ticker: string;
    name: string;
    sector: string;
    industry: string;
    current_price: number;
    change_1d: number;
    change_1d_pct: number;
    change_ytd_pct: number;
    market_cap: number | null;
    volume: number | null;
    avg_volume: number | null;
    fifty_two_week_high: number | null;
    fifty_two_week_low: number | null;
    beta: number | null;
    currency: string;
    rsi_14: number | null;
    sma_50: number | null;
    sma_200: number | null;
    pre_market_price: number | null;
    pre_market_change: number | null;
    pre_market_change_pct: number | null;
}

export interface RiskGauge {
    score: number; // 0–100, higher = riskier
    label: string; // LOW | MODERATE | HIGH | VERY HIGH
    components: Record<string, number>;
    explanation: string;
}

export interface KPI {
    key: string;
    label: string;
    value: number | null;
    formatted: string;
    unit: string;
}

export interface PricePoint {
    date: string; // YYYY-MM-DD
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
}

/** Lightweight close-only point used in sparklines and watchlist charts. */
export interface ClosePricePoint {
    date: string;
    close: number;
}

export interface ChartAnnotation {
    date: string; // YYYY-MM-DD
    label: string;
    type: string; // high52 | low52 | dividend | split | earnings
}

export interface PriceChart {
    prices: PricePoint[];
    annotations: ChartAnnotation[];
}

export interface PriceChartResponse {
    ticker: string;
    period: string;
    interval: string;
    prices: PricePoint[];
    annotations: ChartAnnotation[];
}

export interface GridCell {
    key: string;
    label: string;
    value: number | null;
    formatted: string;
    status: string; // green | amber | red | neutral
    benchmark: string;
}

export interface ValuationGrid {
    cells: GridCell[];
    score: number; // 0–100, value-normalised
}

export interface HealthGrid {
    cells: GridCell[];
    score: number;
}

export interface GrowthGrid {
    cells: GridCell[];
    score: number;
}

export interface ScoreBar {
    label: string;
    score: number;
    weight: number;
    weighted: number;
    color: string; // hex
}

export interface ScoreBreakdown {
    bars: ScoreBar[];
    total: number; // 0–100 weighted composite
    grade: string; // A | B | C | D | F
}

export interface QuarterRow {
    period: string; // YYYY-MM-DD (quarter end)
    revenue: number | null;
    revenue_growth_yoy: number | null;
    net_income: number | null;
    eps: number | null;
    eps_surprise_pct: number | null;
    gross_margin: number | null;
    ebitda: number | null;
}

export interface EarningsUpdate {
    last_report_date: string | null;
    next_report_date: string | null;
    last_eps_actual: number | null;
    last_eps_estimate: number | null;
    eps_surprise_pct: number | null;
    revenue_actual: number | null;
    revenue_estimate: number | null;
    revenue_surprise_pct: number | null;
    analyst_count: number | null;
    forward_pe: number | null;
    forward_eps: number | null;
}

export interface CatalystRisk {
    type: string; // catalyst | risk
    label: string;
    detail: string;
    severity: string; // high | medium | low
}

export interface SignalBar {
    label: string; // BUY | HOLD | SELL
    pct: number;
    color: string; // hex
}

export interface AnalystBreakdown {
    strong_buy: number;
    buy: number;
    hold: number;
    sell: number;
    strong_sell: number;
    total: number;
}

export interface RatingVerdict {
    analyst_rating: string | null;
    analyst_target_price: number | null;
    upside_pct: number | null;
    analyst_breakdown: AnalystBreakdown | null;
    signal_bars: SignalBar[];
    verdict: string;
    confidence: string; // High | Medium | Low
}

export interface NewsItem {
    title: string;
    source: string;
    published: string; // ISO-8601 UTC datetime string
    url: string;
    summary: string | null;
    sentiment: string; // positive | negative | neutral
}

/** A single user transaction for the ticker being viewed in the stock report. */
export interface UserPosition {
    id: number;
    date: string; // ISO-8601 datetime string
    type: TransactionType;
    shares: number;
    price: number;
    fees: number;
    total: number;
    envelope_name: string;
}

export interface StockSignals {
    signals: Partial<{
        'RSI (14)': SignalValue;
        'MACD (12/26/9)': SignalValue;
        'Bollinger %B': SignalValue;
        'SMA 50/200': SignalValue;
        'Stochastic %K/%D': SignalValue;
        'Volume Trend': SignalValue;
    }>;
    raw_values: Partial<{
        'RSI (14)': number;
        'Bollinger %B': number;
        'SMA 50/200': { sma_50: number; sma_200: number };
        'Stochastic %K/%D': number;
        'Volume Trend': number;
    }>;
    buy_pct: number;
    hold_pct: number;
    sell_pct: number;
    summary: string;
}

export interface WacPosition {
    envelope_name: string;
    envelope_color: string;
    shares: number;
    avg_cost: number;
    cost_basis: number;
}

/** Buy/sell transaction counts and share totals from yfinance insider_purchases (last 6 months). */
export interface InsiderActivity {
    buy_count: number | null;
    sell_count: number | null;
    buy_shares: number | null;
    sell_shares: number | null;
    net_shares: number | null;
}

export interface StockReport {
    stock_bar: StockBar;
    risk_gauge: RiskGauge;
    kpi_strip: KPI[];
    valuation_grid: ValuationGrid;
    health_grid: HealthGrid;
    growth_grid: GrowthGrid;
    score_breakdown: ScoreBreakdown;
    quarterly_trend: QuarterRow[];
    earnings_update: EarningsUpdate;
    catalysts_risks: CatalystRisk[];
    rating_verdict: RatingVerdict;
    news: NewsItem[];
    user_positions: UserPosition[];
    wac_by_envelope: WacPosition[];
    signals: StockSignals;
    sector_weightings: Record<string, number>;
    insider_activity: InsiderActivity;
    in_watchlist: boolean;
}

// ---------------------------------------------------------------------------
// PROFILE / PORTFOLIO
// ---------------------------------------------------------------------------

export interface TransactionOut {
    id: number;
    user: string;
    date: string; // ISO-8601 datetime string
    type: TransactionType;
    ticker: string | null;
    shares: number;
    price: number;
    fees: number;
    total: number;
    envelope_name: string; // resolved via FK join at read time
    note: string | null;
    realized_pnl: number | null;
    realized_pnl_pct: number | null;
}

export interface EnvelopeOut {
    id: number;
    name: string;
    color: string;
    cash_available: number;
}

// ---------------------------------------------------------------------------
// DASHBOARD
// ---------------------------------------------------------------------------

export interface WatchlistRow {
    ticker: string;
    name: string;
    current_price: number;
    prev_close: number;
    change_1d: number;
    change_1d_pct: number;
    sector: string | null;
    currency: string;
    history_7d: ClosePricePoint[];
    pre_market_price: number | null;
    pre_market_change: number | null;
    pre_market_change_pct: number | null;
}

export interface PositionRow {
    ticker: string;
    /** Company or ETF full name from yfinance. null if unavailable (e.g. regional instrument). */
    name: string | null;
    envelope_name: string;
    currency: string;
    shares: number;
    avg_cost: number;
    cost_basis: number;
    current_price: number;
    current_value: number;
    unrealized_pnl: number;
    unrealized_pnl_pct: number;
    /** % of TOTAL portfolio (equity + cash). Sum < 100% when cash is held — intentional. */
    allocation_pct: number;
    change_1d: number;
    change_1d_pct: number;
}

export interface EnvelopeSummary {
    id: number;
    name: string;
    color: string;
    cash_available: number;
    total_value: number; // cash + equity mark-to-market
    capital_in: number; // SUM(DEPOSIT + DIVIDEND) − SUM(WITHDRAW), all-time
}

export interface DashboardTotals {
    total_value: number;
    total_cash: number;
    total_cost_basis: number;
    total_pnl: number;
    total_pnl_pct: number;
    total_change_1d: number;
    total_change_1d_pct: number;
    /** Net cash deposited (DEPOSIT − WITHDRAW) per trailing window. Keys: 30d, 90d, 180d, 1y, 5y. */
    net_deposits: Record<DepositPeriod, number>;
    /** Total dividend income received in trailing 90 days. */
    dividend_income_90d: number;
}

export interface DashboardResponse {
    watchlist: WatchlistRow[];
    positions: PositionRow[];
    envelopes: EnvelopeSummary[];
    transactions: TransactionOut[];
    totals: DashboardTotals;
    user_currency: string;
}

// ---------------------------------------------------------------------------
// PORTFOLIO OVERVIEW  GET /api/profile/envelope/overview?period=…
// ---------------------------------------------------------------------------

export type OverviewPeriod = '1w' | '1mo' | '3mo' | '6mo' | 'ytd' | '1y' | '3y';

export interface EnvelopeEvent {
    date: string; // YYYY-MM-DD (trading day the event was applied)
    type: TransactionType;
    ticker: string | null;
    envelope_name: string;
    amount: number;
    shares: number;
}

export interface EnvelopeSeriesLine {
    name: string; // envelope name
    color: string; // hex
    values: number[]; // parallel array to dates[]
}

export interface EnvelopeDayStats {
    date: string;
    change: number;
    change_pct: number;
}

export interface EnvelopeStats {
    period_days: number;
    /** Net cash deposited (DEPOSIT − WITHDRAW) within the period. */
    net_deposits: number;
    /** Total dividend income received within the period. */
    dividend_income: number;
    tickers_held: number;
    volatility_annualized_pct: number | null;
    best_day: EnvelopeDayStats | null;
    worst_day: EnvelopeDayStats | null;
    /** Count of BUY + SELL equity trades within the period. */
    trades_count: number;
}

export interface EnvelopeOverviewResponse {
    period: OverviewPeriod;
    dates: string[]; // YYYY-MM-DD, one per trading day
    series: EnvelopeSeriesLine[]; // values[i] corresponds to dates[i]
    events: EnvelopeEvent[];
    stats: EnvelopeStats;
    benchmark_pct: (number | null)[];
    portfolio_pct: (number | null)[];
}

// ---------------------------------------------------------------------------
// TICKER SEARCH  GET /api/profile/watchlist/search?q=…&limit=8
// ---------------------------------------------------------------------------

export interface TickerSearchResult {
    ticker: string;
    name: string;
    /** Current market price. */
    value: number;
    change_1d_pct: number;
    currency: string;
    exchange: string | null;
    /** EQUITY | ETF | MUTUALFUND | INDEX */
    quote_type: string;
}

// ---------------------------------------------------------------------------
// NEWS  GET /api/news/{ticker}?limit=20
// Returns NewsItem[] directly (same model as in StockReport).
// ---------------------------------------------------------------------------

// (re-exported for clarity at call sites)
export type { NewsItem as NewsResponse };
