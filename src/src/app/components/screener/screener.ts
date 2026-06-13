import { Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { SkeletonModule } from 'primeng/skeleton';
import { ApiService } from '../../services/api.service';
import { UtilsService } from '../../services/utils.service';
import { ScreenerRow, SignalValue } from '../../int';

type SortCol =
    | 'ticker' | 'name' | 'price'
    | 'change_1d_pct' | 'change_5d_pct' | 'change_1m_pct'
    | 'rsi_14' | 'bollinger_b'
    | 'macd_signal' | 'sma_signal' | 'stochastic_k' | 'volume_trend_ratio'
    | 'buy_pct' | 'sentiment_score';

type FilterValue = 'ALL' | SignalValue;

interface ScreenerRowView extends ScreenerRow {
    dominant_signal: SignalValue;
    sentiment_score: number | null;
    sentiment_label: string | null;
    sentiment_loading: boolean;
}

@Component({
    selector: 'app-screener',
    standalone: true,
    imports: [DecimalPipe, RouterLink, SkeletonModule],
    templateUrl: './screener.html',
    styleUrls: ['./screener.scss'],
})
export class ScreenerComponent {
    private readonly apiService  = inject(ApiService);
    private readonly utilsService = inject(UtilsService);
    private readonly destroyRef  = inject(DestroyRef);

    readonly isLoading    = signal(true);
    readonly rows         = signal<ScreenerRowView[]>([]);
    readonly filterSignal = signal<FilterValue>('ALL');
    readonly sortCol      = signal<SortCol>('ticker');
    readonly sortDir      = signal<'asc' | 'desc'>('asc');

    readonly filterPills: { value: FilterValue; label: string; activeClass: string }[] = [
        {
            value: 'ALL',
            label: 'All',
            activeClass: 'bg-primary-900 text-white border-primary-900 dark:bg-primary-50 dark:text-primary-900 dark:border-primary-50',
        },
        { value: 'BUY',  label: 'Buy',  activeClass: 'bg-emerald-600 text-white border-emerald-600' },
        { value: 'HOLD', label: 'Hold', activeClass: 'bg-amber-500 text-white border-amber-500' },
        { value: 'SELL', label: 'Sell', activeClass: 'bg-red-500 text-white border-red-500' },
    ];

    readonly filteredRows = computed(() => {
        const filter = this.filterSignal();
        const col    = this.sortCol();
        const dir    = this.sortDir();
        const mul    = dir === 'asc' ? 1 : -1;

        let rows = this.rows();
        if (filter !== 'ALL') {
            rows = rows.filter(r => r.dominant_signal === filter);
        }

        return [...rows].sort((a, b) => {
            // Nulls always sort to the bottom regardless of direction.
            const numCmp = (av: number | null, bv: number | null): number => {
                if (av === null && bv === null) return 0;
                if (av === null) return 1;
                if (bv === null) return -1;
                return mul * (av - bv);
            };
            const sigVal = (s: string | null): number =>
                s === 'BUY' ? 2 : s === 'HOLD' ? 1 : s === 'SELL' ? 0 : -1;

            switch (col) {
                case 'ticker':           return mul * a.ticker.localeCompare(b.ticker);
                case 'name':             return mul * a.name.localeCompare(b.name);
                case 'price':            return mul * (a.current_price - b.current_price);
                case 'change_1d_pct':    return mul * (a.change_1d_pct - b.change_1d_pct);
                case 'change_5d_pct':    return numCmp(a.change_5d_pct, b.change_5d_pct);
                case 'change_1m_pct':    return numCmp(a.change_1m_pct, b.change_1m_pct);
                case 'rsi_14':           return numCmp(a.rsi_14, b.rsi_14);
                case 'bollinger_b':      return numCmp(a.bollinger_b, b.bollinger_b);
                case 'macd_signal':      return numCmp(sigVal(a.macd_signal), sigVal(b.macd_signal));
                case 'sma_signal':       return numCmp(sigVal(a.sma_signal), sigVal(b.sma_signal));
                case 'stochastic_k':     return numCmp(a.stochastic_k, b.stochastic_k);
                case 'volume_trend_ratio': return numCmp(a.volume_trend_ratio, b.volume_trend_ratio);
                case 'buy_pct':          return mul * (a.buy_pct - b.buy_pct);
                case 'sentiment_score':  return numCmp(a.sentiment_score, b.sentiment_score);
                default:                 return 0;
            }
        });
    });

    constructor() {
        this.apiService.getScreener().pipe(
            takeUntilDestroyed(this.destroyRef),
        ).subscribe({
            next: (data) => {
                this.rows.set(data.map(row => ({
                    ...row,
                    dominant_signal: row.buy_pct >= 60 ? 'BUY' : row.sell_pct >= 60 ? 'SELL' : 'HOLD',
                    sentiment_score:   null,
                    sentiment_label:   null,
                    sentiment_loading: false,
                })));
                this.isLoading.set(false);
            },
            error: () => {
                this.isLoading.set(false);
                this.utilsService.toast('error', 'Error', 'Failed to load screener data');
            },
        });
    }

    toggleSort(col: SortCol): void {
        if (this.sortCol() === col) {
            this.sortDir.update(d => (d === 'asc' ? 'desc' : 'asc'));
        } else {
            this.sortCol.set(col);
            // String cols default ascending; numeric/signal cols default descending.
            this.sortDir.set(col === 'ticker' || col === 'name' ? 'asc' : 'desc');
        }
    }

    loadSentiment(event: MouseEvent, ticker: string): void {
        event.stopPropagation();
        this.rows.update(rows =>
            rows.map(r => r.ticker === ticker ? { ...r, sentiment_loading: true } : r)
        );
        this.apiService.getScreenerSentiment(ticker).pipe(
            takeUntilDestroyed(this.destroyRef),
        ).subscribe({
            next: (s) => {
                this.rows.update(rows =>
                    rows.map(r =>
                        r.ticker === ticker
                            ? { ...r, sentiment_loading: false, sentiment_score: s.sentiment_score, sentiment_label: s.label }
                            : r
                    )
                );
            },
            error: () => {
                this.rows.update(rows =>
                    rows.map(r => r.ticker === ticker ? { ...r, sentiment_loading: false } : r)
                );
                this.utilsService.toast('warn', 'Sentiment', 'Could not load sentiment for ' + ticker);
            },
        });
    }

    signalClass(sig: string | null): string {
        if (sig === 'BUY')  return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400';
        if (sig === 'SELL') return 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400';
        if (sig === 'HOLD') return 'bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-400';
        return 'bg-primary-100 text-primary-400 dark:bg-primary-700/50 dark:text-primary-500';
    }

    sentimentClass(label: string | null): string {
        if (label === 'positive') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400';
        if (label === 'negative') return 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400';
        return 'bg-primary-50 text-primary-500 dark:bg-primary-700/50 dark:text-primary-400';
    }

    sortIcon(col: SortCol): string {
        if (this.sortCol() !== col) return 'pi pi-sort text-primary-300 dark:text-primary-600';
        return this.sortDir() === 'asc' ? 'pi pi-sort-up text-primary-700 dark:text-primary-200' : 'pi pi-sort-down text-primary-700 dark:text-primary-200';
    }
}
