'use client';

/**
 * Painel de entrada do cliente — Sprint 4 / R7 (**desejável**, não bloqueante).
 *
 * Vive numa rota própria (`/clientes/{id}/painel`) para não disputar a rota da
 * Lista, que continua sendo a tela principal do cliente.
 *
 * **Só dados já persistidos, nenhuma consulta cara nova.** As duas queries que
 * ele faz já existem e já estão no cache do TanStack:
 *   - `useClientDetail` (o `<ClientShell>` já buscou) → contas sincronizadas;
 *   - `useReconciliationsList` filtrada pelo mês corrente → conciliações do mês
 *     e a soma de anomalias; a última conciliação sai do 1º item da lista sem
 *     filtro (o backend ordena por `created_at DESC`).
 *
 * ⚠️ **Rótulo honesto:** o card diz "Anomalias no mês", não "anomalias em
 * aberto". O `anomaly_count` da sessão conta TODAS as anomalias, resolvidas ou
 * não, e não existe endpoint cross-sessão de anomalias abertas. Chamar isso de
 * "em aberto" seria um número errado com nome bonito.
 */

import { ArrowRight, CalendarCheck, Landmark, ListChecks, ShieldAlert } from 'lucide-react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';
import { useClientDetail, useReconciliationsList } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import { formatReferenceMonth, formatSyncedAt } from '@/lib/format';
import { currentMonth } from '@/lib/validation/reconciliations';

import { ReconciliationStatusBadge } from './reconciliation-status-badge';

/** Teto de leitura do mês — o painel é um resumo, não um relatório. */
const MONTH_PAGE_SIZE = 100;

export function ClientDashboard({ clientId }: { clientId: string }) {
  const month = currentMonth();
  const detailQuery = useClientDetail(clientId);
  const monthQuery = useReconciliationsList(clientId, { page: 1, pageSize: MONTH_PAGE_SIZE, month });
  const latestQuery = useReconciliationsList(clientId, { page: 1, pageSize: 1 });

  const isLoading = detailQuery.isLoading || monthQuery.isLoading || latestQuery.isLoading;
  const isError = detailQuery.isError || monthQuery.isError || latestQuery.isError;

  if (isLoading) return <DashboardSkeleton />;

  if (isError) {
    const err = monthQuery.error ?? detailQuery.error ?? latestQuery.error;
    return (
      <div
        role="alert"
        className="bg-destructive/5 border-destructive/30 text-destructive flex flex-col items-start gap-3 rounded-lg border p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
      >
        <span>
          {err instanceof ApiError ? err.userMessage : 'Não foi possível carregar o painel.'}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void detailQuery.refetch();
            void monthQuery.refetch();
            void latestQuery.refetch();
          }}
        >
          Tentar novamente
        </Button>
      </div>
    );
  }

  const monthSessions = monthQuery.data?.data ?? [];
  const monthTotal = monthQuery.data?.pagination.total ?? 0;
  const anomaliesInMonth = monthSessions.reduce((sum, s) => sum + s.anomaly_count, 0);
  const accounts = detailQuery.data?.accounts ?? [];
  const latest = latestQuery.data?.data[0];

  return (
    <section aria-labelledby="dashboard-heading" className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="dashboard-heading" className="text-lg font-semibold">
          Painel — {formatReferenceMonth(month)}
        </h2>
        <Button asChild>
          <Link href={`/clientes/${clientId}`}>
            <ListChecks className="h-4 w-4" aria-hidden="true" />
            Ir para as conciliações
          </Link>
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          icon={<ListChecks className="text-muted-foreground h-5 w-5" aria-hidden="true" />}
          label="Conciliações no mês"
          value={String(monthTotal)}
        />
        <StatTile
          icon={<ShieldAlert className="text-warning h-5 w-5" aria-hidden="true" />}
          label="Anomalias no mês"
          value={String(anomaliesInMonth)}
          hint={
            monthTotal > MONTH_PAGE_SIZE
              ? `Soma das ${MONTH_PAGE_SIZE} conciliações mais recentes do mês.`
              : undefined
          }
        />
        <StatTile
          icon={<Landmark className="text-muted-foreground h-5 w-5" aria-hidden="true" />}
          label="Contas sincronizadas"
          value={String(accounts.length)}
          hint={formatSyncedAt(detailQuery.data?.accounts_synced_at)}
        />
        <StatTile
          icon={<CalendarCheck className="text-muted-foreground h-5 w-5" aria-hidden="true" />}
          label="Última conciliação"
          value={latest === undefined ? '—' : formatReferenceMonth(latest.reference_month)}
          badge={latest === undefined ? undefined : <ReconciliationStatusBadge status={latest.status} />}
        />
      </div>

      {latest === undefined ? (
        <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center">
          <p className="text-muted-foreground text-sm">
            Este cliente ainda não tem conciliações. Comece pela lista de conciliações.
          </p>
          <Button asChild>
            <Link href={`/clientes/${clientId}`}>
              Criar conciliação
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
          </Button>
        </div>
      ) : (
        <Button variant="outline" asChild>
          <Link href={`/clientes/${clientId}/conciliacao/${latest.id}`}>
            Abrir a última conciliação
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </Button>
      )}
    </section>
  );
}

interface StatTileProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  badge?: React.ReactNode;
}

function StatTile({ icon, label, value, hint, badge }: StatTileProps) {
  return (
    <div className="bg-card space-y-2 rounded-lg border p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <p className="text-muted-foreground text-xs">{label}</p>
        {icon}
      </div>
      <p className="truncate text-2xl font-semibold tabular-nums">{value}</p>
      {badge}
      {hint !== undefined && <p className="text-muted-foreground text-xs">{hint}</p>}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div role="status" className="space-y-6" aria-busy="true" aria-label="Carregando painel">
      <div className="bg-muted h-6 w-56 animate-pulse rounded" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-card h-24 animate-pulse rounded-lg border" />
        ))}
      </div>
    </div>
  );
}
