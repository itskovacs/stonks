import { Injectable, inject, signal } from '@angular/core';
import { MessageService } from 'primeng/api';
import { TransactionType } from '../int';
import { STORAGE_KEYS } from './auth.service';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type ToastSeverity = 'info' | 'warn' | 'error' | 'success';

interface TransactionTypeOption {
    label: string;
    value: TransactionType;
}

// Dark-mode preference key — scoped to this app
const DARK_MODE_KEY = 'STONKS_DARK_MODE';

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------
@Injectable({ providedIn: 'root' })
export class UtilsService {
    private readonly messageService = inject(MessageService);

    /** Reactive loading overlay message. Set to "" to hide. */
    readonly loadingMessage = signal<string>('');

    /**
     * Full list of transaction types for dropdowns/selects.
     * Typed against the TransactionType union from int.ts so the compiler
     * will flag this if the union ever gains or loses a member.
     */
    readonly transactionTypes: TransactionTypeOption[] = [
        { label: 'Buy', value: 'BUY' },
        { label: 'Sell', value: 'SELL' },
        { label: 'Deposit', value: 'DEPOSIT' },
        { label: 'Withdraw', value: 'WITHDRAW' },
        { label: 'Dividend', value: 'DIVIDEND' },
    ];

    // ── User ──────────────────────────────────────────────────────────────────

    /**
     * Read-only accessor for the currently logged-in username.
     * Uses the canonical STORAGE_KEYS.USERNAME constant — the same key
     * that AuthService writes to — so the two services are always in sync.
     */
    get loggedUser(): string {
        return localStorage.getItem(STORAGE_KEYS.USERNAME) ?? '';
    }

    // ── Dark mode ─────────────────────────────────────────────────────────────

    readonly darkMode = signal(false);

    /** Call once on app startup (e.g. in AppComponent.ngOnInit) to restore preference. */
    initDarkMode(): void {
        const saved = localStorage.getItem(DARK_MODE_KEY) === 'true';
        document.documentElement.classList.toggle('dark', saved);
        this.darkMode.set(saved);
    }

    toggleDarkMode(): void {
        const enabled = localStorage.getItem(DARK_MODE_KEY) === 'true';
        localStorage.setItem(DARK_MODE_KEY, String(!enabled));
        document.documentElement.classList.toggle('dark', !enabled);
        this.darkMode.set(!enabled);
    }

    // ── Loading overlay ───────────────────────────────────────────────────────

    setLoading(message: string): void {
        this.loadingMessage.set(message);
    }

    // ── Toast notifications ───────────────────────────────────────────────────

    toast(severity: ToastSeverity = 'info', summary = 'Info', detail = '', life = 3000): void {
        this.messageService.add({ severity, summary, detail, life });
    }
}
