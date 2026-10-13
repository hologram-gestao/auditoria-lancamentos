/**
 * CNPJ na tela (86e3n70q0, bloco C): máscara e o espelho de `normalize_cnpj`
 * (`apps/api/app/core/cnpj.py`). Os CNPJs são os do mock do Omie.
 */
import { describe, expect, it } from 'vitest';

import { cnpjDigits, formatCnpj, isValidCnpj } from '../cnpj';

describe('formatCnpj', () => {
  it('mascara aos poucos, enquanto a pessoa digita', () => {
    expect(formatCnpj('12')).toBe('12');
    expect(formatCnpj('12345')).toBe('12.345');
    expect(formatCnpj('12345678')).toBe('12.345.678');
    expect(formatCnpj('123456780001')).toBe('12.345.678/0001');
    expect(formatCnpj('12345678000195')).toBe('12.345.678/0001-95');
  });

  it('ignora o que não é dígito e corta no 14º', () => {
    expect(formatCnpj('12.345.678/0001-9599')).toBe('12.345.678/0001-95');
    expect(cnpjDigits('ab12.345')).toBe('12345');
  });
});

describe('isValidCnpj (mesma regra do servidor)', () => {
  it.each(['12.345.678/0001-95', '98765432000198', '11.222.333/0001-81'])('%s é válido', (c) => {
    expect(isValidCnpj(c)).toBe(true);
  });

  it.each([
    ['dígito verificador errado', '12.345.678/0001-96'],
    ['13 dígitos', '1234567800019'],
    ['todos iguais', '11111111111111'],
    ['caractere fora da máscara', '12a45678000195'],
  ])('recusa: %s', (_nome, c) => {
    expect(isValidCnpj(c)).toBe(false);
  });
});
