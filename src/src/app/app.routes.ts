import { Routes } from '@angular/router';
import { TickerComponent } from './components/ticker/ticker';
import { DashboardComponent } from './components/dashboard/dashboard';
import { AuthComponent } from './components/auth/auth.component';
import { authGuard } from './services/auth.guard';

export const routes: Routes = [
    {
        path: 'auth',
        pathMatch: 'full',
        component: AuthComponent,
        title: 'Stonks - Authentication',
    },
    {
        path: '',
        canActivate: [authGuard],
        children: [
            {
                path: '',
                component: DashboardComponent,
                title: 'Stonks - Dashboard',
            },
            {
                path: 'ticker/:id',
                pathMatch: 'full',
                component: TickerComponent,
                title: 'Stonks - Ticker',
            },
        ],
    },

    { path: '**', redirectTo: '/', pathMatch: 'full' },
];
