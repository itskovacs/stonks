import { Component, computed, effect, inject, input, model, signal, untracked } from '@angular/core';
import { DecimalPipe, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { ApiService } from '../../services/api.service';
import { EnvelopeSummary, EnvelopeSeriesLine, EnvelopeStats, OverviewPeriod, PositionRow, TransactionOut } from '../../int';

@Component({
    selector: 'app-envelope-stats-modal',
    standalone: true,
    imports: [DecimalPipe, DatePipe, RouterLink, ButtonModule],
    templateUrl: './envelope-stats-modal.html',
})
export class EnvelopeStatsModalComponent {
    private readonly api = inject(ApiService);

    envelope = input.required<EnvelopeSummary>();
    positions = input.required<PositionRow[]>();
    transactions = input.required<TransactionOut[]>();
    currency = input.required<string>();
    visible = model<boolean>(false);

    readonly isStatsLoading = signal(false);
    readonly periodStats = signal<EnvelopeStats | null>(null);
    readonly periodSeries = signal<EnvelopeSeriesLine[]>([]);
    readonly selectedPeriod = signal<OverviewPeriod>('1y');

    readonly periods: Array<{ label: string; value: OverviewPeriod }> = [
        { label: '1W', value: '1w' },
        { label: '1M', value: '1mo' },
        { label: '3M', value: '3mo' },
        { label: '6M', value: '6mo' },
        { label: 'YTD', value: 'ytd' },
        { label: '1Y', value: '1y' },
        { label: '3Y', value: '3y' },
    ];

    readonly envPositions = computed(() =>
        this.positions().filter((p) => p.envelope_name === this.envelope().name),
    );

    readonly snapshot = computed(() => {
        const env = this.envelope();
        const positions = this.envPositions();
        const txs = this.transactions().filter((t) => t.envelope_name === env.name);

        const equity = positions.reduce((s, p) => s + p.current_value, 0);
        const cost_basis = positions.reduce((s, p) => s + p.cost_basis, 0);
        const unrealized_pnl = equity - cost_basis;
        const unrealized_pnl_pct = cost_basis > 0 ? (unrealized_pnl / cost_basis) * 100 : 0;

        const change_1d = positions.reduce((s, p) => s + p.change_1d * p.shares, 0);
        const prev_equity = equity - change_1d;
        const change_1d_pct = prev_equity > 0 ? (change_1d / prev_equity) * 100 : 0;

        const realized_pnl = txs
            .filter((t) => t.type === 'SELL' && t.realized_pnl != null)
            .reduce((s, t) => s + (t.realized_pnl ?? 0), 0);

        // HPR: total return on all capital deployed (including cash), all-time
        const hpr_pct = env.capital_in > 0 ? ((env.total_value - env.capital_in) / env.capital_in) * 100 : 0;

        // ROIC: return on capital actually in positions (excludes idle cash)
        const roic_pct = cost_basis > 0 ? (unrealized_pnl / cost_basis) * 100 : 0;

        return { equity, cost_basis, unrealized_pnl, unrealized_pnl_pct, change_1d, change_1d_pct, realized_pnl, hpr_pct, roic_pct };
    });

    /** Absolute investment return over the selected period, net of capital flows. */
    readonly periodReturnAbs = computed<number | null>(() => {
        const series = this.periodSeries();
        const stats = this.periodStats();
        if (!series.length || !stats || !series[0].values.length) return null;
        const vals = series[0].values;
        return vals[vals.length - 1] - vals[0] - stats.net_deposits;
    });

    /** Realized P&L from SELL transactions within the selected period. */
    readonly periodRealizedPnl = computed(() => {
        const cutoff = this.periodCutoffDate(this.selectedPeriod());
        const envName = this.envelope().name;
        return this.transactions()
            .filter((t) => t.type === 'SELL' && t.envelope_name === envName && new Date(t.date) >= cutoff && t.realized_pnl != null)
            .reduce((s, t) => s + (t.realized_pnl ?? 0), 0);
    });

    constructor() {
        // Track visible() and selectedPeriod() explicitly; use untracked() for the fetch
        // to prevent loadStats() internal reads from adding extra dependencies.
        effect(() => {
            const isVisible = this.visible();
            const _period = this.selectedPeriod();
            if (isVisible) {
                untracked(() => this.loadStats());
            } else {
                this.periodStats.set(null);
                this.periodSeries.set([]);
            }
        });
    }

    private loadStats(): void {
        this.isStatsLoading.set(true);
        this.api.getEnvelopesOverview(this.selectedPeriod(), 'XWD.TO', [this.envelope().id]).subscribe({
            next: (res) => {
                this.periodStats.set(res.stats);
                this.periodSeries.set(res.series);
                this.isStatsLoading.set(false);
            },
            error: () => this.isStatsLoading.set(false),
        });
    }

    private periodCutoffDate(period: OverviewPeriod): Date {
        const now = new Date();
        switch (period) {
            case '1w':  return new Date(now.getTime() - 7 * 86400000);
            case '1mo': { const d = new Date(now); d.setMonth(d.getMonth() - 1); return d; }
            case '3mo': { const d = new Date(now); d.setMonth(d.getMonth() - 3); return d; }
            case '6mo': { const d = new Date(now); d.setMonth(d.getMonth() - 6); return d; }
            case 'ytd': return new Date(now.getFullYear(), 0, 1);
            case '1y':  { const d = new Date(now); d.setFullYear(d.getFullYear() - 1); return d; }
            case '3y':  { const d = new Date(now); d.setFullYear(d.getFullYear() - 3); return d; }
        }
    }

    selectPeriod(period: OverviewPeriod): void {
        if (this.selectedPeriod() === period) return;
        this.selectedPeriod.set(period);
        // effect handles reload via selectedPeriod() tracking
    }

    close(): void {
        this.visible.set(false);
    }

    pnlClass(value: number): string {
        return value >= 0 ? 'text-emerald-500 dark:text-emerald-400' : 'text-red-400';
    }

    pnlSign(value: number): string {
        return value >= 0 ? '+' : '';
    }
}
