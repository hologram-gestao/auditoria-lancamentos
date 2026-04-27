/**
 * Badge de status do cliente — Doc §9.1: verde "Ativo" / vermelho "Inativo".
 *
 * Cores aplicadas via Tailwind direto (mesma decisão de `user-badges`):
 * a paleta do shadcn não tem variant "success" e criar variantes só pra
 * duas badges seria over-engineering pra MVP.
 */

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export function ClientStatusBadge({ active }: { active: boolean }) {
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
