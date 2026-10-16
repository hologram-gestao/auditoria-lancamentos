/**
 * 86e3qhhrd — o mínimo da busca de fornecedor pelo nome conta só letras e dígitos,
 * sem acento: o mesmo critério do servidor, que recusa com 400 "a-b" ou "  .  ".
 * Divergir faria a tela disparar uma busca que o servidor nega.
 */
import { describe, expect, it } from 'vitest';

import { SUPPLIER_SEARCH_MIN_CHARS, supplierSearchUsefulLength } from '../use-omie-postings';

describe('supplierSearchUsefulLength', () => {
  it.each([
    ['ab', 2],
    ['a-b', 2],
    ['  .  ', 0],
    ['sóf', 3],
    ['Papelária Demo', 13],
    ['12.345', 5],
  ])('"%s" tem %i caracteres úteis', (text, expected) => {
    expect(supplierSearchUsefulLength(text)).toBe(expected);
  });

  it('o mínimo é o do servidor', () => {
    expect(SUPPLIER_SEARCH_MIN_CHARS).toBe(3);
  });
});
