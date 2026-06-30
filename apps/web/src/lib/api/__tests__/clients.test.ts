/**
 * Testes do helper `isCreditCardAccount` (FRONT 1.4).
 *
 * Guarda o bug M-1: cartão é `CR`; `CA` é Conta Aplicação (investimento) e
 * NÃO pode ser tratado como cartão.
 */
import { describe, expect, it } from 'vitest';

import { isCreditCardAccount } from '@/lib/api/clients';

describe('isCreditCardAccount', () => {
  it('reconhece CR como cartão de crédito', () => {
    expect(isCreditCardAccount('CR')).toBe(true);
  });

  it('NÃO trata CA (Conta Aplicação) como cartão — anti-M-1', () => {
    expect(isCreditCardAccount('CA')).toBe(false);
  });

  it.each(['CC', 'CX', 'CP', 'PG', ''])('não é cartão: %s', (tipo) => {
    expect(isCreditCardAccount(tipo)).toBe(false);
  });

  it('normaliza espaço e caixa que o Omie às vezes devolve', () => {
    expect(isCreditCardAccount('  cr ')).toBe(true);
    expect(isCreditCardAccount('Cr')).toBe(true);
  });
});
