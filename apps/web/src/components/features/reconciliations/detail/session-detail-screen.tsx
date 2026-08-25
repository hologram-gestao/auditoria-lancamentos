'use client';

/**
 * Detalhe da conciliação — Sprint 4 / R3.
 *
 * Abrir um item da lista cai AQUI (e não direto na revisão): o topo traz os
 * **totalizadores** e o **resumo geral de saldos**, e as abas de revisão
 * (Movimentações / Divergências Omie / Anomalias / Resumo) continuam abaixo,
 * com a aba ativa na URL (`?tab=`).
 *
 * Três estados, um por status da sessão:
 *   - `processing` → progresso (a tela é OPCIONAL: quem quiser pode sair, o
 *     servidor conclui e notifica). O `useSessionDetail` faz o polling e a tela
 *     vira totalizadores sozinha quando termina;
 *   - `error` → mensagem genérica + **código** (S2/R9), em `destructive`,
 *     **nunca em âmbar** — âmbar não é fundo de banner, e erro não é aviso;
 *   - demais → totalizadores + resumo + abas.
 *
 * O breadcrumb e o nome do cliente vêm do `<ClientShell>` (layout do cliente),
 * por isso esta tela não repete a trilha de navegação.
 */

import { AlertCircle, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { ReconciliationStatusBadge } from '@/components/features/clients/reconciliation-status-badge';
import { AuthorLabel } from '@/components/features/reconciliations/author-label';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useClientDetail } from '@/hooks/use-clients';
import {
  useDiscardReconciliation,
  useReprocessReconciliation,
  useSessionDetail,
} from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { SessionDetail } from '@/lib/api/reconciliations';
import { formatCreatedAt, formatReferenceMonth } from '@/lib/format';

import { AnomaliesTab } from '../review/anomalies-tab';
import { GlossarySeal } from '../review/glossary-seal';
import { MovementsTab } from '../review/movements-tab';
import { OmieDivergencesTab } from '../review/omie-divergences-tab';
import { SummaryTab } from '../review/summary-tab';
import { accountNameFor } from '../session-label';

import { ExportReportButton } from './export-report-button';
import { SessionFilesPanel } from './session-files-panel';
import { BalanceSummary, SessionTotals } from './session-totals';

type TabId = 'movements' | 'divergencias' | 'anomalias' | 'resumo';
const VALID_TABS: ReadonlySet<string> = new Set([
  'movements',
  'divergencias',
  'anomalias',
  'resumo',
]);
const DEFAULT_TAB: TabId = 'movements';

interface SessionDetailScreenProps {
  clientId: string;
  sessionId: string;
}

