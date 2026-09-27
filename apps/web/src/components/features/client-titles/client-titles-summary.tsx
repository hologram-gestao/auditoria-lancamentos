/**
 * Bloco de totais e atraso por faixa da carteira (Sprint 11 / R3 · R4; totais
 * clicáveis desde a 86e3eq9uy).
 *
 * "R$ 107.413,10 em aberto · R$ 82.865,50 em 90+" — o número que a reunião de
 * acompanhamento usa, e que hoje só existe numa planilha montada à mão.
 *
 * ⚠️ **Todos os números vêm do SERVIDOR**, da rota `/summary`, calculados sobre
 * a carteira INTEIRA do cliente. Somar a página no navegador daria um valor que
 * muda conforme a paginação e o filtro — e atraso errado é pior que atraso
 * nenhum, que foi exatamente o problema do relatório manual que motivou a
 * sprint. Este componente é só exibição: ele não faz conta nenhuma, nem a de
 * "vencido = soma dos quatro baldes" (o servidor já garante a identidade).
 *
 * Um cartão por TIPO porque a pergunta é diferente nos dois: a receber é
 * inadimplência de cliente, a pagar é obrigação da empresa. Misturá-los num
 * total único produziria o número que não serve para decisão nenhuma.
 *
 * **Cada valor é um botão que FILTRA a lista** (86e3eq9uy): o recorte vai para
 * a URL com os mesmos parâmetros de sempre (`type`, `situation`, `bucket`),
 * aplicados no servidor. A tabela de `summaryFilterParams` é a fonte única do
 * mapeamento, e é ela que decide também quando um botão está ATIVO (os três
 * parâmetros batem exatamente). "Em aberto" leva `situation=em_aberto` de
 * propósito: sem ele a lista traria títulos liquidados ou ausentes na origem
 * que o card não conta, e o total da paginação deixaria de bater com o card.
 *
 * `<dl>` e não tabela: são pares rótulo/valor, e o leitor de tela anuncia o par
 * junto. O botão vive DENTRO do `<dd>`; a `<div>` entre o `<dl>` e o par é
 * permitida; um `<p>` solto ao lado do `<dd>` **não** é (axe `definition-list`,
 * SERIOUS).
 */
import type { AgingBucket, AgingTotals, TitleType } from '@/lib/contracts';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

import { BUCKET_LABELS, TITLE_TYPE_LABELS } from './client-titles-badges';

/** Os QUATRO baldes de atraso, na ordem da segmentação do escritório. */
const OVERDUE_BUCKETS = ['1_30', '31_60', '61_90', '90_mais'] as const satisfies readonly Exclude<
  AgingBucket,
  'a_vencer'
>[];

/** Só o 90+ é destrutivo: é o dinheiro que a reunião procura primeiro. */
const BUCKET_EMPHASIS: Record<(typeof OVERDUE_BUCKETS)[number], string> = {
  '1_30': '',
  '31_60': '',
  '61_90': 'text-warning',
  '90_mais': 'text-destructive',
};

/** As sete chaves clicáveis de um card: três totais e quatro baldes. */
export type SummaryFilterKey = 'em_aberto' | 'a_vencer' | 'vencido' | AgingBucket;

/** O recorte da lista que o clique aplica — e que decide se o botão está ativo. */
export interface SummaryFilterParams {
  type: TitleType;
  situation: 'em_aberto' | 'vencido' | null;
  bucket: AgingBucket | null;
}

/** O recorte ATUAL da tela, como está na URL (ausente = `null`). */
export interface SummaryFilterState {
  type: TitleType | null;
  situation: 'em_aberto' | 'vencido' | null;
  bucket: AgingBucket | null;
}

/**
 * A tabela do mapeamento (fonte única): botão → parâmetros da URL. `T` é o
 * tipo do card clicado.
 *
 *   Em aberto → type=T, situation=em_aberto, sem bucket
 *   A vencer  → type=T, bucket=a_vencer, sem situation
 *   Vencido   → type=T, situation=vencido, sem bucket
 *   1 a 30 / 31 a 60 / 61 a 90 / 90+ dias → type=T, bucket=<balde>, sem situation
 */
