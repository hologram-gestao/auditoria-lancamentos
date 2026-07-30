/**
 * Badges de papel e status do usuário DO CLIENTE (Sprint 5 / R5).
 *
 * Diferente do `features/users/user-badges` (usuários do sistema), aqui as cores
 * saem **só de token semântico** (`info`, `success`, `destructive`) — nada de
 * `blue-50`/`zinc-100` da paleta crua do Tailwind, que muda de significado
 * quando a marca troca e não tem par no tema escuro sem duplicar classe.
 */
import { cn } from '@/lib/utils';
import {
  CLIENT_USER_ROLE_LABELS,
  type ClientUserRoleFormValue,
} from '@/lib/validation/client-users';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

/**
 * `role` chega como `string` do contrato ("lenient out": um papel novo no
 * backend não pode derrubar a listagem). Valor fora da whitelist cai num
 * neutro com o valor cru — visível, sem quebrar.
 */
export function ClientUserRoleBadge({ role }: { role: string }) {
  const label = CLIENT_USER_ROLE_LABELS[role as ClientUserRoleFormValue] ?? role;
  const isManager = role === 'client_manager';
  return (
    <span
      className={cn(
        baseBadge,
        isManager
          ? 'bg-info-muted text-info ring-info/30'
          : 'bg-muted text-muted-foreground ring-border',
      )}
    >
      {label}
    </span>
  );
}

export function ClientUserStatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        baseBadge,
        active
          ? 'bg-success-muted text-success ring-success/30'
          : 'bg-destructive/10 text-destructive ring-destructive/30',
      )}
    >
      {active ? 'Ativo' : 'Inativo'}
    </span>
  );
}
