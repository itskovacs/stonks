import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Observable, ReplaySubject, tap } from 'rxjs';
import { ApiService } from './api.service';
import { UtilsService } from './utils.service';
import { AuthParams, Token } from '../int';

// ---------------------------------------------------------------------------
// Storage keys — single source of truth for the entire auth layer.
// utils.service.ts imports USERNAME_KEY from here to keep them in sync.
// ---------------------------------------------------------------------------
export const STORAGE_KEYS = {
    ACCESS_TOKEN: 'STONKS_AT',
    REFRESH_TOKEN: 'STONKS_RT',
    USERNAME: 'STONKS_USER',
} as const;

@Injectable({ providedIn: 'root' })
export class AuthService {
    /** Exposed so the interceptor and other services can scope API requests. */
    public readonly apiBaseUrl = inject(ApiService).apiBaseUrl;

    private readonly http = inject(HttpClient);
    private readonly router = inject(Router);
    private readonly utilsService = inject(UtilsService);

    /**
     * Deduplicates concurrent token-refresh calls.
     * If a refresh HTTP request is already in flight, subsequent callers receive
     * the same shared ReplaySubject rather than triggering a second request.
     */
    private refreshLock$: ReplaySubject<Token> | null = null;

    // ── Storage accessors ─────────────────────────────────────────────────────

    get accessToken(): string {
        return localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN) ?? '';
    }
    set accessToken(token: string) {
        localStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, token);
    }

    get refreshToken(): string {
        return localStorage.getItem(STORAGE_KEYS.REFRESH_TOKEN) ?? '';
    }
    set refreshToken(token: string) {
        localStorage.setItem(STORAGE_KEYS.REFRESH_TOKEN, token);
    }

    get loggedUser(): string {
        return localStorage.getItem(STORAGE_KEYS.USERNAME) ?? '';
    }
    set loggedUser(user: string) {
        localStorage.setItem(STORAGE_KEYS.USERNAME, user);
    }

    authParams(): Observable<AuthParams> {
        return this.http.get<AuthParams>(this.apiBaseUrl + '/auth/params');
    }

    storeTokens(tokens: Token): void {
        this.accessToken = tokens.access_token;
        this.refreshToken = tokens.refresh_token;
    }

    // ── Auth state ────────────────────────────────────────────────────────────

    /**
     * Synchronous: reads directly from localStorage.
     * The auth guard returns a plain boolean thanks to this — no Observable needed.
     */
    isLoggedIn(): boolean {
        return !!(this.loggedUser && this.accessToken);
    }

    // ── HTTP operations ───────────────────────────────────────────────────────

    login(credentials: { username: string; password: string }): Observable<Token> {
        return this.http.post<Token>(`${this.apiBaseUrl}/auth/login`, credentials).pipe(
            tap((tokens: Token) => {
                this.loggedUser = credentials.username;
                this.storeTokens(tokens);
            }),
        );
    }

    register(credentials: { username: string; password: string }): Observable<Token> {
        return this.http.post<Token>(`${this.apiBaseUrl}/auth/register`, credentials).pipe(
            tap((tokens: Token) => {
                this.loggedUser = credentials.username;
                this.storeTokens(tokens);
            }),
        );
    }

    /**
     * Silently refreshes the access token.
     *
     * Uses a ReplaySubject lock so that N parallel 401s in the interceptor all
     * wait on the same single HTTP call rather than each firing their own refresh.
     * The lock is cleared (set to null) on both success and error.
     */
    refreshAccessToken(): Observable<Token> {
        if (this.refreshLock$) {
            return this.refreshLock$.asObservable();
        }

        this.refreshLock$ = new ReplaySubject<Token>(1);

        this.http
            .post<Token>(`${this.apiBaseUrl}/auth/refresh`, {
                refresh_token: this.refreshToken,
            })
            .subscribe({
                next: (tokens: Token) => {
                    this.accessToken = tokens.access_token;
                    this.refreshLock$?.next(tokens);
                    this.refreshLock$?.complete();
                    this.refreshLock$ = null;
                },
                error: (err: unknown) => {
                    this.refreshLock$?.error(err);
                    this.refreshLock$ = null;
                },
            });

        return this.refreshLock$.asObservable();
    }

    logout(message = '', isError = false): void {
        this.clearStorage();

        if (message) {
            this.utilsService.toast(isError ? 'error' : 'success', isError ? 'Session Ended' : 'Logged Out', message);
        }

        this.router.navigate(['/auth']);
    }

    // ── Token inspection ──────────────────────────────────────────────────────

    /**
     * Returns true when the JWT's `exp` claim is in the past (or unreadable).
     * @param offsetSeconds  Treat tokens expiring within this many seconds as expired.
     */
    isTokenExpired(token: string, offsetSeconds = 0): boolean {
        if (!token) return true;
        const expiry = this.parseTokenExpiry(token);
        if (!expiry) return true;
        return expiry.getTime() <= Date.now() + offsetSeconds * 1000;
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private clearStorage(): void {
        Object.values(STORAGE_KEYS).forEach((key) => localStorage.removeItem(key));
    }

    /**
     * Decodes the JWT payload and extracts the `exp` claim as a Date.
     * Returns null on any parse error so callers treat the token as expired.
     */
    private parseTokenExpiry(token: string): Date | null {
        try {
            const payloadB64 = token.split('.')[1];
            if (!payloadB64) return null;

            // Normalise base64url → base64, pad to a multiple of 4
            const b64 = payloadB64.replace(/-/g, '+').replace(/_/g, '/');
            const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);

            const decoded = JSON.parse(
                decodeURIComponent(
                    atob(padded)
                        .split('')
                        .map((c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0'))
                        .join(''),
                ),
            ) as Record<string, unknown>;

            return typeof decoded['exp'] === 'number' ? new Date(decoded['exp'] * 1000) : null;
        } catch {
            return null;
        }
    }
}
