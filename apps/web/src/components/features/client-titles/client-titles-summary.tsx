/**
 * Bloco de agregados e aging da carteira (Sprint 11 / R3 · R4).
 *
 * "R$ 107.413,10 em aberto · R$ 82.865,50 em 90+" — o número que a reunião de
 * acompanhamento usa, e que hoje só existe numa planilha montada à mão.
 *
 * ⚠️ **Todos os números vêm do SERVIDOR**, da rota `/summary`, calculados sobre
 * a carteira INTEIRA do cliente. Somar a página no navegador daria um valor que
 * muda conforme a paginação e o filtro — e aging errado é pior que aging
 * nenhum, que foi exatamente o problema do relatório manual que motivou a
 * sprint. Este componente é só exibição: ele não faz conta nenhuma, nem a de
 * "vencido = soma dos quatro baldes" (o servidor já garante a identidade).
 *
 * Um cartão por TIPO porque a pergunta é diferente nos dois: a receber é
 * inadimplência de cliente, a pagar é obrigação da empresa. Misturá-los num
 * total único produziria o número que não serve para decisão nenhuma.
 *
 * `<dl>` e não tabela: são pares rótulo/valor, e o leitor de tela anuncia o par
 * junto. A `<div>` entre o `<dl>` e o par é permitida; um `<p>` solto ao lado
 * do `<dd>` **não** é (axe `definition-list`, SERIOUS).
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

function TotalsCard({
  titleType,
  totals,
  headingId,
}: {
  titleType: TitleType;
  totals: AgingTotals;
  headingId: string;
}) {
  return (
    <section aria-labelledby={headingId} className="bg-card space-y-3 rounded-lg border p-4">
      <h3 id={headingId} className="text-sm font-semibold">
        {TITLE_TYPE_LABELS[titleType]}
      </h3>

      <dl className="grid grid-cols-3 gap-3">
        <div>
          <dt className="text-muted-foreground text-xs font-medium">Em aberto</dt>
          <dd>
            {/* `whitespace-nowrap` não é enfeite: valor monetário que quebra
                depois do hífen é lido como outra coisa (defeito da S7). */}
            <span className="block whitespace-nowrap text-xl font-semibold tabular-nums">
              {formatBRL(totals.totalEmAberto)}
            </span>
            <span className="text-muted-foreground text-xs">
              {totals.qtdEmAberto} {totals.qtdEmAberto === 1 ? 'título' : 'títulos'}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs font-medium">A vencer</dt>
          <dd>
            <span className="block whitespace-nowrap text-xl font-semibold tabular-nums">
              {formatBRL(totals.totalAVencer)}
            </span>
            <span className="text-muted-foreground text-xs">
              {totals.qtdAVencer} {totals.qtdAVencer === 1 ? 'título' : 'títulos'}
            </span>
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs font-medium">Vencido</dt>
          <dd>
            <span className="text-destructive block whitespace-nowrap text-xl font-semibold tabular-nums">
              {formatBRL(totals.totalVencido)}
            </span>
            <span className="text-muted-foreground text-xs">
              {totals.qtdVencido} {totals.qtdVencido === 1 ? 'título' : 'títulos'}
            </span>
          </dd>
        </div>
      </dl>

      <dl className="grid grid-cols-2 gap-2 border-t pt-3 sm:grid-cols-4">
        {OVERDUE_BUCKETS.map((bucket) => (
          <div key={bucket}>
            <dt className="text-muted-foreground text-xs font-medium">{BUCKET_LABELS[bucket]}</dt>
            <dd
              className={cn(
                'whitespace-nowrap text-sm font-medium tabular-nums',
                BUCKET_EMPHASIS[bucket],
              )}
            >
              {formatBRL(bucketValue(totals, bucket))}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function ClientTitlesSummaryBlock({
  summary,
}: {
  summary: {
    aReceber: AgingTotals;
    aPagar: AgingTotals;
    referenceDate: string;
  };
}) {
  return (
    <div className="space-y-2">
      <div className="grid gap-3 lg:grid-cols-2">
        {/* A receber primeiro: é a dor que originou a sprint (inadimplência). */}
        <TotalsCard
          titleType="a_receber"
          totals={summary.aReceber}
          headingId="carteira-totais-a-receber"
        />
        <TotalsCard
          titleType="a_pagar"
          totals={summary.aPagar}
          headingId="carteira-totais-a-pagar"
        />
      </div>
      {/* A data de referência é do SERVIDOR. Recalcular o aging com o relógio do
          navegador poria o mesmo título em baldes diferentes para pessoas em
          fusos diferentes — dizer de quando é o corte evita a dúvida. */}
      <p className="text-muted-foreground text-xs">
        Aging calculado sobre a carteira inteira, com referência em{' '}
        {formatBRDate(summary.referenceDate)}.
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
        <div key={card} className="space-y-3 rounded-lg border p-4">
          <div className="bg-muted h-4 w-24 animate-pulse rounded" />
          <div className="grid grid-cols-3 gap-3">
            {Array.from({ length: 3 }).map((__, stat) => (
              <div key={stat} className="space-y-2">
                <div className="bg-muted h-3 w-16 animate-pulse rounded" />
                <div className="bg-muted h-6 w-24 animate-pulse rounded" />
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2 border-t pt-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((__, bucket) => (
              <div key={bucket} className="space-y-2">
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
