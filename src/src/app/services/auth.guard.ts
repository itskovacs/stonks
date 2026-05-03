import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';
import { UtilsService } from './utils.service';

/**
 * Protects all routes under the authenticated shell.
 *
 * isLoggedIn() is synchronous (reads localStorage), so the guard returns a
 * plain boolean | UrlTree — no Observable ceremony required.
 *
 * On redirect, the attempted URL is stored as an encoded `redirectURL` query
 * param so the auth component can navigate there after a successful login.
 */
export const authGuard: CanActivateFn = (_, state) => {
    if (inject(AuthService).isLoggedIn()) {
        return true;
    }

    inject(UtilsService).toast('warn', 'Authentication Required', 'You must be logged in to access this page');

    // Preserve the attempted URL for post-login redirect.
    // Exclude /auth itself to avoid a self-referential redirect loop.
    const redirectParam = state.url && state.url !== '/auth' ? `?redirectURL=${encodeURIComponent(state.url)}` : '';
    return inject(Router).parseUrl(`/auth${redirectParam}`);
};
