import { Component, signal, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ButtonModule } from 'primeng/button';

const STONKS_COUNTER = 'STONKS_SUPPORT_COUNTER';
const STONKS_SUPPORTED = 'STONKS_SUPPORT_DONE';

@Component({
    selector: 'app-support',
    standalone: true,
    imports: [CommonModule, ButtonModule],
    template: `
        @if (showSupport()) {
            <div
                animate.enter="slide-from-y"
                animate.leave="a-slide-from-y"
                class="w-full fixed bottom-4 left-1/2 -translate-x-1/2 sm:left-4 sm:translate-x-0 z-50 animate-fade-in-up pointer-events-none">
                <div
                    class="pointer-events-auto w-[90vw] sm:w-md flex flex-col gap-3 p-4 ring-1 ring-black/5 dark:ring-white/10 bg-white/70 dark:bg-primary-900/70
          backdrop-blur-xl rounded-lg shadow-2xl mx-auto sm:ml-0">
                    <div class="flex items-center gap-3">
                        <div
                            class="flex items-center justify-center size-10 rounded-full bg-blue-100 dark:bg-blue-900/30 shrink-0">
                            <i class="pi pi-chart-line text-blue-500 animate-pulse"></i>
                        </div>
                        <div>
                            <h3 class="text-sm font-bold text-primary-900 dark:text-white leading-tight">
                                Support Stonks
                            </h3>
                            <p class="text-xs font-medium text-primary-600 dark:text-primary-400 mt-1 leading-relaxed">
                                Stonks is free to use and maintained through community support. Consider supporting its
                                development with a tiny dividend by <b>buying me a coffee</b>.
                            </p>
                        </div>
                    </div>
                    <div class="flex items-center justify-between gap-2 mt-1">
                        <p-button label="Not now" size="small" text severity="secondary" (click)="dismiss()" />
                        <a href="https://ko-fi.com/itskovacs" target="_blank" rel="noopener noreferrer">
                            <p-button
                                label="Support"
                                size="small"
                                icon="pi pi-heart-fill"
                                iconPos="right"
                                (click)="dismiss(true)" />
                        </a>
                    </div>
                </div>
            </div>
        }
    `,
})
export class SupportComponent implements OnInit {
    showSupport = signal(false);

    ngOnInit() {
        const didSupport = localStorage.getItem(STONKS_SUPPORTED);
        if (didSupport) return;

        const openCount = parseInt(localStorage.getItem(STONKS_COUNTER) || '0', 10) + 1;
        localStorage.setItem(STONKS_COUNTER, openCount.toString());
        if (openCount > 9) setTimeout(() => this.showSupport.set(true), 2500);
    }

    dismiss(support: boolean = false) {
        this.showSupport.set(false);
        localStorage.setItem(STONKS_COUNTER, '0');
        if (support) localStorage.setItem(STONKS_SUPPORTED, '1');
    }
}
