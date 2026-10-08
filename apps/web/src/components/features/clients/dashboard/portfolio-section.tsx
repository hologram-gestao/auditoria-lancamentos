/**
 * "Carteira em aberto" e "Fluxo previsto pelos vencimentos" do painel do
 * cliente (86e3k1q54).
 *
 * Fontes, todas do servidor e calculadas sobre a carteira INTEIRA:
 *   - `/titles/summary` (S11): em aberto, a vencer, vencido e os quatro baldes;
 *   - `/titles/receivables-report` (S15): inadimplência real do a receber;
 *   - `/titles/flow` (86e3k1q4g): as faixas de vencimento e o líquido.
 *
 * **Nada aqui é calculado no navegador** além da geometria das barras. A barra de
 * atraso é decorativa (`aria-hidden`): os quatro valores estão escritos ao lado.
 */
import { Loader2, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import { toast } from 'sonner';

import { BUCKET_LABELS } from '@/components/features/client-titles/client-titles-badges';
import {
  BUCKET_TONE,
  OVERDUE_BUCKETS,
} from '@/components/features/client-titles/client-titles-summary';
import { EmptyState } from '@/components/shared/empty-state';
import { Money } from '@/components/shared/money';
import { PortfolioVignette } from '@/components/shared/vignettes';
import { Button } from '@/components/ui/button';
import { useSyncClientTitles } from '@/hooks/use-client-titles';
import { ApiError } from '@/lib/api/client';
import { PRODUCT_NAME } from '@/lib/brand';
import type { AgingTotals, ReceivablesReport, TitlesFlow, TitlesSummary } from '@/lib/contracts';
import { formatBRDate, formatSyncedAt } from '@/lib/format';
import { cn } from '@/lib/utils';

import { DashboardCard } from './dashboard-card';
import { FlowChart } from './flow-chart';

/**
 * Fatia de cada balde na barra de atraso: 1 a 30 e 31 a 60 em `warning`, 61 a 90
 * e 90+ em `destructive`, o primeiro de cada par mais claro. A cor é só reforço;
 * o rótulo e o valor estão no texto.
 */
const BUCKET_SEGMENT: Record<(typeof OVERDUE_BUCKETS)[number], string> = {
  '1_30': 'bg-warning opacity-60',
  '31_60': 'bg-warning',
  '61_90': 'bg-destructive opacity-60',
  '90_mais': 'bg-destructive',
};

const BUCKET_FIELD: Record<(typeof OVERDUE_BUCKETS)[number], keyof AgingTotals> = {
  '1_30': 'bucket1a30',
  '31_60': 'bucket31a60',
  '61_90': 'bucket61a90',
  '90_mais': 'bucket90Mais',
};

function amount(value: string): number {
  const num = Number(value);
  return Number.isFinite(num) ? num : 0;
}

interface PortfolioCardsProps {
  summary: TitlesSummary;
  /** `undefined` enquanto carrega ou quando falhou: o rodapé some, o card fica. */
  report: ReceivablesReport | undefined;
  flow: TitlesFlow | undefined;
}

export function PortfolioCards({ summary, report, flow }: PortfolioCardsProps) {
  const dueSoon = flow?.buckets.find((b) => b.bucket === 'ate_7')?.aPagar;
  const delinquency = report?.aReceber.inadimplencia;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <SideCard
          title="A receber"
          titleId="dashboard-card-receivable"
          totals={summary.aReceber}
          footer={
            delinquency !== undefined ? (
              <FooterLine label="Inadimplência real (sem contexto)">
                <Money value={delinquency.total} tone="overdue" className="font-medium" />{' '}
                <span className="text-muted-foreground">
                  ({delinquency.qtd} {delinquency.qtd === 1 ? 'título' : 'títulos'})
                </span>
              </FooterLine>
            ) : undefined
          }
        />
        <SideCard
          title="A pagar"
          titleId="dashboard-card-payable"
          totals={summary.aPagar}
          footer={
            dueSoon !== undefined ? (
              <FooterLine label="Vence nos próximos 7 dias">
                <Money value={dueSoon.total} className="font-medium" />{' '}
                <span className="text-muted-foreground">
                  ({dueSoon.count} {dueSoon.count === 1 ? 'título' : 'títulos'})
                </span>
              </FooterLine>
            ) : undefined
          }
        />
      </div>
      <p className="text-muted-foreground text-xs">
        {formatSyncedAt(summary.syncedAt)} · aging com referência em{' '}
        {formatBRDate(summary.referenceDate)}
        {summary.syncFailedAt != null && (
          <span className="text-warning"> · a última tentativa de sincronizar falhou</span>
        )}
      </p>
    </div>
  );
}

function FooterLine({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <p className="flex flex-wrap items-baseline justify-between gap-x-2 border-t pt-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span>{children}</span>
    </p>
  );
}

