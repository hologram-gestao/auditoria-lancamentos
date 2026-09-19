/**
 * Badges visuais reusáveis nas linhas da tabela de usuários (Doc §8.2).
 *   - Perfil:  admin → azul · manager → cinza
 *   - Status:  ativo → verde · inativo → vermelho
 *
 * Pintam por TOKEN semântico (86e2n39hb): o tema tem `success`/`info` e
 * companhia no globals.css, e é o token que flipa entre claro/escuro — cor
 * fixa da paleta aqui não recebe a marca da 86e2ukrc9 nem o tema escuro.
 *
 * O perfil era um TERNÁRIO `isAdmin ? 'Admin' : 'Gerente'` (86e36ed1d): com
 * mais de dois papéis possíveis, o `else` deixava de ser "gerente" e virava
 * "qualquer outro papel, rotulado como gerente" — um `platform_admin` caindo
 * ali seria apresentado como Gerente, que é o oposto do que ele é. Agora o
 * rótulo vem de `USER_ROLE_LABELS` (a mesma tabela do header) e o tom de um
 * `Record` exaustivo: papel novo na whitelist da API quebra a compilação até
 * alguém decidir a cor dele, em vez de herdar a do gerente em silêncio.
 */

import type { UserRoleValue } from '@/lib/api/users';
import { USER_ROLE_LABELS } from '@/lib/authz';
import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

const ROLE_TONE: Record<UserRoleValue, string> = {
  admin: 'bg-info-muted text-info ring-info/30',
  manager: 'bg-muted text-muted-foreground ring-border',
};

export function UserRoleBadge({ role }: { role: UserRoleValue }) {
  return <span className={cn(baseBadge, ROLE_TONE[role])}>{USER_ROLE_LABELS[role]}</span>;
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
