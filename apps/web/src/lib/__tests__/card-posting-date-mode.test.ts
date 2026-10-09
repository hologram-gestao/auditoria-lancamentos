import { describe, expect, it } from 'vitest';

import {
  CARD_POSTING_DATE_MODES,
  CARD_POSTING_DATE_MODE_LABEL,
  isCardPostingDateMode,
  sessionCardProcessLine,
} from '@/lib/card-posting-date-mode';
import { formatBRDate } from '@/lib/format';

describe('vocabulário do processo do cartão (86e3n70p0)', () => {
  it('os rótulos de tela são os combinados', () => {
    expect(CARD_POSTING_DATE_MODE_LABEL).toEqual({
      purchase_date: 'Na data da compra',
      invoice_due_date: 'No vencimento da fatura',
    });
    expect(CARD_POSTING_DATE_MODES[0]).toBe('purchase_date');
  });

  it('reconhece só os dois valores do contrato', () => {
    expect(isCardPostingDateMode('invoice_due_date')).toBe(true);
    expect(isCardPostingDateMode('vencimento')).toBe(false);
  });
});

describe('sessionCardProcessLine', () => {
  const base = { account_type: 'credit_card', invoice_due_date: '2026-10-10' };

  it('modo vencimento mostra o lote da fatura', () => {
    expect(
      sessionCardProcessLine({ ...base, card_posting_date_mode: 'invoice_due_date' }, formatBRDate),
    ).toBe('Compras lançadas no vencimento da fatura · Lote da fatura: 10/10/2026');
  });

  it('modo compra mostra o vencimento como informação', () => {
    expect(
      sessionCardProcessLine({ ...base, card_posting_date_mode: 'purchase_date' }, formatBRDate),
    ).toBe('Compras lançadas na data da compra · Vencimento da fatura: 10/10/2026');
    expect(
      sessionCardProcessLine(
        { ...base, card_posting_date_mode: 'purchase_date', invoice_due_date: null },
        formatBRDate,
      ),
    ).toBe('Compras lançadas na data da compra');
  });

  it('fora do cartão e em sessão antiga não há linha', () => {
    expect(
      sessionCardProcessLine(
        { ...base, account_type: 'checking', card_posting_date_mode: 'invoice_due_date' },
        formatBRDate,
      ),
    ).toBeNull();
    expect(
      sessionCardProcessLine({ ...base, card_posting_date_mode: null }, formatBRDate),
    ).toBeNull();
  });
});
