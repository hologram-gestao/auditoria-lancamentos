/**
 * Badge de severidade — S15 FRONT 11.2. Cores espelham o padrão da tela de
 * revisão (vermelho = critical, âmbar = moderate, cinza = info).
 *
 * Severidade chega como string lenient do backend; valores fora do enum caem
 * num fallback neutro (não quebra o render se o catálogo legado tiver algum
 * valor exótico).
 *
 * `AnomalyTypeStatusBadge`: ativo (verde) / inativo (vermelho), igual à
 * tabela de usuários.
 */

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

const severityClasses: Record<string, string> = {
  critical:
    'bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-800',
  moderate:
    'bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-800',
  info: 'bg-zinc-100 text-zinc-700 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-700',
};

const severityLabels: Record<string, string> = {
  critical: 'Crítico',
  moderate: 'Moderado',
  info: 'Informativo',
};

export function SeverityBadge({ severity }: { severity: string }) {
  const cls = severityClasses[severity] ?? severityClasses['info']!;
  const label = severityLabels[severity] ?? severity;
  return <span className={cn(baseBadge, cls)}>{label}</span>;
}

export function AnomalyTypeStatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        baseBadge,
        active
          ? 'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-800'
          : 'bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-800',
      )}
    >
      {active ? 'Ativo' : 'Inativo'}
    </span>
  );
}
