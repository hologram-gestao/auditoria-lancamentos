/**
 * Regra dos contadores do menu do cliente (86e3k1q3x): quais itens contam, em
 * que tom e com que nome acessível. Zero não é pendência. Desde o ajuste de
 * 08/10/2026, o De-para conta o MAIOR "sem decisão" entre os destinos e cada
 * contador traz o detalhe do tooltip.
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
      titles: {
        ...BASE.titles!,
        overdueCount: 1,
        aReceber: { overdueCount: 1, overdueTotal: '10.00' },
      },
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
      countLabel: '1 em andamento: 0 em processamento · 1 em revisão',
      countDetail: '1 em andamento: 0 em processamento · 1 em revisão',
    });
    expect(counts.titles).toEqual({
      count: 1,
      countTone: 'destructive',
      countLabel: '1 título vencido: 1 a receber · 0 a pagar',
      countDetail: '1 título vencido: 1 a receber · 0 a pagar',
    });
    expect(counts.mapping).toEqual({
      count: 1,
      countTone: 'warning',
      countLabel: '1 categoria sem decisão · conta_contabil: 1',
      countDetail: '1 categoria sem decisão · conta_contabil: 1',
    });
  });

  it('`titles` nulo (papel sem a carteira) não conta vencidos', () => {
    expect(clientNavCounts({ ...BASE, titles: null }).titles).toBeUndefined();
  });

  it('De-para conta o MAIOR "sem decisão" entre os destinos, não a soma', () => {
    // O caso real de 08/10/2026: a mesma base sem decisão em cinco destinos.
    const counts = clientNavCounts({
      ...BASE,
      mapping: ['a', 'b', 'c', 'd', 'e'].map((code) => ({
        destinationCode: code,
        withoutDecision: 265,
        coveragePct: '0.0',
        materialized: false,
      })),
    });
    expect(counts.mapping?.count).toBe(265);
  });

  it('o detalhe do De-para lista os destinos pendentes do maior para o menor, pelo nome', () => {
    const names = new Map([
      ['a', 'Demonstrativo contábil'],
      ['c', 'Fluxo de caixa'],
    ]);
    const counts = clientNavCounts(
      {
        ...BASE,
        mapping: [
          { destinationCode: 'a', withoutDecision: 2, coveragePct: '10.0', materialized: false },
          { destinationCode: 'b', withoutDecision: 0, coveragePct: '100.0', materialized: true },
          { destinationCode: 'c', withoutDecision: 5, coveragePct: null, materialized: false },
          { destinationCode: 'd', withoutDecision: 2, coveragePct: null, materialized: false },
        ],
      },
      names,
    );
    expect(counts.mapping?.count).toBe(5);
    // Sem nome resolvido (`d`), o código; destino sem pendência (`b`) não entra; no
    // empate (`a` e `d`), a ordem do servidor.
    expect(counts.mapping?.countDetail).toBe(
      '5 categorias sem decisão · Fluxo de caixa: 5, Demonstrativo contábil: 2, d: 2',
    );
    expect(counts.mapping?.countLabel).toBe(counts.mapping?.countDetail);
  });

  it('os detalhes de Conciliações e Carteira separam o status e o lado, no plural', () => {
    const counts = clientNavCounts({
      ...BASE,
      reconciliations: {
        ...BASE.reconciliations,
        byStatus: { processing: 2, reviewing: 3, done: 1, error: 1 },
      },
      titles: {
        ...BASE.titles!,
        overdueCount: 12,
        aPagar: { overdueCount: 3, overdueTotal: '4380.00' },
        aReceber: { overdueCount: 9, overdueTotal: '78485.50' },
      },
    });
    expect(counts.reconciliations?.count).toBe(5);
    expect(counts.reconciliations?.countDetail).toBe(
      '5 em andamento: 2 em processamento · 3 em revisão',
    );
    expect(counts.titles?.countDetail).toBe('12 títulos vencidos: 9 a receber · 3 a pagar');
  });
});
