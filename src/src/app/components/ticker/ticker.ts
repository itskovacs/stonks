import {
    Component,
    computed,
    DestroyRef,
    effect,
    ElementRef,
    inject,
    linkedSignal,
    signal,
    viewChild,
} from '@angular/core';
import { DecimalPipe, DatePipe, KeyValuePipe } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { BehaviorSubject, combineLatest, filter, map, switchMap, tap } from 'rxjs';
import { Chart, ChartConfiguration, Plugin, registerables } from 'chart.js';
import { ApiService } from '../../services/api.service';
import { PriceChartResponse, StockBar, TransactionType } from '../../int';
import { TooltipComponent } from '../../shared/tooltip';

Chart.register(...registerables);

// ---------------------------------------------------------------------------
// Chart period union — mirrors the literal union in ApiService.getStockChart
// ---------------------------------------------------------------------------
type ChartPeriod = '1d' | '1w' | '1m' | '3m' | '6m' | 'ytd' | '1y' | '5y';

type TxBadgeConfig = { displayValue: string; class: string };

@Component({
    selector: 'app-ticker',
    standalone: true,
    imports: [DecimalPipe, DatePipe, RouterLink, TooltipComponent, KeyValuePipe],
    templateUrl: './ticker.html',
    styleUrls: ['./ticker.scss'],
})
export class TickerComponent {
    private readonly apiService = inject(ApiService);
    private readonly route = inject(ActivatedRoute);
    private readonly destroyRef = inject(DestroyRef);

    // ── Transaction badge config (user positions ledger) ──────────────────────
    readonly txConfig: Record<TransactionType, TxBadgeConfig> = {
        BUY: { displayValue: 'BUY', class: 'bg-blue-50 text-blue-700 border-blue-200/60' },
        SELL: { displayValue: 'SELL', class: 'bg-rose-50 text-rose-700 border-rose-200/60' },
        DEPOSIT: { displayValue: 'DEP', class: 'bg-emerald-50 text-emerald-700 border-emerald-200/60' },
        WITHDRAW: { displayValue: 'WDW', class: 'bg-amber-50 text-amber-700 border-amber-200/60' },
        DIVIDEND: { displayValue: 'DIV', class: 'bg-violet-50 text-violet-700 border-violet-200/60' },
    };

    // ── Route param shared source ──────────────────────────────────────────────
    private readonly ticker$ = this.route.paramMap.pipe(
        map((params) => params.get('id')),
        filter((ticker): ticker is string => !!ticker),
    );

    // ── Stock report ──────────────────────────────────────────────────────────
    readonly report = toSignal(this.ticker$.pipe(switchMap((ticker) => this.apiService.getStockReport(ticker))));
    readonly kvOrder = () => 0;

    // ── Derived computed signals ──────────────────────────────────────────────

    /**
     * RSI label + CSS classes resolved once; avoids repeating the threshold
     * logic four times across the template.
     */
    readonly rsiLabel = computed(() => {
        const rsi = this.report()?.stock_bar.rsi_14;
        if (rsi == null) return null;
        if (rsi >= 70)
            return {
                text: 'OVERBOUGHT',
                badgeClass: 'bg-red-100 text-red-700',
                textClass: 'text-red-600',
                barClass: 'bg-red-500',
            };
        if (rsi <= 30)
            return {
                text: 'OVERSOLD',
                badgeClass: 'bg-green-100 text-green-700',
                textClass: 'text-green-600',
                barClass: 'bg-green-500',
            };
        return {
            text: 'NEUTRAL',
            badgeClass: 'bg-primary-100 text-primary-700',
            textClass: 'text-primary-900',
            barClass: 'bg-primary-500',
        };
    });

    readonly macdSignal = computed(
        () => (this.report()?.signals.signals['MACD (12/26/9)'] as 'BUY' | 'HOLD' | 'SELL' | null) ?? null,
    );
    readonly macdLabel = computed(() => this.getSignalLabel(this.macdSignal()));

    readonly bollingerLabel = computed(() => {
        const signal = this.report()?.signals.signals['Bollinger %B'];
        return this.getSignalLabel(signal);
    });
    readonly bollingerValue = computed(
        () => (this.report()?.signals.raw_values['Bollinger %B'] as number | null) ?? null,
    );

