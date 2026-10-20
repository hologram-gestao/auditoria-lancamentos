/**
 * `lib/cpf.ts` (86e3tttaf): o espelho de `apps/api/app/core/cpf.py::normalize_cpf`. Os
 * casos são os MESMOS números do `tests/parties_builder.py` do backend.
 */
import { describe, expect, it } from 'vitest';

import { isValidCpf } from '@/lib/cpf';

describe('isValidCpf', () => {
  it.each(['52998224725', '529.982.247-25', '111.444.777-35', ' 529 982 247 25 '])(
    'aceita %s',
    (raw) => {
      expect(isValidCpf(raw)).toBe(true);
    },
  );

  it.each([
    ['dígito verificador errado', '529.982.247-26'],
    ['todos os dígitos iguais', '111.111.111-11'],
    ['dez dígitos', '5299822472'],
    ['caractere fora da máscara', '529.982.247/25'],
    ['letra', '52998224a25'],
    ['vazio', ''],
  ])('recusa %s', (_motivo, raw) => {
    expect(isValidCpf(raw)).toBe(false);
  });
});
