/**
 * O documento do vínculo manual (86e3tttaf): `isValidPartyDocument` e `partyLinkSchema`,
 * espelho de `client_accounting_chart/parties.parse_typed_document` no servidor (14
 * dígitos é CNPJ, 11 é CPF, outro tamanho é inválido, dígito verificador conferido).
 */
import { describe, expect, it } from 'vitest';

import {
  PARTY_DOCUMENT_MAX_CHARS,
  isValidPartyDocument,
  partyLinkSchema,
} from '@/lib/validation/accounting-parties';

describe('isValidPartyDocument', () => {
  it.each(['45723174000110', '45.723.174/0001-10', '52998224725', '529.982.247-25'])(
    'aceita %s',
    (raw) => {
      expect(isValidPartyDocument(raw)).toBe(true);
    },
  );

  it.each([
    ['CNPJ com dígito errado', '45.723.174/0001-19'],
    ['CPF com dígito errado', '529.982.247-26'],
    ['12 dígitos', '457231740001'],
    ['CPF com barra', '529.982.247/25'],
    ['texto', 'abc'],
  ])('recusa %s', (_motivo, raw) => {
    expect(isValidPartyDocument(raw)).toBe(false);
  });
});

describe('partyLinkSchema', () => {
  const base = { accountId: 'acc-1', partyType: 'cliente' as const };

  it('aceita conta, tipo e documento válidos', () => {
    expect(partyLinkSchema.safeParse({ ...base, document: '45.723.174/0001-10' }).success).toBe(
      true,
    );
  });

  it('recusa sem conta, sem documento e acima do teto do servidor', () => {
    expect(
      partyLinkSchema.safeParse({ ...base, accountId: '', document: '52998224725' }).success,
    ).toBe(false);
    expect(partyLinkSchema.safeParse({ ...base, document: '  ' }).success).toBe(false);
    expect(
      partyLinkSchema.safeParse({ ...base, document: '5'.repeat(PARTY_DOCUMENT_MAX_CHARS + 1) })
        .success,
    ).toBe(false);
  });

  it('o teto é o mesmo do servidor (MAX_PARTY_DOCUMENT_INPUT_CHARS = 24)', () => {
    expect(PARTY_DOCUMENT_MAX_CHARS).toBe(24);
  });
});
