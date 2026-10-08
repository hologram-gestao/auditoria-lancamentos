'use client';

/**
 * Painel do cliente (86e3k1q54): o fechamento do mês, a carteira com atraso e o
 * fluxo previsto, a origem e a atividade.
 *
 * **Nenhum número é calculado no navegador.** As contagens vêm do resumo do
 * cliente (`/summary`, do mês que o SERVIDOR decide), os valores da carteira do
 * `/titles/summary`, do relatório de recebíveis e do `/titles/flow`. O painel
 * cruza só o que é exibição: o status de cada conta (lista do mês) e os NOMES
 * (conta, tipo de anomalia, destino), que vêm dos catálogos de sempre porque o
 * resumo só carrega códigos.
 *
 * **Cada bloco carrega e falha SOZINHO**: skeleton e `role="alert"` com "Tentar
 * novamente" por bloco. Quem não lê a carteira (`view_client_receivables`, ou um
 * 403 do servidor) não vê o bloco da carteira nem o do fluxo, e o resto segue.
 *
 * "Nova conciliação" abre a MESMA gaveta da lista, só para quem a lista também
 * oferece (`reconciliationCreation`); cliente encerrado é só leitura.
 *
 * **A gestão de origens mora em Contas Bancárias** (08/10/2026). Aqui ela vira
 * uma linha de estado na faixa "Atividade", e a seção inteira (S9) só aparece,
 * em largura total e antes do fechamento, quando não há origem ATIVA: aí ela é
 * a ação necessária. A decisão é o `origin_status` do detalhe, nunca o
 * `provider_type`. Encerrado não ganha a seção (não há o que conectar).
 */

import { Plus } from 'lucide-react';
import Link from 'next/link';

import { reconciliationsPath } from '@/components/features/navigation/nav-items';
import { reconciliationCreation } from '@/components/features/reconciliations/create/creation-availability';
import { useCreateReconciliationDrawer } from '@/components/features/reconciliations/create/use-create-reconciliation-drawer';
import { Button } from '@/components/ui/button';
import { useAnomalyTypesList } from '@/hooks/use-anomaly-types';
import { useMappingDestinations } from '@/hooks/use-client-mapping';
import { useClientSummary } from '@/hooks/use-client-summary';
import {
  useClientTitlesFlow,
  useClientTitlesSummary,
  useReceivablesReport,
} from '@/hooks/use-client-titles';
import { useClientDetail, useReconciliationsList } from '@/hooks/use-clients';
import { useGlossaryList } from '@/hooks/use-glossary';
import { ApiError } from '@/lib/api/client';
import { hasPermission, isPlatformScoped } from '@/lib/authz';
import { formatReferenceMonth } from '@/lib/format';
import { originCodeFor } from '@/lib/origin-capabilities';
import { useAuthStore } from '@/stores/auth';

import { ClientConnectionsSection } from './connections/client-connections-section';
import { ActivitySection } from './dashboard/activity-section';
import { BlockError, CardSkeleton } from './dashboard/dashboard-card';
import { MonthClosing } from './dashboard/month-closing';
import { NoReconciliations } from './dashboard/no-reconciliations';
import { FlowCard, PortfolioCards, PortfolioNeverSynced } from './dashboard/portfolio-section';

/** Teto da lista do mês: uma conciliação por conta, e o cache de contas é pequeno. */
const MONTH_PAGE_SIZE = 100;

/** "Outubro de 2026" → "outubro de 2026", para caber no meio da frase. */
function monthInSentence(referenceMonth: string): string {
  const label = formatReferenceMonth(referenceMonth);
  return label.charAt(0).toLowerCase() + label.slice(1);
}

