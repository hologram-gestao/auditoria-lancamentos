/**
 * Testes da prévia do parse (FRONT 02.2).
 *
 * Foco: o checksum de saldos (BACK 02.3) governa o botão "Confirmar e
 * processar". `ok=false` bloqueia a confirmação e mostra a razão; `ok=true`
 * libera o fluxo. É a defesa visual contra parse incompleto — se ela some,
 * um parse com linhas faltando "parece certo".
 */

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ParsePreview } from '@/components/features/reconciliations/parse-preview';
import type { ChecksumResult, ParsedStatement } from '@/lib/api/reconciliations';

function makeStatement(overrides: Partial<ParsedStatement> = {}): ParsedStatement {
  return {
    bank_name: 'Banco Teste',
    account_type: 'checking',
    period_start: '2026-07-01',
    period_end: '2026-07-31',
    opening_balance: '1000.00',
    closing_balance: '1150.00',
    transactions: [
      { date: '2026-07-03', description: 'Compra', amount: '-50.00', balance: null, is_payment: false },
      { date: '2026-07-10', description: 'Depósito', amount: '200.00', balance: null, is_payment: false },
    ],
    ...overrides,
  };
}

function makeChecksum(overrides: Partial<ChecksumResult> = {}): ChecksumResult {
  return {
    ok: true,
    account_type: 'checking',
    expected: '1150.00',
    computed: '1150.00',
    difference: '0.00',
    tolerance: '0.01',
    reason: null,
    ...overrides,
  };
}

describe('ParsePreview — bloqueio pelo checksum', () => {
  it('checksum ok → botão habilitado e confirmação dispara', () => {
    const onConfirm = vi.fn();
    render(
      <ParsePreview
        parsed={makeStatement()}
        checksum={makeChecksum({ ok: true })}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
        isConfirming={false}
      />,
    );

    const confirm = screen.getByRole('button', { name: 'Confirmar e processar' });
    expect(confirm).not.toBeDisabled();
    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Saldos conferem/i)).toBeInTheDocument();
  });

  it('checksum falho → botão bloqueado, razão exibida, confirmação não dispara', () => {
    const onConfirm = vi.fn();
    const reason = 'Saldo inicial + movimentações (R$ 900,00) não fecha com o saldo final (R$ 1.150,00).';
    render(
      <ParsePreview
        parsed={makeStatement()}
        checksum={makeChecksum({
          ok: false,
          expected: '1150.00',
          computed: '900.00',
          difference: '250.00',
          reason,
        })}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
        isConfirming={false}
      />,
    );

    const confirm = screen.getByRole('button', { name: 'Confirmar e processar' });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent(reason);
    expect(screen.getByText(/importação bloqueada/i)).toBeInTheDocument();
  });

  it('cartão sem razão explícita → usa rótulo "Total da fatura" e fallback de texto', () => {
    render(
      <ParsePreview
        parsed={makeStatement({ account_type: 'credit_card' })}
        checksum={makeChecksum({ ok: false, account_type: 'credit_card', reason: null })}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
        isConfirming={false}
      />,
    );

    expect(screen.getByText(/Total da fatura/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/não bate com o saldo final/i);
  });
});
