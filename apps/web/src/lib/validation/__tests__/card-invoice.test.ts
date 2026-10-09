/** Fatura de cartão na gaveta (86e3n70p0): data DD/MM/AAAA e obrigatoriedade por processo. */
import { describe, expect, it } from 'vitest';

import {
  INVOICE_DUE_DATE_INVALID_MESSAGE,
  INVOICE_DUE_DATE_REQUIRED_MESSAGE,
  cardInvoiceSchema,
  parseBrDate,
} from '@/lib/validation/reconciliations';

describe('parseBrDate', () => {
  it('converte DD/MM/AAAA para ISO sem passar por fuso', () => {
    expect(parseBrDate('10/10/2026')).toBe('2026-10-10');
    expect(parseBrDate(' 01/01/2027 ')).toBe('2027-01-01');
  });

  it.each(['31/02/2026', '00/10/2026', '10/13/2026', '2026-10-10', '10/10/26', ''])(
    'recusa %s',
    (value) => {
      expect(parseBrDate(value)).toBeNull();
    },
  );

  it('aceita 29/02 só em ano bissexto', () => {
    expect(parseBrDate('29/02/2028')).toBe('2028-02-29');
    expect(parseBrDate('29/02/2026')).toBeNull();
  });
});

describe('cardInvoiceSchema', () => {
  function message(values: { card_posting_date_mode: string; invoice_due_date: string }) {
    const result = cardInvoiceSchema.safeParse(values);
    return result.success ? null : result.error.issues[0]?.message;
  }

  it('no processo do vencimento a data é obrigatória', () => {
    expect(message({ card_posting_date_mode: 'invoice_due_date', invoice_due_date: '' })).toBe(
      INVOICE_DUE_DATE_REQUIRED_MESSAGE,
    );
    expect(
      message({ card_posting_date_mode: 'invoice_due_date', invoice_due_date: '10/10/2026' }),
    ).toBeNull();
  });

  it('no processo da compra a data é opcional, mas se vier tem de ser real', () => {
    expect(message({ card_posting_date_mode: 'purchase_date', invoice_due_date: '' })).toBeNull();
    expect(
      message({ card_posting_date_mode: 'purchase_date', invoice_due_date: '31/02/2026' }),
    ).toBe(INVOICE_DUE_DATE_INVALID_MESSAGE);
  });
});
