import { describe, expect, it } from 'vitest';

import { createAnomalyTypeSchema, updateAnomalyTypeSchema } from '@/lib/validation/anomaly-types';

describe('createAnomalyTypeSchema', () => {
  const valid = {
    code: 'extra_charge',
    name: 'Cobrança extra',
    description: 'Lançamento sem contrapartida no extrato.',
    severity: 'critical' as const,
  };

  it('aceita payload válido em snake_case', () => {
    const result = createAnomalyTypeSchema.safeParse(valid);
    expect(result.success).toBe(true);
  });

  it('rejeita code começando com número', () => {
    const result = createAnomalyTypeSchema.safeParse({ ...valid, code: '1invalid' });
    expect(result.success).toBe(false);
  });

  it('rejeita code com letras maiúsculas', () => {
    const result = createAnomalyTypeSchema.safeParse({ ...valid, code: 'ExtraCharge' });
    expect(result.success).toBe(false);
  });

  it('rejeita code com hífen', () => {
    const result = createAnomalyTypeSchema.safeParse({ ...valid, code: 'extra-charge' });
    expect(result.success).toBe(false);
  });

  it('rejeita code maior que 50 chars', () => {
    const result = createAnomalyTypeSchema.safeParse({ ...valid, code: 'a'.repeat(51) });
    expect(result.success).toBe(false);
  });

  it('rejeita severity fora do enum', () => {
    const result = createAnomalyTypeSchema.safeParse({ ...valid, severity: 'urgent' });
    expect(result.success).toBe(false);
  });

  it('rejeita nome vazio', () => {
    const result = createAnomalyTypeSchema.safeParse({ ...valid, name: '' });
    expect(result.success).toBe(false);
  });
});

describe('updateAnomalyTypeSchema', () => {
  it('aceita payload sem code (code é imutável)', () => {
    const result = updateAnomalyTypeSchema.safeParse({
      name: 'Novo nome',
      description: 'Nova descrição',
      severity: 'moderate',
    });
    expect(result.success).toBe(true);
  });

  it('rejeita description vazia', () => {
    const result = updateAnomalyTypeSchema.safeParse({
      name: 'OK',
      description: '',
      severity: 'info',
    });
    expect(result.success).toBe(false);
  });
});
