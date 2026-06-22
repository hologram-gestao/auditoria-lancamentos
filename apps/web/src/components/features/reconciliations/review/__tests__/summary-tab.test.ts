/**
 * Testes do heurístico de encargos da fatura de cartão (FRONT 1.8).
 *
 * Encargos = IOF, juros, multa identificados pela descrição (case-insensitive,
 * substring). Aba 4 — Resumo soma esses valores entre as compras.
 */
import { describe, expect, it } from 'vitest';

import { isChargeDescription } from '@/components/features/reconciliations/review/summary-tab';

describe('isChargeDescription', () => {
  it.each([
    'IOF sobre compra internacional',
    'Juros rotativo',
    'JUROS DE MORA',
    'Multa por atraso',
    'iof',
  ])('reconhece encargo: %s', (descr) => {
    expect(isChargeDescription(descr)).toBe(true);
  });

  it.each(['Mercado Livre', 'Posto Shell', 'Estorno compra', 'Netflix.com'])(
    'não é encargo: %s',
    (descr) => {
      expect(isChargeDescription(descr)).toBe(false);
    },
  );
});
