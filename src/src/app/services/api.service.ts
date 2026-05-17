import { inject, Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
    AlertOut,
    AlertRequest,
    AlertUpdateRequest,
    DashboardResponse,
    EnvelopeOverviewResponse,
    EnvelopeRequest,
    MutationResponse,
    NewsItem,
    OverviewPeriod,
    PriceChartResponse,
    ProjectionRequest,
    ProjectionResponse,
    StockReport,
    TickerRequest,
    TickerSearchResult,
    TransactionRequest,
    UserSettingsOut,
    UserSettingsRequest,
    WatchlistRow,
} from '../int';

@Injectable({
    providedIn: 'root',
})
export class ApiService {
    public readonly apiBaseUrl: string = '/api';
    private http = inject(HttpClient);

    // ---------------------------------------------------------------------------
    // News — GET /news/{ticker}?limit=<1-50>
    // ---------------------------------------------------------------------------

    getNews(ticker: string, limit: number = 20): Observable<NewsItem[]> {
        const params = new HttpParams().set('limit', limit);
        return this.http.get<NewsItem[]>(`${this.apiBaseUrl}/news/${ticker}`, { params });
    }

    // ---------------------------------------------------------------------------
    // Stock Report — GET /stock/report/{ticker}
    // Stock Chart  — GET /stock/chart/{ticker}?period=<period>
    // ---------------------------------------------------------------------------

    getStockReport(ticker: string): Observable<StockReport> {
        return this.http.get<StockReport>(`${this.apiBaseUrl}/stock/report/${ticker}`);
    }

    /**
     * Valid period values: 1d | 1w | 1m | 3m | 6m | ytd | 1y | 5y
     * Note: chart periods differ from overview periods — do not substitute OverviewPeriod here.
     */
    invalidateTickerCache(ticker: string): Observable<void> {
        return this.http.delete<void>(`${this.apiBaseUrl}/stock/cache/${ticker}`);
    }

    getStockChart(
        ticker: string,
        period: '1d' | '1w' | '1m' | '3m' | '6m' | 'ytd' | '1y' | '5y' = '1y',
    ): Observable<PriceChartResponse> {
        const params = new HttpParams().set('period', period);
        return this.http.get<PriceChartResponse>(`${this.apiBaseUrl}/stock/chart/${ticker}`, { params });
    }

    // ---------------------------------------------------------------------------
    // Dashboard — GET /profile/dashboard (single monolith call — do not split)
    // ---------------------------------------------------------------------------

    getDashboard(forceRefresh = false): Observable<DashboardResponse> {
        const params = forceRefresh ? new HttpParams().set('force_refresh', 'true') : undefined;
        return this.http.get<DashboardResponse>(`${this.apiBaseUrl}/profile/dashboard`, params ? { params } : {});
    }

    // ---------------------------------------------------------------------------
    // Portfolio History Overview — GET /profile/envelope/overview?period=…
    // ---------------------------------------------------------------------------

    getEnvelopesOverview(
        period: OverviewPeriod = '1y',
        benchmarkTicker = 'XWD.TO',
        envelopeIds: number[] = [],
    ): Observable<EnvelopeOverviewResponse> {
        let params = new HttpParams().set('period', period).set('benchmark', benchmarkTicker);
        envelopeIds.forEach((id) => {
            params = params.append('envelope_ids', id);
        });
        return this.http.get<EnvelopeOverviewResponse>(`${this.apiBaseUrl}/profile/envelope/overview`, { params });
    }

    // ---------------------------------------------------------------------------
    // Watchlist
    // ---------------------------------------------------------------------------

    /**
     * Retrieve trending tickers
     */
    getTrendingTickers(): Observable<WatchlistRow[]> {
        return this.http.get<WatchlistRow[]>(`${this.apiBaseUrl}/profile/watchlist/trending`);
    }

    /**
     * Search for tickers — GET /profile/watchlist/search?q=&limit=
     * Debounce on the caller side; do not fire on every keystroke.
     * limit: 1–20, defaults to 8.
     */
    searchWatchlist(q: string, limit: number = 8): Observable<TickerSearchResult[]> {
        const params = new HttpParams().set('q', q).set('limit', limit);
        return this.http.get<TickerSearchResult[]>(`${this.apiBaseUrl}/profile/watchlist/search`, { params });
    }

