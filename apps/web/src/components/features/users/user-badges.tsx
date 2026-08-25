/**
 * Badges visuais reusáveis nas linhas da tabela de usuários (Doc §8.2).
 *   - Perfil:  admin → azul · manager → cinza
 *   - Status:  ativo → verde · inativo → vermelho
 *
 * Pintam por TOKEN semântico (86e2n39hb): o tema tem `success`/`info` e
 * companhia no globals.css, e é o token que flipa entre claro/escuro — cor
 * fixa da paleta aqui não recebe a marca da 86e2ukrc9 nem o tema escuro.
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
          ? 'bg-info-muted text-info ring-info/30'
          : 'bg-muted text-muted-foreground ring-border',
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
          ? 'bg-success-muted text-success ring-success/30'
          : 'bg-destructive-muted text-destructive ring-destructive/30',
      )}
    >
      {active ? 'Ativo' : 'Inativo'}
    </span>
  );
}
