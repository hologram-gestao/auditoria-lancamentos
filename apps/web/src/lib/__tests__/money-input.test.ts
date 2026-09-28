/**
 * `lib/money-input.ts` — RAW (centavos) no estado, conversão só nas bordas
 * (Sprint 14 — FRONT 14.6, o campo "Total do arquivo").
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 */
import { describe, expect, it } from 'vitest';

import { centsFromTyped, centsToDecimalString, formatCentsForInput } from '@/lib/money-input';

describe('centsFromTyped — o que foi digitado vira centavos crus', () => {
  it('fica só com dígitos e o sinal', () => {
    expect(centsFromTyped('1.234,56')).toBe('123456');
    expect(centsFromTyped('R$ 12,3')).toBe('123');
    expect(centsFromTyped('-1.250,00')).toBe('-125000');
    // O sinal digitado depois do número também vale.
    expect(centsFromTyped('1.250,00-')).toBe('-125000');
  });

  it('zeros à esquerda saem, sinal sem valor vira vazio, zero é zero', () => {
    expect(centsFromTyped('0001')).toBe('1');
    expect(centsFromTyped('')).toBe('');
    expect(centsFromTyped('-')).toBe('');
    expect(centsFromTyped('0')).toBe('0');
    expect(centsFromTyped('-0,00')).toBe('0');
  });

  it('respeita o teto de dígitos do contrato (12 inteiros + 2 casas)', () => {
    expect(centsFromTyped('1'.repeat(20))).toBe('1'.repeat(14));
  });
});

describe('formatCentsForInput — exibição pt-BR', () => {
  it('formata com separador de milhar e duas casas', () => {
    expect(formatCentsForInput('123456')).toBe('1.234,56');
    expect(formatCentsForInput('5')).toBe('0,05');
    expect(formatCentsForInput('-125000')).toBe('-1.250,00');
    expect(formatCentsForInput('')).toBe('');
    expect(formatCentsForInput('0')).toBe('0,00');
  });
});

describe('centsToDecimalString — o decimal que o backend aceita', () => {
  it('converte por aritmética de string, sem arredondar', () => {
    expect(centsToDecimalString('123456')).toBe('1234.56');
    expect(centsToDecimalString('5')).toBe('0.05');
    expect(centsToDecimalString('100')).toBe('1.00');
    expect(centsToDecimalString('-125000')).toBe('-1250.00');
    expect(centsToDecimalString('0')).toBe('0.00');
    expect(centsToDecimalString('-0')).toBe('0.00');
  });

  it('vazio é "não informado" (null), nunca "0.00"', () => {
    expect(centsToDecimalString('')).toBeNull();
  });

  it('casa com o padrão do servidor', () => {
    const pattern = /^-?\d{1,12}([.,]\d{1,2})?$/;
    for (const cents of ['1', '123456', '-7', '99999999999999']) {
      expect(centsToDecimalString(cents)).toMatch(pattern);
    }
  });
});