    /**
     * Response includes updated watchlist: string[] — sync local state from it;
     * do not re-fetch the dashboard.
     */
    addToWatchlist(data: TickerRequest): Observable<MutationResponse> {
        return this.http.post<MutationResponse>(`${this.apiBaseUrl}/profile/watchlist/add`, data);
    }

    removeFromWatchlist(data: TickerRequest): Observable<MutationResponse> {
        return this.http.post<MutationResponse>(`${this.apiBaseUrl}/profile/watchlist/remove`, data);
    }

    // ---------------------------------------------------------------------------
    // Envelopes
    // ---------------------------------------------------------------------------

    addEnvelope(data: EnvelopeRequest): Observable<MutationResponse> {
        return this.http.post<MutationResponse>(`${this.apiBaseUrl}/profile/envelopes/add`, data);
    }

    putEnvelope(id: number, data: EnvelopeRequest): Observable<MutationResponse> {
        return this.http.put<MutationResponse>(`${this.apiBaseUrl}/profile/envelopes/${id}`, data);
    }

    /**
     * Cascade-deletes all transactions belonging to the envelope.
     * id is the integer envelope ID from EnvelopeSummary — store it at dashboard load time.
     */
    removeEnvelope(id: number): Observable<MutationResponse> {
        return this.http.delete<MutationResponse>(`${this.apiBaseUrl}/profile/envelopes/${id}`);
    }

    // ---------------------------------------------------------------------------
    // Transactions
    // ---------------------------------------------------------------------------

    /**
     * Response includes transaction: TransactionOut (the newly created row).
     */
    addTransaction(data: TransactionRequest): Observable<MutationResponse> {
        return this.http.post<MutationResponse>(`${this.apiBaseUrl}/profile/transactions`, data);
    }

    /**
     * Returns an empty object {} on success — treat any 2xx as success.
     */
    deleteTransaction(id: number): Observable<object> {
        return this.http.delete<object>(`${this.apiBaseUrl}/profile/transactions/${id}`);
    }

    exportTransactions(envelopeName: string): Observable<string[]> {
        return this.http.get<string[]>(`${this.apiBaseUrl}/profile/transactions/export`, {
            params: { envelope_name: envelopeName },
        });
    }

    // ---------------------------------------------------------------------------
    // User Settings — GET/PUT /profile/settings
    // ---------------------------------------------------------------------------

    getSettings(): Observable<UserSettingsOut> {
        return this.http.get<UserSettingsOut>(`${this.apiBaseUrl}/profile/settings`);
    }

    /**
     * Pass only the fields to update; omit or pass null to leave a field unchanged.
     * Returns the full updated settings object.
     */
    updateSettings(data: UserSettingsRequest): Observable<UserSettingsOut> {
        return this.http.put<UserSettingsOut>(`${this.apiBaseUrl}/profile/settings`, data);
    }

    // ---------------------------------------------------------------------------
    // Alerts — /profile/alerts
    // ---------------------------------------------------------------------------

    getAlerts(): Observable<AlertOut[]> {
        return this.http.get<AlertOut[]>(`${this.apiBaseUrl}/profile/alerts`);
    }

    createAlert(data: AlertRequest): Observable<AlertOut> {
        return this.http.post<AlertOut>(`${this.apiBaseUrl}/profile/alerts`, data);
    }

    /**
     * At least one of target_price or trigger_above must be set.
     * Updating either field re-arms the alert automatically.
     */
    updateAlert(id: number, data: AlertUpdateRequest): Observable<AlertOut> {
        return this.http.put<AlertOut>(`${this.apiBaseUrl}/profile/alerts/${id}`, data);
    }

    deleteAlert(id: number): Observable<MutationResponse> {
        return this.http.delete<MutationResponse>(`${this.apiBaseUrl}/profile/alerts/${id}`);
    }

    // ---------------------------------------------------------------------------
    // Projection — POST /projection
    // ---------------------------------------------------------------------------

    postProjection(data: ProjectionRequest): Observable<ProjectionResponse> {
        return this.http.post<ProjectionResponse>(`${this.apiBaseUrl}/projection`, data);
    }
}
