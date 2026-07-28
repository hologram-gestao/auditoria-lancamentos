/**
 * Testes dos leitores de estado-na-URL.
 *
 * A URL é editável pela pessoa (e colável de outro contexto), então cada leitor
 * precisa degradar para o default em vez de repassar lixo ao backend — um
 * `?page=abc` não pode virar 422 na carga inicial da tela.
 */
import { describe, expect, it } from 'vitest';

import { readEnum, readPositiveInt } from '@/hooks/use-url-state';

describe('readPositiveInt', () => {
  it('lê um inteiro positivo', () => {
    expect(readPositiveInt('3', 1)).toBe(3);
  });

  it('cai no default quando ausente', () => {
    expect(readPositiveInt(null, 20)).toBe(20);
  });

  it('cai no default em valor inválido (texto, zero, negativo, fracionário)', () => {
    expect(readPositiveInt('abc', 20)).toBe(20);
    expect(readPositiveInt('0', 20)).toBe(20);
    expect(readPositiveInt('-3', 20)).toBe(20);
    expect(readPositiveInt('1.5', 20)).toBe(20);
  });
});

describe('readEnum', () => {
  const ALLOWED = ['processing', 'processed', 'error'] as const;

  it('aceita valor da lista', () => {
    expect(readEnum('processed', ALLOWED)).toBe('processed');
  });

  it('devolve undefined (= sem filtro) para ausente ou fora da lista', () => {
    expect(readEnum(null, ALLOWED)).toBeUndefined();
    // `reviewing` é status do BANCO, não do filtro do produto — mandá-lo
    // devolveria 400 no backend.
    expect(readEnum('reviewing', ALLOWED)).toBeUndefined();
  });
});
