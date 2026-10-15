/**
 * Testes do helper `isCreditCardAccount` (FRONT 1.4).
 *
 * Guarda o bug M-1: cartão é `CR`; `CA` é Conta Aplicação (investimento) e
 * NÃO pode ser tratado como cartão.
 */
import { describe, expect, it } from 'vitest';

import { isCreditCardAccount, resolveSessionAccountType } from '@/lib/api/clients';

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

/**
 * Espelho de `session_account_type_from_omie_tipo` (backend, 86e3qyu04): a
 * tabela abaixo é a mesma de `tests/unit/test_session_account_type.py`.
 */
describe('resolveSessionAccountType', () => {
  it.each([
    ['CR', 'checking', 'credit_card'],
    ['CA', 'credit_card', 'investment'],
    ['CC', 'credit_card', 'checking'],
    ['CC', 'investment', 'checking'],
  ] as const)('código conhecido %s vence o arquivo (%s → %s)', (omie, file, expected) => {
    expect(resolveSessionAccountType(omie, file)).toBe(expected);
  });

  it.each([
    ['AC', 'credit_card', 'credit_card'],
    ['AC', 'checking', 'checking'],
    ['AC', 'investment', 'investment'],
    ['PG', 'credit_card', 'credit_card'],
    ['CX', 'credit_card', 'credit_card'],
    [undefined, 'credit_card', 'credit_card'],
    ['', 'credit_card', 'credit_card'],
  ] as const)(
    'código desconhecido %s deixa o arquivo decidir (%s → %s)',
    (omie, file, expected) => {
      expect(resolveSessionAccountType(omie, file)).toBe(expected);
    },
  );

  it.each(['AC', 'PG', undefined, ''])('código desconhecido %s sem arquivo é checking', (omie) => {
    expect(resolveSessionAccountType(omie, undefined)).toBe('checking');
  });

  it('normaliza espaço e caixa do código do Omie', () => {
    expect(resolveSessionAccountType('  cr ', 'checking')).toBe('credit_card');
    expect(resolveSessionAccountType('ac', 'credit_card')).toBe('credit_card');
  });
});
