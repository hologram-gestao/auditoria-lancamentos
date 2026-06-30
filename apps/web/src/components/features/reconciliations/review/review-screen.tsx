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

import { AlertTriangle, ChevronRight, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

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
  useSessionStatus,
} from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
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
  // Mantemos a chamada incondicional pra respeitar Rules of Hooks — o
  // `useSessionStatus` por dentro já evita polling quando status='error'.
  const statusQuery = useSessionStatus(sessionId);

  const referenceMonthLabel = formatReferenceMonth(sessionInfo?.reference_month);
  const accountName = useMemo(() => {
    if (sessionInfo === undefined || clientQuery.data === undefined) return undefined;
    const account = clientQuery.data.accounts.find(
      (a) => a.omie_conta_id === sessionInfo.omie_conta_id,
    );
    return account?.name ?? `Conta Omie ${sessionInfo.omie_conta_id}`;
  }, [clientQuery.data, sessionInfo]);
  // FRONT 1.8: tipo normalizado da sessão ('credit_card' p/ fatura de cartão).
  const isCard = sessionInfo?.account_type === 'credit_card';
  // Mini-fase conta aplicação ('investment' p/ extrato de CDB/aplicação).
  const isInvestment = sessionInfo?.account_type === 'investment';

  const counts = {
    conciliated: statusQuery.data?.conciliated_count ?? sessionInfo?.conciliated_count ?? 0,
    semOmie: statusQuery.data?.sem_omie_count ?? sessionInfo?.sem_omie_count ?? 0,
    omieSemArquivo:
      statusQuery.data?.omie_sem_arquivo_count ?? sessionInfo?.omie_sem_arquivo_count ?? 0,
    anomaly: statusQuery.data?.anomaly_count ?? sessionInfo?.anomaly_count ?? 0,
  };

  const totalFileEntries = sessionInfo?.total_file_entries ?? 0;

  // Sessão em erro: renderiza tela de erro com botão "Tentar novamente" SEM
  // abrir as abas (cujos endpoints retornariam 409 ConflictError pela guarda
  // em `_load_session_for_rbac`). Checagem feita depois de todos os hooks
  // pra respeitar Rules of Hooks.
  if (sessionInfo?.status === 'error') {
    return (
      <ReviewErrorScreen
        clientId={clientId}
        sessionId={sessionId}
        clientName={clientQuery.data?.name}
        errorMessage={sessionInfo.error_message}
      />
    );
  }

  return (
    <div className="space-y-6">
      <ReviewHeader
        clientId={clientId}
        clientName={clientQuery.data?.name ?? 'Cliente'}
        sessionId={sessionId}
        referenceMonthLabel={referenceMonthLabel}
        accountName={accountName}
        isCard={isCard}
        isInvestment={isInvestment}
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
          <MovementsTab sessionId={sessionId} isCard={isCard} />
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
            isCard={isCard}
            totalFileEntries={totalFileEntries}
            counts={counts}
            referenceMonthLabel={referenceMonthLabel}
            balances={
              sessionInfo === undefined
                ? undefined
                : {
                    start: sessionInfo.balance_start,
                    endFile: sessionInfo.balance_end_file,
                    endOmie: sessionInfo.balance_end_omie,
                    difference: sessionInfo.balance_difference,
                  }
            }
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

interface ReviewErrorScreenProps {
  clientId: string;
  sessionId: string;
  clientName?: string;
  errorMessage: string | null;
}

/**
 * Tela mostrada quando a sessão está em `status='error'`. Não dispara
 * nenhum dos endpoints de revisão (que retornariam 409). Oferece o botão
 * "Tentar novamente" que chama `POST /reconciliations/{id}/reprocess` e
 * redireciona pra rota de processing (mesmo fluxo do create).
 */
function ReviewErrorScreen({
  clientId,
  sessionId,
  clientName,
  errorMessage,
}: ReviewErrorScreenProps) {
  const router = useRouter();
  const reprocessMutation = useReprocessReconciliation(sessionId, clientId);
  const discardMutation = useDiscardReconciliation(sessionId, clientId);
  const [confirmDiscardOpen, setConfirmDiscardOpen] = useState(false);

  async function handleReprocess() {
    try {
      await reprocessMutation.mutateAsync();
      toast.success('Reprocessamento iniciado.');
      // Navega pra tela de processing (mesma URL do create, com polling +
      // redirect automático pra revisão quando terminar). `router.refresh()`
      // sozinho não basta — ele recarrega server components mas o
      // ReviewScreen continua na mesma URL e cai de novo no ReviewErrorScreen
      // até o TanStack re-buscar o detail e o status virar `processing`.
      router.push(`/clientes/${clientId}/conciliacao/processando/${sessionId}`);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível reprocessar a conciliação.';
      toast.error(message);
    }
  }

  async function handleDiscard() {
    try {
      await discardMutation.mutateAsync();
      toast.success('Conciliação descartada.');
      setConfirmDiscardOpen(false);
      // Após descartar, a sessão some — volta pro detalhe do cliente.
      router.push(`/clientes/${clientId}`);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível descartar a conciliação.';
      toast.error(message);
    }
  }

  const isAnyPending = reprocessMutation.isPending || discardMutation.isPending;

  return (
    <div className="space-y-6">
      <nav aria-label="Breadcrumb" className="text-muted-foreground text-sm">
        <ol className="flex items-center gap-1.5">
          <li>
            <Link href="/clientes" className="hover:text-foreground hover:underline">
              Clientes
            </Link>
          </li>
          <li aria-hidden="true">
            <ChevronRight className="h-3.5 w-3.5" />
          </li>
          <li>
            <Link href={`/clientes/${clientId}`} className="hover:text-foreground hover:underline">
              {clientName ?? 'Cliente'}
            </Link>
          </li>
          <li aria-hidden="true">
            <ChevronRight className="h-3.5 w-3.5" />
          </li>
          <li className="text-foreground font-medium" aria-current="page">
            Conciliação com erro
          </li>
        </ol>
      </nav>

      <div className="bg-destructive/5 border-destructive/30 text-destructive space-y-4 rounded-lg border p-6">
        <div className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
          <div className="space-y-1">
            <h1 className="text-lg font-semibold">Esta conciliação terminou em erro</h1>
            <p className="text-sm">
              {errorMessage ?? 'O processamento da conciliação falhou. Tente novamente.'}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => void handleReprocess()}
            disabled={isAnyPending}
            aria-live="polite"
          >
            {reprocessMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
            )}
            {reprocessMutation.isPending ? 'Reprocessando…' : 'Tentar novamente'}
          </Button>
          <Button
            variant="ghost"
            onClick={() => setConfirmDiscardOpen(true)}
            disabled={isAnyPending}
            className="text-destructive hover:text-destructive hover:bg-destructive/10"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            Descartar
          </Button>
          <Button variant="ghost" asChild>
            <Link href={`/clientes/${clientId}`}>Voltar para o cliente</Link>
          </Button>
        </div>
      </div>

      <Dialog open={confirmDiscardOpen} onOpenChange={setConfirmDiscardOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Descartar esta conciliação?</DialogTitle>
            <DialogDescription>
              A conciliação some do histórico do cliente e libera o mesmo arquivo + mês de
              referência para uma nova tentativa. Esta ação não pode ser desfeita pela interface — o
              registro fica preservado no banco apenas para auditoria.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmDiscardOpen(false)}
              disabled={discardMutation.isPending}
            >
              Cancelar
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleDiscard()}
              disabled={discardMutation.isPending}
              aria-live="polite"
            >
              {discardMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              )}
              {discardMutation.isPending ? 'Descartando…' : 'Descartar conciliação'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
