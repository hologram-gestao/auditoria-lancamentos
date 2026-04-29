'use client';

/**
 * Card de uma sessão de conciliação no histórico — Doc §10.1.
 *
 * - Status `processing`: card com spinner e "Em processamento…"; oculta
 *   contadores e link "Ver detalhes" (sessão ainda não tem dados).
 * - Status `done`/`reviewing`: mostra contadores e link.
 * - Status `error`: mostra `error_message` se vier do back; nunca mostra contadores.
 *
 * Resolução do nome da conta: feita via `accountLookup` montado no pai
 * (filtro de conta vive lá). Se o `omie_conta_id` da sessão não bater com
 * nenhuma conta atual (conta deletada no Omie), o front degrada para
 * "Conta #{id}" — não bloqueia a UI.
 */

import { format } from 'date-fns';
import { ptBR } from 'date-fns/locale';
import { AlertCircle, ArrowRight, CheckCircle2, Loader2, XCircle } from 'lucide-react';
import Link from 'next/link';

import type { ReconciliationSessionSummary } from '@/lib/api/clients';
import { cn } from '@/lib/utils';

import { ReconciliationStatusBadge } from './reconciliation-status-badge';

interface ReconciliationCardProps {
  session: ReconciliationSessionSummary;
  accountName: string;
}

export function ReconciliationCard({ session, accountName }: ReconciliationCardProps) {
  const isProcessing = session.status === 'processing';
  const isError = session.status === 'error';
  const showCounters = session.status === 'done' || session.status === 'reviewing';

  const referenceLabel = formatReferenceMonth(session.reference_month);
  const createdAtLabel = format(new Date(session.created_at), "d 'de' MMM 'de' yyyy 'às' HH'h'mm", {
    locale: ptBR,
  });

  return (
    <article className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-0.5">
          <p className="text-sm font-medium leading-tight">{accountName}</p>
          <p className="text-muted-foreground text-xs">{referenceLabel}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ReconciliationStatusBadge status={session.status} />
          {session.anomaly_count > 0 && (
            <span
              className={cn(
                'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
                'bg-orange-50 text-orange-700 ring-orange-200',
                'dark:bg-orange-950/40 dark:text-orange-300 dark:ring-orange-800',
              )}
            >
              {session.anomaly_count} anomalia{session.anomaly_count === 1 ? '' : 's'}
            </span>
          )}
        </div>
      </div>

      {isProcessing && (
        <div className="text-muted-foreground flex items-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Em processamento…
        </div>
      )}

      {isError && session.error_message !== null && (
        <p className="text-destructive text-sm">{session.error_message}</p>
      )}

      {showCounters && <Counters session={session} />}

      <div className="text-muted-foreground flex items-center justify-between text-xs">
        <span>Criada em {createdAtLabel}</span>
        {!isProcessing && (
          <Link
            href={`/conciliacao/${session.id}`}
            className="text-primary inline-flex items-center gap-1 font-medium hover:underline"
          >
            Ver detalhes
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
          </Link>
        )}
      </div>
    </article>
  );
}

function Counters({ session }: { session: ReconciliationSessionSummary }) {
  return (
    <div className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
      <span className="inline-flex items-center gap-1">
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
        {session.conciliated_count} conciliado{session.conciliated_count === 1 ? '' : 's'}
      </span>
      <span className="inline-flex items-center gap-1">
        <AlertCircle className="h-3.5 w-3.5 text-amber-600" aria-hidden="true" />
        {session.sem_omie_count} sem Omie
      </span>
      <span className="inline-flex items-center gap-1">
        <XCircle className="h-3.5 w-3.5 text-red-600" aria-hidden="true" />
        {session.omie_sem_arquivo_count} Omie sem arquivo
      </span>
    </div>
  );
}

function formatReferenceMonth(referenceMonth: string): string {
  // Backend devolve `YYYY-MM-DD` (primeiro dia do mês). new Date("2026-04-01")
  // interpreta como UTC e pode pular para março em timezones ocidentais.
  // Construímos a data localmente para evitar isso.
  const [yearStr, monthStr] = referenceMonth.split('-');
  const year = Number(yearStr);
  const month = Number(monthStr);
  if (!Number.isFinite(year) || !Number.isFinite(month)) {
    return referenceMonth;
  }
  const date = new Date(year, month - 1, 1);
  const formatted = format(date, "MMMM 'de' yyyy", { locale: ptBR });
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}
