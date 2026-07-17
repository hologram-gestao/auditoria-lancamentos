/**
 * Testes da prévia do parsing.
 *
 * Cobre duas features que convivem no mesmo componente:
 *   - FRONT 1.4 (FASE 1): adaptações de fatura de cartão (título/legenda).
 *   - BACK 02.3 (Sprint 2): o checksum de saldos BLOQUEIA a confirmação
 *     quando o extrato não fecha — a defesa contra parse incompleto.
 *
 * Componente de props planas (sem Radix), então o render é determinístico.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ParsePreview } from '@/components/features/reconciliations/parse-preview';
import type { ChecksumResult, ParsedStatement } from '@/lib/api/reconciliations';

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

function makeChecksum(overrides: Partial<ChecksumResult> = {}): ChecksumResult {
  return {
    ok: true,
    applicable: true,
    account_type: 'checking',
    expected: '100.00',
    computed: '100.00',
    difference: '0.00',
    tolerance: '0.01',
    reason: null,
    ...overrides,
  };
}

const noop = vi.fn();

describe('ParsePreview — fatura de cartão', () => {
  it('cartão: título "Prévia da fatura — {conta}" + legenda de compras/estornos', () => {
    render(
      <ParsePreview
        parsed={makeParsed('credit_card')}
        checksum={makeChecksum({ account_type: 'credit_card' })}
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
        checksum={makeChecksum()}
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

describe('ParsePreview — bloqueio por checksum (BACK 02.3)', () => {
  const REASON = 'O extrato não fecha: diferença de R$ 42,00. Revise antes de conciliar.';

  it('checksum ok: confirmação liberada e sem alerta', () => {
    render(
      <ParsePreview
        parsed={makeParsed('checking')}
        checksum={makeChecksum({ ok: true })}
        isCard={false}
        accountName="Sicredi"
        onCancel={noop}
        onConfirm={noop}
        isConfirming={false}
      />,
    );
    expect(screen.getByRole('button', { name: 'Confirmar e processar' })).toBeEnabled();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('checksum falhou: bloqueia a confirmação e mostra o motivo do backend', () => {
    render(
      <ParsePreview
        parsed={makeParsed('checking')}
        checksum={makeChecksum({ ok: false, reason: REASON })}
        isCard={false}
        accountName="Sicredi"
        onCancel={noop}
        onConfirm={noop}
        isConfirming={false}
      />,
    );
    expect(screen.getByRole('button', { name: 'Confirmar e processar' })).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent(/Os saldos não fecham/);
    expect(screen.getByRole('alert')).toHaveTextContent(/diferença de R\$ 42,00/);
    // Sem rota de "continuar mesmo assim": o cancelar vira a saída explícita.
    expect(screen.getByRole('button', { name: 'Selecionar outro arquivo' })).toBeVisible();
  });

  it('conta aplicação: não bloqueia mesmo com diferença (applicable=false)', () => {
    // Rendimento/IOF/IR entram no saldo sem virar movimentação — a identidade
    // não fecha nem num parse perfeito, então não pode virar veredito.
    render(
      <ParsePreview
        parsed={makeParsed('investment')}
        checksum={makeChecksum({
          ok: true,
          applicable: false,
          account_type: 'investment',
          expected: '1530.00',
          computed: '1500.00',
          difference: '30.00',
        })}
        isCard={false}
        accountName="BTG CDB"
        onCancel={noop}
        onConfirm={noop}
        isConfirming={false}
      />,
    );
    expect(screen.getByRole('button', { name: 'Confirmar e processar' })).toBeEnabled();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('checksum null (chamada legada): não bloqueia', () => {
    render(
      <ParsePreview
        parsed={makeParsed('checking')}
        checksum={null}
        isCard={false}
        accountName="Sicredi"
        onCancel={noop}
        onConfirm={noop}
        isConfirming={false}
      />,
    );
    expect(screen.getByRole('button', { name: 'Confirmar e processar' })).toBeEnabled();
  });
});
