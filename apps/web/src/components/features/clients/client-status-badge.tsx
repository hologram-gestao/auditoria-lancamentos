/**
 * Badge de status do cliente — Doc §9.1: verde "Ativo" / vermelho "Inativo".
 *
 * 86e36pm1z: cliente ENCERRADO (closed_at preenchido) ganha selo próprio em
 * neutro — encerrado não é "inativo temporário", é terminal; o vermelho de
 * "Inativo" leria como problema a corrigir.
 *
 * Cores aplicadas via Tailwind direto (mesma decisão de `user-badges`):
 * a paleta do shadcn não tem variant "success" e criar variantes só pra
 * duas badges seria over-engineering pra MVP.
 */

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export function ClientStatusBadge({
  active,
  closedAt = null,
}: {
  active: boolean;
  closedAt?: string | null;
}) {
  if (closedAt != null) {
    return (
      <span className={cn(baseBadge, 'bg-muted text-muted-foreground ring-border')}>Encerrado</span>
    );
  }
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
