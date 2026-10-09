/**
 * Lançar no Omie x processo do cartão da conciliação (86e3n70p0).
 *
 * Espelho de `_require_purchase_date_process` do servidor: no processo "no
 * vencimento da fatura" o lote inteiro é recusado (o `IncluirLancCC` grava a
 * data da compra), então a tela não oferece a ação. Decisão do Pedro, 08/10/2026.
 */
import { describe, expect, it } from 'vitest';

import {
  INVOICE_DUE_DATE_POSTING_BLOCK_MESSAGE,
  processAllowsPosting,
} from '../omie-posting-eligibility';

describe('processAllowsPosting', () => {
  it('o processo da compra e a sessão antiga (sem processo gravado) podem lançar', () => {
    expect(processAllowsPosting({ card_posting_date_mode: 'purchase_date' })).toBe(true);
    expect(processAllowsPosting({ card_posting_date_mode: null })).toBe(true);
    expect(processAllowsPosting({})).toBe(true);
  });

  it('o processo do vencimento não pode, e a tela diz por quê', () => {
    expect(processAllowsPosting({ card_posting_date_mode: 'invoice_due_date' })).toBe(false);
    expect(INVOICE_DUE_DATE_POSTING_BLOCK_MESSAGE).toMatch(/lance as compras diretamente no Omie/);
  });
});
