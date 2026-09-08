/**
 * Chip da categoria de cliente (86e34jd8m).
 *
 * O backend guarda um TOM semântico, nunca um hex — e aqui cada tom vira o
 * par de tokens do tema (§7: cor em componente é sempre token; o tema da
 * marca e o escuro chegam sozinhos). Tom desconhecido cai no neutro: um valor
 * novo no banco não pode quebrar a lista.
 */

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex max-w-full items-center truncate rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

const toneClasses: Record<string, string> = {
  neutral: 'bg-muted text-muted-foreground ring-border',
  primary: 'bg-primary/10 text-primary ring-primary/30',
  info: 'bg-info-muted text-info ring-info/30',
  success: 'bg-success-muted text-success ring-success/30',
  warning: 'bg-warning-muted text-warning ring-warning/30',
};

/** Rótulos do select de tom — nomes de INTENÇÃO, não de cor (o tema decide a cor). */
export const CLIENT_CATEGORY_TONE_LABELS: Record<string, string> = {
  neutral: 'Neutro',
  primary: 'Destaque',
  info: 'Informativo',
  success: 'Positivo',
  warning: 'Atenção',
};

export const CLIENT_CATEGORY_TONES = ['neutral', 'primary', 'info', 'success', 'warning'] as const;

interface CategoryBadgeProps {
  name: string;
  tone: string;
  className?: string;
}

export function CategoryBadge({ name, tone, className }: CategoryBadgeProps) {
  const cls = toneClasses[tone] ?? toneClasses['neutral']!;
  return (
    <span className={cn(baseBadge, cls, className)} title={undefined}>
      {name}
    </span>
  );
}
