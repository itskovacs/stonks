import { Component, computed, DestroyRef, effect, inject, model, output, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { FloatLabelModule } from 'primeng/floatlabel';
import { InputTextModule } from 'primeng/inputtext';
import { TabsModule } from 'primeng/tabs';
import { ApiService } from '../../services/api.service';
import { UserSettingsOut } from '../../int';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { UtilsService } from '../../services/utils.service';
import { AuthService } from '../../services/auth.service';

@Component({
    selector: 'app-settings-modal',
    standalone: true,
    imports: [DialogModule, ButtonModule, ReactiveFormsModule, InputTextModule, FloatLabelModule, TabsModule],
    templateUrl: './settings-modal.html',
})
export class SettingsModalComponent {
    private readonly apiService = inject(ApiService);
    private readonly authService = inject(AuthService);
    private readonly utilsService = inject(UtilsService);
    private readonly destroyRef = inject(DestroyRef);

    readonly visible = model<boolean>(false);
    readonly saved = output<UserSettingsOut>();

    readonly isLoading = signal(false);
    readonly isSaving = signal(false);
    readonly saveError = signal<string | null>(null);

    readonly isDarkMode = computed(() => this.utilsService.darkMode());

    readonly form = new FormGroup({
        currency: new FormControl<string>('', { nonNullable: true }),
        apprise_url: new FormControl<string>('', { nonNullable: true }),
    });

    constructor() {
        effect(() => {
            if (this.visible()) this.load();
        });
    }

    toggleDarkMode(): void {
        this.utilsService.toggleDarkMode();
    }

    load(): void {
        this.isLoading.set(true);
        this.saveError.set(null);
        this.apiService
            .getSettings()
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: (settings) => {
                    this.form.patchValue({
                        currency: settings.currency ?? '',
                        apprise_url: settings.apprise_url ?? '',
                    });
                    this.isLoading.set(false);
                },
                error: () => this.isLoading.set(false),
            });
    }

    save(): void {
        this.isSaving.set(true);
        this.saveError.set(null);
        const { currency, apprise_url } = this.form.getRawValue();
        const payload = {
            currency: currency.trim() || null,
            apprise_url: apprise_url.trim() || null,
        };
        this.apiService
            .updateSettings(payload)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
                next: (result) => {
                    this.isSaving.set(false);
                    this.saved.emit(result);
                    this.visible.set(false);
                },
                error: () => {
                    this.saveError.set('Failed to save settings. Please try again.');
                    this.isSaving.set(false);
                },
            });
    }

    logout(): void {
        this.authService.logout();
    }

    toGithub() {
        window.open('https://github.com/itskovacs/stonks', '_blank');
    }

    toggle(): void {
        this.visible.set(false);
    }
}