function timeOf(epochMs: number): string {
  const date = new Date(epochMs);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

export function ClientDashboard({ clientId }: { clientId: string }) {
  const user = useAuthStore((s) => s.user);
  const detailQuery = useClientDetail(clientId);
  const summaryQuery = useClientSummary(clientId);
  const referenceMonth = summaryQuery.data?.referenceMonth;
  const monthQuery = useReconciliationsList(
    clientId,
    { page: 1, pageSize: MONTH_PAGE_SIZE, month: referenceMonth ?? '' },
    { enabled: referenceMonth !== undefined },
  );

  const canViewTitles = hasPermission(user, 'view_client_receivables');
  const titlesSummaryQuery = useClientTitlesSummary(clientId, { enabled: canViewTitles });
  const reportQuery = useReceivablesReport(clientId, { enabled: canViewTitles });
  const flowQuery = useClientTitlesFlow(clientId, { enabled: canViewTitles });

  const detail = detailQuery.data;
  // Nomes dos destinos: o catálogo da organização DO CLIENTE, pela mesma regra
  // da tela do de-para (só a plataforma precisa dizer qual organização).
  const platform = isPlatformScoped(user);
  const clientOrganizationId = detail?.organization.id ?? null;
  const destinationsQuery = useMappingDestinations(platform ? clientOrganizationId : null, {
    enabled: user !== null && (!platform || clientOrganizationId !== null),
  });
  const anomalyTypesQuery = useAnomalyTypesList({ page: 1, pageSize: 100 });
  const glossaryQuery = useGlossaryList(clientId, { page: 1, pageSize: 1 });
  const creation = useCreateReconciliationDrawer(clientId);

  const { isClosed, canCreate } = reconciliationCreation(detail);
  const summary = summaryQuery.data;
  const originStatus = detail?.origin_status ?? 'sem_origem';
  // A seção completa só sem origem ativa, e nunca antes do detalhe chegar: o
  // padrão `sem_origem` piscaria a seção para quem tem origem.
  const showOriginSection = detail !== undefined && !isClosed && originStatus !== 'ativa';

  const anomalyTypeNames = new Map(
    (anomalyTypesQuery.data?.data ?? []).map((type) => [type.code, type.name]),
  );
  const destinationNames = new Map(
    (destinationsQuery.data ?? []).map((destination) => [destination.type, destination.name]),
  );

  // A carteira some INTEIRA (e o fluxo junto) para quem não a lê: pela matriz,
  // antes de qualquer request, ou por um 403 do servidor.
  const showPortfolio = canViewTitles && !isForbidden(titlesSummaryQuery.error);
  const canSyncTitles =
    hasPermission(user, 'sync_client_receivables') &&
    !isClosed &&
    detail !== undefined &&
    originCodeFor(detail.origin_status, detail.connections, 'listar_titulos_em_aberto') === null;

  const subtitle = [
    detail?.name,
    referenceMonth !== undefined ? `fechamento de ${monthInSentence(referenceMonth)}` : undefined,
    summaryQuery.dataUpdatedAt > 0
      ? `atualizado às ${timeOf(summaryQuery.dataUpdatedAt)}`
      : undefined,
  ].filter((part): part is string => part !== undefined);

  const closingLoading = summaryQuery.isLoading || detailQuery.isLoading || monthQuery.isLoading;
  const closingError = summaryQuery.error ?? detailQuery.error ?? monthQuery.error;

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h1 className="text-xl font-semibold">Painel</h1>
          {subtitle.length > 0 && (
            <p className="text-muted-foreground text-sm">{subtitle.join(' · ')}</p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline">
            <Link href={reconciliationsPath(clientId)}>Ver conciliações</Link>
          </Button>
          {detail !== undefined && canCreate && (
            <Button type="button" onClick={creation.open}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              Nova conciliação
            </Button>
          )}
        </div>
      </header>

      {showOriginSection && (
        <ClientConnectionsSection
          clientId={clientId}
          originStatus={originStatus}
          isClosed={isClosed}
        />
      )}

      <section aria-labelledby="dashboard-closing-heading" className="space-y-3">
        <h2 id="dashboard-closing-heading" className="text-base font-semibold">
          Fechamento do mês
        </h2>
        {closingLoading ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {['conciliações', 'anomalias', 'compras do cartão', 'de-para'].map((label) => (
              <CardSkeleton key={label} label={`Carregando ${label}`} />
            ))}
          </div>
        ) : closingError !== null || summary === undefined || detail === undefined ? (
          <BlockError
            error={closingError}
            fallback="Não foi possível carregar o fechamento do mês."
            onRetry={() => {
              void summaryQuery.refetch();
              void detailQuery.refetch();
              void monthQuery.refetch();
            }}
          />
        ) : (
          <MonthClosing
            clientId={clientId}
            summary={summary}
            accounts={detail.accounts ?? []}
            monthSessions={monthQuery.data?.data ?? []}
            anomalyTypeNames={anomalyTypeNames}
            destinationNames={destinationNames}
            emptyState={
              summary.latestSession === null ? (
                <NoReconciliations clientId={clientId} detail={detail} />
              ) : undefined
            }
          />
        )}
      </section>

      {showPortfolio && (
        <section aria-labelledby="dashboard-portfolio-heading" className="space-y-3">
          <h2 id="dashboard-portfolio-heading" className="text-base font-semibold">
            Carteira em aberto
          </h2>
          {titlesSummaryQuery.isLoading ? (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <CardSkeleton label="Carregando a receber" />
              <CardSkeleton label="Carregando a pagar" />
            </div>
          ) : titlesSummaryQuery.isError || titlesSummaryQuery.data === undefined ? (
            <BlockError
              error={titlesSummaryQuery.error}
              fallback="Não foi possível carregar a carteira."
              onRetry={() => void titlesSummaryQuery.refetch()}
            />
          ) : titlesSummaryQuery.data.neverSynced ? (
            <PortfolioNeverSynced clientId={clientId} canSync={canSyncTitles} />
          ) : (
            <PortfolioCards
              summary={titlesSummaryQuery.data}
              report={reportQuery.data}
              flow={flowQuery.data}
            />
          )}
        </section>
      )}

      {/* O fluxo em LARGURA INTEIRA (08/10/2026): cinco faixas com dois valores
          cada não cabem numa coluna de dois terços. */}
      {showPortfolio &&
        titlesSummaryQuery.data?.neverSynced !== true &&
        (flowQuery.isLoading || titlesSummaryQuery.isLoading ? (
          <CardSkeleton label="Carregando o fluxo previsto" className="h-72" />
        ) : flowQuery.isError || flowQuery.data === undefined ? (
          isForbidden(flowQuery.error) ? null : (
            <BlockError
              error={flowQuery.error}
              fallback="Não foi possível carregar o fluxo previsto."
              onRetry={() => void flowQuery.refetch()}
            />
          )
        ) : (
          <FlowCard flow={flowQuery.data} />
        ))}

      <ActivitySection
        clientId={clientId}
        originStatus={originStatus}
        connections={detail?.connections ?? []}
        isClosed={isClosed}
        canManageOrigins={hasPermission(user, 'manage_client_connections')}
        latestSession={summary?.latestSession}
        glossary={
          glossaryQuery.data !== undefined
            ? {
                total: glossaryQuery.data.pagination.total,
                version: glossaryQuery.data.data.version,
              }
            : undefined
        }
      />

      {canCreate && creation.drawer}
    </div>
  );
}
