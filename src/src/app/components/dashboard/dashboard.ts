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
import { DecimalPipe, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import {
    BehaviorSubject,
    combineLatest,
    concatMap,
    debounceTime,
    forkJoin,
    from,
    startWith,
    Subject,
    switchMap,
    take,
    tap,
    toArray,
} from 'rxjs';
import { Chart, ChartConfiguration, Plugin, registerables } from 'chart.js';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { ButtonModule } from 'primeng/button';
import { FloatLabelModule } from 'primeng/floatlabel';
import { SkeletonModule } from 'primeng/skeleton';
import { SelectModule } from 'primeng/select';
import { DatePickerModule } from 'primeng/datepicker';
import { InputNumberModule } from 'primeng/inputnumber';
import { TextareaModule } from 'primeng/textarea';
import { TableModule } from 'primeng/table';
import { SelectButtonModule } from 'primeng/selectbutton';
import { MultiSelectModule } from 'primeng/multiselect';
import { ApiService } from '../../services/api.service';
import { UtilsService } from '../../services/utils.service';
import {
    AlertOut,
    ClosePricePoint,
    DashboardTotals,
    EnvelopeSeriesLine,
    EnvelopeSummary,
    EnvelopeStats,
    OverviewPeriod,
    TickerSearchResult,
    TransactionOut,
    TransactionType,
    UserSettingsOut,
    WatchlistRow,
    DepositPeriod,
} from '../../int';
import { CompactNumberPipe } from '../../shared/amount.normalize';
import { AlertsModalComponent } from '../../modals/alerts-modal/alerts-modal';
import { SettingsModalComponent } from '../../modals/settings-modal/settings-modal';
import { DialogService } from 'primeng/dynamicdialog';
import { YesNoModalComponent } from '../../modals/yes-no-modal/yes-no-modal.component';

Chart.register(...registerables);

// ---------------------------------------------------------------------------
// Transaction type display config — keyed on the full TransactionType union
// so the compiler will flag any missing member.
// ---------------------------------------------------------------------------
type TxDisplayConfig = { color: string; letter: string; displayValue: string; class: string };

@Component({
    selector: 'app-dashboard',
    standalone: true,
    imports: [
        DecimalPipe,
        DatePipe,
        RouterLink,
        ReactiveFormsModule,
        FormsModule,
        DialogModule,
        InputTextModule,
        FloatLabelModule,
        ButtonModule,
        SelectModule,
        DatePickerModule,
        SelectButtonModule,
        InputNumberModule,
        MultiSelectModule,
        TextareaModule,
        TableModule,
        SkeletonModule,
        CompactNumberPipe,
        AlertsModalComponent,
        SettingsModalComponent,
    ],
    templateUrl: './dashboard.html',
    styleUrls: ['./dashboard.scss'],
})
export class DashboardComponent {
    private readonly apiService = inject(ApiService);
    private readonly utilsService = inject(UtilsService);
    private readonly destroyRef = inject(DestroyRef);
    private readonly dialogService = inject(DialogService);

    // ── Static config ─────────────────────────────────────────────────────────

    readonly transactionTypes = this.utilsService.transactionTypes;
    readonly txConfig: Record<TransactionType, TxDisplayConfig> = {
        BUY: {
            color: '#3b82f6',
            letter: 'B',
            displayValue: 'BUY',
            class: 'bg-blue-50 text-blue-700 border-blue-200/60 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-700/40',
        },
        SELL: {
            color: '#f43f5e',
            letter: 'S',
            displayValue: 'SELL',
            class: 'bg-rose-50 text-rose-700 border-rose-200/60 dark:bg-rose-900/20 dark:text-rose-400 dark:border-rose-700/40',
        },
        DEPOSIT: {
            color: '#10b981',
            letter: 'D',
            displayValue: 'DEP',
            class: 'bg-emerald-50 text-emerald-700 border-emerald-200/60 dark:bg-emerald-900/20 dark:text-emerald-400 dark:border-emerald-700/40',
        },
        WITHDRAW: {
            color: '#f59e0b',
            letter: 'W',
            displayValue: 'WDW',
            class: 'bg-amber-50 text-amber-700 border-amber-200/60 dark:bg-amber-900/20 dark:text-amber-400 dark:border-amber-700/40',
        },
        DIVIDEND: {
            color: '#8b5cf6',
            letter: 'V', // "D" is used by DEPOSIT; "V" from diVidend
            displayValue: 'DIV',
            class: 'bg-violet-50 text-violet-700 border-violet-200/60 dark:bg-violet-900/20 dark:text-violet-400 dark:border-violet-700/40',
        },
    };
    public readonly multiSelectPt: any = {
        hiddenInput: {
            inputmode: 'none',
            readonly: true,
        },
    };

    // ── Ticker browser ────────────────────────────────────────────────────────

    readonly isTickerBrowserOpen = signal(false);
    readonly tickerQuery = signal('');
    readonly isSearchingTickers = signal(false);
    readonly tickerSearchResults = signal<TickerSearchResult[]>([]);
    readonly activePaletteIdx = signal(-1);
    readonly tickerSearchInput = viewChild<ElementRef<HTMLInputElement>>('tickerSearchInput');
    private tickerSearchDebounce: ReturnType<typeof setTimeout> | null = null;

    // ── Data pipeline ─────────────────────────────────────────────────────────

    readonly isRevealed = signal(false);
    readonly isChartLoading = signal(false);
    readonly benchmarkMode = signal(false);
    private readonly refreshTrigger$ = new Subject<void>();
    private readonly rangeTrigger$ = new BehaviorSubject<OverviewPeriod>('1y');

    private readonly data = toSignal(
        combineLatest([this.refreshTrigger$.pipe(startWith(undefined)), this.rangeTrigger$]).pipe(
            tap(() => this.isChartLoading.set(true)),
            switchMap(([, range]) =>
                forkJoin({
                    dashboard: this.apiService.getDashboard(),
                    overview: this.apiService.getEnvelopesOverview(range),
                }),
            ),
            tap(() => this.isChartLoading.set(false)),
        ),
    );

    readonly totals = computed<DashboardTotals>(
        () =>
            this.data()?.dashboard.totals ?? {
                total_value: 0,
                total_cash: 0,
                total_cost_basis: 0,
                total_pnl: 0,
                total_pnl_pct: 0,
                total_change_1d: 0,
                total_change_1d_pct: 0,
                net_deposits: { '30d': 0, '90d': 0, '180d': 0, '1y': 0, '5y': 0 },
                dividend_income_90d: 0,
            },
    );

    readonly currencySymbol = linkedSignal(() => this.data()?.dashboard.user_currency ?? '€');

    readonly depositEntries: Array<{ key: DepositPeriod; label: string }> = [
        { key: '30d', label: '1M' },
        { key: '90d', label: '3M' },
        { key: '1y', label: '1Y' },
    ];

    isLedgerFilterMode = signal<boolean>(false);
    selectedEnvelopes = signal<EnvelopeSummary[]>([]);
    selectedTypes = signal<string[]>([]);
    filteredTransactions = computed(() => {
        const allTransactions = this.transactions();
        const envFilter = this.selectedEnvelopes();
        const typeFilter = this.selectedTypes();

        return allTransactions.filter((trade) => {
            const matchesEnv = envFilter.length === 0 || envFilter.some((e) => e.name === trade.envelope_name);
            const matchesType = typeFilter.length === 0 || typeFilter.includes(trade.type);

            return matchesEnv && matchesType;
        });
    });

    readonly watchlist = signal<WatchlistRow[]>([]);
    readonly trending = signal<WatchlistRow[]>([]);
    readonly viewSettings = signal(false);

    readonly positions = computed(() => {
        const raw = this.data()?.dashboard.positions ?? [];
        return [...raw].sort((a, b) => {
            const envCmp = a.envelope_name.localeCompare(b.envelope_name);
            return envCmp !== 0 ? envCmp : b.current_value - a.current_value;
        });
    });

    readonly transactions = computed(() => this.data()?.dashboard.transactions ?? []);
    readonly envelopes = computed(() => this.data()?.dashboard.envelopes ?? []);
    readonly overviewStats = computed<EnvelopeStats | null>(() => this.data()?.overview.stats ?? null);
    readonly chartSeries = computed(() => this.data()?.overview.series ?? []);
    readonly chartDates = computed(() => this.data()?.overview.dates ?? []);
    readonly chartBenchmarkPct = computed(() => this.data()?.overview.benchmark_pct ?? []);
    readonly chartPortfolioPct = computed(() => this.data()?.overview.portfolio_pct ?? []);
    readonly isDarkMode = computed(() => this.utilsService.darkMode());
    /** True once the first API response has resolved — guards against flashing empty states on initial load. */
    readonly isDataLoaded = computed(() => this.data() !== undefined);

    /**
     * Case A: user has no portfolio data at all (no positions and no transactions).
     * Show the "add your first transaction" empty state instead of the chart card.
     */
    readonly hasAnyPortfolioData = computed(
        () =>
            (this.data()?.dashboard.positions.length ?? 0) > 0 || (this.data()?.dashboard.transactions.length ?? 0) > 0,
    );

    /**
     * Case B: user has data but none falls within the selected period.
     * The overview series will be present but all values arrays will be empty or zero.
     */
    readonly hasChartData = computed(() => this.chartSeries().some((s) => s.values.some((v) => v > 0)));

    /**
     * Per-envelope P&L / invested / allocation aggregates, computed once per
     * data change rather than once per row render in the group-header template.
     */
    readonly envelopeMetrics = computed(() => {
        const positions = this.data()?.dashboard.positions ?? [];
        return positions.reduce<
            Record<string, { invested: number; pnl: number; allocation: number; cost_basis: number }>
        >((acc, p) => {
            const key = p.envelope_name;
            if (!acc[key]) acc[key] = { invested: 0, pnl: 0, allocation: 0, cost_basis: 0 };
            acc[key].invested += p.current_value;
            acc[key].pnl += p.unrealized_pnl;
            acc[key].allocation += p.allocation_pct;
            acc[key].cost_basis += p.cost_basis;
            return acc;
        }, {});
    });

    // ── Settings modal ────────────────────────────────────────────────────────

    readonly settingsModalVisible = signal(false);

    onSettingsSaved(settings: UserSettingsOut): void {
        if (settings.currency) this.currencySymbol.set(settings.currency);
    }

    // ── Alerts modal ──────────────────────────────────────────────────────────

    readonly userAlerts = signal<AlertOut[]>([]);
    readonly alertsModalVisible = signal(false);
    readonly alertsModalTicker = signal<WatchlistRow>({} as WatchlistRow);

    hasAlertsFor(ticker: string): boolean {
        return this.userAlerts().some((a) => a.ticker === ticker);
    }

    openAlertsModal(ticker: WatchlistRow, event: Event): void {
        event.stopPropagation();
        this.alertsModalTicker.set(ticker);
        this.alertsModalVisible.set(true);
    }

    onAlertChanged(): void {
        this.apiService
            .getAlerts()
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: (alerts) => this.userAlerts.set(alerts),
            });
    }

    // ── Envelope dialog ───────────────────────────────────────────────────────

    readonly isEnvelopeDialogOpen = signal(false);
    readonly isCreatingEnvelope = signal(false);

    /**
     * Tracks which envelope is being edited.
     * null  → create mode ("New Envelope")
     * number → edit mode ("Edit Envelope") with that envelope's id
     */
    readonly editingEnvelopeId = signal<number | null>(null);
    get isEditingEnvelope(): boolean {
        return this.editingEnvelopeId() !== null;
    }

    readonly envelopeColors = ['#3b82f6', '#10b981', '#8b5cf6', '#f59e0b', '#f43f5e', '#06b6d4', '#1e293b', '#ec4899'];

    readonly envelopeForm = new FormGroup({
        name: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
        color: new FormControl('', { nonNullable: true, validators: [Validators.required] }),
    });

    // ── Time range selector ───────────────────────────────────────────────────

    readonly timeRanges = signal([
        { label: '1W', value: '1w' as OverviewPeriod, active: false },
        { label: '1M', value: '1mo' as OverviewPeriod, active: false },
        { label: '3M', value: '3mo' as OverviewPeriod, active: false },
        { label: '6M', value: '6mo' as OverviewPeriod, active: false },
        { label: 'YTD', value: 'ytd' as OverviewPeriod, active: false },
        { label: '1Y', value: '1y' as OverviewPeriod, active: true },
        { label: '3Y', value: '3y' as OverviewPeriod, active: false },
    ]);

    // ── Transaction dialog ────────────────────────────────────────────────────

    readonly isTransactionDialogOpen = signal(false);
    readonly isCreatingTransaction = signal(false);
    readonly transactionMode = signal<'form' | 'paste'>('form');
    readonly isParsingError = signal<string | null>(null);
    readonly sellTickerNotice = signal<{ message: string; isWarning: boolean } | null>(null);
    readonly dividendMode = signal<'flat' | 'per-share'>('flat');
    readonly bulkPasteContent = new FormControl('', { nonNullable: true });

    readonly transactionForm = new FormGroup({
        type: new FormControl<TransactionType>('BUY', { nonNullable: true, validators: [Validators.required] }),
        envelope_name: new FormControl<string | null>(null, [Validators.required]),
        date: new FormControl<Date>(new Date(), { nonNullable: true, validators: [Validators.required] }),
        price: new FormControl<number | null>(null, [Validators.required, Validators.min(0)]),
        ticker: new FormControl<string | null>(null),
        shares: new FormControl<number | null>(null),
        fees: new FormControl<number>(0, { nonNullable: true }),
        note: new FormControl<string | null>(null),
    });

    // ── Chart ─────────────────────────────────────────────────────────────────

    readonly chartCanvas = viewChild<ElementRef<HTMLCanvasElement>>('portfolioChart');
    private chartInstance: Chart | null = null;
    private readonly hiddenSeries = signal<Set<string>>(new Set());

    // ── Constructor ───────────────────────────────────────────────────────────

    constructor() {
        // Destroy the Chart.js instance when the component is torn down
        this.destroyRef.onDestroy(() => this.chartInstance?.destroy());

        // Dynamic validators: BUY/SELL require ticker + shares; cash flows do not
        this.transactionForm.controls.type.valueChanges.pipe(takeUntilDestroyed()).subscribe((type) => {
            const { ticker, shares, fees } = this.transactionForm.controls;
            if (type === 'BUY' || type === 'SELL') {
                ticker.setValidators([Validators.required]);
                shares.setValidators([Validators.required, Validators.min(0.000001)]);
            } else {
                ticker.clearValidators();
                shares.clearValidators();
                ticker.setValue(null);
                shares.setValue(null);
                fees.setValue(0);
            }
            ticker.updateValueAndValidity();
            shares.updateValueAndValidity();
            this.sellTickerNotice.set(null);
            this.dividendMode.set('flat');
        });

        // SELL auto-envelope: switch envelope to whichever holds the typed ticker
        this.transactionForm.controls.ticker.valueChanges
            .pipe(debounceTime(400), takeUntilDestroyed())
            .subscribe((raw) => {
                if (this.transactionForm.controls.type.value !== 'SELL') return;
                this.sellTickerNotice.set(null);

                const ticker = raw?.trim().toUpperCase();
                if (!ticker) return;

                const currentEnvelope = this.transactionForm.controls.envelope_name.value;
                const positions = this.positions();

                if (positions.some((p) => p.ticker === ticker && p.envelope_name === currentEnvelope)) return;

                const match = positions.find((p) => p.ticker === ticker);
                if (match) {
                    this.transactionForm.controls.envelope_name.setValue(match.envelope_name);
                    this.sellTickerNotice.set({
                        message: `Envelope auto-set to "${match.envelope_name}"`,
                        isWarning: false,
                    });
                    return;
                }

                this.sellTickerNotice.set({ message: `${ticker} is not held in any envelope.`, isWarning: true });
            });

        // Re-render the chart whenever the data signals change
        effect(() => {
            const series = this.chartSeries();
            const dates = this.chartDates();
            const txs = this.transactions();
            const benchmarkMode = this.benchmarkMode();
            const benchmarkPct = this.chartBenchmarkPct();
            const portfolioPct = this.chartPortfolioPct();
            const isDarkMode = this.isDarkMode();
            const canvasRef = this.chartCanvas();

            if (series.length > 0 && dates.length > 0 && canvasRef?.nativeElement) {
                requestAnimationFrame(() =>
                    this.renderChart(
                        canvasRef.nativeElement,
                        dates,
                        series,
                        txs,
                        benchmarkMode,
                        benchmarkPct,
                        portfolioPct,
                        isDarkMode,
                    ),
                );
            }
        });

        effect(() => {
            const rows = this.data()?.dashboard.watchlist;
            if (rows) this.watchlist.set(rows);
        });

        this.apiService
            .getAlerts()
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: (alerts) => this.userAlerts.set(alerts),
            });
    }

    // ── Chart rendering (preserved as-is per product spec) ───────────────────

    private renderChart(
        canvas: HTMLCanvasElement,
        dates: string[],
        series: EnvelopeSeriesLine[],
        transactions: TransactionOut[],
        benchmarkMode: boolean,
        benchmarkPct: (number | null)[],
        portfolioPct: (number | null)[],
        isDarkMode: boolean,
    ): void {
        if (this.chartInstance) {
            this.chartInstance.destroy();
        }

        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const gridColor = isDarkMode ? '#3f3f46' : '#f1f5f9';
        const tickColor = isDarkMode ? '#a1a1aa' : '#64748b';
        const crosshairColor = isDarkMode ? '#52525b' : '#cbd5e1';
        const pointBg = isDarkMode ? '#27272a' : '#ffffff';

        const txByDateIndex = new Map<number, TransactionOut[]>();
        transactions.forEach((tx) => {
            const tTime = new Date(tx.date.slice(0, 10)).getTime();
            if (isNaN(tTime)) return;

            let closestIdx = -1;
            let minDiff = Infinity;

            dates.forEach((d, i) => {
                const dTime = new Date(d).getTime();
                if (isNaN(dTime)) return;
                const diff = Math.abs(tTime - dTime);
                if (diff < minDiff) {
                    minDiff = diff;
                    closestIdx = i;
                }
            });

            if (closestIdx !== -1 && minDiff < 4 * 24 * 60 * 60 * 1000) {
                if (!txByDateIndex.has(closestIdx)) txByDateIndex.set(closestIdx, []);
                txByDateIndex.get(closestIdx)!.push(tx);
            }
        });

        const transactionMarkersPlugin: Plugin = {
            id: 'transactionMarkers',
            afterDatasetsDraw: (chart: any) => {
                const ctx = chart.ctx;
                const datasets = chart.data.datasets;

                const drawBubblesAt = (dsIndex: number, pointIndex: number, txList: TransactionOut[]) => {
                    const meta = chart.getDatasetMeta(dsIndex);
                    if (!meta?.data[pointIndex]) return;
                    const { x, y } = meta.data[pointIndex];
                    const uniqueTypes = Array.from(new Set(txList.map((tx) => tx.type)));
                    uniqueTypes.forEach((type, i) => {
                        const tConf = this.txConfig[type];
                        const color = tConf.color;
                        const letter = tConf.letter;
                        const bubbleR = 6;
                        const connectorLen = 10;
                        const bubbleLift = 8;
                        const chartArea = chart.chartArea;
                        const nearRightEdge = x + bubbleR + connectorLen > chartArea.right - 2;
                        const yOffset = y - 14 - i * 8;
                        const bubbleX = nearRightEdge ? x - connectorLen - bubbleR : x;
                        const bubbleY = nearRightEdge ? yOffset - bubbleLift : yOffset;

                        ctx.save();
                        ctx.beginPath();
                        const stemStartY = i === 0 ? y - 3 : y - 14 - (i - 1) * 10;
                        ctx.moveTo(x, stemStartY);
                        ctx.lineTo(x, yOffset);
                        ctx.strokeStyle = color;
                        ctx.lineWidth = 1;
                        ctx.globalAlpha = 0.35;
                        ctx.stroke();

                        if (nearRightEdge) {
                            ctx.beginPath();
                            ctx.moveTo(x, yOffset);
                            ctx.lineTo(bubbleX, bubbleY);
                            ctx.strokeStyle = color;
                            ctx.lineWidth = 1;
                            ctx.globalAlpha = 0.45;
                            ctx.stroke();
                        }

                        ctx.globalAlpha = 1;
                        ctx.beginPath();
                        ctx.arc(bubbleX, bubbleY, bubbleR, 0, Math.PI * 2);
                        ctx.fillStyle = color;
                        ctx.fill();
                        ctx.lineWidth = 1.5;
                        ctx.strokeStyle = this.isDarkMode() ? '#000000' : '#ffffff';
                        ctx.stroke();

                        ctx.fillStyle = '#ffffff';
                        ctx.font = 'bold 8px ui-sans-serif, system-ui, sans-serif';
                        ctx.textAlign = 'center';
                        ctx.textBaseline = 'middle';
                        ctx.fillText(letter, bubbleX, bubbleY + 0.5);
                        ctx.restore();
                    });
                };

                if (!benchmarkMode) {
                    txByDateIndex.forEach((dayTxs, index) => {
                        const txByEnv = new Map<string, TransactionOut[]>();
                        dayTxs.forEach((t) => {
                            if (!txByEnv.has(t.envelope_name)) txByEnv.set(t.envelope_name, []);
                            txByEnv.get(t.envelope_name)!.push(t);
                        });
                        txByEnv.forEach((envTxs, envName) => {
                            const dsIndex = datasets.findIndex((ds: any) => ds.label === envName);
                            if (dsIndex !== -1 && chart.isDatasetVisible(dsIndex))
                                drawBubblesAt(dsIndex, index, envTxs);
                        });
                    });
                }
            },
        };

        const crosshairPlugin: Plugin = {
            id: 'crosshair',
            afterDraw: (chart) => {
                if (!chart.tooltip?.getActiveElements()?.length) return;
                const activePoint = chart.tooltip.getActiveElements()[0];
                const ctx = chart.ctx;
                const x = activePoint.element.x;
                const topY = chart.scales['y'].top;
                const bottomY = chart.scales['y'].bottom;

                ctx.save();
                ctx.beginPath();
                ctx.moveTo(x, topY);
                ctx.lineTo(x, bottomY);
                ctx.lineWidth = 1;
                ctx.strokeStyle = crosshairColor;
                ctx.setLineDash([4, 4]);
                ctx.stroke();
                ctx.restore();
            },
        };

        const portfolioLineColor = '#3b82f6';
        const benchmarkLineColor = '#f59e0b';

        const datasets = benchmarkMode
            ? (() => {
                  const gradPort = ctx.createLinearGradient(0, 0, 0, canvas.height);
                  gradPort.addColorStop(0, portfolioLineColor + '30');
                  gradPort.addColorStop(1, portfolioLineColor + '05');
                  return [
                      {
                          label: 'Portfolio',
                          data: portfolioPct,
                          borderColor: portfolioLineColor,
                          backgroundColor: gradPort,
                          borderWidth: 2,
                          fill: true,
                          tension: 0.3,
                          pointRadius: 0,
                          pointHoverRadius: 6,
                          pointBackgroundColor: pointBg,
                          pointBorderColor: portfolioLineColor,
                          pointBorderWidth: 2,
                      },
                      {
                          label: 'WPEA.PA',
                          data: benchmarkPct,
                          borderColor: benchmarkLineColor,
                          backgroundColor: 'transparent',
                          borderWidth: 2,
                          borderDash: [5, 4],
                          fill: false,
                          tension: 0.3,
                          pointRadius: 0,
                          pointHoverRadius: 6,
                          pointBackgroundColor: pointBg,
                          pointBorderColor: benchmarkLineColor,
                          pointBorderWidth: 2,
                      },
                  ];
              })()
            : series
                  .filter((s) => s.name !== 'Total')
                  .map((s) => {
                      const colorSet = s.color;
                      const gradient = ctx.createLinearGradient(0, 0, 0, canvas.height);
                      gradient.addColorStop(0, colorSet + '60');
                      gradient.addColorStop(1, colorSet + '0D');

                      return {
                          label: s.name,
                          data: s.values,
                          borderColor: colorSet,
                          backgroundColor: gradient,
                          borderWidth: 2,
                          fill: true,
                          tension: 0.3,
                          pointRadius: 0,
                          pointHoverRadius: 6,
                          pointBackgroundColor: pointBg,
                          pointBorderColor: colorSet,
                          pointBorderWidth: 2,
                      };
                  });

        const config: ChartConfiguration = {
            type: 'line',
            data: { labels: dates, datasets },
            plugins: [crosshairPlugin, transactionMarkersPlugin],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { top: 30, right: 0, left: 0, bottom: 0 } },
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: {
                            maxTicksLimit: 7,
                            color: tickColor,
                            font: { family: 'Inter, ui-sans-serif, system-ui, sans-serif', size: 11, weight: 500 },
                        },
                        border: { display: false },
                    },
                    y: {
                        stacked: !benchmarkMode,
                        grid: { color: gridColor, tickLength: 0 },
                        border: { display: false, dash: [4, 4] },
                        ticks: {
                            callback: (value) =>
                                benchmarkMode
                                    ? (Number(value) >= 0 ? '+' : '') + Number(value).toFixed(1) + '%'
                                    : new Intl.NumberFormat(navigator.language, {
                                          notation: 'compact',
                                          maximumFractionDigits: 1,
                                      }).format(Number(value)),
                            color: tickColor,
                            font: { family: 'ui-monospace, SFMono-Regular, monospace', size: 11 },
                        },
                    },
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: false,
                        position: 'nearest',
                        external: (context) => {
                            const { chart, tooltip } = context;

                            let tooltipEl = chart.canvas.parentNode?.querySelector(
                                'div.custom-chartjs-tooltip',
                            ) as HTMLElement;
                            if (!tooltipEl) {
                                tooltipEl = document.createElement('div');
                                tooltipEl.className =
                                    'custom-chartjs-tooltip absolute z-[100] pointer-events-none transition-all duration-300 ease-out transform -translate-y-1/2';
                                chart.canvas.parentNode?.appendChild(tooltipEl);
                            }

                            if (tooltip.opacity === 0) {
                                tooltipEl.style.opacity = '0';
                                tooltipEl.style.transform = 'translateY(-50%) scale(0.95)';
                                return;
                            }

                            const formatCurrency = (val: number) =>
                                new Intl.NumberFormat(navigator.language, {
                                    notation: 'compact',
                                    maximumFractionDigits: 1,
                                }).format(val);
                            const formatNumber = (val: number) =>
                                new Intl.NumberFormat(navigator.language, { maximumFractionDigits: 4 }).format(val);

                            const index = tooltip.dataPoints[0].dataIndex;
                            const dayTxs = txByDateIndex.get(index) || [];

                            let html = `<div class="bg-white/95 dark:bg-zinc-900/95 backdrop-blur-md border border-slate-200/60 dark:border-zinc-700/60 shadow-2xl shadow-slate-200/50 dark:shadow-zinc-900/50 rounded-2xl p-4 w-[280px]">`;
                            html += `<p class="text-xs font-extrabold text-slate-400 dark:text-zinc-500 mb-3 uppercase tracking-widest">${tooltip.title[0]}</p>`;

                            if (benchmarkMode) {
                                let portVal: number | null = null;
                                let benchVal: number | null = null;
                                tooltip.dataPoints.forEach((dp) => {
                                    const val = dp.raw as number | null;
                                    if (val === null) return;
                                    const color = (dp.dataset as any).borderColor || '#000';
                                    const sign = val >= 0 ? '+' : '';
                                    html += `
                  <div class="flex justify-between items-center gap-4 mb-2">
                    <div class="flex items-center gap-2">
                      <span class="w-2.5 h-2.5 rounded-full ring-2 ring-white dark:ring-zinc-900 shadow-sm" style="background-color: ${color}"></span>
                      <span class="text-[13px] font-semibold text-slate-700 dark:text-zinc-300">${dp.dataset.label}</span>
                    </div>
                    <span class="text-[13px] font-medium text-slate-900 dark:text-zinc-50 font-mono tracking-tight">${sign}${val.toFixed(2)}%</span>
                  </div>`;
                                    if (dp.dataset.label === 'Portfolio') portVal = val;
                                    else benchVal = val;
                                });
                                if (portVal !== null && benchVal !== null) {
                                    const delta = portVal - benchVal;
                                    const sign = delta >= 0 ? '+' : '';
                                    const deltaColor = delta >= 0 ? '#10b981' : '#f43f5e';
                                    html += `
                <div class="mt-3 pt-3 border-t border-slate-100/80 dark:border-zinc-700/80 flex justify-between items-center">
                  <span class="text-[13px] font-extrabold text-slate-900 dark:text-zinc-50">vs WPEA.PA</span>
                  <span class="text-sm font-extrabold font-mono tracking-tight" style="color:${deltaColor}">${sign}${delta.toFixed(2)}%</span>
                </div>`;
                                }
                            } else {
                                let total = 0;
                                tooltip.dataPoints.forEach((dp) => {
                                    const color = (dp.dataset as any).borderColor || '#000';
                                    const val = dp.raw as number;
                                    total += val;
                                    html += `
                  <div class="flex justify-between items-center gap-4 mb-2">
                    <div class="flex items-center gap-2">
                      <span class="w-2.5 h-2.5 rounded-full ring-2 ring-white dark:ring-zinc-900 shadow-sm" style="background-color: ${color}"></span>
                      <span class="text-[13px] font-semibold text-slate-700 dark:text-zinc-300">${dp.dataset.label}</span>
                    </div>
                    <span class="text-[13px] font-medium text-slate-900 dark:text-zinc-50 font-mono tracking-tight">${formatCurrency(val)} €</span>
                  </div>`;
                                });
                                html += `
                <div class="mt-3 pt-3 border-t border-slate-100/80 dark:border-zinc-700/80 flex justify-between items-center">
                  <span class="text-[13px] font-extrabold text-slate-900 dark:text-zinc-50">Total</span>
                  <span class="text-sm font-extrabold text-slate-900 dark:text-zinc-50 font-mono tracking-tight">${formatCurrency(total)} €</span>
                </div>`;
                            }

                            if (!benchmarkMode && dayTxs.length > 0) {
                                html += `
                  <div class="mt-4 pt-4 border-t border-slate-200 dark:border-zinc-700 border-dashed">
                    <p class="text-[10px] font-extrabold text-slate-400 dark:text-zinc-500 uppercase tracking-widest mb-3">Transactions</p>
                    <div class="flex flex-col gap-2.5">`;

                                const groupedTxs = new Map<string, TransactionOut[]>();
                                dayTxs.forEach((tx) => {
                                    const key = `${tx.type}_${tx.envelope_name}`;
                                    if (!groupedTxs.has(key)) groupedTxs.set(key, []);
                                    groupedTxs.get(key)!.push(tx);
                                });

                                groupedTxs.forEach((txs) => {
                                    const firstTx = txs[0];
                                    const type = firstTx.type;
                                    const tConf = this.txConfig[type];
                                    const badgeClass = tConf.class;
                                    const actionLabel = tConf.displayValue;

                                    html += `<div class="bg-slate-50 dark:bg-zinc-800 border border-slate-100 dark:border-zinc-700 rounded-xl p-2.5 flex flex-col gap-2">`;
                                    html += `
                    <div class="flex justify-between items-center">
                      <div class="flex items-center gap-2">
                        <span class="text-[9px] font-black uppercase px-1.5 py-0.5 rounded flex items-center border ${badgeClass}">${actionLabel}</span>
                        <span class="text-[10px] font-bold text-slate-400 dark:text-zinc-500 uppercase tracking-wider">${firstTx.envelope_name}</span>
                      </div>
                    </div>`;

                                    html += `<div class="flex flex-col gap-1.5 mt-0.5">`;
                                    let groupTotal = 0;

                                    if (type === 'BUY' || type === 'SELL') {
                                        txs.forEach((tx) => {
                                            const txTotal = (tx.shares || 0) * (tx.price || 0);
                                            groupTotal += txTotal;
                                            html += `
                        <div class="flex justify-between items-center">
                          <div class="flex items-center gap-2">
                            <span class="text-xs font-bold text-slate-900 dark:text-zinc-50">${tx.ticker}</span>
                            <span class="text-[10px] text-slate-500 dark:text-zinc-400 font-medium bg-white dark:bg-zinc-700 px-1.5 py-0.5 border border-slate-200 dark:border-zinc-600 rounded-md">
                              ${formatNumber(tx.shares)} × ${formatCurrency(tx.price)} €
                            </span>
                          </div>
                          <span class="text-xs font-bold text-slate-900 dark:text-zinc-50 font-mono">${formatCurrency(txTotal)} €</span>
                        </div>`;
                                        });
                                        if (txs.length > 1) {
                                            html += `
                        <div class="flex justify-between items-center pt-1.5 mt-0.5 border-t border-slate-200/60 dark:border-zinc-600/60">
                          <span class="text-[9px] font-bold text-slate-400 dark:text-zinc-500 uppercase tracking-widest">Total ${actionLabel}</span>
                          <span class="text-xs font-bold text-slate-900 dark:text-zinc-50 font-mono">${formatCurrency(groupTotal)} €</span>
                        </div>`;
                                        }
                                    } else {
                                        const sign = type === 'DEPOSIT' || type === 'DIVIDEND' ? '+' : '-';
                                        txs.forEach((tx) => {
                                            groupTotal += tx.price;
                                            html += `
                        <div class="flex justify-end items-center">
                          <span class="text-[11px] font-medium text-slate-600 dark:text-zinc-300 font-mono">${sign}${formatCurrency(tx.price)} €</span>
                        </div>`;
                                        });
                                        if (txs.length > 1) {
                                            html += `
                        <div class="flex justify-end items-center pt-1.5 mt-0.5 border-t border-slate-200/60 dark:border-zinc-600/60">
                          <span class="text-xs font-extrabold text-slate-900 dark:text-zinc-50 font-mono">${sign}${formatCurrency(groupTotal)} €</span>
                        </div>`;
                                        }
                                    }

                                    html += `</div></div>`;
                                });

                                html += `</div></div>`;
                            }

                            html += `</div>`;
                            tooltipEl.innerHTML = html;

                            const tooltipWidth = 280;
                            const canvasWidth = chart.canvas.clientWidth;
                            const margin = 12;
                            let left = tooltip.caretX - tooltipWidth / 2;
                            if (left < margin) left = margin;
                            if (left + tooltipWidth > canvasWidth - margin) left = canvasWidth - tooltipWidth - margin;

                            tooltipEl.style.opacity = '1';
                            tooltipEl.style.transform = 'translateY(-50%) scale(1)';
                            tooltipEl.style.left = left + 'px';
                            tooltipEl.style.top = (+tooltip.caretY - 24).toString() + 'px';
                        },
                    },
                },
            },
        };

        this.chartInstance = new Chart(canvas, config);

        // Restore hidden series — safe: rAF context is not effect-tracked
        const hidden = this.hiddenSeries();
        if (hidden.size > 0) {
            hidden.forEach((name) => {
                const idx = this.chartInstance!.data.datasets.findIndex((ds) => ds.label === name);
                if (idx !== -1) this.chartInstance!.setDatasetVisibility(idx, false);
            });
            this.chartInstance.update('none');
        }
    }

    // ── Sparkline helper ──────────────────────────────────────────────────────

    /**
     * Converts `history_7d` close prices into an SVG path string using smooth
     * cubic bezier curves. Pass `fill = true` to close the path as an area fill.
     * Returns an empty string for insufficient data (renders nothing gracefully).
     */
    sparklinePath(points: ClosePricePoint[], fill = false, w = 120, h = 36, pad = 3): string {
        if (!points?.length || points.length < 2) return '';

        const closes = points.map((p) => p.close);
        const min = Math.min(...closes);
        const max = Math.max(...closes);
        const range = max - min || 1;

        const coords = closes.map((v, i) => ({
            x: (i / (closes.length - 1)) * w,
            y: pad + (1 - (v - min) / range) * (h - pad * 2),
        }));

        let d = `M${coords[0].x},${coords[0].y}`;
        for (let i = 1; i < coords.length; i++) {
            const p = coords[i - 1];
            const c = coords[i];
            const mx = (p.x + c.x) / 2;
            d += ` C${mx},${p.y} ${mx},${c.y} ${c.x},${c.y}`;
        }

        if (fill) d += ` L${w},${h} L0,${h} Z`;
        return d;
    }

    // ── Envelope CRUD ─────────────────────────────────────────────────────────

    openEnvelopeDialog(fill?: EnvelopeSummary): void {
        this.envelopeForm.reset();
        this.editingEnvelopeId.set(fill?.id ?? null);
        if (fill) this.envelopeForm.patchValue({ name: fill.name, color: fill.color });
        this.isEnvelopeDialogOpen.set(true);
    }

    closeEnvelopeDialog(): void {
        this.isEnvelopeDialogOpen.set(false);
    }

    createEnvelope(): void {
        if (this.envelopeForm.invalid) return;
        this.isCreatingEnvelope.set(true);
        const payload = { ...this.envelopeForm.getRawValue(), name: this.envelopeForm.controls.name.value.trim() };

        this.apiService
            .addEnvelope(payload)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: () => {
                    this.isCreatingEnvelope.set(false);
                    this.closeEnvelopeDialog();
                    this.refreshTrigger$.next();
                },
                error: () => this.isCreatingEnvelope.set(false),
            });
    }

    updateEnvelope(): void {
        if (this.envelopeForm.invalid) return;
        const id = this.editingEnvelopeId();
        if (id === null) return;

        this.isCreatingEnvelope.set(true);
        const payload = { ...this.envelopeForm.getRawValue(), name: this.envelopeForm.controls.name.value.trim() };

        this.apiService
            .putEnvelope(id, payload)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: () => {
                    this.isCreatingEnvelope.set(false);
                    this.closeEnvelopeDialog();
                    this.refreshTrigger$.next();
                },
                error: () => this.isCreatingEnvelope.set(false),
            });
    }

    deleteEnvelope(): void {
        const id = this.editingEnvelopeId();
        if (id === null) return;

        this.isCreatingEnvelope.set(true);

        this.apiService
            .removeEnvelope(id)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: () => {
                    this.isCreatingEnvelope.set(false);
                    this.closeEnvelopeDialog();
                    this.refreshTrigger$.next();
                },
                error: () => this.isCreatingEnvelope.set(false),
            });
    }

    // ── Transaction CRUD ──────────────────────────────────────────────────────

    openTransactionDialog(): void {
        this.transactionForm.reset({
            type: 'BUY',
            envelope_name: null,
            date: new Date(),
            price: null,
            ticker: null,
            shares: null,
            fees: 0,
            note: null,
        });
        this.bulkPasteContent.reset();
        this.transactionMode.set('form');
        this.sellTickerNotice.set(null);
        this.dividendMode.set('flat');

        const availableEnvelopes = this.envelopes();
        if (availableEnvelopes.length > 0) {
            this.transactionForm.controls.envelope_name.setValue(availableEnvelopes[0].name);
        }

        this.isTransactionDialogOpen.set(true);
    }

    closeTransactionDialog(): void {
        this.isTransactionDialogOpen.set(false);
        this.sellTickerNotice.set(null);
    }

    setTransactionMode(mode: 'form' | 'paste'): void {
        this.transactionMode.set(mode);
        this.isParsingError.set(null);
    }

    setDividendMode(mode: 'flat' | 'per-share'): void {
        this.dividendMode.set(mode);
        const { ticker, shares } = this.transactionForm.controls;
        if (mode === 'per-share') {
            ticker.setValidators([Validators.required]);
            shares.setValidators([Validators.required, Validators.min(0.000001)]);
        } else {
            ticker.clearValidators();
            shares.clearValidators();
            ticker.setValue(null);
            shares.setValue(null);
        }
        ticker.updateValueAndValidity();
        shares.updateValueAndValidity();
    }

    createTransaction(): void {
        if (this.transactionMode() === 'form') {
            if (this.transactionForm.invalid) return;
            this.isCreatingTransaction.set(true);

            const { type, envelope_name, date, price, ticker, shares, fees, note } = this.transactionForm.getRawValue();

            const d = new Date(date);
            const isoDate = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T00:00:00`;

            const payload = {
                type,
                envelope_name: envelope_name!,
                date: isoDate,
                price: price!,
                ticker: ticker?.trim().toUpperCase() ?? null,
                shares: shares ?? null,
                fees: fees || 0,
                note: note?.trim() || null,
            };

            this.apiService
                .addTransaction(payload)
                .pipe(takeUntilDestroyed(this.destroyRef))
                .subscribe({
                    next: () => {
                        this.isCreatingTransaction.set(false);
                        this.closeTransactionDialog();
                        this.refreshTrigger$.next();
                    },
                    error: () => this.isCreatingTransaction.set(false),
                });
        } else {
            // ── Bulk paste mode ─────────────────────────────────────────────────
            const text = this.bulkPasteContent.value;
            if (!text?.trim()) return;

            this.isCreatingTransaction.set(true);
            this.isParsingError.set(null);

            const lines = text.split('\n').filter((l) => l.trim() !== '');
            const parsed: any[] = [];

            try {
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i].trim();
                    const quoteMatch = line.match(/^(\S+)\s+"([^"]+)"\s+(.+)$/);
                    if (!quoteMatch) {
                        throw new Error(
                            `Line ${i + 1}: envelope_name must be wrapped in quotes. ` +
                                `Expected format: TYPE "Envelope Name" DD/MM/YYYY ...`,
                        );
                    }

                    const type: TransactionType = quoteMatch[1].toUpperCase() as TransactionType;
                    const envelope_name = quoteMatch[2];
                    const rest = quoteMatch[3].trim().split(/\s+/);

                    if (rest.length < 2) throw new Error(`Line ${i + 1} is missing required fields.`);

                    const [day, month, year] = rest[0].split('/');
                    const d = parseInt(day, 10),
                        m = parseInt(month, 10),
                        y = parseInt(year, 10);
                    if (
                        isNaN(d) ||
                        isNaN(m) ||
                        isNaN(y) ||
                        m < 1 ||
                        m > 12 ||
                        d < 1 ||
                        d > 31 ||
                        y < 1900 ||
                        y > 2100
                    ) {
                        throw new Error(`Line ${i + 1}: invalid date — use DD/MM/YYYY`);
                    }

                    const dateObj = new Date(y, m - 1, d);
                    const isoDate = new Date(dateObj.getTime() - dateObj.getTimezoneOffset() * 60000)
                        .toISOString()
                        .slice(0, 19);

                    let ticker = null,
                        shares = null,
                        price: number,
                        fees = 0;

                    if (type === 'BUY' || type === 'SELL') {
                        if (rest.length < 5)
                            throw new Error(`Line ${i + 1} (BUY/SELL): TYPE "Envelope" DATE TICKER SHARES PRICE FEES`);
                        ticker = rest[1].toUpperCase();
                        shares = parseFloat(rest[2].replace(',', '.'));
                        price = parseFloat(rest[3].replace(',', '.'));
                        fees = parseFloat(rest[4].replace(',', '.'));
                    } else if (
                        type === 'DIVIDEND' &&
                        rest.length >= 4 &&
                        isNaN(parseFloat(rest[1].replace(',', '.')))
                    ) {
                        // Per-share form: DIVIDEND "Envelope" DATE TICKER SHARES PRICE
                        ticker = rest[1].toUpperCase();
                        shares = parseFloat(rest[2].replace(',', '.'));
                        price = parseFloat(rest[3].replace(',', '.'));
                    } else {
                        price = parseFloat(rest[1].replace(',', '.'));
                    }

                    if (isNaN(price!)) throw new Error(`Line ${i + 1}: invalid numeric value.`);

                    parsed.push({ type, envelope_name, date: isoDate, ticker, shares, price, fees, note: null });
                }
            } catch (e: any) {
                this.isParsingError.set(e.message);
                this.isCreatingTransaction.set(false);
                return;
            }

            from(parsed)
                .pipe(
                    concatMap((tx) => this.apiService.addTransaction(tx)),
                    toArray(),
                    takeUntilDestroyed(this.destroyRef),
                )
                .subscribe({
                    next: () => {
                        this.isCreatingTransaction.set(false);
                        this.closeTransactionDialog();
                        this.refreshTrigger$.next();
                    },
                    error: () => {
                        this.isParsingError.set('API Error: some transactions failed to save.');
                        this.isCreatingTransaction.set(false);
                    },
                });
        }
    }

    downloadTransactions(envelopeName: string): void {
        this.apiService
            .exportTransactions(envelopeName)
            .pipe(take(1))
            .subscribe({
                next: (lines) => {
                    const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `${envelopeName}.txt`;
                    a.click();
                    URL.revokeObjectURL(url);
                },
            });
    }

    deleteTransaction(id: number): void {
        this.apiService
            .deleteTransaction(id)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: () => this.refreshTrigger$.next(),
                error: (err) => console.error('Failed to delete transaction', err),
            });
    }

    // ── Watchlist ─────────────────────────────────────────────────────────────

    isInWatchlist(ticker: string): boolean {
        return this.watchlist().some((w) => w.ticker === ticker);
    }

    addToWatchlist(ticker: string): void {
        if (this.isInWatchlist(ticker)) return;

        this.apiService
            .addToWatchlist({ ticker: ticker })
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: (res) => {
                    if (res.ticker) this.watchlist.update((list) => [...list, res.ticker!]);
                },
                error: (err) => console.error('Failed to add to watchlist', err),
            });
    }

    confirmRemoveFromWatchlist(ticker: WatchlistRow): void {
        const confirmModal = this.dialogService.open(YesNoModalComponent, {
            header: 'Confirm',
            modal: true,
            closable: true,
            dismissableMask: true,
            draggable: false,
            resizable: false,
            breakpoints: {
                '640px': '90vw',
            },
            data: `Unfollow ${ticker.ticker} (${ticker.name})?`,
        })!;

        confirmModal.onClose.subscribe({
            next: (bool: boolean) => {
                if (!bool) return;
                this.removeFromWatchlist(ticker.ticker);
            },
        });
    }

    removeFromWatchlist(ticker: string): void {
        this.apiService
            .removeFromWatchlist({ ticker })
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: () => this.watchlist.update((list) => list.filter((w) => w.ticker !== ticker)),
                error: (err) => console.error('Failed to remove from watchlist', err),
            });
    }

    // ── Time range ────────────────────────────────────────────────────────────

    isSeriesHidden(name: string): boolean {
        return this.hiddenSeries().has(name);
    }

    toggleSeries(name: string): void {
        if (!this.chartInstance) return;
        const idx = this.chartInstance.data.datasets.findIndex((ds) => ds.label === name);
        if (idx === -1) return;
        const currentlyVisible = this.chartInstance.isDatasetVisible(idx);
        this.chartInstance.setDatasetVisibility(idx, !currentlyVisible);
        this.chartInstance.update('none');
        this.hiddenSeries.update((s) => {
            const next = new Set(s);
            currentlyVisible ? next.add(name) : next.delete(name);
            return next;
        });
    }

    toggleBenchmark(): void {
        this.hiddenSeries.set(new Set());
        this.benchmarkMode.update((v) => !v);
    }

    setTimeRange(label: string): void {
        const ranges = this.timeRanges();
        const target = ranges.find((r) => r.label === label);
        if (!target || target.active) return;
        this.timeRanges.update((r) => r.map((x) => ({ ...x, active: x.label === label })));
        this.rangeTrigger$.next(target.value);
    }

    // ── Ticker browser ────────────────────────────────────────────────────────

    openTickerBrowser(): void {
        if (!this.trending().length)
            this.apiService
                .getTrendingTickers()
                .pipe(take(1))
                .subscribe({
                    next: (trending) => this.trending.set(trending),
                });
        this.isTickerBrowserOpen.set(true);
        this.tickerQuery.set('');
        this.tickerSearchResults.set([]);
        this.activePaletteIdx.set(-1);
        setTimeout(() => this.tickerSearchInput()?.nativeElement.focus(), 30);
    }

    closeTickerBrowser(): void {
        this.isTickerBrowserOpen.set(false);
        if (this.tickerSearchDebounce) clearTimeout(this.tickerSearchDebounce);
    }

    onBackdropClick(): void {
        this.closeTickerBrowser();
    }

    onTickerQueryChange(query: string): void {
        this.tickerQuery.set(query);
        this.activePaletteIdx.set(-1);
        if (this.tickerSearchDebounce) clearTimeout(this.tickerSearchDebounce);

        if (!query.trim()) {
            this.tickerSearchResults.set([]);
            this.isSearchingTickers.set(false);
            return;
        }

        this.isSearchingTickers.set(true);
        this.tickerSearchDebounce = setTimeout(() => this.searchTickers(query.trim()), 280);
    }

    private searchTickers(query: string): void {
        this.apiService
            .searchWatchlist(query)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: (results) => {
                    this.tickerSearchResults.set(results);
                    this.isSearchingTickers.set(false);
                },
                error: () => {
                    this.tickerSearchResults.set([]);
                    this.isSearchingTickers.set(false);
                },
            });
    }

    toggleReveal(): void {
        this.isRevealed.update((v) => !v);
    }

    toggleSettings(): void {
        this.viewSettings.update((v) => !v);
    }

    toggleLedgerFilterMode() {
        this.isLedgerFilterMode.update((v) => !v);
        this.selectedEnvelopes.set([]);
        this.selectedTypes.set([]);
    }

    getTxConfig(type: string): TxDisplayConfig {
        return this.txConfig[type as TransactionType];
    }
}
