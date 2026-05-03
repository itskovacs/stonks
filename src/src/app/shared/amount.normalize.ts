import { Pipe, PipeTransform } from '@angular/core';

@Pipe({
    name: 'compactNumber',
    standalone: true, // Remove this line if you are using NgModules instead of standalone components
})
export class CompactNumberPipe implements PipeTransform {
    transform(value: number | string | null | undefined, maxFractionDigits: number = 0): string {
        if (value == null) return '0';

        const num = typeof value === 'string' ? parseFloat(value) : value;
        if (isNaN(num)) return '0';

        return new Intl.NumberFormat(navigator.language, {
            notation: 'compact',
            compactDisplay: 'short',
            maximumFractionDigits: maxFractionDigits,
        }).format(num);
    }
}
