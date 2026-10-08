/**
 * "Fechamento do mês" do painel do cliente (86e3k1q54): quatro cards lidos do
 * resumo do cliente (`GET /clients/{id}/summary`), do mês que o SERVIDOR decide.
 *
 * Nenhum número é calculado aqui: contagens, cobertura e totais vêm do resumo.
 * Os NOMES (conta, tipo de anomalia, destino) vêm dos catálogos que já existem,
 * nunca do resumo, que só carrega códigos. Código sem nome no catálogo aparece
 * como código: a linha não some.
 */
import Link from 'next/link';

import {
  mappingPreviewPath,
  reconciliationsPath,
} from '@/components/features/navigation/nav-items';
import { Money } from '@/components/shared/money';
import type { BankAccount } from '@/lib/api/clients';
import type { ClientSummary, ReconciliationSessionListResponse } from '@/lib/contracts';
import { formatPercent } from '@/lib/format';
import { cn } from '@/lib/utils';

import { DashboardCard, ProgressBar } from './dashboard-card';

type SessionRow = ReconciliationSessionListResponse['data'][number];

/**
 * O status de uma conta NO FECHAMENTO. Aqui `reviewing` e `done` são estados
 * diferentes, ao contrário do badge da lista (que colapsa os dois em
 * "Processada"): o card responde "quantas contas já fecharam", e em revisão
 * ainda não fechou.
 */
type ClosingState = 'done' | 'reviewing' | 'processing' | 'error' | 'none';

const CLOSING_LABEL: Record<ClosingState, string> = {
  done: 'Concluída',
  reviewing: 'Em revisão',
  processing: 'Em processamento',
  error: 'Erro',
  none: 'Sem conciliação',
};

/** Pílula: fundo `-muted` com o texto no token SÓLIDO (o par dos badges). */
const CLOSING_PILL: Record<ClosingState, string> = {
  done: 'bg-success-muted text-success',
  reviewing: 'bg-info-muted text-info',
  processing: 'bg-info-muted text-info',
  error: 'bg-destructive-muted text-destructive',
  none: 'bg-muted text-muted-foreground',
};

/** Fatia da barra segmentada (uma por conta). */
const CLOSING_SEGMENT: Record<ClosingState, string> = {
  done: 'bg-success',
  reviewing: 'bg-info',
  processing: 'bg-info',
  error: 'bg-destructive',
  none: 'bg-muted-foreground/30',
};

function closingStateOf(session: SessionRow | undefined): ClosingState {
  switch (session?.status) {
    case 'done':
      return 'done';
    case 'reviewing':
      return 'reviewing';
    case 'processing':
      return 'processing';
    case 'error':
      return 'error';
    default:
      return 'none';
  }
}

interface MonthClosingProps {
  clientId: string;
  summary: ClientSummary;
  accounts: readonly BankAccount[];
  /** Sessões ativas do mês do resumo (a lista filtrada por `month`). */
  monthSessions: readonly SessionRow[];
  /** Código do tipo de anomalia → nome (catálogo global). */
  anomalyTypeNames: ReadonlyMap<string, string>;
  /** Tipo do destino (`destinationCode`) → nome (catálogo da organização). */
  destinationNames: ReadonlyMap<string, string>;
  /** Cliente sem conciliação nenhuma: o card de conciliações vira este estado vazio. */
  emptyState?: React.ReactNode;
}

export function MonthClosing({
  clientId,
  summary,
  accounts,
  monthSessions,
  anomalyTypeNames,
  destinationNames,
  emptyState,
}: MonthClosingProps) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <ReconciliationsCard
        summary={summary}
        accounts={accounts}
        monthSessions={monthSessions}
        emptyState={emptyState}
      />
      <AnomaliesCard
        clientId={clientId}
        summary={summary}
        monthSessions={monthSessions}
        anomalyTypeNames={anomalyTypeNames}
      />
      <CardPurchasesCard clientId={clientId} summary={summary} monthSessions={monthSessions} />
      <MappingCard clientId={clientId} summary={summary} destinationNames={destinationNames} />
    </div>
  );
}

/**
 * Ordem da lista visível do card: quem tem sessão primeiro, pelo que pede mais
 * atenção (em processamento, em revisão, erro, concluída), e por último as
 * habituais ainda sem conciliação no mês.
 */
const CLOSING_ORDER: Record<ClosingState, number> = {
  processing: 0,
  reviewing: 1,
  error: 2,
  done: 3,
  none: 4,
};

/**
 * Quantos meses definem as contas habituais. Espelho do `HABITUAL_MONTHS` do
 * resumo (`apps/api/app/modules/client_summary/service.py`): quem decide quais
 * contas são habituais é o servidor; aqui só o texto da explicação usa o número.
 */
