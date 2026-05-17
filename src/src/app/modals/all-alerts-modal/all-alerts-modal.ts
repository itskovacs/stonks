import { Component, computed, effect, inject, model, output, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { InputNumberModule } from 'primeng/inputnumber';
import { SelectButtonModule } from 'primeng/selectbutton';
import { ApiService } from '../../services/api.service';
import { AlertOut } from '../../int';

@Component({
    selector: 'app-all-alerts-modal',
    standalone: true,
    imports: [ButtonModule, ReactiveFormsModule, InputNumberModule, SelectButtonModule, DecimalPipe],
    templateUrl: './all-alerts-modal.html',
})
export class AllAlertsModalComponent {
    private readonly api = inject(ApiService);

    readonly visible = model<boolean>(false);
    readonly changed = output<void>();

    private readonly alerts = signal<AlertOut[]>([]);
    readonly isLoading = signal(false);
    readonly isSaving = signal(false);
    readonly editingId = signal<number | null>(null);

    readonly grouped = computed(() => {
        const map = new Map<string, AlertOut[]>();
        for (const a of this.alerts()) {
            const bucket = map.get(a.ticker);
            if (bucket) bucket.push(a);
            else map.set(a.ticker, [a]);
        }
        return [...map.entries()].map(([ticker, items]) => ({ ticker, items }));
    });

    readonly totalCount = computed(() => this.alerts().length);

    readonly directionOptions = [
        { label: '↑ Above', value: true },
        { label: '↓ Below', value: false },
    ];

    readonly editForm = new FormGroup({
        target_price: new FormControl<number | null>(null, [Validators.required, Validators.min(0.01)]),
        trigger_above: new FormControl<boolean>(true, { nonNullable: true }),
    });

    constructor() {
        effect(() => {
            if (this.visible()) this.load();
        });
    }

    private load(): void {
        this.isLoading.set(true);
        this.api.getAlerts().subscribe({
            next: (alerts) => {
                this.alerts.set(alerts);
                this.isLoading.set(false);
            },
            error: () => this.isLoading.set(false),
        });
    }

    startEdit(alert: AlertOut): void {
        this.editingId.set(alert.id);
        this.editForm.setValue({ target_price: alert.target_price, trigger_above: alert.trigger_above });
    }

    cancelEdit(): void {
        this.editingId.set(null);
        this.editForm.reset({ trigger_above: true });
    }

    saveEdit(): void {
        if (this.editForm.invalid) return;
        const id = this.editingId();
        if (id == null) return;
        const { target_price, trigger_above } = this.editForm.getRawValue();
        this.isSaving.set(true);
        this.api.updateAlert(id, { target_price: target_price!, trigger_above }).subscribe({
            next: (updated) => {
                this.alerts.update((list) => list.map((a) => (a.id === id ? updated : a)));
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
                this.alerts.update((list) => list.filter((a) => a.id !== id));
                this.changed.emit();
            },
        });
    }

    toggle(): void {
        this.editingId.set(null);
        this.editForm.reset({ trigger_above: true });
        this.visible.set(false);
    }
}
