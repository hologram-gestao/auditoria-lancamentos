/**
 * Testes do schema do formulário de nova conciliação (FRONT 1.4).
 *
 * Foco: a tolerância de data saiu do formulário (BACK 1.6 a tornou fixa no
 * backend) — um payload válido NÃO precisa mais de `tolerance_days`.
 */
import { describe, expect, it } from 'vitest';

import { newReconciliationSchema } from '@/lib/validation/reconciliations';

function makeFile(name = 'extrato.pdf'): File {
  return new File(['conteudo'], name, { type: 'application/pdf' });
}

describe('newReconciliationSchema', () => {
  it('aceita um payload válido SEM tolerance_days', () => {
    const result = newReconciliationSchema.safeParse({
      omie_conta_id: 42,
      reference_month: '2026-04',
      file: makeFile(),
    });
    expect(result.success).toBe(true);
  });

  it('não inclui tolerance_days no resultado parseado', () => {
    const result = newReconciliationSchema.safeParse({
      omie_conta_id: 42,
      reference_month: '2026-04',
      file: makeFile(),
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data).not.toHaveProperty('tolerance_days');
    }
  });

  it('rejeita mês futuro', () => {
    const result = newReconciliationSchema.safeParse({
      omie_conta_id: 42,
      reference_month: '2999-12',
      file: makeFile(),
    });
    expect(result.success).toBe(false);
  });

  it('rejeita extensão não suportada', () => {
    const result = newReconciliationSchema.safeParse({
      omie_conta_id: 42,
      reference_month: '2026-04',
      file: makeFile('fatura.txt'),
    });
    expect(result.success).toBe(false);
  });
});