export function SessionDetailScreen({ clientId, sessionId }: SessionDetailScreenProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const detailQuery = useSessionDetail(sessionId);
  const clientQuery = useClientDetail(clientId);

  const rawTab = searchParams.get('tab');
  const activeTab: TabId =
    rawTab !== null && VALID_TABS.has(rawTab) ? (rawTab as TabId) : DEFAULT_TAB;

  function handleTabChange(value: string) {
    const sp = new URLSearchParams(searchParams.toString());
    sp.set('tab', value);
    router.replace(`?${sp.toString()}`, { scroll: false });
  }

  const detail = detailQuery.data;
  // Fonte única do rótulo (86e2u513w): o breadcrumb do ClientShell deriva o
  // mesmo nome do mesmo helper — divergir aqui é mostrar duas contas diferentes
  // na mesma tela.
  const accountName = useMemo(() => {
    if (detail === undefined) return undefined;
    return accountNameFor(clientQuery.data?.accounts ?? [], detail.omie_conta_id);
  }, [clientQuery.data, detail]);

  if (detailQuery.isLoading) {
    return <DetailSkeleton />;
  }

  if (detailQuery.isError || detail === undefined) {
    const err = detailQuery.error;
    const isNotFound = err instanceof ApiError && err.status === 404;
    return (
      <div
        role="alert"
        className="bg-destructive/5 border-destructive/30 text-destructive space-y-3 rounded-lg border p-6"
      >
        <h2 className="text-lg font-semibold">
          {isNotFound ? 'Conciliação não encontrada' : 'Não foi possível carregar a conciliação'}
        </h2>
        <p className="text-sm">
          {err instanceof ApiError ? err.userMessage : 'Ocorreu um erro inesperado.'}
        </p>
        {!isNotFound && (
          <Button variant="outline" size="sm" onClick={() => void detailQuery.refetch()}>
            Tentar novamente
          </Button>
        )}
      </div>
    );
  }

  const referenceLabel = formatReferenceMonth(detail.reference_month);
  const isProcessing = detail.status === 'processing';
  const isError = detail.status === 'error';

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h1 className="truncate text-xl font-semibold">
            {accountName} · {referenceLabel}
          </h1>
          {/* `flex-wrap`: em 390px o selo do glossário não cabe ao lado do badge
              de status — sem isto ele sairia da viewport (o mesmo defeito que o
              "Sair" do header teve na S5, ADR-010-FE). */}
          {/* 86e2n39f1 — a mesma informação do card da lista, no header. */}
          {detail.created_by && (
            <p className="text-muted-foreground text-sm">
              Conciliado por <AuthorLabel author={detail.created_by} />
              {detail.created_at ? ` · ${formatCreatedAt(detail.created_at)}` : ''}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <ReconciliationStatusBadge status={detail.status} />
            {/* Sprint 6 / R4: só aparece quando a análise realmente usou o
                glossário. Cliente sem glossário → tela idêntica à de antes. */}
            <GlossarySeal used={detail.qualification_used_glossary} />
          </div>
        </div>
        {!isProcessing && !isError && (
          <ExportReportButton sessionId={sessionId} referenceMonthLabel={referenceLabel} />
        )}
      </header>

      {isProcessing ? (
        <ProcessingPanel />
      ) : isError ? (
        <ErrorPanel clientId={clientId} sessionId={sessionId} detail={detail} />
      ) : (
        <>
          <SessionTotals detail={detail} />
          <BalanceSummary detail={detail} />
          <SessionFilesPanel sessionId={sessionId} isProcessing={false} />

          <Tabs value={activeTab} onValueChange={handleTabChange}>
            <TabsList className="h-auto flex-wrap">
              <TabsTrigger value="movements">
                Movimentações ({detail.total_file_entries})
              </TabsTrigger>
              <TabsTrigger value="divergencias">
                Divergências Omie ({detail.omie_sem_arquivo_count})
              </TabsTrigger>
              <TabsTrigger value="anomalias">Anomalias ({detail.anomaly_count})</TabsTrigger>
              <TabsTrigger value="resumo">Resumo</TabsTrigger>
            </TabsList>

            <TabsContent value="movements">
              <MovementsTab sessionId={sessionId} isCard={detail.account_type === 'credit_card'} />
            </TabsContent>
            <TabsContent value="divergencias">
              <OmieDivergencesTab sessionId={sessionId} />
            </TabsContent>
            <TabsContent value="anomalias">
              <AnomaliesTab sessionId={sessionId} isCard={detail.account_type === 'credit_card'} />
            </TabsContent>
            <TabsContent value="resumo">
              {/* Mesma fonte única do topo — por construção os números batem. */}
              <SummaryTab
                isCard={detail.account_type === 'credit_card'}
                totalFileEntries={detail.total_file_entries}
                counts={{
                  conciliated: detail.conciliated_count,
                  semOmie: detail.sem_omie_count,
                  omieSemArquivo: detail.omie_sem_arquivo_count,
                  anomaly: detail.anomaly_count,
                }}
                amounts={{
                  credits: detail.credits_total,
                  debits: detail.debits_total,
                  cardCharges: detail.card_charges_total ?? null,
                }}
                anomaliesBreakdown={{
                  critical: detail.anomalies_critical,
                  moderate: detail.anomalies_moderate,
                  info: detail.anomalies_info,
                  resolved: detail.anomalies_resolved,
                }}
                referenceMonthLabel={referenceLabel}
                balances={{
                  start: detail.balance_start ?? null,
                  endFile: detail.balance_end_file ?? null,
                  endOmie: detail.balance_end_omie ?? null,
                  difference: detail.balance_difference ?? null,
                }}
              />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}

/**
 * Progresso — informativo, **não** um bloqueio. A frase é o coração da sprint:
 * ninguém precisa ficar nesta tela.
 */
function ProcessingPanel() {
  return (
    <div
      role="status"
      className="border-info/40 bg-info-muted text-info flex items-start gap-3 rounded-lg border p-6"
    >
      <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-medium">Conciliação em processamento</p>
        <p className="text-sm">
          Estamos buscando os lançamentos no Omie e cruzando com o arquivo. Você pode sair desta
          tela ou fechar o navegador — avisamos no sino quando terminar.
        </p>
      </div>
    </div>
  );
}

/**
 * Erro: mensagem GENÉRICA + código. Nada de `error_message` (linguagem interna
 * do backend, S2/R9) e nada de fundo âmbar — erro é `destructive`.
 */
function ErrorPanel({
  clientId,
  sessionId,
  detail,
}: {
  clientId: string;
  sessionId: string;
  detail: SessionDetail;
}) {
  const router = useRouter();
  const reprocess = useReprocessReconciliation(sessionId, clientId);
  const discard = useDiscardReconciliation(sessionId, clientId);
  const [confirmOpen, setConfirmOpen] = useState(false);

  async function handleReprocess() {
    try {
      await reprocess.mutateAsync();
      toast.success('Reprocessamento iniciado.');
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível reprocessar a conciliação.',
      );
    }
  }

  async function handleDiscard() {
    try {
      await discard.mutateAsync();
      toast.success('Conciliação excluída.');
      router.push(`/clientes/${clientId}`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível excluir a conciliação.',
      );
    }
  }

  const busy = reprocess.isPending || discard.isPending;

  return (
    <div className="space-y-4">
      <div
        role="alert"
        className="bg-destructive/5 border-destructive/30 text-destructive flex items-start gap-3 rounded-lg border p-6"
      >
        <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div className="space-y-2">
          <p className="font-semibold">Não foi possível concluir esta conciliação.</p>
          <p className="text-sm">
            Tente novamente. Se o problema persistir, informe o código ao suporte
            {detail.error_code != null ? (
              <>
                : <strong>{detail.error_code}</strong>
              </>
            ) : (
              '.'
            )}
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleReprocess()}
              disabled={busy}
              aria-live="polite"
            >
              {reprocess.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              {reprocess.isPending ? 'Reprocessando…' : 'Tentar novamente'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmOpen(true)}
              disabled={busy}
              className="text-destructive hover:text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              Excluir conciliação
            </Button>
          </div>
        </div>
      </div>

      {/* Mesmo em erro, saber QUAL parte falhou é o que permite corrigir. */}
      <SessionFilesPanel sessionId={sessionId} isProcessing={false} />

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Excluir esta conciliação?</DialogTitle>
            <DialogDescription>
              A conciliação sai da lista e a conta+mês fica livre para uma nova. Esta ação não pode
              ser desfeita pela interface.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)} disabled={busy}>
              Voltar
            </Button>
            <Button
              variant="destructive"
              onClick={() => void handleDiscard()}
              disabled={busy}
              aria-live="polite"
            >
              {discard.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              )}
              {discard.isPending ? 'Excluindo…' : 'Excluir conciliação'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div role="status" className="space-y-6" aria-busy="true" aria-label="Carregando conciliação">
      <div className="bg-muted h-7 w-72 animate-pulse rounded" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="bg-card h-16 animate-pulse rounded-lg border" />
        ))}
      </div>
      <div className="bg-card h-28 animate-pulse rounded-lg border" />
    </div>
  );
}
