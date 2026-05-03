import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ToastModule } from 'primeng/toast';
import { SupportComponent } from './shared/support';

@Component({
    selector: 'app-root',
    standalone: true,
    imports: [RouterOutlet, ToastModule, SupportComponent],
    templateUrl: './app.component.html',
    styleUrls: ['./app.component.scss'],
})
export class AppComponent {}