    // %B is already 0–1 (sometimes outside), clamp to 0–100 for bar
    readonly bollingerBarPct = computed(() => {
        const v = this.bollingerValue();
        if (v == null) return null;
        return Math.min(100, Math.max(0, v * 100));
    });

    readonly smaLabel = computed(() => {
        const signal = this.report()?.signals.signals['SMA 50/200'];
        return this.getSignalLabel(signal);
    });

    readonly smaRaw = computed(
        () => this.report()?.signals.raw_values['SMA 50/200'] as { sma_50: number; sma_200: number } | null,
    );

    readonly smaCrossPercent = computed(() => {
        const raw = this.smaRaw();
        if (!raw) return null;
        // Normalize: show SMA50 relative to SMA200 (capped 0–100 for display)
        const ratio = (raw.sma_50 / raw.sma_200) * 50; // 50 = neutral, <50 bearish, >50 bullish
        return Math.min(100, Math.max(0, ratio));
    });

    /** Analyst rating badge class — reflects actual direction of the rating. */
    readonly ratingBadgeClass = computed(() => {
        const r = this.report()?.rating_verdict.analyst_rating?.toUpperCase() ?? '';
        if (r.includes('BUY')) return 'bg-green-100 text-green-700';
        if (r.includes('SELL')) return 'bg-red-100 text-red-700';
        return 'bg-primary-100 text-primary-600';
    });

    /** Risk gauge badge — maps label to color. */
    readonly riskBadgeClass = computed(() => {
        const l = this.report()?.risk_gauge.label?.toUpperCase() ?? '';
        if (l === 'LOW') return 'bg-green-100 text-green-700';
        if (l === 'MODERATE') return 'bg-amber-100 text-amber-700';
        if (l === 'HIGH') return 'bg-red-100 text-red-700';
        if (l === 'VERY HIGH') return 'bg-red-200 text-red-800';
        return 'bg-primary-100 text-primary-600';
    });

    // ── Watchlist ─────────────────────────────────────────────────────────────
    readonly isWatchlisted = linkedSignal(() => this.report()?.in_watchlist ?? false);

    toggleWatchlist(): void {
        const ticker = this.report()?.stock_bar.ticker;
        if (!ticker) return;

        if (this.isWatchlisted()) {
            this.apiService
                .removeFromWatchlist({ ticker })
                .pipe(takeUntilDestroyed(this.destroyRef))
                .subscribe({ next: () => this.isWatchlisted.set(false) });
        } else {
            this.apiService
                .addToWatchlist({ ticker })
                .pipe(takeUntilDestroyed(this.destroyRef))
                .subscribe({ next: () => this.isWatchlisted.set(true) });
        }
    }

    // ── Price chart ───────────────────────────────────────────────────────────

    readonly chartCanvas = viewChild<ElementRef<HTMLCanvasElement>>('tickerChart');
    private chartInstance: Chart | null = null;

    readonly isChartLoading = signal(false);

    readonly chartPeriods = signal<{ label: string; value: ChartPeriod; active: boolean }[]>([
        { label: '1D', value: '1d', active: false },
        { label: '1W', value: '1w', active: false },
        { label: '1M', value: '1m', active: false },
        { label: '3M', value: '3m', active: true },
        { label: '6M', value: '6m', active: false },
        { label: 'YTD', value: 'ytd', active: false },
        { label: '1Y', value: '1y', active: false },
        { label: '5Y', value: '5y', active: false },
    ]);

    private readonly chartPeriodTrigger$ = new BehaviorSubject<ChartPeriod>('3m');

    private readonly chartData = toSignal(
        combineLatest([this.ticker$, this.chartPeriodTrigger$]).pipe(
            tap(() => this.isChartLoading.set(true)),
            switchMap(([ticker, period]) => this.apiService.getStockChart(ticker, period)),
            tap(() => this.isChartLoading.set(false)),
        ),
    );
    readonly diffOverPeriodPct = computed(() => {
        const prices = this.chartData()?.prices;
        if (!prices) return NaN;
        const first = prices[0].close;
        const last = prices[prices.length - 1].close;
        return (last * 100) / first - 100;
    });

    setChartPeriod(value: ChartPeriod): void {
        if (this.isChartLoading()) return;
        this.chartPeriods.update((periods) => periods.map((p) => ({ ...p, active: p.value === value })));
        this.chartPeriodTrigger$.next(value);
    }

