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
        desc: 'Share price ÷ earnings per share over the trailing 12 months. Shows how much investors pay today per unit of actual reported profit.',
        eli5: "P/E of 25 means you pay 25 years of current earnings upfront. A high P/E isn't bad if earnings are growing fast — the question is whether the growth justifies the price.",
        good: 'Below 20× — historically reasonable across most non-tech sectors',
        bad: 'Above 40× — embeds very aggressive growth assumptions; any earnings miss triggers a sharp de-rating',
    },
    pe_forward: {
        desc: 'Share price ÷ next-12-month analyst EPS consensus. Values the stock on expected future profits rather than the trailing record.',
        eli5: 'If forward P/E is lower than trailing P/E, earnings are expected to grow — the stock gets cheaper over time if forecasts hold. The catch: forecasts are frequently too optimistic.',
        good: "Below 18× — the market isn't pricing in exceptional execution",
        bad: 'Above 35× — little room for disappointment; a downward revision reprices the stock hard',
    },
    peg: {
        desc: 'P/E ratio ÷ expected annual EPS growth rate. Adjusts the valuation multiple for growth to enable fairer comparisons between fast and slow growers.',
        eli5: 'A company growing at 30% deserves a higher P/E than one growing at 5%. PEG normalises for this: below 1 means growth is cheap relative to the price being paid.',
        good: 'Below 1.0 — growth is underpriced relative to the current multiple',
        bad: 'Above 2.0 — paying a large premium even after accounting for expected growth',
    },
    ps: {
        desc: 'Market cap ÷ trailing 12-month revenue. Prices the stock relative to its top line — useful when earnings are negative or highly volatile.',
        eli5: 'P/S of 8 means investors pay 8 years of annual sales as the market cap. Since revenue is not profit, high P/S only makes sense if margins are high or expanding rapidly.',
        good: 'Below 3× — reasonable for most profitable businesses',
        bad: 'Above 10× — prices in years of flawless execution; any revenue disappointment triggers outsized selling',
    },
    pb: {
        desc: "Share price ÷ book value per share (net assets on the balance sheet). Measures the premium investors pay over the company's accounting net worth.",
        eli5: 'P/B of 5 means you pay 5 euros for every 1 euro of net assets. High P/B is normal for software and platforms whose value lies in people and IP, not physical assets.',
        good: 'Below 2× — market prices the stock close to its net asset value',
        bad: 'Above 6× — demands consistently exceptional returns on equity to avoid being overvalued',
    },
    price_book: {
        desc: "Share price ÷ book value per share. The market premium (or discount) assigned to the company's net balance-sheet assets.",
        eli5: 'P/B below 1 means you could theoretically buy the company and immediately sell its assets for more than you paid — which often signals distress rather than opportunity.',
        good: 'Below 2× is reasonable; below 1× can signal value or distress — always verify the reason',
        bad: 'Extremely high P/B is only justified by durable above-average returns on equity',
    },
    ev_ebitda: {
        desc: 'Enterprise Value (market cap + net debt) ÷ EBITDA. The total acquisition cost of the business relative to its pre-tax, pre-depreciation cash earnings.',
        eli5: "Unlike P/E, EV/EBITDA works across companies regardless of how they're financed or taxed — making peer comparisons apples-to-apples. It answers: how many years of EBITDA does the whole business cost?",
        good: 'Below 12× — reasonable for most industries',
        bad: 'Above 20× — expensive; only justified by durable high-margin growth',
    },
    ev_rev: {
        desc: 'Enterprise Value ÷ annual revenue. Like P/S but uses total company value (equity + net debt) against sales, giving a cleaner cross-company comparison that accounts for capital structure differences.',
        eli5: 'EV/Rev of 6 means the market values the entire business — debts included — at 6 times yearly sales. For low-margin businesses, this multiple is almost always too high to sustain.',
        good: 'Below 4× — reasonable across most sectors',
        bad: 'Above 10× — only sustainable for very high-margin, hyper-growth businesses',
    },
    div_yield: {
        desc: 'Annual dividend per share ÷ current share price. The cash income return from holding the stock, expressed as a percentage, independent of any price appreciation.',
        eli5: 'A 4% yield means for every 100 euros invested, the company returns 4 euros/year in cash. Watch out: yield rises when the stock price falls — a very high yield can signal a dividend under threat.',
        good: 'Above 2% is attractive for income investors; a rising dividend over time signals management confidence in cash generation',
        bad: 'Yield above 8% often means the market doubts sustainability — verify against free cash flow and the payout ratio',
    },

    // ── Financial health metrics ──────────────────────────────────────────────

    current_ratio: {
        desc: 'Current assets ÷ current liabilities. Measures whether the company can cover all financial obligations due within the next 12 months using assets it already holds.',
        eli5: "Ratio of 1.5 means the company holds 1.50 euros in short-term assets for every 1 euro owed in the next year. Below 1 means the math doesn't work without raising more cash.",
        good: 'Above 1.5× — comfortable short-term liquidity cushion',
        bad: 'Below 1.0× — current liabilities exceed current assets; may require asset sales, borrowing, or equity issuance to meet near-term obligations',
    },
    quick_ratio: {
        desc: '(Current assets − inventory) ÷ current liabilities. A stricter liquidity test that excludes inventory, which may not convert to cash quickly enough to pay urgent debts.',
        eli5: 'If the quick ratio is 0.7, the company can cover only 70% of its near-term debts using its most liquid assets alone — it needs to sell inventory or borrow to close the gap.',
        good: 'Above 1.0× — liquid assets fully cover near-term liabilities without relying on inventory conversion',
        bad: 'Below 0.5× — serious near-term liquidity constraint; limited ability to respond to sudden cash needs',
    },
    de_ratio: {
        desc: "Total debt ÷ shareholders' equity. The proportion of financing that comes from creditors versus owners. Displayed as the actual ratio (e.g. 1.50×) after correcting yfinance's ×100 percentage scale.",
        eli5: 'D/E of 2 means the company borrowed twice its equity base. Leverage amplifies returns in good times and losses in bad times. Sector context matters: utilities routinely carry D/E of 5+.',
        good: 'Below 1.0× — conservative financing; resilient across interest rate cycles',
        bad: 'Above 2.0× — elevated refinancing risk; a revenue downturn or rate rise tightens the squeeze',
    },
    interest_cov: {
        desc: "EBIT ÷ annual interest expense. Counts how many times operating profit covers the company's debt interest bill — a direct measure of debt serviceability.",
        eli5: 'Coverage of 8 means operating profit is 8 times the interest payment — plenty of buffer. Coverage of 1.2 means one bad quarter could make the interest bill unaffordable.',
        good: 'Above 3× is safe; above 5× indicates the debt is easily serviced from current earnings',
        bad: 'Below 1.5× — almost no cushion; a modest revenue decline could trigger covenant breaches or default risk',
    },
    interest_cover: {
        desc: "EBIT ÷ annual interest expense. Measures how many times operating profit covers the company's debt interest payments.",
        eli5: 'Coverage of 8 means operating profit is 8 times the interest bill. Coverage near 1 means one bad quarter could threaten debt servicing entirely.',
        good: 'Above 5× is strong — the debt is comfortably covered by operating earnings',
        bad: 'Below 2× leaves very little room before debt servicing becomes a problem',
    },
    roe: {
        desc: "Net profit ÷ shareholders' equity. How much profit the company generates per unit of capital investors have put in — the core measure of management effectiveness.",
        eli5: 'ROE of 20% means the company turns every 100 euros of shareholder equity into 20 euros of annual profit. High ROE driven by excessive debt is a warning, not a strength — check D/E alongside.',
        good: 'Above 15% is strong; above 20% is exceptional (health score ideal anchor)',
        bad: 'Negative ROE means the company is destroying equity value; only acceptable in a deliberate early-growth phase with a clear profitability path',
    },
    roa: {
        desc: 'Net income ÷ total assets. Measures how efficiently the company converts all its resources — funded by both debt and equity — into profit.',
        eli5: 'ROA of 10% means every 100 euros of assets generates 10 euros in profit per year. Asset-light businesses (software, platforms) often exceed 20%; capital-heavy industries rarely surpass 5%.',
        good: 'Above 5% is the green threshold here; above 8% is strong (health score ideal anchor)',
        bad: 'Negative ROA means assets are consuming rather than generating value',
    },
    gross_margin: {
        desc: '(Revenue − cost of goods sold) ÷ revenue. The share of each sales euro remaining after direct production costs, before operating expenses like R&D, marketing, and salaries.',
        eli5: 'Gross margin of 60% means 60 cents of every 1 euro in sales survives to cover the rest of the business. It directly reflects pricing power and manufacturing efficiency.',
        good: 'Above 40% indicates strong pricing power; software and platform businesses typically exceed 70%',
        bad: 'Below 10% signals a commodity business where margins are competed to the bone',
    },
    net_margin: {
        desc: 'Net income ÷ revenue. The percentage of each sales euro that survives all costs — production, operating expenses, interest, and taxes — to become reported profit.',
        eli5: "Net margin of 12% means 12 cents of every 1 euro in sales becomes actual profit after everything is paid. It's the true bottom-line rate of the business.",
        good: 'Above 10% is healthy for most industries; above 20% signals strong cost control and pricing power',
        bad: 'Negative net margin means the company loses money on every euro of sales — only acceptable short-term for high-growth companies with an explicit path to profitability',
    },
    op_cashflow: {
        desc: 'Cash generated from core business operations before investing or financing activities. Unlike net income, it cannot be manipulated through accounting choices around depreciation or accruals.',
        eli5: 'A company can report profits while burning cash due to receivables timing and working capital shifts. Positive operating cash flow means the business genuinely collects more cash than it spends running itself.',
        good: 'Consistently positive and growing operating cash flow is the baseline requirement for a financially self-sustaining business',
        bad: 'Negative operating cash flow means the core business consumes cash — requires ongoing external funding to remain operational',
    },
    free_cash: {
        desc: 'Operating cash flow minus capital expenditure. Cash available after sustaining and maintaining the business — what could be returned to shareholders or reinvested in growth.',
        eli5: 'Unlike reported earnings, FCF is nearly impossible to manufacture through accounting. Consistent positive FCF means the business creates real cash value beyond what it spends to keep the lights on.',
        good: 'Consistently positive and growing FCF enables dividends, buybacks, and debt reduction without needing external capital',
        bad: 'Persistent negative FCF requires ongoing debt or equity issuance — manageable for early-growth companies, unsustainable for mature ones',
    },

    // ── Growth metrics ────────────────────────────────────────────────────────

    rev_growth: {
        desc: 'Year-over-year percentage change in total revenue — the most direct signal of whether the business is expanding or contracting in terms of sales.',
        eli5: 'Revenue growth of 15% means the company sold 15% more this year than in the same period last year. Sustained top-line growth is the foundation of long-term value creation.',
        good: 'Above 10% YoY is considered strong; above 20% from a large company is exceptional',
        bad: 'Negative revenue growth is a serious red flag unless the company is deliberately shedding low-quality business lines',
    },
    earn_growth: {
        desc: "Year-over-year change in net income over the trailing 12 months. Measures whether the company's total profit is expanding or contracting versus the prior year period.",
        eli5: 'Earnings growth of 20% means the company made 20% more profit this year than in the same 12-month period last year. Sustained earnings growth is the primary driver of long-term share price appreciation.',
        good: 'Above 10% YoY is considered strong',
        bad: 'Negative earnings growth puts sustained downward pressure on valuation unless the cause is clearly temporary and non-recurring',
    },
    earn_qtrly: {
        desc: 'Year-over-year EPS change for the most recently reported quarter versus the same quarter 12 months prior. A real-time pulse on near-term earnings direction.',
        eli5: 'If Q3 EPS last year was 1.00 and this year is 1.10, quarterly growth is +10%. It answers directly: is this company making more money right now than it was a year ago?',
        good: 'Above 5% confirms earnings momentum is intact',
        bad: 'Negative quarterly growth, especially following prior deceleration, signals a potential trend reversal',
    },
    rev_per_share: {
        desc: 'Total annual revenue ÷ diluted shares outstanding. Tracks top-line productivity per share, revealing whether revenue growth is being diluted by heavy share issuance.',
        eli5: 'If total revenue grows but revenue per share is flat, management is issuing shares as fast as the business is growing — diluting ownership without adding per-share value.',
        good: 'Rising revenue per share confirms growth outpaces any dilution from new share issuance',
    },
    free_cf: {
        desc: 'Operating cash flow minus capital expenditure. The real cash the business generates after all spending needed to maintain and run operations.',
        eli5: 'Positive free cash flow means the business earns more cash than it spends — the surplus can fund dividends, buybacks, acquisitions, or debt repayment without raising external capital.',
        good: 'Positive FCF is the baseline standard for a self-funding business',
        bad: 'Negative FCF requires continuously raising debt or diluting shareholders to fund operations',
    },
    eps_growth: {
        desc: 'Year-over-year change in earnings per share. Tracks per-share profit growth after accounting for any dilution from newly issued shares.',
        eli5: 'EPS growth is more meaningful than raw profit growth because it shows earnings expanding relative to each share you own. Rising total profits with flat EPS signals heavy dilution.',
        good: 'Consistent positive EPS growth shows earnings power compounding at the per-share level',
        bad: 'Declining EPS while revenue grows signals deteriorating margins or aggressive share dilution',
    },
    eps_growth_5y: {
        desc: 'Analyst consensus for annualised EPS growth compounded over the next 5 years. The long-term earnings trajectory Wall Street projects.',
        eli5: 'At 10% annual EPS growth, earnings roughly double every 7 years. This number drives most of the variation in long-term price targets and discounted cash flow models.',
        good: 'Above 10% annualised is considered robust, durable growth for a mature company',
        bad: 'Forecasts beyond 2–3 years carry wide uncertainty — use as directional guidance, not precision',
    },
    fcf_growth: {
        desc: 'Year-over-year change in free cash flow. Whether actual cash generation is accelerating or decelerating, independent of headline earnings.',
        eli5: 'Growing FCF means the company generates more real cash each year. Profits can be flattered by accounting choices; FCF growth cannot be manufactured.',
        good: 'Accelerating FCF growth often precedes dividend increases and share buyback announcements',
        bad: 'Shrinking FCF despite revenue growth signals rising capital intensity or deteriorating working capital management',
    },
    analyst_rev: {
        desc: 'Recent direction and magnitude of analyst revenue estimate revisions — whether the professional consensus is becoming more or less optimistic about future revenue.',
        eli5: "When analysts raise estimates, they expect better results than before. Markets often price in revisions before they're published — upward revisions are a leading signal.",
        good: 'Positive revisions signal improving business momentum and often precede price appreciation',
        bad: 'Downward revisions tend to cascade — the first cut is statistically rarely the last',
    },

    // ── KPI-strip specific entries ────────────────────────────────────────────

    revenue_ttm: {
        desc: 'Total revenue earned over the trailing 12 months, updated with each quarterly report on a rolling basis. The broadest measure of current business scale.',
        eli5: 'Revenue TTM gives a current-year view without waiting for the calendar year to close. Compare the growth rate rather than the absolute number when evaluating across differently-sized companies.',
        good: 'Consistently growing TTM revenue confirms the business is expanding; track the rate of change quarter-over-quarter for acceleration or deceleration signals',
    },
    eps_ttm: {
        desc: 'Net income attributable to shareholders ÷ diluted shares outstanding over the trailing 12 months. The per-share earnings figure used in all P/E ratio calculations.',
        eli5: 'EPS is the simplest bridge between company profit and share price. If EPS is 5 and the stock trades at 100, investors pay 20× each euro of annual per-share earnings.',
        good: 'Rising EPS over multiple quarters confirms compounding earnings power at the per-share level',
        bad: 'Declining EPS driven by dilution rather than earnings decline warrants scrutiny of the capital allocation strategy',
    },

    // ── Technical indicators ──────────────────────────────────────────────────

    rsi: {
        desc: '14-day Relative Strength Index — a 0–100 momentum oscillator computed as the ratio of average up-closes to average down-closes over 14 sessions. Measures the speed and magnitude of recent price changes.',
        eli5: "RSI doesn't predict direction — it flags when a stock has moved very aggressively in one direction and may be overextended. Above 70 flags potential overbought conditions; below 30 flags potential oversold.",
        good: '30–70 is neutral territory; below 30 may indicate an oversold condition worth investigating against underlying fundamentals',
        bad: 'Above 70 signals an aggressive recent rally — not a sell signal alone, but warrants caution on fresh long entries',
    },
    fifty_two_week_range: {
        desc: 'The highest and lowest traded prices over the past 52 weeks. Provides historical price context for evaluating whether the current quote is cheap or expensive relative to recent history.',
        eli5: 'Near the 52-week low could mean distress — or genuine opportunity if fundamentals are sound. Near the high means momentum is strong but you may be buying late. The range contextualises, not decides.',
        good: 'Price consolidating mid-range after a sustained move often signals healthy digestion before the next directional leg',
        bad: 'Price at the extreme low warrants investigation: has something fundamentally changed, or is this temporary sentiment-driven weakness?',
    },
    beta: {
        desc: "Historical sensitivity of the stock's daily returns versus the benchmark index over the past year. β = 1.0 means the stock moves in lockstep with the overall market.",
        eli5: 'Beta 1.5 means when the market drops 10%, this stock has historically dropped 15%. Beta 0.5 means it drops only 5%. Higher beta: higher potential reward and higher potential loss.',
        good: '0.5–1.0 is lower market sensitivity — suitable for defensive or income-oriented positioning',
        bad: 'Above 2.0 amplifies both gains and losses substantially; position sizing must account for magnified drawdowns',
    },
    market_cap: {
        desc: "Share price × total diluted shares outstanding. The market's real-time consensus on the total equity value of the company.",
        eli5: 'Market cap is the price tag to buy every share today. Large cap (>10B) typically means more liquidity and analyst coverage; small cap (<2B) means more volatility and potential pricing inefficiency.',
        good: 'Larger caps offer tighter bid/ask spreads, deeper liquidity, and more institutional ownership stability — relevant for position sizing',
        bad: 'Market cap alone says nothing about valuation — a 2T company can be cheap; a 200M company can be wildly expensive',
    },
    next_earnings: {
        desc: 'The scheduled date of the next quarterly earnings release, alongside the analyst EPS consensus estimate for that period.',
        eli5: "Earnings dates are the single highest-volatility event for most stocks. The market reaction is driven by results versus expectations — not the absolute number. 'Beat and raise' is the ideal outcome.",
        good: 'Consistently beating EPS estimates builds a positive surprise track record that analysts price into future forecasts',
        bad: 'Missing estimates — even narrowly — often triggers outsized selling as forward estimates are simultaneously revised downward',
    },
    sma_cross: {
        desc: 'Relationship between the 50-day and 200-day Simple Moving Averages. The ratio (SMA 50 ÷ SMA 200) shows how far the short-term trend sits above or below the long-term trend.',
        eli5: 'The 200-day SMA is the long-term heartbeat of the stock. When the 50-day crosses above it (Golden Cross), short-term buyers are outpacing the long-term average — a widely watched bullish confirmation. The reverse is a Death Cross.',
        good: 'SMA 50 above SMA 200 (ratio > 1) confirms an established uptrend; a fresh Golden Cross after a prolonged downtrend is a high-attention entry signal for trend-followers',
        bad: 'SMA 50 below SMA 200 (ratio < 1) confirms a downtrend in force; a Death Cross typically precedes institutional de-risking and sustained selling pressure',
    },
    bollinger_pct: {
        desc: 'Bollinger %B — where the current price sits within the Bollinger Bands (±2 standard deviations around the 20-day moving average), expressed as 0–1. Values above 1 are above the upper band; below 0 are below the lower band.',
        eli5: 'Think of the bands as the channel the stock normally trades within. %B above 1 means price has broken above its normal range — unusual strength. Below 0 means unusual weakness. Neither is automatically buy or sell without a confirming signal.',
        good: '%B rising from below 0.2 after a period of compression signals a potential breakout worth watching with volume confirmation',
        bad: '%B above 1.0 during weakening momentum or declining volume can signal exhaustion rather than sustained breakout strength',
    },
    macd: {
        desc: 'Moving Average Convergence Divergence — the difference between the 12-day and 26-day exponential moving averages (EMA), plotted with a 9-day signal line and a histogram showing the gap between them.',
        eli5: 'MACD tracks whether short-term momentum is accelerating or decelerating versus the recent trend. When the fast line crosses above the slow line, buyers are gaining control. Crossing below signals sellers are taking over.',
        good: 'MACD line crossing above the signal line (bullish crossover) indicates strengthening upward momentum — a classic entry confirmation signal',
        bad: 'MACD crossing below the signal line, especially from above zero, signals momentum is shifting to sellers and recent gains may be unwinding',
    },

    // ── Risk sub-components ───────────────────────────────────────────────────
    // Scores are 0–100 where HIGHER = MORE RISK.
    // Thresholds mirror _norm_linear safe/risky anchors in risk_engine.py.
    // Default sector anchors are shown; sector-calibrated values apply for
    // Financial Services, Utilities, Real Estate, Technology, and Healthcare.

    volatility: {
        desc: 'Risk sub-score (0–100) derived from annualised standard deviation of daily log-returns over 252 trading days. Weight: 25% of composite risk score. Anchors: σ < 15% → score 0, σ > 60% → score 100.',
        eli5: 'Annualised volatility of 25% means daily moves, scaled to a year, are consistent with a ±25% price range. High volatility amplifies both gains and losses and makes it psychologically harder to hold through drawdowns.',
        good: 'Below 15% annualised: muted price swings, typical of large-cap mature businesses — score near 0',
        bad: 'Above 60% annualised: extreme turbulence; severe short-term losses are possible even with a correct long-term thesis — score near 100',
    },
    leverage: {
        desc: 'Risk sub-score (0–100) combining D/E ratio (60% weight) and interest coverage (40% weight). Weight: 20% of composite risk score. D/E anchors (default): 0× → safe, 4× → risky. IC anchors: >5× → safe, <1× → risky.',
        eli5: 'High leverage amplifies returns in good times and losses in bad times. The interest coverage part checks whether current profits are large enough to safely service the debt even if revenue dips.',
        good: 'D/E below 1.0× and interest coverage above 5× indicate a conservatively financed business — score near 0',
        bad: 'D/E above 4.0× or interest coverage below 1.0× signals elevated refinancing risk — especially dangerous when rates rise or revenues fall',
    },
    valuation: {
        desc: 'Composite valuation assessment. As a risk sub-score (0–100, higher = more expensive = more risk, weight 20%): P/E (60%) and P/B (40%) versus sector-calibrated thresholds. As a factor score (0–100, higher = better): weighted blend of P/E, Fwd P/E, PEG, P/B, and EV/EBITDA.',
        eli5: "High valuation risk means you're paying a lot relative to the company's earnings and assets. If growth disappoints, expensive stocks fall harder because there's no valuation support to cushion the decline.",
        good: 'Low risk score / high factor score: the stock trades at a reasonable multiple — valuation provides a margin of safety',
        bad: 'High risk score / low factor score: an expensive stock where any disappointment can trigger a large de-rating, especially in rising rate environments',
    },
    liquidity: {
        desc: 'Risk sub-score (0–100) combining current ratio (55% weight) and quick ratio (45% weight). Weight: 15% of composite risk score. CR anchors (default): >2.0× → safe, <0.8× → risky. QR anchors: >1.5× → safe, <0.5× → risky.',
        eli5: "A high liquidity risk score means the company's short-term debts outpace its liquid assets. Meeting upcoming obligations may require selling assets, cutting investment, or raising emergency funding.",
        good: 'Current ratio above 2.0× and quick ratio above 1.5× — strong ability to absorb near-term financial shocks — score near 0',
        bad: 'Current ratio below 0.8× or quick ratio below 0.5× — cannot cover imminent obligations from liquid assets alone — score near 100',
    },
    earnings: {
        desc: 'Risk sub-score (0–100) combining EPS miss frequency (60% weight) — proportion of recent quarters where actual EPS fell below analyst estimates — and trailing revenue growth direction (40% weight). Weight: 10% of composite risk score.',
        eli5: 'Consistently missing earnings estimates erodes analyst and investor confidence, triggering downward revisions that cascade into further price pressure. Revenue decline adds a second layer of concern about business health.',
        good: 'Miss rate below 20% of recent quarters and positive revenue growth indicate a predictable, expanding business — score near 0',
        bad: 'Miss rate above 50% of recent quarters combined with declining revenue signals execution problems or deteriorating demand — score near 100',
    },
    market_beta: {
        desc: 'Risk sub-score (0–100) derived from systematic β versus the benchmark. Weight: 10% of composite risk score. Anchors: β < 0.8 → score 0, β > 2.0 → score 100. Negative β is treated as moderate risk (40) — counter-cyclical assets carry significant structural risks.',
        eli5: 'Market beta risk is the portion of volatility you cannot diversify away. Even in a well-diversified portfolio, a high-beta stock will swing hard whenever the whole market moves.',
        good: 'β below 0.8 — the stock moves less than the market; reduces portfolio drawdowns during broad sell-offs — score near 0',
        bad: 'β above 2.0 — the stock historically moves 2× the market; position sizing must account for amplified drawdowns — score near 100',
    },

    // ── Factor scorecard bars (bar.label.toLowerCase()) ───────────────────────
    // Weights from _build_score_breakdown: Valuation 35%, Financial Health 35%, Growth 30%.
    // Each bar score is 0–100 via _norm_score() with ideal/poor anchors in report_builder.py.

    'financial health': {
        desc: 'Composite factor score (0–100, weight 35%) blending net margin (20%), ROE (20%), gross margin (15%), current ratio (15%), quick ratio (15%), and ROA (15%). Measures the overall financial robustness of the business.',
        eli5: 'A high financial health score means the company is profitable, efficient with capital, and liquid enough to weather downturns without external help. Low scores flag thin margins, poor returns, or a stretched balance sheet.',
        good: 'Above 70 — strong across most health dimensions; the business generates solid returns and holds comfortable liquidity',
        bad: 'Below 35 — multiple dimensions weak simultaneously; thin margins, poor capital efficiency, or potential short-term financial stress',
    },

    growth: {
        desc: 'Composite factor score (0–100, weight 30%) blending revenue growth (35%), earnings growth (30%), quarterly EPS growth (20%), and a binary FCF check (15% — positive FCF = 75, negative = 25). Measures overall expansion trajectory.',
        eli5: 'A high growth score means the business is expanding revenues and profits at an above-average rate — the kind of company that can grow into a high valuation over time rather than being permanently overpriced.',
        good: 'Above 65 — broad-based expansion across multiple metrics; the company is compounding at a healthy rate',
        bad: 'Below 35 — growth has stalled or deteriorated across most dimensions; multiple metrics are negative or below threshold',
    },

    // ── Retained general entries ──────────────────────────────────────────────

    price_sales: {
        desc: 'Market cap ÷ annual revenue. Compares investor cost to top-line sales — useful for unprofitable companies where P/E is undefined.',
        eli5: "P/S of 5 means you pay 5 euros for every 1 euro of yearly revenue. Revenue doesn't equal profit — high P/S only makes sense if margins are high or rapidly expanding.",
        good: 'Below 2× is generally reasonable for profitable businesses',
        bad: 'Above 10× prices in years of perfect execution — a single revenue miss can trigger a sharp de-rating',
    },
    quality: {
        desc: 'Composite score reflecting profitability, balance-sheet strength, and earnings stability. Measures how durable and consistent business performance is over time.',
        eli5: 'A high quality score means the company makes reliable profits, avoids heavy debt, and delivers predictable results year after year — the kind of business that holds up in downturns.',
        good: 'Above 70 indicates a financially sound, competitively advantaged business',
        bad: 'Below 40 flags concerns around profitability, leverage, or the reliability of reported earnings',
    },
    value: {
        desc: 'Composite score reflecting how cheap or expensive the stock appears relative to its fundamentals versus sector peers.',
        eli5: "High value score doesn't mean the stock will go up — it means you're paying less per unit of earnings and assets than comparable companies right now.",
        good: 'Above 65 suggests the stock trades at a meaningful discount versus peers on multiple valuation metrics',
        bad: 'Below 35 means an expensive valuation that demands exceptional execution to avoid eventual disappointment',
    },
    momentum: {
        desc: 'Composite score tracking recent price and earnings-revision performance. Captures whether the prevailing trend currently favours buyers or sellers.',
        eli5: "Momentum is a 'follow the trend' signal, not a fundamentals signal. Stocks trending up with improving estimates tend to keep doing so — until they don't.",
        good: 'Above 70 — a strong upward price trend with improving analyst expectations supporting it',
        bad: 'Extreme momentum (>90) can precede sharp reversals as crowded positions unwind quickly',
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
            <!-- Trigger label — dashed underline signals interactivity -->
            <span
                [class]="
                    textSize() +
                    ' text-slate-500 uppercase tracking-wider font-bold border-b border-dashed border-slate-400/60 pb-px group-hover:text-blue-600 transition-colors'
                ">
                {{ label() }}
            </span>

            <!-- Tooltip panel — appears above the trigger -->
            <div
                [class]="
                    position() === 'bottom'
                        ? 'absolute invisible opacity-0 group-hover:visible group-hover:opacity-100 top-full mt-3 left-1/2 -translate-x-1/2 max-w-[calc(100vw-2rem)] z-50 transition-all duration-200 ease-out -translate-y-1 group-hover:translate-y-0 pointer-events-none'
                        : 'absolute invisible opacity-0 group-hover:visible group-hover:opacity-100 bottom-full mb-3 left-1/2 -translate-x-1/2 max-w-[calc(100vw-2rem)] z-50 transition-all duration-200 ease-out translate-y-1 group-hover:translate-y-0 pointer-events-none'
                ">
                <div
                    class="p-4 bg-slate-900/97 backdrop-blur-xl rounded-2xl shadow-2xl border border-white/8 text-left">
                    <!-- Header -->
                    <p class="text-[10px] font-black text-slate-400 uppercase tracking-[0.16em] mb-2">
                        {{ label() }}
                    </p>

                    <!-- Description -->
                    <p class="text-[13px] text-slate-200 leading-relaxed">
                        {{
                            glossary[metricKey()]?.desc ??
                                'A key financial metric used to evaluate company performance.'
                        }}
                    </p>

                    <!-- ELI5 -->
                    @if (glossary[metricKey()]?.eli5) {
                        <div class="mt-3 p-2.5 bg-blue-500/10 border border-blue-500/20 rounded-lg">
                            <p class="text-blue-200 text-[12px] leading-snug flex gap-2 items-start">
                                <span class="text-blue-400 font-black shrink-0 mt-px">💡</span>
                                <span>{{ glossary[metricKey()]!.eli5 }}</span>
                            </p>
                        </div>
                    }

                    <!-- Good / Bad thresholds -->
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

                <!-- Arrow pointing down toward the trigger -->
                <div
                    [class]="
                        position() === 'bottom'
                            ? 'absolute bottom-full left-1/2 -translate-x-1/2 -mb-px border-8 border-transparent border-b-slate-900/97 pointer-events-none'
                            : 'absolute top-full left-1/2 -translate-x-1/2 -mt-px border-8 border-transparent border-t-slate-900/97 pointer-events-none'
                    "></div>
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