const HABITUAL_MONTHS = 3;

interface ClosingRow {
  id: number;
  name: string;
  state: ClosingState;
}

/**
 * A meta do card é o conjunto HABITUAL (`habitualAccountIds`, decidido no
 * servidor): as contas conciliadas no mês ou nos meses anteriores da janela. O
 * cache de contas não serve de meta: ele traz caixinha, adiantamento, reembolso
 * e cartões que ninguém concilia todo mês. As demais contas do cache ficam
 * recolhidas num `<details>`, só pelo nome.
 */
function ReconciliationsCard({
  summary,
  accounts,
  monthSessions,
  emptyState,
}: Pick<MonthClosingProps, 'summary' | 'accounts' | 'monthSessions' | 'emptyState'>) {
  const habitual = new Set(summary.reconciliations.habitualAccountIds);
  const byAccount = new Map(monthSessions.map((s) => [s.omie_conta_id, s]));
  const names = new Map(accounts.map((a) => [a.omie_conta_id, a.name]));
  // Visível: as habituais e, sem assumir a definição do servidor, qualquer
  // conta com sessão no mês. Conta fora do cache aparece pelo código: a linha
  // não some.
  const visibleIds = new Set([...habitual, ...byAccount.keys()]);
  const rows: ClosingRow[] = [...visibleIds]
    .map((id) => ({
      id,
      name: names.get(id) ?? `Conta ${id}`,
      state: closingStateOf(byAccount.get(id)),
    }))
    .sort(
      (a, b) =>
        CLOSING_ORDER[a.state] - CLOSING_ORDER[b.state] || a.name.localeCompare(b.name, 'pt-BR'),
    );
  const others = accounts
    .filter((a) => !visibleIds.has(a.omie_conta_id))
    .sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'));
  const habitualTotal = habitual.size;
  const habitualDone = rows.filter((r) => habitual.has(r.id) && r.state === 'done').length;

  return (
    <DashboardCard title="Conciliações do mês" titleId="dashboard-card-reconciliations">
      {emptyState ?? (
        <>
          {rows.length === 0 ? (
            <p className="text-sm font-medium" data-testid="closing-headline">
              Nenhuma conciliação neste mês ainda
            </p>
          ) : (
            <div className="space-y-0.5">
              <p className="text-sm" data-testid="closing-headline">
                <span className="text-2xl font-semibold tabular-nums">{habitualDone}</span>{' '}
                <span className="text-muted-foreground">
                  de {habitualTotal}{' '}
                  {habitualTotal === 1 ? 'conta habitual concluída' : 'contas habituais concluídas'}
                </span>
              </p>
              <p className="text-muted-foreground text-xs">
                Habituais: contas conciliadas nos últimos {HABITUAL_MONTHS} meses
              </p>
            </div>
          )}
          {rows.length > 0 && (
            <div
              aria-hidden="true"
              className="flex h-2 w-full gap-0.5 overflow-hidden rounded-full"
            >
              {rows.map(({ id, state }) => (
                <div
                  key={id}
                  data-testid="closing-segment"
                  data-state={state}
                  className={cn('h-full flex-1', CLOSING_SEGMENT[state])}
                />
              ))}
            </div>
          )}
          {rows.length > 0 && (
            <ul className="space-y-1.5 text-sm" data-testid="closing-accounts">
              {rows.map(({ id, name, state }) => (
                <li key={id} className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate">{name}</span>
                  <span
                    className={cn(
                      'shrink-0 rounded-full px-2 py-0.5 text-xs font-medium',
                      CLOSING_PILL[state],
                    )}
                  >
                    {CLOSING_LABEL[state]}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {others.length > 0 && (
            <details className="group text-sm" data-testid="closing-other-accounts">
              <summary className="text-muted-foreground hover:text-foreground cursor-pointer rounded-sm">
                Outras {others.length}{' '}
                {others.length === 1 ? 'conta sem conciliação' : 'contas sem conciliação'} neste mês
              </summary>
              <ul className="mt-2 space-y-1">
                {others.map((account) => (
                  <li key={account.id} className="text-muted-foreground truncate">
                    {account.name}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </DashboardCard>
  );
}

function AnomaliesCard({
  clientId,
  summary,
  monthSessions,
  anomalyTypeNames,
}: Pick<MonthClosingProps, 'clientId' | 'summary' | 'monthSessions' | 'anomalyTypeNames'>) {
  const { openTotal, byType, resolvedInMonth } = summary.anomalies;
  // A conciliação do mês mais recente com anomalia (a lista vem por `created_at
  // DESC`); sem nenhuma, o link vai para a lista.
  const target = monthSessions.find((s) => s.anomaly_count > 0);
  const href =
    target !== undefined
      ? `/clientes/${clientId}/conciliacao/${target.id}?tab=anomalias`
      : reconciliationsPath(clientId);
  return (
    <DashboardCard
      title="Anomalias em aberto"
      titleId="dashboard-card-anomalies"
      footer={
        openTotal > 0 ? (
          <Link href={href} className="text-link font-medium underline-offset-4 hover:underline">
            Revisar anomalias
          </Link>
        ) : undefined
      }
    >
      <p
        className={cn(
          'text-2xl font-semibold tabular-nums',
          openTotal > 0 ? 'text-warning' : 'text-foreground',
        )}
      >
        {openTotal}
      </p>
      {byType.length > 0 ? (
        <ul className="space-y-1 text-sm">
          {byType.map((item) => (
            <li key={item.code} className="flex min-w-0 items-center justify-between gap-2">
              <span className="truncate">{anomalyTypeNames.get(item.code) ?? item.code}</span>
              <span className="shrink-0 font-medium tabular-nums">{item.count}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground text-sm">Nenhuma anomalia em aberto neste mês.</p>
      )}
      <p className="text-muted-foreground text-xs">Resolvidas no mês: {resolvedInMonth}</p>
    </DashboardCard>
  );
}

function CardPurchasesCard({
  clientId,
  summary,
  monthSessions,
}: Pick<MonthClosingProps, 'clientId' | 'summary' | 'monthSessions'>) {
  const { count, totalAmount } = summary.cardPurchasesToPost;
  const cardSession = monthSessions.find((s) => s.account_type === 'credit_card');
  return (
    <DashboardCard
      title="Compras do cartão a lançar"
      titleId="dashboard-card-card-purchases"
      footer={
        cardSession !== undefined ? (
          <Link
            href={`/clientes/${clientId}/conciliacao/${cardSession.id}`}
            className="text-link font-medium underline-offset-4 hover:underline"
          >
            Abrir a fatura
          </Link>
        ) : undefined
      }
    >
      {cardSession === undefined ? (
        <p className="text-muted-foreground text-sm">Nenhuma fatura de cartão neste mês.</p>
      ) : (
        <>
          <p className="text-sm">
            <span className="text-2xl font-semibold tabular-nums">{count}</span>{' '}
            <span className="text-muted-foreground">
              {count === 1 ? 'compra sem lançamento no Omie' : 'compras sem lançamento no Omie'}
            </span>
          </p>
          <p className="text-sm">
            Total: <Money value={totalAmount} tone="sign" className="font-medium" />
          </p>
        </>
      )}
    </DashboardCard>
  );
}

function MappingCard({
  clientId,
  summary,
  destinationNames,
}: Pick<MonthClosingProps, 'clientId' | 'summary' | 'destinationNames'>) {
  const destinations = summary.mapping;
  return (
    <DashboardCard
      title="De-para do mês"
      titleId="dashboard-card-mapping"
      footer={
        destinations.length > 0 ? (
          <Link
            href={mappingPreviewPath(clientId, summary.referenceMonth)}
            className="text-link font-medium underline-offset-4 hover:underline"
          >
            Decidir no de-para
          </Link>
        ) : undefined
      }
    >
      {destinations.length === 0 ? (
        <p className="text-muted-foreground text-sm">Nenhum destino de de-para ativo.</p>
      ) : (
        <ul className="space-y-3">
          {destinations.map((item) => {
            const coverage = item.coveragePct === null ? null : Number(item.coveragePct);
            return (
              <li key={item.destinationCode} className="space-y-1.5 text-sm">
                <div className="flex min-w-0 items-baseline justify-between gap-2">
                  <span className="truncate font-medium">
                    {destinationNames.get(item.destinationCode) ?? item.destinationCode}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    <span className="sr-only">Cobertura: </span>
                    {formatPercent(item.coveragePct)}
                  </span>
                </div>
                <ProgressBar percent={coverage ?? 0} className="bg-info" />
                <p className="text-xs">
                  <span className="text-muted-foreground">Categorias sem decisão: </span>
                  <span
                    className={cn(
                      'font-medium tabular-nums',
                      item.withoutDecision > 0 && 'text-warning',
                    )}
                  >
                    {item.withoutDecision}
                  </span>
                </p>
                <p className="text-muted-foreground text-xs">
                  Materialização: {item.materialized ? 'Concluída' : 'Pendente'}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </DashboardCard>
  );
}
