/**
 * Testes do header da revisão (FRONT 1.8 — badge de tipo + título de cartão).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/use-reconciliations', () => ({
  useExportReconciliation: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { ReviewHeader } from '@/components/features/reconciliations/review/review-header';

const counts = { conciliated: 0, semOmie: 0, omieSemArquivo: 0, anomaly: 0 };

describe('ReviewHeader', () => {
  it('cartão: badge azul "Cartão de Crédito" + título "Conciliação · Cartão · …"', () => {
    render(
      <ReviewHeader
        clientId="c1"
        clientName="Cliente X"
        sessionId="s1"
        referenceMonthLabel="Abril/2026"
        accountName="Nubank PJ"
        isCard
        counts={counts}
      />,
    );
    expect(screen.getByText('Cartão de Crédito')).toBeVisible();
    expect(
      screen.getByRole('heading', { name: 'Conciliação · Cartão · Nubank PJ · Abril/2026' }),
    ).toBeVisible();
  });

  it('conta corrente: badge "Conta Corrente" + título com "Conta Corrente"', () => {
    render(
      <ReviewHeader
        clientId="c1"
        clientName="Cliente X"
        sessionId="s1"
        referenceMonthLabel="Abril/2026"
        accountName="Sicredi 91263-1"
        isCard={false}
        counts={counts}
      />,
    );
    expect(screen.getByText('Conta Corrente')).toBeVisible();
    expect(
      screen.getByRole('heading', {
        name: 'Conciliação · Conta Corrente · Sicredi 91263-1 · Abril/2026',
      }),
    ).toBeVisible();
  });

  it('omite segmentos vazios no título (conta ainda hidratando)', () => {
    render(
      <ReviewHeader
        clientId="c1"
        clientName="Cliente X"
        sessionId="s1"
        referenceMonthLabel="Abril/2026"
        accountName={undefined}
        isCard
        counts={counts}
      />,
    );
    expect(
      screen.getByRole('heading', { name: 'Conciliação · Cartão · Abril/2026' }),
    ).toBeVisible();
  });
});
