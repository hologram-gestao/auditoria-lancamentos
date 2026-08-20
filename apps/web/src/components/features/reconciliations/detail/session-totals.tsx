'use client';

/**
 * Totalizadores + resumo geral de saldos do detalhe — Sprint 4 / R3.
 *
 * **Fonte única.** Todos os números vêm do `SessionDetailPayload`, que o
 * backend materializa (`reconciliations.totals`). O front NÃO recalcula nada:
 * lista, topo do detalhe e abas leem o mesmo valor e por isso não divergem
 * (learning "valor derivado calculado em 2 lugares diverge").
 *
 * **RAW no estado, conversão só na exibição.** Os saldos chegam como string
 * (Decimal do Pydantic) e só viram texto localizado no `formatBRL` — nada de
 * `Number()` no estado, que perderia precisão antes da hora.
 */

import {
  AlertCircle,
  CheckCircle2,
  FileText,
  Files,
  ShieldAlert,
  XCircle,
  type LucideIcon,
} from 'lucide-react';

import type { SessionDetail } from '@/lib/api/reconciliations';
import { formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

/** Tolerância de R$ 0,01 — a MESMA regra do matcher (CLAUDE.md §5.1). */
const BALANCE_TOLERANCE = 0.01;

export function SessionTotals({ detail }: { detail: SessionDetail }) {
  return (
    <section aria-labelledby="totals-heading" className="space-y-3">
      <h2 id="totals-heading" className="sr-only">
        Totalizadores da conciliação
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard
          icon={FileText}
          label="Movimentações"
          value={detail.total_file_entries}
          tone="neutral"
        />
        <StatCard
          icon={CheckCircle2}
          label="Conciliados"
          value={detail.conciliated_count}
          tone="success"
          // 86e2u513b — o card soma exatas + divergentes, mas o filtro
          // "Conciliadas (data exata)" mostra só as exatas. Sem este subtexto
          // a diferença (120 no card, 97 na tabela) não tinha explicação.
          hint={
            detail.conciliated_divergent_count > 0
              ? `inclui ${detail.conciliated_divergent_count} com data divergente`
              : undefined
          }
        />
        <StatCard
          icon={AlertCircle}
          label="Sem Omie"
          value={detail.sem_omie_count}
          tone="warning"
        />
        <StatCard
          icon={XCircle}
          label="Omie sem arquivo"
          value={detail.omie_sem_arquivo_count}
          tone="destructive"
        />
        <StatCard
          icon={ShieldAlert}
          label="Anomalias"
          value={detail.anomaly_count}
          tone={detail.anomaly_count > 0 ? 'warning' : 'neutral'}
        />
        <StatCard icon={Files} label="Arquivos" value={detail.total_files} tone="neutral" />
      </div>
    </section>
  );
}

type Tone = 'neutral' | 'success' | 'warning' | 'destructive';

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'text-muted-foreground',
  success: 'text-success',
  warning: 'text-warning',
  destructive: 'text-destructive',
};

interface StatCardProps {
  icon: LucideIcon;
  label: string;
  value: number;
  tone: Tone;
  /** Subtexto explicativo (ex.: de que o número é composto). */
  hint?: string;
}

function StatCard({ icon: Icon, label, value, tone, hint }: StatCardProps) {
  return (
    <div className="bg-card flex items-center gap-3 rounded-lg border p-3 shadow-sm">
      <Icon className={cn('h-5 w-5 shrink-0', TONE_CLASSES[tone])} aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-xl font-semibold tabular-nums leading-tight">{value}</p>
        <p className="text-muted-foreground truncate text-xs">{label}</p>
        {hint !== undefined && <p className="text-muted-foreground text-[10px]">{hint}</p>}
      </div>
    </div>
  );
}

/**
 * Resumo geral de saldos: anterior · arquivo · Omie · diferença + status.
 *
 * `null` em qualquer saldo significa sessão legada (processada antes do
 * cálculo de saldos existir) — exibimos "Indisponível" em vez de "R$ 0,00",
 * que seria uma afirmação falsa.
 */
export function BalanceSummary({ detail }: { detail: SessionDetail }) {
  const status = resolveBalanceStatus(detail.balance_difference);

  return (
    <section aria-labelledby="balances-heading" className="bg-card rounded-lg border p-4 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="balances-heading" className="text-sm font-semibold">
          Resumo geral
        </h2>
        <span className={cn('text-sm font-semibold', status.className)}>{status.label}</span>
      </div>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <BalanceCell label="Saldo anterior" value={detail.balance_start} />
        <BalanceCell label="Saldo final (arquivo)" value={detail.balance_end_file} />
        <BalanceCell label="Saldo final (Omie)" value={detail.balance_end_omie} />
        <BalanceCell label="Diferença" value={detail.balance_difference} emphasize />
      </dl>
    </section>
  );
}

function BalanceCell({
  label,
  value,
  emphasize = false,
}: {
  label: string;
  value: string | null | undefined;
  emphasize?: boolean;
}) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className={cn('tabular-nums', emphasize ? 'text-base font-semibold' : 'text-sm')}>
        {value == null ? (
          <span className="text-muted-foreground">Indisponível</span>
        ) : (
          formatBRL(value)
        )}
      </dd>
    </div>
  );
}

/**
 * "Conferido" quando |diferença| ≤ R$ 0,01 (mesma tolerância do matcher);
 * "Divergente" acima disso; "Indisponível" quando não há saldo calculado.
 */
export function resolveBalanceStatus(difference: string | null | undefined): {
  label: string;
  className: string;
} {
  if (difference == null) {
    return { label: 'Indisponível', className: 'text-muted-foreground' };
  }
  const value = Number(difference);
  if (!Number.isFinite(value)) {
    return { label: 'Indisponível', className: 'text-muted-foreground' };
  }
  return Math.abs(value) <= BALANCE_TOLERANCE
    ? { label: 'Conferido', className: 'text-success' }
    : { label: 'Divergente', className: 'text-destructive' };
}
