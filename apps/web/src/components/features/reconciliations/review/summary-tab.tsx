'use client';

/**
 * Aba 4 — Resumo (FRONT 9.17, Doc §14.7).
 *
 * Decisão pragmática (briefing §17): a S11 não tem endpoint dedicado de
 * saldos consolidados (entrega S14). Aqui mostramos:
 *   - Placeholder explícito para a tabela de saldos (substituído por
 *     dados reais quando S14 chegar).
 *   - Indicadores agregados via lista paginada (pedimos pageSize=50, 1
 *     página) — para o MVP de demo, isso cobre clientes com até 50
 *     movimentações. Para volumes maiores, deixamos um aviso explícito
 *     e o `% conciliado` cai pra estimativa a partir dos contadores.
 *   - Breakdown de anomalias a partir de uma listagem ampla
 *     (`pageSize=50`, sem filtro). TODO em S14: endpoint agregado.
 *
 * NÃO usa charts. Texto + tabela apenas (briefing §"O que NÃO fazer").
 */

import { useMemo } from 'react';

import { useAnomalies, useFileEntries } from '@/hooks/use-reconciliations';
import { formatBRL } from '@/lib/format';

interface SummaryCounts {
  conciliated: number;
  semOmie: number;
  omieSemArquivo: number;
  anomaly: number;
}

interface SummaryTabProps {
  sessionId: string;
  totalFileEntries: number;
  counts: SummaryCounts;
  referenceMonthLabel: string;
}

const AGGREGATION_LIMIT = 50;

export function SummaryTab({
  sessionId,
  totalFileEntries,
  counts,
  referenceMonthLabel,
}: SummaryTabProps) {
  // Pega uma página de 50 com TODAS as situações pra calcular créditos/débitos
  // somados localmente. Acima de 50, a soma fica subestimada → mostramos aviso.
  const creditsQuery = useFileEntries(sessionId, {
    page: 1,
    pageSize: AGGREGATION_LIMIT,
    type: 'credit',
  });
  const debitsQuery = useFileEntries(sessionId, {
    page: 1,
    pageSize: AGGREGATION_LIMIT,
    type: 'debit',
  });
  const anomaliesQuery = useAnomalies(sessionId, {
    page: 1,
    pageSize: AGGREGATION_LIMIT,
    resolved: 'all',
  });

  const creditsTotal = useMemo(
    () => sumAmounts(creditsQuery.data?.data ?? []),
    [creditsQuery.data],
  );
  const debitsTotal = useMemo(
    () => Math.abs(sumAmounts(debitsQuery.data?.data ?? [])),
    [debitsQuery.data],
  );

  const creditsTruncated = (creditsQuery.data?.pagination.total ?? 0) > AGGREGATION_LIMIT;
  const debitsTruncated = (debitsQuery.data?.pagination.total ?? 0) > AGGREGATION_LIMIT;
  const anomaliesTruncated = (anomaliesQuery.data?.pagination.total ?? 0) > AGGREGATION_LIMIT;

  const conciliatedPct = totalFileEntries === 0 ? 0 : (counts.conciliated / totalFileEntries) * 100;

  const breakdown = useMemo(() => {
    const list = anomaliesQuery.data?.data ?? [];
    return {
      critical: list.filter((a) => a.anomaly_type.severity === 'critical').length,
      moderate: list.filter((a) => a.anomaly_type.severity === 'moderate').length,
      info: list.filter((a) => a.anomaly_type.severity === 'info').length,
      resolved: list.filter((a) => a.resolved).length,
    };
  }, [anomaliesQuery.data]);

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Saldos consolidados</h2>
        <div className="bg-muted/40 rounded-md border border-dashed p-4 text-sm">
          <p className="text-muted-foreground">
            Saldos consolidados (saldo anterior, saldo arquivo, saldo Omie, diferença) serão
            exibidos após a entrega da S14. Esta tela já reflete os contadores e indicadores
            agregados disponíveis hoje.
          </p>
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Indicadores</h2>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Indicator label="Mês de referência" value={referenceMonthLabel || '—'} />
          <Indicator label="Total movimentações" value={String(totalFileEntries)} />
          <Indicator
            label="Total créditos"
            value={formatBRL(creditsTotal)}
            hint={creditsTruncated ? `Soma das primeiras ${AGGREGATION_LIMIT} entradas` : undefined}
          />
          <Indicator
            label="Total débitos"
            value={formatBRL(debitsTotal)}
            hint={debitsTruncated ? `Soma das primeiras ${AGGREGATION_LIMIT} entradas` : undefined}
          />
          <Indicator label="Conciliados" value={String(counts.conciliated)} />
          <Indicator label="Sem Omie" value={String(counts.semOmie)} />
          <Indicator label="Omie sem arquivo" value={String(counts.omieSemArquivo)} />
          <Indicator label="% conciliado" value={`${conciliatedPct.toFixed(1)}%`} />
        </dl>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Anomalias</h2>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Indicator label="Críticas" value={String(breakdown.critical)} />
          <Indicator label="Moderadas" value={String(breakdown.moderate)} />
          <Indicator label="Informativas" value={String(breakdown.info)} />
          <Indicator label="Resolvidas" value={String(breakdown.resolved)} />
        </dl>
        {anomaliesTruncated && (
          <p className="text-muted-foreground text-xs">
            Há mais de {AGGREGATION_LIMIT} anomalias nesta sessão; o breakdown acima considera
            apenas as {AGGREGATION_LIMIT} primeiras. Endpoint agregado dedicado entra na S14.
          </p>
        )}
      </section>
    </div>
  );
}

function Indicator({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-card space-y-0.5 rounded-md border p-3">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-xl font-semibold tabular-nums">{value}</dd>
      {hint !== undefined && <p className="text-muted-foreground text-[10px]">{hint}</p>}
    </div>
  );
}

function sumAmounts(entries: { amount: string }[]): number {
  return entries.reduce((acc, e) => acc + Number(e.amount), 0);
}
