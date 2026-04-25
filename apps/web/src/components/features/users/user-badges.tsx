/**
 * Badges visuais reusáveis nas linhas da tabela de usuários (Doc §8.2).
 *   - Perfil:  admin → azul · manager → cinza
 *   - Status:  ativo → verde · inativo → vermelho
 *
 * As cores são aplicadas via Tailwind direto (não como variantes do shadcn Badge)
 * porque a paleta padrão do shadcn não tem "success"; criar variantes sob medida
 * só pra duas badges seria over-engineering pra MVP.
 */

import type { UserRoleValue } from '@/lib/api/users';
import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export function UserRoleBadge({ role }: { role: UserRoleValue }) {
  const isAdmin = role === 'admin';
  return (
    <span
      className={cn(
        baseBadge,
        isAdmin
          ? 'bg-blue-50 text-blue-700 ring-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:ring-blue-800'
          : 'bg-zinc-100 text-zinc-700 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:ring-zinc-700',
      )}
    >
      {isAdmin ? 'Admin' : 'Gerente'}
    </span>
  );
}

export function UserStatusBadge({ active }: { active: boolean }) {
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
