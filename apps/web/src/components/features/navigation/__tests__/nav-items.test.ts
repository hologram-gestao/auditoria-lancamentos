/**
 * Regra dos contadores do menu do cliente (86e3k1q3x): quais itens contam, em
 * que tom e com que nome acessível. Zero não é pendência.
 */
import { describe, expect, it } from 'vitest';

import {
  clientNavCounts,
  dashboardPath,
  reconciliationsPath,
} from '@/components/features/navigation/nav-items';
import type { ClientSummary } from '@/lib/contracts';

const BASE: ClientSummary = {
  referenceMonth: '2026-10',
  reconciliations: {
    accountsTotal: 2,
    accountsWithSession: 0,
    byStatus: { processing: 0, reviewing: 0, done: 0, error: 0 },
    habitualAccountIds: [],
  },
  anomalies: { openTotal: 0, byType: [], resolvedInMonth: 0 },
  cardPurchasesToPost: { count: 0, totalAmount: '0.00' },
  mapping: [],
  titles: {
    overdueCount: 0,
    overdueTotal: '0.00',
    aPagar: { overdueCount: 0, overdueTotal: '0.00' },
    aReceber: { overdueCount: 0, overdueTotal: '0.00' },
    syncedAt: null,
    neverSynced: true,
  },
  latestSession: null,
};

describe('rotas do cliente (08/10/2026)', () => {
  it('a lista de conciliações é a raiz do cliente e o painel mora em /painel', () => {
    expect(reconciliationsPath('c1')).toBe('/clientes/c1');
    expect(dashboardPath('c1')).toBe('/clientes/c1/painel');
  });
});

describe('clientNavCounts', () => {
  it('sem pendência nenhuma, nenhum contador', () => {
    expect(clientNavCounts(BASE)).toEqual({});
  });

  it('erro e concluída não contam como em andamento; anomalia não vira contador', () => {
    const counts = clientNavCounts({
      ...BASE,
      reconciliations: {
        ...BASE.reconciliations,
        byStatus: { processing: 0, reviewing: 0, done: 3, error: 2 },
      },
      anomalies: { openTotal: 9, byType: [{ code: 'x', count: 9 }], resolvedInMonth: 0 },
    });
    expect(counts).toEqual({});
  });

  it('cada pendência no tom da tela de destino, com singular quando 1', () => {
    const counts = clientNavCounts({
      ...BASE,
      reconciliations: {
        ...BASE.reconciliations,
        byStatus: { processing: 0, reviewing: 1, done: 0, error: 0 },
      },
      titles: { ...BASE.titles!, overdueCount: 1 },
      mapping: [
        {
          destinationCode: 'conta_contabil',
          withoutDecision: 1,
          coveragePct: null,
          materialized: false,
        },
      ],
    });
    expect(counts.reconciliations).toEqual({
      count: 1,
      countTone: 'info',
      countLabel: '1 em andamento',
    });
    expect(counts.titles).toEqual({
      count: 1,
      countTone: 'destructive',
      countLabel: '1 título vencido',
    });
    expect(counts.mapping).toEqual({
      count: 1,
      countTone: 'warning',
      countLabel: '1 categoria sem decisão',
    });
  });

  it('`titles` nulo (papel sem a carteira) não conta vencidos', () => {
    expect(clientNavCounts({ ...BASE, titles: null }).titles).toBeUndefined();
  });

  it('soma o "sem decisão" de todos os destinos', () => {
    const counts = clientNavCounts({
      ...BASE,
      mapping: [
        { destinationCode: 'a', withoutDecision: 2, coveragePct: '10.0', materialized: false },
        { destinationCode: 'b', withoutDecision: 0, coveragePct: '100.0', materialized: true },
        { destinationCode: 'c', withoutDecision: 5, coveragePct: null, materialized: false },
      ],
    });
    expect(counts.mapping?.count).toBe(7);
    expect(counts.mapping?.countLabel).toBe('7 categorias sem decisão');
  });
});
