'use client';

/**
 * Relatório de recebíveis (Sprint 15 — BACK 15.2 / FRONT 15.2).
 *
 * Substitui o alarme falso do relatório manual: separa, para os dois lados
 * (a pagar/a receber), inadimplência real de vencido-com-contexto — os dois
 * grupos vêm PRONTOS do servidor, calculados sobre a carteira INTEIRA
 * (`GET .../titles/receivables-report`). Mesma permissão de leitura da
 * carteira (`view_client_receivables`): não expõe texto decifrado nem
 * identificador de título, só agregados.
 *
 * Cliente sem nenhum contexto registrado: tudo cai em `inadimplencia`, sem
 * erro — é o BASELINE (100% inadimplência), não um estado de falha.
 */
import { useReceivablesReport } from '@/hooks/use-client-titles';
import { ApiError } from '@/lib/api/client';
import type { ReceivablesGroup, ReceivablesSide, TitleType } from '@/lib/contracts';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

import { TITLE_TYPE_LABELS } from './client-titles-badges';

const OVERDUE_BUCKETS = ['bucket1a30', 'bucket31a60', 'bucket61a90', 'bucket90Mais'] as const;

const BUCKET_LABELS_BY_KEY: Record<(typeof OVERDUE_BUCKETS)[number], string> = {
  bucket1a30: '1 a 30 dias',
  bucket31a60: '31 a 60 dias',
  bucket61a90: '61 a 90 dias',
  bucket90Mais: '90+ dias',
};

function GroupCard({
  title,
  group,
  emphasis,
  headingId,
}: {
  title: string;
  group: ReceivablesGroup;
  emphasis: 'destructive' | 'neutral';
  headingId: string;
}) {
  return (
    <section aria-labelledby={headingId} className="bg-card space-y-3 rounded-lg border p-4">
      <h4 id={headingId} className="text-sm font-semibold">
        {title}
      </h4>
      <dl>
        <div>
          <dt className="text-muted-foreground text-xs font-medium">Total</dt>
          <dd>
            <span
              className={cn(
                'block whitespace-nowrap text-xl font-semibold tabular-nums',
                emphasis === 'destructive' && 'text-destructive',
              )}
            >
              {formatBRL(group.total)}
            </span>
            <span className="text-muted-foreground text-xs">
              {group.qtd} {group.qtd === 1 ? 'título' : 'títulos'}
            </span>
          </dd>
        </div>
      </dl>
      <dl className="grid grid-cols-2 gap-2 border-t pt-3 sm:grid-cols-4">
        {OVERDUE_BUCKETS.map((bucket) => (
          <div key={bucket}>
            <dt className="text-muted-foreground text-xs font-medium">
              {BUCKET_LABELS_BY_KEY[bucket]}
            </dt>
            <dd className="whitespace-nowrap text-sm font-medium tabular-nums">
              {formatBRL(group[bucket])}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function SideBlock({ titleType, side }: { titleType: TitleType; side: ReceivablesSide }) {
  return (
    <section
      aria-labelledby={`receivables-${titleType}`}
      className="space-y-3 rounded-lg border p-4"
    >
      <h3 id={`receivables-${titleType}`} className="text-base font-semibold">
        {TITLE_TYPE_LABELS[titleType]}
      </h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <GroupCard
          title="Inadimplência real"
          group={side.inadimplencia}
          emphasis="destructive"
          headingId={`receivables-${titleType}-inadimplencia`}
        />
        <GroupCard
          title="Vencido com contexto"
          group={side.vencidoComContexto}
          emphasis="neutral"
          headingId={`receivables-${titleType}-vencido-com-contexto`}
        />
      </div>
    </section>
  );
}

export function ReceivablesReportScreen({ clientId }: { clientId: string }) {
  const reportQuery = useReceivablesReport(clientId);

  if (reportQuery.isLoading) {
    return <ReceivablesReportSkeleton />;
  }

  if (reportQuery.isError) {
    return (
      <div
        role="alert"
        className="border-destructive/30 bg-destructive/5 text-destructive space-y-3 rounded-lg border p-6"
      >
        <p className="text-sm font-medium">Não foi possível carregar o relatório de recebíveis</p>
        <p className="text-sm">
          {reportQuery.error instanceof ApiError
            ? reportQuery.error.userMessage
            : 'Tente novamente em instantes.'}
        </p>
      </div>
    );
  }

  const report = reportQuery.data;
  if (!report) return null;

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        Separa, para os títulos vencidos, o que é inadimplência real do que tem um contexto
        registrado (acordo, antecipação, nota a cancelar, cobrança suspensa) — calculado sobre a
        carteira inteira, com referência em {formatBRDate(report.referenceDate)}.
      </p>
      <SideBlock titleType="a_receber" side={report.aReceber} />
      <SideBlock titleType="a_pagar" side={report.aPagar} />
    </div>
  );
}

function ReceivablesReportSkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Carregando o relatório de recebíveis"
      className="space-y-4"
    >
      {Array.from({ length: 2 }).map((_, side) => (
        <div key={side} className="space-y-3 rounded-lg border p-4">
          <div className="bg-muted h-4 w-32 animate-pulse rounded" />
          <div className="grid gap-3 sm:grid-cols-2">
            {Array.from({ length: 2 }).map((__, card) => (
              <div key={card} className="space-y-3 rounded-lg border p-4">
                <div className="bg-muted h-4 w-24 animate-pulse rounded" />
                <div className="bg-muted h-6 w-24 animate-pulse rounded" />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
