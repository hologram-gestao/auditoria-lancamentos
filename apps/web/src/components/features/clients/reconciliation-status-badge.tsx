/**
 * Badge dos 4 status de uma sessão de conciliação — Doc §10.1.
 *
 * Cores aplicadas via Tailwind direto (mesmo critério de `client-status-badge`):
 * a paleta default do shadcn não tem variants suficientes e criar uma só pra
 * isto seria over-engineering pra MVP.
 *
 * Status desconhecido (futuro do backend) renderiza em cinza com o valor cru —
 * tela continua funcionando mesmo se o backend evoluir antes do front.
 */

import { Loader2 } from 'lucide-react';

import { cn } from '@/lib/utils';

const baseBadge =
  'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

const STATUS_LABEL: Record<string, string> = {
  processing: 'Processando…',
  reviewing: 'Aguardando revisão',
  done: 'Concluída',
  error: 'Erro',
};

const STATUS_CLASSES: Record<string, string> = {
  processing:
    'bg-blue-50 text-blue-700 ring-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:ring-blue-800',
  reviewing:
    'bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-800',
  done: 'bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-800',
  error:
    'bg-red-50 text-red-700 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-800',
};

const FALLBACK_CLASSES =
  'bg-slate-50 text-slate-700 ring-slate-200 dark:bg-slate-900/40 dark:text-slate-300 dark:ring-slate-700';

export function ReconciliationStatusBadge({ status }: { status: string }) {
  const label = STATUS_LABEL[status] ?? status;
  const classes = STATUS_CLASSES[status] ?? FALLBACK_CLASSES;
  return (
    <span className={cn(baseBadge, classes)}>
      {status === 'processing' && <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />}
      {label}
    </span>
  );
}
