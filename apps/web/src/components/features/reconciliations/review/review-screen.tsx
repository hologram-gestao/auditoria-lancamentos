'use client';

/**
 * Orquestrador da Tela de Revisão — `[FRONT 9.11]`, Doc §14.1.
 *
 * Estado da aba sincronizado com a query string (`?tab=...`):
 *   - F5 mantém a aba; URL é compartilhável.
 *   - Default: `movements`. Valores fora do whitelist caem no default.
 *
 * Dados estáticos da sessão (referência, conta, total de movimentações):
 *   - Vêm do endpoint dedicado `GET /api/v1/reconciliations/{id}` via
 *     `useSessionDetail`. Antes resolvíamos via scan O(N) do histórico
 *     paginado do cliente (`useReconciliationsList`), que quebrava
 *     silenciosamente em clientes com > 100 sessões.
 *
 * Contadores vivos:
 *   - `useSessionStatus(sessionId)` sem polling externo (polling só roda
 *     enquanto `status === 'processing'`; aqui já entramos em `reviewing`).
 *   - Toda mutation invalida `statusKey(sessionId)` → header atualiza sem
 *     intervenção do componente.
 */

import { useRouter, useSearchParams } from 'next/navigation';
import { useMemo } from 'react';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useClientDetail } from '@/hooks/use-clients';
import { useSessionDetail, useSessionStatus } from '@/hooks/use-reconciliations';
import { cn } from '@/lib/utils';

import { AnomaliesTab } from './anomalies-tab';
import { MovementsTab } from './movements-tab';
import { OmieDivergencesTab } from './omie-divergences-tab';
import { ReviewHeader } from './review-header';
import { SummaryTab } from './summary-tab';

interface ReviewScreenProps {
  clientId: string;
  sessionId: string;
}

type TabId = 'movements' | 'divergencias' | 'anomalias' | 'resumo';
const VALID_TABS: ReadonlySet<TabId> = new Set([
  'movements',
  'divergencias',
  'anomalias',
  'resumo',
]);
const DEFAULT_TAB: TabId = 'movements';

/** Meses em PT-BR para o breadcrumb. ISO 1-12 → label. */
const MONTHS_PT_BR: readonly string[] = [
  'Janeiro',
  'Fevereiro',
  'Março',
  'Abril',
  'Maio',
  'Junho',
  'Julho',
  'Agosto',
  'Setembro',
  'Outubro',
  'Novembro',
  'Dezembro',
];

function formatReferenceMonth(iso: string | undefined): string {
  if (iso === undefined) return '';
  const match = /^(\d{4})-(\d{2})/.exec(iso);
  if (!match) return iso;
  const [, year, monthStr] = match;
  const monthIdx = Number(monthStr) - 1;
  const label = MONTHS_PT_BR[monthIdx] ?? monthStr;
  return `${label}/${year}`;
}

export function ReviewScreen({ clientId, sessionId }: ReviewScreenProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawTab = searchParams.get('tab');
  const activeTab: TabId =
    rawTab !== null && VALID_TABS.has(rawTab as TabId) ? (rawTab as TabId) : DEFAULT_TAB;

  function handleTabChange(value: string) {
    const sp = new URLSearchParams(searchParams.toString());
    sp.set('tab', value);
    router.replace(`?${sp.toString()}`, { scroll: false });
  }

  // Cliente + contas (cache L1) para resolver nome do cliente e label da conta.
  const clientQuery = useClientDetail(clientId);

  // Sessão (estático): endpoint dedicado — O(1) por sessionId, sem scan.
  const detailQuery = useSessionDetail(sessionId);
  const sessionInfo = detailQuery.data;

  // Status vivo (contadores). Não polla — só se status virar processing.
  const statusQuery = useSessionStatus(sessionId);

  const referenceMonthLabel = formatReferenceMonth(sessionInfo?.reference_month);
  const accountLabel = useMemo(() => {
    if (sessionInfo === undefined || clientQuery.data === undefined) return undefined;
    const account = clientQuery.data.accounts.find(
      (a) => a.omie_conta_id === sessionInfo.omie_conta_id,
    );
    if (account === undefined) return `Conta Omie ${sessionInfo.omie_conta_id}`;
    return `${account.name} · ${account.bank_name}`;
  }, [clientQuery.data, sessionInfo]);

  const counts = {
    conciliated: statusQuery.data?.conciliated_count ?? sessionInfo?.conciliated_count ?? 0,
    semOmie: statusQuery.data?.sem_omie_count ?? sessionInfo?.sem_omie_count ?? 0,
    omieSemArquivo:
      statusQuery.data?.omie_sem_arquivo_count ?? sessionInfo?.omie_sem_arquivo_count ?? 0,
    anomaly: statusQuery.data?.anomaly_count ?? sessionInfo?.anomaly_count ?? 0,
  };

  const totalFileEntries = sessionInfo?.total_file_entries ?? 0;

  return (
    <div className="space-y-6">
      <ReviewHeader
        clientId={clientId}
        clientName={clientQuery.data?.name ?? 'Cliente'}
        referenceMonthLabel={referenceMonthLabel}
        accountLabel={accountLabel}
        counts={counts}
      />

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList className="h-auto flex-wrap">
          <TabsTrigger value="movements">Movimentações ({totalFileEntries})</TabsTrigger>
          <TabsTrigger value="divergencias">
            Divergências Omie ({counts.omieSemArquivo})
          </TabsTrigger>
          <TabsTrigger value="anomalias">
            <span className={cn(counts.anomaly > 0 && 'text-orange-700 dark:text-orange-300')}>
              Anomalias ({counts.anomaly})
            </span>
          </TabsTrigger>
          <TabsTrigger value="resumo">Resumo</TabsTrigger>
        </TabsList>

        <TabsContent value="movements">
          <MovementsTab sessionId={sessionId} />
        </TabsContent>
        <TabsContent value="divergencias">
          <OmieDivergencesTab sessionId={sessionId} />
        </TabsContent>
        <TabsContent value="anomalias">
          <AnomaliesTab sessionId={sessionId} />
        </TabsContent>
        <TabsContent value="resumo">
          <SummaryTab
            sessionId={sessionId}
            totalFileEntries={totalFileEntries}
            counts={counts}
            referenceMonthLabel={referenceMonthLabel}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
