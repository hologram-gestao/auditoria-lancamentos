/**
 * O selo "não veio na última importação" do plano contábil (86e3qhhrm).
 *
 * A reimportação não inativa: a conta que não veio na planilha continua como
 * estava e o servidor grava `absentSince` (a data da primeira importação em que
 * ela sumiu). O selo AVISA, não bloqueia: a conta pode não existir mais no
 * sistema contábil, e o arquivo contábil chegaria com uma conta inexistente.
 *
 * Um texto só para a lista do plano e para o aviso do de-para.
 */

import { AlertTriangle } from 'lucide-react';

import { formatCreatedAt } from '@/lib/format';
import { cn } from '@/lib/utils';

export const ABSENT_FROM_IMPORT_LABEL = 'Não veio na última importação';

/** "Não veio na última importação desde 17/10/2026 às 10h05". */
export function absentFromImportText(absentSince: string): string {
  return `${ABSENT_FROM_IMPORT_LABEL} desde ${formatCreatedAt(absentSince)}`;
}

export function AbsentFromImportBadge({
  absentSince,
  className,
}: {
  absentSince: string;
  className?: string;
}) {
  return (
    <span
      data-testid="accounting-account-absent-badge"
      className={cn(
        'bg-warning-muted text-warning ring-warning/30 inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        className,
      )}
    >
      <AlertTriangle className="h-3 w-3 shrink-0" aria-hidden="true" />
      <span>{ABSENT_FROM_IMPORT_LABEL}</span>
      <span className="sr-only">{` desde ${formatCreatedAt(absentSince)}`}</span>
    </span>
  );
}
