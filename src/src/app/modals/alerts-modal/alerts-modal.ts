import { Component, computed, effect, inject, input, model, output, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputNumberModule } from 'primeng/inputnumber';
import { SelectButtonModule } from 'primeng/selectbutton';
import { ApiService } from '../../services/api.service';
import { AlertableTicker, AlertOut } from '../../int';

@Component({
    selector: 'app-alerts-modal',
    standalone: true,
    imports: [DialogModule, ButtonModule, ReactiveFormsModule, InputNumberModule, SelectButtonModule, DecimalPipe],
    templateUrl: './alerts-modal.html',
})
export class AlertsModalComponent {
    private readonly api = inject(ApiService);

    ticker = input.required<AlertableTicker>();
    visible = model<boolean>(false);
    changed = output<void>();

    private readonly allAlerts = signal<AlertOut[]>([]);
    readonly isLoading = signal(false);
    readonly isSaving = signal(false);

    readonly tickerAlerts = computed(() =>
        this.allAlerts()
            .filter((a) => a.ticker === this.ticker().ticker)
            .sort((a, b) => {
                if (a.trigger_above !== b.trigger_above) return a.trigger_above ? -1 : 1;
                return b.target_price - a.target_price;
            })
    );

    readonly showCreateForm = signal(false);
    readonly editingId = signal<number | null>(null);

    readonly directionOptions = [
        { label: '↑ Above', value: true },
        { label: '↓ Below', value: false },
    ];

    readonly quickTargets = [3, 5, 8, 10, -3, -5, -10];

    quickSetPrice(pct: number): void {
        const wac = this.ticker().avg_cost;
        if (!wac) return;
        const target_price = Math.round(wac * (1 + pct / 100) * 100) / 100;
        this.isSaving.set(true);
        this.api.createAlert({ ticker: this.ticker().ticker, target_price, trigger_above: pct > 0 }).subscribe({
            next: (created) => {
                this.allAlerts.update((list) => [...list, created]);
                this.isSaving.set(false);
                this.changed.emit();
            },
            error: () => this.isSaving.set(false),
        });
    }

    readonly createForm = new FormGroup({
        target_price: new FormControl<number | null>(null, [Validators.required, Validators.min(0.01)]),
        trigger_above: new FormControl<boolean>(true, { nonNullable: true }),
        notes: new FormControl<string>('', { nonNullable: true }),
        actionable: new FormControl<boolean>(false, { nonNullable: true }),
    });

    readonly editForm = new FormGroup({
        target_price: new FormControl<number | null>(null, [Validators.required, Validators.min(0.01)]),
        trigger_above: new FormControl<boolean>(true, { nonNullable: true }),
        notes: new FormControl<string>('', { nonNullable: true }),
        actionable: new FormControl<boolean>(false, { nonNullable: true }),
    });

    constructor() {
        effect(() => {
            if (this.visible() && this.ticker()) {
                this.load();
            }
        });
    }

    private load(): void {
        this.isLoading.set(true);
        this.api.getAlerts().subscribe({
            next: (alerts) => {
                this.allAlerts.set(alerts);
                this.isLoading.set(false);
            },
            error: () => this.isLoading.set(false),
        });
    }

    startEdit(alert: AlertOut): void {
        this.editingId.set(alert.id);
        this.editForm.setValue({
            target_price: alert.target_price,
            trigger_above: alert.trigger_above,
            notes: alert.notes ?? '',
            actionable: alert.actionable,
        });
    }

    cancelEdit(): void {
        this.editingId.set(null);
        this.editForm.reset({ trigger_above: true, notes: '', actionable: false });
    }

    saveEdit(): void {
        if (this.editForm.invalid) return;
        const id = this.editingId();
        if (id == null) return;
        const { target_price, trigger_above, notes, actionable } = this.editForm.getRawValue();
        this.isSaving.set(true);
        this.api.updateAlert(id, { target_price: target_price!, trigger_above, notes, actionable }).subscribe({
            next: (updated) => {
                this.allAlerts.update((list) => list.map((a) => (a.id === id ? updated : a)));
                this.editingId.set(null);
                this.isSaving.set(false);
                this.changed.emit();
            },
            error: () => this.isSaving.set(false),
        });
    }

    deleteAlert(id: number): void {
        this.api.deleteAlert(id).subscribe({
            next: () => {
                this.allAlerts.update((list) => list.filter((a) => a.id !== id));
                this.changed.emit();
            },
        });
    }

    createAlert(): void {
        if (this.createForm.invalid) return;
        const { target_price, trigger_above, notes, actionable } = this.createForm.getRawValue();
        this.isSaving.set(true);
        this.api.createAlert({
            ticker: this.ticker().ticker,
            target_price: target_price!,
            trigger_above,
            notes: notes || null,
            actionable,
        }).subscribe({
            next: (created) => {
                this.allAlerts.update((list) => [...list, created]);
                this.showCreateForm.set(false);
                this.createForm.reset({ trigger_above: true, notes: '', actionable: false });
                this.isSaving.set(false);
                this.changed.emit();
            },
            error: () => this.isSaving.set(false),
        });
    }

    onHide(): void {
        this.showCreateForm.set(false);
        this.editingId.set(null);
        this.createForm.reset({ trigger_above: true, notes: '', actionable: false });
        this.editForm.reset({ trigger_above: true, notes: '', actionable: false });
    }

    toggle(): void {
        this.onHide();
        this.visible.set(false);
    }
}
