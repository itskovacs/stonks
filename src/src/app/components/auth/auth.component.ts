import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { ButtonModule } from 'primeng/button';
import { FloatLabelModule } from 'primeng/floatlabel';
import { FocusTrapModule } from 'primeng/focustrap';
import { InputTextModule } from 'primeng/inputtext';
import { SkeletonModule } from 'primeng/skeleton';
import { AuthService } from '../../services/auth.service';

// ---------------------------------------------------------------------------
// Typed form shape — FormControl<string> with nonNullable ensures getRawValue()
// always returns { username: string; password: string }, never Partial<>.
// ---------------------------------------------------------------------------
interface AuthForm {
    username: FormControl<string>;
    password: FormControl<string>;
}

@Component({
    selector: 'app-auth',
    standalone: true,
    imports: [ReactiveFormsModule, FloatLabelModule, ButtonModule, InputTextModule, SkeletonModule, FocusTrapModule],
    templateUrl: './auth.component.html',
    styleUrl: './auth.component.scss',
})
export class AuthComponent {
    private readonly authService = inject(AuthService);
    private readonly router = inject(Router);
    private readonly destroyRef = inject(DestroyRef);

    /**
     * The URL the user attempted before the guard redirected them here.
     * authGuard encodes the URL with encodeURIComponent, so we decode on read.
     * Falls back to "/" (dashboard root) when no redirect param is present.
     */
    private readonly redirectUrl: string = (() => {
        const raw = inject(ActivatedRoute).snapshot.queryParams['redirectURL'];
        return raw ? decodeURIComponent(raw) : '/';
    })();

    readonly isRegistering = signal(false);
    readonly isLoading = signal(false);

    readonly form = new FormGroup<AuthForm>({
        username: new FormControl('', {
            nonNullable: true,
            validators: [
                Validators.required,
                Validators.minLength(1),
                Validators.maxLength(19),
                Validators.pattern(/^[a-zA-Z0-9_-]+$/),
            ],
        }),
        password: new FormControl('', {
            nonNullable: true,
            validators: [Validators.required],
        }),
    });

    // Convenience getters for template validation bindings
    get username() {
        return this.form.controls.username;
    }
    get password() {
        return this.form.controls.password;
    }

    /** Toggle between sign-in and register, resetting form state on every switch. */
    toggleMode(): void {
        this.isRegistering.update((v) => !v);
        this.form.reset();
    }

    /** Single entry point for both login and register — template always calls this. */
    submit(): void {
        if (this.form.invalid || this.isLoading()) return;
        this.form.markAllAsTouched(); // surface any untouched validation errors
        if (this.form.invalid) return;
        this.isRegistering() ? this.register() : this.login();
    }

    // ---------------------------------------------------------------------------
    // Private HTTP operations
    // ---------------------------------------------------------------------------

    private login(): void {
        this.beginSubmit();

        this.authService
            .login(this.form.getRawValue())
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: () => this.router.navigateByUrl(this.redirectUrl),
                error: () => this.endSubmitOnError(),
            });
    }

    private register(): void {
        this.beginSubmit();

        this.authService
            .register(this.form.getRawValue())
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: () => this.router.navigateByUrl(this.redirectUrl),
                error: () => this.endSubmitOnError(),
            });
    }

    // ---------------------------------------------------------------------------
    // Submit state helpers
    // ---------------------------------------------------------------------------

    private beginSubmit(): void {
        this.isLoading.set(true);
        this.form.disable();
    }

    /**
     * Re-enables the form on HTTP error. Clears only the password so the username
     * field is preserved — less friction for the user to retry.
     * The interceptor already displayed an error toast; no duplicate needed here.
     */
    private endSubmitOnError(): void {
        this.isLoading.set(false);
        this.form.enable();
        this.form.controls.password.reset();
    }
}
