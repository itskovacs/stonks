import { HttpErrorResponse, HttpEvent, HttpHandlerFn, HttpRequest } from '@angular/common/http';
import { inject } from '@angular/core';
import { Observable, catchError, switchMap, take, throwError } from 'rxjs';
import { AuthService } from './auth.service';
import { UtilsService } from './utils.service';

// ---------------------------------------------------------------------------
// Known HTTP error code → user-facing copy
// ---------------------------------------------------------------------------
const ERROR_CONFIG: Record<number, { title: string; detail: string }> = {
    400: { title: 'Bad Request', detail: 'The server could not understand this request' },
    403: { title: 'Forbidden', detail: 'You are not permitted to perform this action' },
    404: { title: 'Not Found', detail: 'The requested resource does not exist' },
    409: { title: 'Conflict', detail: 'A conflict occurred with the current state of the resource' },
    413: { title: 'Payload Too Large', detail: 'The request payload exceeds the server limit' },
    422: { title: 'Unprocessable Entity', detail: 'The request body failed validation' },
    502: { title: 'Bad Gateway', detail: 'Check connectivity — the upstream server is unreachable' },
    503: { title: 'Service Unavailable', detail: 'The server is temporarily unavailable' },
};

// ---------------------------------------------------------------------------
// Functional interceptor — registered via withInterceptors([authInterceptor])
// ---------------------------------------------------------------------------
export const authInterceptor = (req: HttpRequest<unknown>, next: HttpHandlerFn): Observable<HttpEvent<unknown>> => {
    const authService = inject(AuthService);
    const utilsService = inject(UtilsService);

    // Attach Content-Type only for requests that actually carry a body.
    // Omitting it from GET/DELETE avoids spurious CORS preflight headers.
    if (req.body !== null && !(req.body instanceof FormData) && !req.headers.has('Content-Type')) {
        req = req.clone({ setHeaders: { 'Content-Type': 'application/json' } });
    }

    // Attach Bearer token for all /api routes when a locally valid token exists.
    // The interceptor still handles server-side 401s via the refresh flow below.
    if (
        req.url.startsWith(authService.apiBaseUrl) &&
        authService.accessToken &&
        !authService.isTokenExpired(authService.accessToken)
    ) {
        req = req.clone({ setHeaders: { Authorization: `Bearer ${authService.accessToken}` } });
    }

    return next(req).pipe(
        catchError((err: HttpErrorResponse) => {
            // ── 401 Unauthorized — handled first, before generic error mapping ────
            if (err.status === 401) {
                // The refresh endpoint itself returned 401 → refresh token is expired → hard logout
                if (req.url.endsWith('/refresh')) {
                    authService.logout('Your session has expired. Please log in again.', true);
                    return throwError(() => err);
                }

                // We have an access token but the server rejected it → attempt a silent refresh.
                // refreshAccessToken() deduplicates concurrent calls, so this is safe under
                // multiple parallel in-flight requests all hitting 401 simultaneously.
                if (authService.accessToken) {
                    return authService.refreshAccessToken().pipe(
                        take(1),
                        switchMap((tokens) =>
                            next(req.clone({ setHeaders: { Authorization: `Bearer ${tokens.access_token}` } })),
                        ),
                    );
                }

                // No token at all — redirect silently (logout already shows a toast)
                authService.logout(err.error?.detail ?? 'You must be authenticated', true);
                return throwError(() => err);
            }

            // ── All other status codes ────────────────────────────────────────────
            const config = ERROR_CONFIG[err.status];

            // FastAPI surfaces validation detail as a string (scalar) or an array of
            // field errors. Prefer the scalar form; fall back to the generic copy.
            const serverDetail = typeof err.error?.detail === 'string' ? err.error.detail : null;
            const message = serverDetail ?? err.message ?? config?.detail ?? 'Unknown error — check the console';
            const title = config?.title ?? 'Request Error';

            console.error(`[HTTP ${err.status} – ${title}]`, err);
            utilsService.toast('error', title, message);
            return throwError(() => err);
        }),
    );
};
