/**
 * Badge da coluna "Situação" (FRONT 9.12). String lenient — mostra fallback
 * cinza para estados não previstos (memória feedback_pydantic).
 */
import { Check, MinusCircle, AlertTriangle } from 'lucide-react';

import { cn } from '@/lib/utils';

interface SituationBadgeProps {
  situation: string;
}

export function SituationBadge({ situation }: SituationBadgeProps) {
  if (situation === 'conciliado') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800',
          'dark:bg-emerald-900/40 dark:text-emerald-200',
        )}
      >
        <Check className="h-3 w-3" aria-hidden="true" />
        Conciliado
      </span>
    );
  }
  if (situation === 'sem_omie') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800',
          'dark:bg-amber-900/40 dark:text-amber-200',
        )}
      >
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        Sem Omie
      </span>
    );
  }
  if (situation === 'ignorado') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700',
          'dark:bg-slate-700 dark:text-slate-200',
        )}
      >
        <MinusCircle className="h-3 w-3" aria-hidden="true" />
        Ignorado
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-700 dark:text-slate-200">
      {situation}
    </span>
  );
}
