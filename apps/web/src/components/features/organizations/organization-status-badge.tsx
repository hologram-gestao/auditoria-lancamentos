/**
 * Badge de situação da organização (86e36ecwa).
 *
 * "Suspensa" não é um rótulo decorativo: com `active=false` o staff daquela
 * organização cai no request seguinte e a plataforma deixa de criar cliente
 * nela. Por isso o par de cores é `success`/`destructive`, e não um cinza
 * neutro — é um estado que interrompe o trabalho de gente.
 *
 * Pinta por TOKEN semântico (86e2n39hb): cor fixa da paleta não acompanha os
 * três temas.
 */

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export function OrganizationStatusBadge({ active }: { active: boolean }) {
  return (
    <span
      className={cn(
        baseBadge,
        active
          ? 'bg-success-muted text-success ring-success/30'
          : 'bg-destructive-muted text-destructive ring-destructive/30',
      )}
    >
      {active ? 'Ativa' : 'Suspensa'}
    </span>
  );
}