export function summaryFilterParams(
  titleType: TitleType,
  key: SummaryFilterKey,
): SummaryFilterParams {
  switch (key) {
    case 'em_aberto':
      return { type: titleType, situation: 'em_aberto', bucket: null };
    case 'vencido':
      return { type: titleType, situation: 'vencido', bucket: null };
    default:
      return { type: titleType, situation: null, bucket: key };
  }
}

/** Ativo quando os TRÊS parâmetros da URL batem exatamente com a linha da tabela. */
export function isSummaryFilterActive(
  current: SummaryFilterState,
  titleType: TitleType,
  key: SummaryFilterKey,
): boolean {
  const expected = summaryFilterParams(titleType, key);
  return (
    current.type === expected.type &&
    current.situation === expected.situation &&
    current.bucket === expected.bucket
  );
}

function bucketValue(totals: AgingTotals, bucket: (typeof OVERDUE_BUCKETS)[number]): string {
  switch (bucket) {
    case '1_30':
      return totals.bucket1a30;
    case '31_60':
      return totals.bucket31a60;
    case '61_90':
      return totals.bucket61a90;
    case '90_mais':
      return totals.bucket90Mais;
  }
}

const TOTAL_LABELS: Record<'em_aberto' | 'a_vencer' | 'vencido', string> = {
  em_aberto: 'Em aberto',
  a_vencer: 'A vencer',
  vencido: 'Vencido',
};

function countLabel(count: number): string {
  return `${count} ${count === 1 ? 'título' : 'títulos'}`;
}

/**
 * O botão de um valor. Ativo = `aria-pressed="true"` + anel `ring` + fundo
 * `accent`, e TODO o texto passa a `accent-foreground` (par travado no
 * `theme-contrast.test.ts`): manter o vermelho do "Vencido" sobre o fundo
 * `accent` seria um par que nenhum teste mede. Hover não muda cor de fundo
 * (só o anel), para não criar par novo com o valor colorido em cima.
 */
function FilterValueButton({
  active,
  label,
  onClick,
  children,
  className,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={label}
      onClick={onClick}
      className={cn(
        'focus-visible:ring-ring hover:ring-ring block w-full cursor-pointer rounded-md px-2 py-1.5 text-left transition-colors hover:ring-1 focus-visible:outline-none focus-visible:ring-2',
        active && 'bg-accent text-accent-foreground ring-ring ring-2',
        className,
      )}
    >
      {children}
    </button>
  );
}

