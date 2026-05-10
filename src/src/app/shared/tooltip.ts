import { Component, input } from '@angular/core';

// ---------------------------------------------------------------------------
// Glossary — module-level const: initialised once, zero per-instance cost.
//
// Key naming follows the backend exactly:
//   Valuation grid  → _build_valuation_grid  keys: pe_trailing pe_forward peg ps pb ev_ebitda ev_rev div_yield
//   Health grid     → _build_health_grid     keys: current_ratio quick_ratio de_ratio interest_cov roe roa gross_margin net_margin op_cashflow
//   Growth grid     → _build_growth_grid     keys: rev_growth earn_growth earn_qtrly rev_per_share free_cf
//   KPI strip       → _build_kpi_strip       keys: pe_trailing pe_forward ev_ebitda price_book roe roa gross_margin net_margin revenue_ttm free_cash div_yield eps_ttm
//   Risk engine     → compute_risk           keys: volatility leverage valuation liquidity earnings market_beta
//   Score bars      → bar.label.toLowerCase(): "valuation"  "financial health"  "growth"
//   Technical       → explicit template attrs: rsi fifty_two_week_range beta market_cap next_earnings sma_cross bollinger_pct macd
//
// Threshold values in good/bad mirror the green/red cell thresholds from
// report_builder.py and the safe/risky anchors in risk_engine.py exactly.
// ---------------------------------------------------------------------------
const GLOSSARY: Record<string, { desc: string; eli5?: string; good?: string; bad?: string }> = {
    // ── Valuation metrics ─────────────────────────────────────────────────────
    pe_trailing: {
        desc: 'Trailing P/E ratio. Accounts for 30% of Valuation score.',
        eli5: 'Scores 100/100 at 12.0x, and 0/100 at 50.0x. Negative P/E scores 10/100.',
        good: '< 20x ideal; optimally 12.0x for max score.',
        bad: '> 40x expensive; scores 0 at 50.0x.',
    },
    pe_forward: {
        desc: 'Forward P/E ratio. Accounts for 25% of Valuation score.',
        eli5: 'Scores 100/100 at 12.0x, and 0/100 at 40.0x. Negative values score 10/100.',
        good: '< 18x ideal; optimally 12.0x for max score.',
        bad: '> 35x expensive; scores 0 at 40.0x.',
    },
    peg: {
        desc: 'PEG ratio. Accounts for 20% of Valuation score.',
        eli5: 'Scores 100/100 at 0.75x, and 0/100 at 2.5x.',
        good: '< 1x undervalued; optimally 0.75x for max score.',
        bad: '> 2x expensive; scores 0 at 2.5x.',
    },
    ps: {
        desc: 'Price to Sales ratio. Useful when earnings are negative.',
        eli5: 'Prices the stock relative to its top line revenue.',
        good: '< 3x fair value.',
        bad: '> 10x embeds high execution risk.',
    },
    pb: {
        desc: 'Price to Book ratio. Accounts for 15% of Valuation score.',
        eli5: 'Scores 100/100 at 1.5x, and 0/100 at 8.0x.',
        good: '< 2x fair value; optimally 1.5x for max score.',
        bad: '> 6x expensive; scores 0 at 8.0x.',
    },
    price_book: {
        desc: 'Price to Book ratio. Accounts for 15% of Valuation score.',
        eli5: 'Scores 100/100 at 1.5x, and 0/100 at 8.0x.',
        good: '< 2x fair value; optimally 1.5x for max score.',
        bad: '> 6x expensive; scores 0 at 8.0x.',
    },
    ev_ebitda: {
        desc: 'EV / EBITDA. Accounts for 10% of Valuation score.',
        eli5: 'Scores 100/100 at 8.0x, and 0/100 at 25.0x.',
        good: '< 12x ideal; optimally 8.0x for max score.',
        bad: '> 20x expensive; scores 0 at 25.0x.',
    },
    ev_rev: {
        desc: 'Enterprise Value to Revenue. Accounts for debt structure.',
        eli5: 'Total acquisition cost relative to sales.',
        good: '< 4x fair value.',
        bad: '> 10x implies extreme growth expectations.',
    },
    div_yield: {
        desc: 'Dividend Yield. Cash income return from stock.',
        eli5: 'Higher yield gives more cash, but over 8% may be unsustainable.',
        good: '> 2% attractive for income.',
        bad: '< 0% yields nothing, > 8% implies risk.',
    },

    // ── Financial health metrics ──────────────────────────────────────────────
    current_ratio: {
        desc: 'Current Assets / Current Liabilities. 15% of Health score.',
        eli5: 'Scores 100/100 at 2.0x, and 0/100 at 0.8x.',
        good: '> 1.5x healthy; optimally 2.0x for max score.',
        bad: '< 1.0x poor liquidity; scores 0 at 0.8x.',
    },
    quick_ratio: {
        desc: '(Current Assets - Inventory) / Liabilities. 15% of Health score.',
        eli5: 'Scores 100/100 at 1.5x, and 0/100 at 0.5x.',
        good: '> 1.0x healthy; optimally 1.5x for max score.',
        bad: '< 0.5x severe constraint; scores 0 at 0.5x.',
    },
    de_ratio: {
        desc: 'Debt to Equity ratio. Real ratio (yfinance % scaled down).',
        eli5: 'Measures financial leverage and refinancing risk.',
        good: '< 1.0x indicates low leverage and safe financing.',
        bad: '> 2.0x signals elevated risk.',
    },
    interest_cov: {
        desc: 'EBIT / Interest Expense. Direct measure of debt serviceability.',
        eli5: 'Measures buffer against debt payments.',
        good: '> 3.0x safe coverage.',
        bad: '< 1.5x tight buffer.',
    },
    interest_cover: {
        desc: 'EBIT / Interest Expense. Direct measure of debt serviceability.',
        eli5: 'Measures buffer against debt payments.',
        good: '> 5.0x safe coverage.',
        bad: '< 2.0x tight buffer.',
    },
    roe: {
        desc: 'Return on Equity. Accounts for 20% of Health score.',
        eli5: 'Scores 100/100 at 20%, and 0/100 at -5%.',
        good: '> 15% strong; optimally 20% for max score.',
        bad: '< 0% destroying value; scores 0 at -5%.',
    },
    roa: {
        desc: 'Return on Assets. Accounts for 15% of Health score.',
        eli5: 'Scores 100/100 at 8%, and 0/100 at 0%.',
        good: '> 5% good; optimally 8% for max score.',
        bad: '< 0% destroying value; scores 0 at 0%.',
    },
    gross_margin: {
        desc: 'Gross Margin. Accounts for 15% of Health score.',
        eli5: 'Scores 100/100 at 40%, and 0/100 at 5%.',
        good: '> 40% ideal; optimally 40% for max score.',
        bad: '< 10% poor pricing power; scores 0 at 5%.',
    },
    net_margin: {
        desc: 'Net Margin. Accounts for 20% of Health score.',
        eli5: 'Scores 100/100 at 12%, and 0/100 at -5%.',
        good: '> 10% good; optimally 12% for max score.',
        bad: '< 0% losing money; scores 0 at -5%.',
    },
    op_cashflow: {
        desc: 'Operating Cash Flow. Unmanipulated core business cash.',
        eli5: 'Positive value means self-sustaining business.',
        good: '> 0 means business generates cash.',
        bad: '< 0 means core business burns cash.',
    },
    free_cash: {
        desc: 'Free Cash Flow. Positivity defines 15% of Growth score.',
        eli5: 'Scores 75 if positive, 25 if negative, 50 if missing.',
        good: '> 0 means cash available after expenses.',
        bad: '< 0 requires external capital.',
    },

    // ── Growth metrics ────────────────────────────────────────────────────────
    rev_growth: {
        desc: 'Revenue Growth (YoY). Accounts for 35% of Growth score.',
        eli5: 'Scores 100/100 at 15%, and 0/100 at -5%.',
        good: '> 10% strong; optimally 15% for max score.',
        bad: '< 0% shrinking business; scores 0 at -5%.',
    },
    earn_growth: {
        desc: 'Earnings Growth (TTM). Accounts for 30% of Growth score.',
        eli5: 'Scores 100/100 at 15%, and 0/100 at -10%.',
        good: '> 10% strong; optimally 15% for max score.',
        bad: '< 0% shrinking profits; scores 0 at -10%.',
    },
    earn_qtrly: {
        desc: 'Quarterly EPS Growth. Accounts for 20% of Growth score.',
        eli5: 'Scores 100/100 at 10%, and 0/100 at -15%.',
        good: '> 5% positive momentum; optimally 10% for max score.',
        bad: '< 0% declining momentum; scores 0 at -15%.',
    },
    rev_per_share: {
        desc: 'Revenue per Share. Accounts for share dilution.',
        eli5: 'If revenue grows but per-share is flat, dilution is occurring.',
        good: 'Rising revenue per share.',
    },
    free_cf: {
        desc: 'Free Cash Flow. Positivity defines 15% of Growth score.',
        eli5: 'Scores 75 if positive, 25 if negative.',
        good: '> 0 means self-funding business.',
        bad: '< 0 requires raising debt or diluting.',
    },
    eps_growth: {
        desc: 'EPS Growth YoY. Accounts for share dilution.',
        eli5: 'Real per-share profit growth.',
        good: 'Consistent positive growth.',
        bad: 'Declining EPS despite revenue growth.',
    },
    eps_growth_5y: {
        desc: '5-Year EPS Growth Forecast.',
        eli5: 'Wall Street long-term projections.',
        good: '> 10% annualized.',
        bad: 'Long-term forecasts have wide uncertainty.',
    },
    fcf_growth: {
        desc: 'Free Cash Flow Growth.',
        eli5: 'Acceleration of actual unmanufactured cash.',
        good: 'Accelerating growth.',
        bad: 'Shrinking FCF despite revenue growth.',
    },
    analyst_rev: {
        desc: 'Analyst Estimate Revisions.',
        eli5: 'Directional shift in Wall Street consensus.',
        good: 'Positive upward revisions.',
        bad: 'Downward revisions.',
    },

    // ── KPI-strip specific entries ────────────────────────────────────────────
    revenue_ttm: {
        desc: 'Trailing 12-Month Revenue.',
        eli5: 'Broadest measure of current business scale.',
        good: 'Consistently growing TTM revenue.',
    },
    eps_ttm: {
        desc: 'Trailing 12-Month EPS.',
        eli5: 'The per-share earnings used for P/E calculation.',
        good: 'Rising EPS confirms compounding power.',
        bad: 'Declining EPS warrants scrutiny.',
    },

    // ── Technical indicators ──────────────────────────────────────────────────
    rsi: {
        desc: 'RSI (14). Momentum oscillator for overbought/oversold.',
        eli5: 'Measures recent price change speed and magnitude.',
        good: '< 30 is Oversold (BUY signal).',
        bad: '> 70 is Overbought (SELL signal).',
    },
    fifty_two_week_range: {
        desc: '52-Week Price Range.',
        eli5: 'Historical context for current quote.',
        good: 'Mid-range consolidation.',
        bad: 'Extreme low warrants investigation.',
    },
    beta: {
        desc: 'Market Beta. Sensitivity versus benchmark.',
        eli5: '1.0 moves with market. >1.0 amplifies moves.',
        good: '0.5-1.0 is lower market sensitivity.',
        bad: '> 2.0 amplifies both gains and losses.',
    },
    market_cap: {
        desc: 'Market Capitalization. Total equity value.',
        eli5: 'Price tag to buy every share today.',
        good: 'Larger caps offer deeper liquidity.',
    },
    next_earnings: {
        desc: 'Next Earnings Date.',
        eli5: 'Highest-volatility scheduled event.',
        good: 'Consistently beating estimates.',
        bad: 'Missing estimates triggers selling.',
    },
    sma_cross: {
        desc: 'SMA 50 / 200. Macro trend regime.',
        eli5: 'Measures persistent bullish or bearish regime.',
        good: 'SMA 50 > SMA 200 signifies bullish regime (BUY signal).',
        bad: 'SMA 50 < SMA 200 signifies bearish regime (SELL signal).',
    },
    bollinger_pct: {
        desc: 'Bollinger %B (20-day, 2σ).',
        eli5: 'Price position within volatility bands.',
        good: '%B < 0 means price broke below lower band (BUY signal).',
        bad: '%B > 1 means price broke above upper band (SELL signal).',
    },
    macd: {
        desc: 'MACD (12/26/9). Trend direction + histogram filter.',
        eli5: 'Requires histogram acceleration to filter weak crossovers.',
        good: 'MACD > Signal AND Histogram expanding positively (BUY signal).',
        bad: 'MACD < Signal AND Histogram expanding negatively (SELL signal).',
    },

    // ── Risk sub-components ───────────────────────────────────────────────────
    volatility: {
        desc: 'Volatility Risk. 25% of composite risk score.',
        eli5: 'Anchors: <15% annualised scores 0, >60% scores 100.',
        good: '< 15% offers muted price swings (Score near 0).',
        bad: '> 60% indicates extreme turbulence (Score near 100).',
    },
    leverage: {
        desc: 'Leverage Risk. 20% of risk score. D/E (60%) + Interest Cov (40%).',
        eli5: 'D/E Anchors: 0x -> 0, 4x -> 100. IC Anchors: >5x -> 0, <1x -> 100.',
        good: 'D/E < 1.0x and Int. Cov > 5.0x (Score near 0).',
        bad: 'D/E > 4.0x or Int. Cov < 1.0x (Score near 100).',
    },
    liquidity: {
        desc: 'Liquidity Risk. 15% of risk score. CR (55%) + QR (45%).',
        eli5: 'CR Anchors: >2.0x -> 0, <0.8x -> 100. QR Anchors: >1.5x -> 0, <0.5x -> 100.',
        good: 'CR > 2.0x and QR > 1.5x (Score near 0).',
        bad: 'CR < 0.8x or QR < 0.5x (Score near 100).',
    },
    earnings: {
        desc: 'Earnings Risk. 10% of risk score.',
        eli5: 'Miss Frequency (60%) + Revenue Growth Direction (40%).',
        good: 'Miss rate < 20% and positive revenue growth (Score near 0).',
        bad: 'Miss rate > 50% and declining revenue (Score near 100).',
    },
    market_beta: {
        desc: 'Market Beta Risk. 10% of composite risk score.',
        eli5: 'Beta Anchors: <0.8 scores 0, >2.0 scores 100.',
        good: 'Beta < 0.8 reduces portfolio drawdowns (Score near 0).',
        bad: 'Beta > 2.0 amplifies market drops (Score near 100).',
    },

    // ── Factor scorecard bars ─────────────────────────────────────────────────
    valuation: {
        desc: 'Valuation Score (35% total). Normalised across 5 metrics.',
        eli5: 'Grades: A(≥75), B(≥60), C(≥45), D(≥30), F(<30).',
        good: 'Score > 75 implies undervalued stock (Grade A).',
        bad: 'Score < 30 implies heavily overvalued stock (Grade F).',
    },
    'financial health': {
        desc: 'Health Score (35% total). Normalised across 6 metrics.',
        eli5: 'Grades: A(≥75), B(≥60), C(≥45), D(≥30), F(<30).',
        good: 'Score > 75 implies strong margins and liquidity (Grade A).',
        bad: 'Score < 30 implies weak efficiency and high stress (Grade F).',
    },
    growth: {
        desc: 'Growth Score (30% total). Normalised across 4 metrics.',
        eli5: 'Grades: A(≥75), B(≥60), C(≥45), D(≥30), F(<30).',
        good: 'Score > 75 implies compounding growth across metrics (Grade A).',
        bad: 'Score < 30 implies stalling or contracting growth (Grade F).',
    },

    // ── Retained general entries ──────────────────────────────────────────────
    price_sales: {
        desc: 'Market Cap / Annual Revenue.',
        eli5: 'Compares cost to top-line sales.',
        good: '< 2x is reasonable for profitable firms.',
        bad: '> 10x prices in flawless execution.',
    },
    quality: {
        desc: 'Quality Score. Reflects profitability and balance sheet.',
        eli5: 'Measures durable business performance.',
        good: 'Score > 70 indicates sound financial advantage.',
        bad: 'Score < 40 flags fundamental concerns.',
    },
    value: {
        desc: 'Value Score. Compares valuation to fundamentals.',
        eli5: 'Measures how cheap the stock is vs peers.',
        good: 'Score > 65 suggests meaningful discount.',
        bad: 'Score < 35 means expensive valuation.',
    },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
@Component({
    selector: 'app-tooltip',
    standalone: true,
    imports: [],
    template: `
        <div class="relative group flex items-center gap-1.5 cursor-help w-fit">
            <span
                [class]="
                    textSize() +
                    ' text-slate-500 uppercase tracking-wider font-bold border-b border-dashed border-slate-400/60 pb-px group-hover:text-blue-600 transition-colors'
                ">
                {{ label() }}
            </span>

            <div
                [class]="
                    position() === 'bottom'
                        ? 'absolute top-full mt-3 left-1/2 w-72 max-w-[calc(100vw-2rem)] z-50 pointer-events-none invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-200 ease-out'
                        : 'absolute bottom-full mb-3 left-1/2 w-72 max-w-[calc(100vw-2rem)] z-50 pointer-events-none invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-200 ease-out'
                "
                style="--_tx: clamp(calc(18rem - 50vw + 1rem), 9rem, calc(50vw - 1rem));
                       translate: calc(-1 * var(--_tx)) 0">
                <!-- Inner wrapper: owns only the Y slide -->
                <div
                    [class]="
                        position() === 'bottom'
                            ? 'transition-transform duration-200 ease-out -translate-y-1 group-hover:translate-y-0'
                            : 'transition-transform duration-200 ease-out translate-y-1 group-hover:translate-y-0'
                    ">
                    <div
                        class="p-4 bg-slate-900/97 backdrop-blur-xl rounded-2xl shadow-2xl border border-white/8 text-left">
                        <p class="text-[10px] font-black text-slate-400 uppercase tracking-[0.16em] mb-2">
                            {{ label() }}
                        </p>
                        <p class="text-[13px] text-slate-200 leading-relaxed">
                            {{
                                glossary[metricKey()]?.desc ??
                                    'A key financial metric used to evaluate company performance.'
                            }}
                        </p>

                        @if (glossary[metricKey()]?.eli5) {
                            <div class="mt-3 p-2.5 bg-blue-500/10 border border-blue-500/20 rounded-lg">
                                <p class="text-blue-200 text-[12px] leading-snug flex gap-2 items-start">
                                    <span class="text-blue-400 font-black shrink-0 mt-px">💡</span>
                                    <span>{{ glossary[metricKey()]!.eli5 }}</span>
                                </p>
                            </div>
                        }

                        @if (glossary[metricKey()]?.good || glossary[metricKey()]?.bad) {
                            <div class="mt-3 pt-3 border-t border-white/8 space-y-1.5">
                                @if (glossary[metricKey()]?.good) {
                                    <div class="flex items-start gap-2 text-[12px]">
                                        <span class="text-emerald-400 font-black leading-none mt-px shrink-0">↗</span>
                                        <span class="text-slate-300 flex-1">{{ glossary[metricKey()]!.good }}</span>
                                    </div>
                                }
                                @if (glossary[metricKey()]?.bad) {
                                    <div class="flex items-start gap-2 text-[12px]">
                                        <span class="text-red-400 font-black leading-none mt-px shrink-0">↘</span>
                                        <span class="text-slate-300 flex-1">{{ glossary[metricKey()]!.bad }}</span>
                                    </div>
                                }
                            </div>
                        }
                    </div>

                    <div
                        [class]="
                            position() === 'bottom'
                                ? 'absolute bottom-full -translate-x-1/2 -mb-px border-8 border-transparent border-b-slate-900/97 pointer-events-none'
                                : 'absolute top-full -translate-x-1/2 -mt-px border-8 border-transparent border-t-slate-900/97 pointer-events-none'
                        "
                        style="left: var(--_tx)"></div>
                </div>
            </div>
        </div>
    `,
})
export class TooltipComponent {
    readonly label = input.required<string>();
    readonly metricKey = input.required<string>();
    readonly position = input<'top' | 'bottom'>('top');

    /** Font-size utility class applied to the trigger label. Defaults to text-xs. */
    readonly textSize = input<string>('text-xs');

    /** Exposed to the template — module-level const avoids per-instance allocation. */
    protected readonly glossary = GLOSSARY;
}