function SideCard({
  title,
  titleId,
  totals,
  footer,
}: {
  title: string;
  titleId: string;
  totals: AgingTotals;
  footer?: React.ReactNode;
}) {
  const overdue = amount(totals.totalVencido);
  return (
    <DashboardCard title={title} titleId={titleId} footer={footer}>
      <dl className="grid grid-cols-3 gap-2 text-sm">
        <div className="min-w-0">
          <dt className="text-muted-foreground text-xs">Em aberto</dt>
          <dd className="font-semibold">
            <Money value={totals.totalEmAberto} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground text-xs">A vencer</dt>
          <dd className="font-semibold">
            <Money value={totals.totalAVencer} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground text-xs">Vencido</dt>
          <dd className="font-semibold">
            <Money value={totals.totalVencido} tone="overdue" />
          </dd>
        </div>
      </dl>
      <div
        aria-hidden="true"
        data-testid="aging-bar"
        className="bg-muted flex h-2 w-full overflow-hidden rounded-full"
      >
        {overdue > 0 &&
          OVERDUE_BUCKETS.map((bucket) => {
            const value = amount(String(totals[BUCKET_FIELD[bucket]]));
            return (
              <div
                key={bucket}
                className={cn('h-full', BUCKET_SEGMENT[bucket])}
                style={{ width: `${(value / overdue) * 100}%` }}
              />
            );
          })}
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:grid-cols-4">
        {OVERDUE_BUCKETS.map((bucket) => (
          <div key={bucket} className="min-w-0">
            <dt className="text-muted-foreground">{BUCKET_LABELS[bucket]}</dt>
            <dd className="font-medium">
              <Money value={String(totals[BUCKET_FIELD[bucket]])} tone={BUCKET_TONE[bucket]} />
            </dd>
          </div>
        ))}
      </dl>
    </DashboardCard>
  );
}

/**
 * Carteira nunca sincronizada: sem zeros que pareçam resultado (R3 da S11). Quem
 * pode sincronizar (`sync_client_receivables`, cliente aberto e origem que lista
 * títulos) recebe o botão; os demais, o texto que diz onde isso se resolve.
 */
export function PortfolioNeverSynced({
  clientId,
  canSync,
}: {
  clientId: string;
  canSync: boolean;
}) {
  const syncMutation = useSyncClientTitles(clientId);
  const isSyncing = syncMutation.isPending;

  async function handleSync() {
    try {
      await syncMutation.mutateAsync();
      toast.success('Carteira sincronizada.');
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível sincronizar a carteira.',
      );
    }
  }

  // Moldura e vinheta do `EmptyState` (86e3h57b5); o texto e as ações não mudaram.
  return (
    <EmptyState
      data-testid="dashboard-portfolio-never-synced"
      className="bg-card"
      vignette={<PortfolioVignette />}
      description={
        canSync
          ? 'A carteira deste cliente ainda não foi sincronizada. Sincronize para ver os títulos em aberto e o fluxo previsto.'
          : 'A carteira deste cliente ainda não foi sincronizada. Quem gerencia o cliente pode sincronizá-la na Carteira.'
      }
      action={
        canSync ? (
          <Button type="button" onClick={() => void handleSync()} disabled={isSyncing}>
            {isSyncing ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
            )}
            {isSyncing ? 'Sincronizando…' : 'Sincronizar agora'}
          </Button>
        ) : (
          <Button asChild variant="outline">
            <Link href={`/clientes/${clientId}/carteira`}>Ir para a Carteira</Link>
          </Button>
        )
      }
    />
  );
}

/** O bloco do fluxo previsto: legenda, gráfico, vencidos em texto e o rodapé honesto. */
export function FlowCard({ flow }: { flow: TitlesFlow }) {
  const overdue = flow.buckets.find((b) => b.bucket === 'vencidos');
  return (
    <DashboardCard title="Fluxo previsto pelos vencimentos" titleId="dashboard-card-flow" level={2}>
      <div className="text-muted-foreground flex flex-wrap gap-4 text-xs">
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="bg-info inline-block h-2.5 w-2.5 rounded-sm" />A
          receber
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden="true" className="bg-warning inline-block h-2.5 w-2.5 rounded-sm" />A
          pagar
        </span>
      </div>
      <FlowChart buckets={flow.buckets} />
      {overdue !== undefined && (
        <p className="text-sm" data-testid="flow-overdue-line">
          <span className="text-muted-foreground">Já vencidos: </span>
          <Money value={overdue.aReceber.total} tone="sign" /> a receber ·{' '}
          <Money value={`-${overdue.aPagar.total}`} tone="sign" /> a pagar
        </p>
      )}
      <p className="text-muted-foreground text-xs">
        {`Soma dos títulos em aberto por faixa de vencimento, com o saldo líquido de cada faixa. Não é saldo de conta: o ${PRODUCT_NAME} não lê saldo das contas.`}
      </p>
    </DashboardCard>
  );
}