function TotalsCard({
  titleType,
  totals,
  headingId,
  active,
  onSelect,
}: {
  titleType: TitleType;
  totals: AgingTotals;
  headingId: string;
  active: SummaryFilterState;
  onSelect: (titleType: TitleType, key: SummaryFilterKey) => void;
}) {
  const typeLabel = TITLE_TYPE_LABELS[titleType];
  const totalsRows = [
    { key: 'em_aberto', amount: totals.totalEmAberto, count: totals.qtdEmAberto, tone: '' },
    { key: 'a_vencer', amount: totals.totalAVencer, count: totals.qtdAVencer, tone: '' },
    {
      key: 'vencido',
      amount: totals.totalVencido,
      count: totals.qtdVencido,
      tone: 'text-destructive',
    },
  ] as const;

  return (
    <section aria-labelledby={headingId} className="bg-card space-y-2 rounded-lg border p-3">
      <h3 id={headingId} className="text-sm font-semibold">
        {typeLabel}
      </h3>

      {/* Uma coluna até `sm`, três a partir dali. Em 390px as três parcelas em
          colunas de ~95px imprimiam os três valores UM POR CIMA DO OUTRO
          (`R$ 107.413R$10.000,0R$ 97.413,10`): `text-lg` + `whitespace-nowrap`
          não cabem em 95px, e o `nowrap` NÃO sai — valor monetário que quebra
          depois do hífen vira outro número (defeito da S7). Quem cede é a
          contagem de colunas, não o valor. */}
      <dl className="grid grid-cols-1 gap-1 sm:grid-cols-3">
        {totalsRows.map((row) => {
          const isActive = isSummaryFilterActive(active, titleType, row.key);
          return (
            <div key={row.key}>
              <dt className="text-muted-foreground px-2 text-xs font-medium">
                {TOTAL_LABELS[row.key]}
              </dt>
              <dd>
                <FilterValueButton
                  active={isActive}
                  label={`${typeLabel}, ${TOTAL_LABELS[row.key]}: ${formatBRL(row.amount)}, ${countLabel(row.count)}. Filtrar a lista`}
                  onClick={() => onSelect(titleType, row.key)}
                >
                  {/* `whitespace-nowrap` não é enfeite: valor monetário que quebra
                      depois do hífen é lido como outra coisa (defeito da S7). */}
                  <span
                    data-summary="total"
                    className={cn(
                      'block whitespace-nowrap text-lg font-semibold tabular-nums',
                      !isActive && row.tone,
                    )}
                  >
                    {formatBRL(row.amount)}
                  </span>
                  <span className={cn('text-xs', !isActive && 'text-muted-foreground')}>
                    {countLabel(row.count)}
                  </span>
                </FilterValueButton>
              </dd>
            </div>
          );
        })}
      </dl>

      <dl className="grid grid-cols-2 gap-1 border-t pt-2 sm:grid-cols-4">
        {OVERDUE_BUCKETS.map((bucket) => {
          const isActive = isSummaryFilterActive(active, titleType, bucket);
          return (
            <div key={bucket}>
              <dt className="text-muted-foreground px-2 text-xs font-medium">
                {BUCKET_LABELS[bucket]}
              </dt>
              <dd>
                <FilterValueButton
                  active={isActive}
                  label={`${typeLabel}, ${BUCKET_LABELS[bucket]}: ${formatBRL(bucketValue(totals, bucket))}. Filtrar a lista`}
                  onClick={() => onSelect(titleType, bucket)}
                >
                  <span
                    className={cn(
                      'block whitespace-nowrap text-sm font-medium tabular-nums',
                      !isActive && BUCKET_EMPHASIS[bucket],
                    )}
                  >
                    {formatBRL(bucketValue(totals, bucket))}
                  </span>
                </FilterValueButton>
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

export function ClientTitlesSummaryBlock({
  summary,
  active,
  onSelect,
}: {
  summary: {
    aReceber: AgingTotals;
    aPagar: AgingTotals;
    referenceDate: string;
  };
  /** O recorte atual da URL: decide qual botão está `aria-pressed`. */
  active: SummaryFilterState;
  /** Clique num valor: quem chama aplica (ou desfaz) o recorte na URL. */
  onSelect: (titleType: TitleType, key: SummaryFilterKey) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="grid gap-3 lg:grid-cols-2">
        {/* A receber primeiro: é a dor que originou a sprint (inadimplência). */}
        <TotalsCard
          titleType="a_receber"
          totals={summary.aReceber}
          headingId="carteira-totais-a-receber"
          active={active}
          onSelect={onSelect}
        />
        <TotalsCard
          titleType="a_pagar"
          totals={summary.aPagar}
          headingId="carteira-totais-a-pagar"
          active={active}
          onSelect={onSelect}
        />
      </div>
      {/* A data de referência é do SERVIDOR. Recalcular o atraso com o relógio do
          navegador poria o mesmo título em baldes diferentes para pessoas em
          fusos diferentes — dizer de quando é o corte evita a dúvida. */}
      <p className="text-muted-foreground text-xs">
        Atraso calculado sobre a carteira inteira, com referência em{' '}
        {formatBRDate(summary.referenceDate)}. Clique num valor para filtrar a lista.
      </p>
    </div>
  );
}

export function ClientTitlesSummarySkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando os agregados da carteira"
      className="grid gap-3 lg:grid-cols-2"
    >
      {Array.from({ length: 2 }).map((_, card) => (
        <div key={card} className="space-y-2 rounded-lg border p-3">
          <div className="bg-muted h-4 w-24 animate-pulse rounded" />
          {/* Mesmas colunas do bloco real: esqueleto com outra grade promete
              uma altura que o conteúdo não cumpre, e a tela pula ao carregar. */}
          <div className="grid grid-cols-1 gap-1 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((__, stat) => (
              <div key={stat} className="space-y-1.5 px-2 py-1.5">
                <div className="bg-muted h-3 w-16 animate-pulse rounded" />
                <div className="bg-muted h-5 w-24 animate-pulse rounded" />
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-1 border-t pt-2 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((__, bucket) => (
              <div key={bucket} className="space-y-1.5 px-2 py-1.5">
                <div className="bg-muted h-3 w-14 animate-pulse rounded" />
                <div className="bg-muted h-4 w-16 animate-pulse rounded" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
