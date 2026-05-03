import { ApplicationConfig, provideZoneChangeDetection, isDevMode } from '@angular/core';
import { provideRouter } from '@angular/router';
import { routes } from './app.routes';
import { providePrimeNG } from 'primeng/config';
import { provideServiceWorker } from '@angular/service-worker';
import { TripThemePreset } from '../mytheme';
import { MessageService } from 'primeng/api';
import { authInterceptor } from './services/auth.interceptor';
import { provideHttpClient, withInterceptors } from '@angular/common/http';

export const appConfig: ApplicationConfig = {
    providers: [
        provideZoneChangeDetection({ eventCoalescing: true }),
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        providePrimeNG({
            translation: {
                firstDayOfWeek: 1,
            },
            theme: {
                preset: TripThemePreset,
                options: { darkModeSelector: '.dark' },
            },
        }),
        MessageService,
        provideServiceWorker('ngsw-worker.js', {
            enabled: !isDevMode(),
            registrationStrategy: 'registerWhenStable:30000',
        }),
    ],
};
