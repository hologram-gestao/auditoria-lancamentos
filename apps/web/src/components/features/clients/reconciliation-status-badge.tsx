/**
 * Badge de status de uma conciliação.
 *
 * **Vocabulário da Sprint 4** — a UI fala a língua do produto, não a do banco:
 *   `processing`            → "Em processamento"
 *   `reviewing` | `done`    → "Processada"
 *   `error`                 → "Erro"
 *
 * Os três estados do banco `reviewing`/`done` colapsam num rótulo só porque,
 * para quem opera, os dois querem dizer a mesma coisa: acabou, pode abrir.
 *
 * Cor vem SÓ de token semântico (`info`/`success`/`destructive`) — badge de
 * status pode ter cor semântica, mas nunca hex/`blue-50` hardcoded.
 *
 * Status desconhecido (backend evoluiu antes do front) renderiza neutro com o
 * valor cru: a tela continua utilizável em vez de quebrar.
 */

import { Loader2 } from 'lucide-react';

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

/** Rótulo do produto para cada status do banco (Sprint 4, §17). */
export function reconciliationStatusLabel(status: string): string {
  switch (status) {
    case 'processing':
      return 'Em processamento';
    case 'reviewing':
    case 'done':
      return 'Processada';
    case 'error':
      return 'Erro';
    default:
      return status;
  }
}

/**
 * ⚠️ Sobre fundo `-muted` o texto é o token SÓLIDO, nunca `-foreground` —
 * `-foreground` é o par do fundo sólido (branco sobre `bg-success`) e aqui
 * daria branco sobre quase-branco (~1.05:1), com o rótulo sumindo da tela.
 */
const STATUS_CLASSES: Record<string, string> = {
  processing: 'bg-info-muted text-info ring-info/30',
  reviewing: 'bg-success-muted text-success ring-success/30',
  done: 'bg-success-muted text-success ring-success/30',
  error: 'bg-destructive/10 text-destructive ring-destructive/30',
};

const FALLBACK_CLASSES = 'bg-muted text-muted-foreground ring-border';

export function ReconciliationStatusBadge({ status }: { status: string }) {
  const label = reconciliationStatusLabel(status);
  const classes = STATUS_CLASSES[status] ?? FALLBACK_CLASSES;
  return (
    <span className={cn(baseBadge, classes)}>
      {status === 'processing' && <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />}
      {label}
    </span>
  );
}