    constructor() {
        this.destroyRef.onDestroy(() => this.chartInstance?.destroy());

        effect(() => {
            const data = this.chartData();
            const report = this.report();
            const canvasRef = this.chartCanvas();

            if (data && report && canvasRef?.nativeElement) {
                requestAnimationFrame(() => this.renderChart(canvasRef.nativeElement, data, report.stock_bar.currency));
            }
        });
    }

    // ── Chart rendering ───────────────────────────────────────────────────────

    private renderChart(canvas: HTMLCanvasElement, chartResponse: PriceChartResponse, currency: string): void {
        if (this.chartInstance) this.chartInstance.destroy();
        const ctx = canvas.getContext('2d');
        if (!ctx || !chartResponse.prices.length) return;

        const { prices, annotations } = chartResponse;
        const labels = prices.map((p) => p.date);
        const closes = prices.map((p) => p.close);

        // Map annotation dates to the closest price index
        const annotationColor: Record<string, string> = {
            high: '#3b82f6',
            low: '#6366f1',
            dividend: '#10b981',
            split: '#f59e0b',
            earnings: '#8b5cf6',
        };

        const annotByIndex = new Map<number, (typeof annotations)[number][]>();
        annotations.forEach((ann) => {
            let closestIdx = -1,
                minDiff = Infinity;
            labels.forEach((d, i) => {
                const diff = Math.abs(new Date(d).getTime() - new Date(ann.date).getTime());
                if (diff < minDiff) {
                    minDiff = diff;
                    closestIdx = i;
                }
            });
            if (closestIdx !== -1 && minDiff < 4 * 24 * 60 * 60 * 1000) {
                if (!annotByIndex.has(closestIdx)) annotByIndex.set(closestIdx, []);
                annotByIndex.get(closestIdx)!.push(ann);
            }
        });

        // Determine line color from overall performance
        const netChange = closes[closes.length - 1] - closes[0];
        const lineColor = netChange >= 0 ? '#10b981' : '#f43f5e';

        const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
        gradient.addColorStop(0, lineColor + '40');
        gradient.addColorStop(1, lineColor + '04');

        // ── Plugins ─────────────────────────────────────────────────────────────

        const crosshairPlugin: Plugin = {
            id: 'crosshair',
            afterDraw: (chart) => {
                if (!chart.tooltip?.getActiveElements()?.length) return;
                const x = chart.tooltip.getActiveElements()[0].element.x;
                const { top, bottom } = chart.scales['y'];
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.lineWidth = 1;
                ctx.strokeStyle = '#cbd5e1';
                ctx.setLineDash([4, 4]);
                ctx.stroke();
                ctx.restore();
            },
        };

        const annotationsPlugin: Plugin = {
            id: 'priceAnnotations',
            afterDatasetsDraw: (chart: any) => {
                const ctx = chart.ctx;
                annotByIndex.forEach((anns, idx) => {
                    const meta = chart.getDatasetMeta(0);
                    if (!meta?.data[idx]) return;

                    const point = meta.data[idx];
                    const y = point.y;
                    const x = meta.data[idx].x;

                    anns.forEach((ann, i) => {
                        const color = annotationColor[ann.type] ?? '#94a3b8';
                        const chartArea = chart.chartArea;

                        // Wait to set the font so measureText is accurate
                        ctx.font = 'bold 9px ui-sans-serif, system-ui, sans-serif';
                        const flagW = ctx.measureText(ann.label).width + 8;

                        // Check if half the badge plus padding exceeds the right edge
                        const nearRightEdge = x + flagW / 2 > chartArea.right - 2;
                        const yOffset = y - 14 - i * 8;

                        // Shift X leftward if near edge, otherwise use normal X
                        const annotX = nearRightEdge ? x - flagW / 2 - 4 : x;
                        const annotY = nearRightEdge ? yOffset - 8 : yOffset;

                        ctx.save();
                        ctx.beginPath();
                        const stemStartY = i === 0 ? y - 3 : y - 14 - (i - 1) * 10;
                        ctx.moveTo(x, stemStartY);
                        ctx.lineTo(annotX, annotY);
                        ctx.strokeStyle = color;
                        ctx.lineWidth = 1;
                        ctx.globalAlpha = 0.45;
                        ctx.stroke();

                        ctx.globalAlpha = 1;

                        // Draw the flag using annotX and annotY to follow the arrow
                        ctx.fillStyle = color + '1A';
                        ctx.strokeStyle = color + '88';
                        ctx.lineWidth = 0.5;
                        ctx.beginPath();
                        ctx.roundRect(annotX - flagW / 2, annotY - 16, flagW, 16, 2);
                        ctx.fill();
                        ctx.stroke();

                        // Draw the text using annotX and annotY
                        ctx.fillStyle = color;
                        // Font is already set earlier for measurement, but reapplying is fine
                        ctx.font = 'bold 9px ui-sans-serif, system-ui, sans-serif';
                        ctx.textAlign = 'center';
                        ctx.textBaseline = 'middle';
                        ctx.fillText(ann.label, annotX, annotY - 8);

                        ctx.restore();
                    });
                });
            },
        };

        const config: ChartConfiguration = {
            type: 'line',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Close',
                        data: closes,
                        borderColor: lineColor,
                        backgroundColor: gradient,
                        borderWidth: 2,
                        fill: true,
                        tension: 0.2,
                        pointRadius: 0,
                        pointHoverRadius: 5,
                        pointBackgroundColor: '#ffffff',
                        pointBorderColor: lineColor,
                        pointBorderWidth: 2,
                    },
                ],
            },
            plugins: [crosshairPlugin, annotationsPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { top: 24, right: 0, left: 0, bottom: 0 } },
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: {
                            maxTicksLimit: 8,
                            color: '#64748b',
                            font: { family: 'Inter, ui-sans-serif, system-ui, sans-serif', size: 10, weight: 500 },
                        },
                        border: { display: false },
                    },
                    y: {
                        position: 'left',
                        grid: { color: '#f1f5f9', tickLength: 0 },
                        border: { display: false, dash: [4, 4] },
                        ticks: {
                            callback: (v) =>
                                new Intl.NumberFormat(navigator.language, {
                                    notation: 'compact',
                                    style: 'currency',
                                    currency: currency || 'USD',
                                }).format(Number(v)),
                            color: '#64748b',
                            font: { family: 'ui-monospace, SFMono-Regular, monospace', size: 10 },
                        },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: false,
                        external: (context) => {
                            const { chart, tooltip } = context;
                            let el = chart.canvas.parentNode?.querySelector('div.ticker-tooltip') as HTMLElement;
                            if (!el) {
                                el = document.createElement('div');
                                el.className =
                                    'ticker-tooltip absolute z-[100] pointer-events-none transition-all duration-200 -translate-y-1/2';
                                chart.canvas.parentNode?.appendChild(el);
                            }
                            if (tooltip.opacity === 0) {
                                el.style.opacity = '0';
                                return;
                            }

                            const i = tooltip.dataPoints[0].dataIndex;
                            const p = prices[i];
                            const close = p?.close ?? 0;
                            const open = p?.open ?? 0;
                            const fmt = (v: number) =>
                                new Intl.NumberFormat(navigator.language, {
                                    style: 'currency',
                                    currency: currency || 'USD',
                                }).format(v);

                            el.innerHTML = `
                <div class="bg-white/95 backdrop-blur-sm border border-slate-200/60 shadow-xl rounded-xl p-3 w-[200px]">
                  <p class="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">${tooltip.title[0] ?? ''}</p>
                  <div class="flex justify-between items-center mb-1">
                    <span class="text-xs font-bold text-slate-900">${fmt(close)}</span>
                    <span class="text-[10px] font-semibold ${close >= open ? 'text-emerald-600' : 'text-red-500'}">
                      ${close >= open ? '+' : ''}${(((close - open) / (open || 1)) * 100).toFixed(2)}%
                    </span>
                  </div>
                  <div class="grid grid-cols-2 gap-x-3 gap-y-0.5 mt-2 pt-2 border-t border-slate-100">
                    <span class="text-[10px] text-slate-400">Open</span>  <span class="text-[10px] font-mono font-bold text-slate-700">${fmt(p?.open ?? 0)}</span>
                    <span class="text-[10px] text-slate-400">High</span>  <span class="text-[10px] font-mono font-bold text-slate-700">${fmt(p?.high ?? 0)}</span>
                    <span class="text-[10px] text-slate-400">Low</span>   <span class="text-[10px] font-mono font-bold text-slate-700">${fmt(p?.low ?? 0)}</span>
                    <span class="text-[10px] text-slate-400">Vol</span>   <span class="text-[10px] font-mono font-bold text-slate-700">${new Intl.NumberFormat(navigator.language, { notation: 'compact' }).format(p?.volume ?? 0)}</span>
                  </div>
                </div>`;
                            el.style.opacity = '1';
                            const tw = 200,
                                cw = chart.canvas.clientWidth,
                                m = 12;
                            let left = tooltip.caretX - tw / 2;
                            if (left < m) left = m;
                            if (left + tw > cw - m) left = cw - tw - m;
                            el.style.left = left + 'px';
                            el.style.top = tooltip.caretY - 20 + 'px';
                        },
                    },
                },
            },
        };

        this.chartInstance = new Chart(canvas, config);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /**
     * Returns the current price's position (0–100) within the 52-week range.
     * Safe against null fields — returns 50 (midpoint) when data is unavailable.
     */
    getFiftyTwoWeekPosition(bar: StockBar): number {
        const { fifty_two_week_low: lo, fifty_two_week_high: hi, current_price: cp } = bar;
        if (lo == null || hi == null) return 50;
        const range = hi - lo;
        if (range === 0) return 50;
        return Math.max(0, Math.min(100, ((cp - lo) / range) * 100));
    }

    /** GridCell status → text colour class. */
    cellTextClass(status: string): string {
        switch (status) {
            case 'green':
                return 'text-green-700';
            case 'red':
                return 'text-red-700';
            case 'amber':
                return 'text-amber-600';
            default:
                return 'text-primary-900';
        }
    }

    /** GridCell status → dot colour class. */
    cellDotClass(status: string): string {
        switch (status) {
            case 'green':
                return 'bg-green-600';
            case 'red':
                return 'bg-red-600';
            case 'amber':
                return 'bg-amber-500';
            default:
                return 'bg-neutral-400';
        }
    }

    /** Sentiment → badge classes for news items. */
    sentimentClass(sentiment: string): string {
        switch (sentiment) {
            case 'positive':
                return 'bg-emerald-50 text-emerald-600';
            case 'negative':
                return 'bg-red-50 text-red-500';
            default:
                return 'bg-primary-50 text-primary-400';
        }
    }

    /** Safe division-based formatter for large numbers displayed in billions. */
    formatBillions(value: number | null, currency: string): string {
        if (value == null) return 'N/A';
        return `${currency}${(value / 1_000_000_000).toFixed(2)}B`;
    }

    /** Multiplies a 0–1 fraction to a percentage string; returns "N/A" on null. */
    formatPct(value: number | null, decimals = 1): string {
        if (value == null) return 'N/A';
        return `${(value * 100).toFixed(decimals)}%`;
    }

    /** Safe `toFixed` for nullable numbers; returns fallback on null. */
    safeFixed(value: number | null | undefined, decimals = 2, fallback = 'N/A'): string {
        if (value == null) return fallback;
        return value.toFixed(decimals);
    }

    /** Safe access into risk_gauge.components Record — returns null if key missing. */
    riskComponent(components: Record<string, number>, key: string): number | null {
        return components?.[key] ?? null;
    }

    getSignalLabel(signal: string | null | undefined): {
        text: string;
        badgeClass: string;
        textClass: string;
        barClass: string;
    } | null {
        if (!signal) return null;

        const map: Record<string, { text: string; badgeClass: string; textClass: string; barClass: string }> = {
            BUY: {
                text: 'BUY',
                badgeClass: 'bg-green-100 text-green-700',
                textClass: 'text-green-600',
                barClass: 'bg-green-500',
            },
            HOLD: {
                text: 'HOLD',
                badgeClass: 'bg-amber-100 text-amber-700',
                textClass: 'text-amber-600',
                barClass: 'bg-amber-400',
            },
            SELL: {
                text: 'SELL',
                badgeClass: 'bg-red-100 text-red-700',
                textClass: 'text-red-600',
                barClass: 'bg-red-500',
            },
        };

        return map[signal.toUpperCase()] ?? null;
    }

    normalizeSectorLabel(str: string) {
        return str
            .split('_')
            .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
            .join(' ');
    }
}
