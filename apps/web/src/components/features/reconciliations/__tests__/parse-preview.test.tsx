/**
 * Testes da prévia do parsing (FRONT 1.4 — adaptações de fatura de cartão).
 *
 * Componente de props planas (sem Radix), então o render é determinístico.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ParsePreview } from '@/components/features/reconciliations/parse-preview';
import type { ParsedStatement } from '@/lib/api/reconciliations';

function makeParsed(account_type: ParsedStatement['account_type']): ParsedStatement {
  return {
    bank_name: 'Nubank',
    account_type,
    period_start: '2026-04-01',
    period_end: '2026-04-30',
    opening_balance: '0.00',
    closing_balance: '-100.00',
    transactions: [{ date: '2026-04-05', description: 'Compra', amount: '-100.00', balance: null }],
  };
}

const noop = vi.fn();

describe('ParsePreview — fatura de cartão', () => {
  it('cartão: título "Prévia da fatura — {conta}" + legenda de compras/estornos', () => {
    render(
      <ParsePreview
        parsed={makeParsed('credit_card')}
        isCard
        accountName="Nubank PJ"
        onCancel={noop}
        onConfirm={noop}
        isConfirming={false}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Prévia da fatura — Nubank PJ' })).toBeVisible();
    expect(
      screen.getByText('Valores negativos = compras · Valores positivos = estornos ou créditos.'),
    ).toBeVisible();
  });

  it('conta corrente: título e ausência de legenda permanecem como antes', () => {
    render(
      <ParsePreview
        parsed={makeParsed('checking')}
        isCard={false}
        accountName="Sicredi 91263-1"
        onCancel={noop}
        onConfirm={noop}
        isConfirming={false}
      />,
    );
    expect(
      screen.getByRole('heading', { name: 'Confirme as movimentações extraídas' }),
    ).toBeVisible();
    expect(screen.queryByText(/Valores negativos = compras/)).toBeNull();
  });
});
