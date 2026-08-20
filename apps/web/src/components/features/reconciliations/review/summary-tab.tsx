'use client';

/**
 * Aba 4 — Resumo (FRONT 9.17, Doc §14.7).
 *
 * Renderiza:
 *   - Saldos consolidados (saldo inicial, saldo final arquivo, saldo final
 *     Omie, diferença + status Conferido/Divergente). Vem do
 *     `SessionDetail` calculado pelo worker pós-matching
 *     (`processing/balances.py`).
 *   - Indicadores (créditos/débitos; no cartão compras/estornos/encargos) e
 *     breakdown de anomalias: números do BACKEND, computados sobre a sessão
 *     INTEIRA em Decimal (86e2u513f). Esta aba NÃO soma nada — a soma no
 *     navegador cobria só as 50 primeiras linhas, em float, e o total exibido
 *     mentia na maioria dos extratos reais. A regra dos encargos (IOF/juros/
 *     multa por descrição) mora em `totals.py`, junto da conta.
 *
 * NÃO usa charts. Texto + tabela apenas (briefing §"O que NÃO fazer").
 */

import { formatBRL } from '@/lib/format';

interface SummaryCounts {
  conciliated: number;
  semOmie: number;
  omieSemArquivo: number;
  anomaly: number;
}

interface SummaryBalances {
  /** Decimal serializado como string. `null` em sessões legadas. */
  start: string | null;
  endFile: string | null;
  endOmie: string | null;
  difference: string | null;
}

interface SummaryAmounts {
  /** Decimal serializado como string — soma da sessão INTEIRA, do backend. */
  credits: string;
  debits: string;
  /** Encargos do cartão (IOF/juros/multa por descrição). `null` fora do cartão. */
  cardCharges: string | null;
}

interface SummaryAnomaliesBreakdown {
  critical: number;
  moderate: number;
  info: number;
  resolved: number;
}

interface SummaryTabProps {
  /** FRONT 1.8: cartão → indicadores de fatura (compras/estornos/encargos/saldo). */
  isCard: boolean;
  totalFileEntries: number;
  counts: SummaryCounts;
  amounts: SummaryAmounts;
  anomaliesBreakdown: SummaryAnomaliesBreakdown;
  referenceMonthLabel: string;
  /** undefined enquanto o `useSessionDetail` ainda carrega. */
  balances: SummaryBalances | undefined;
}

/**
 * Mesma regra da aba 1 do Excel (CLAUDE.md §5.1: tolerância de R$ 0,01).
 * Diferença ≤ R$ 0,01 é "Conferido"; acima é "Divergente".
 */
function resolveBalanceStatus(difference: string | null): {
  label: string;
  className: string;
} {
  if (difference === null) {
    return { label: 'Indisponível', className: 'text-muted-foreground' };
  }
  const value = Number(difference);
  if (Math.abs(value) <= 0.01) {
    return {
      label: 'Conferido',
      className: 'text-emerald-700 dark:text-emerald-300',
    };
  }
  return {
    label: 'Divergente',
    className: 'text-red-700 dark:text-red-300 font-semibold',
  };
}

export function SummaryTab({
  isCard,
  totalFileEntries,
  counts,
  amounts,
  anomaliesBreakdown,
  referenceMonthLabel,
  balances,
}: SummaryTabProps) {
  const creditsTotal = Number(amounts.credits);
  const debitsTotal = Number(amounts.debits);
  const encargosTotal = amounts.cardCharges === null ? 0 : Number(amounts.cardCharges);

  const conciliatedPct = totalFileEntries === 0 ? 0 : (counts.conciliated / totalFileEntries) * 100;

  const breakdown = anomaliesBreakdown;

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Saldos consolidados</h2>
        {balances === undefined ? (
          <div className="text-muted-foreground bg-muted/40 rounded-md border border-dashed p-4 text-sm">
            Carregando saldos…
          </div>
        ) : (
          (() => {
            const status = resolveBalanceStatus(balances.difference);
            return (
              <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Indicator
                  label="Saldo inicial"
                  value={
                    balances.start === null ? 'Indisponível' : formatBRL(Number(balances.start))
                  }
                />
                <Indicator
                  label="Saldo final (arquivo)"
                  value={
                    balances.endFile === null ? 'Indisponível' : formatBRL(Number(balances.endFile))
                  }
                />
                <Indicator
                  label="Saldo final (Omie)"
                  value={
                    balances.endOmie === null ? 'Indisponível' : formatBRL(Number(balances.endOmie))
                  }
                  hint={
                    balances.endOmie === null
                      ? 'Sessão legada — reprocessar para calcular'
                      : undefined
                  }
                />
                <div className="bg-card space-y-0.5 rounded-md border p-3">
                  <dt className="text-muted-foreground text-xs">Status</dt>
                  <dd className={`text-xl font-semibold tabular-nums ${status.className}`}>
                    {status.label}
                  </dd>
                  {balances.difference !== null && status.label === 'Divergente' && (
                    <p className="text-muted-foreground text-[10px]">
                      Diferença: {formatBRL(Number(balances.difference))}
                    </p>
                  )}
                </div>
              </dl>
            );
          })()
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Indicadores</h2>
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Indicator label="Mês de referência" value={referenceMonthLabel || '—'} />
          <Indicator label="Total movimentações" value={String(totalFileEntries)} />
          {isCard ? (
            <>
              <Indicator label="Total de compras" value={formatBRL(debitsTotal)} />
              <Indicator label="Total de estornos" value={formatBRL(creditsTotal)} />
              <Indicator
                label="Total de encargos"
                value={formatBRL(encargosTotal)}
                hint="IOF, juros e multa (por descrição)"
              />
              <Indicator
                label="Saldo da fatura"
                value={
                  balances?.endFile == null ? 'Indisponível' : formatBRL(Number(balances.endFile))
                }
              />
            </>
          ) : (
            <>
              <Indicator label="Total créditos" value={formatBRL(creditsTotal)} />
              <Indicator label="Total débitos" value={formatBRL(debitsTotal)} />
            </>
          )}
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
