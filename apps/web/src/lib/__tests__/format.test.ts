import { describe, expect, it } from 'vitest';

import { shiftISODate } from '@/lib/format';

describe('shiftISODate', () => {
  it('subtrai days_diff positivo (Omie anterior ao extrato)', () => {
    // days_diff = extrato - omie. Positivo → Omie é ANTES → -days_diff recua.
    expect(shiftISODate('2026-07-03', -1)).toBe('2026-07-02');
  });

  it('soma days_diff negativo (Omie posterior ao extrato)', () => {
    // days_diff negativo → Omie é DEPOIS → -days_diff avança.
    expect(shiftISODate('2026-07-03', 1)).toBe('2026-07-04');
  });

  it('atravessa a virada de mês sem timezone-shift', () => {
    expect(shiftISODate('2026-07-01', -3)).toBe('2026-06-28');
    expect(shiftISODate('2026-06-30', 2)).toBe('2026-07-02');
  });

  it('atravessa a virada de ano', () => {
    expect(shiftISODate('2026-01-01', -1)).toBe('2025-12-31');
  });

  it('delta 0 devolve a mesma data', () => {
    expect(shiftISODate('2026-07-03', 0)).toBe('2026-07-03');
  });

  it('devolve a entrada inalterada para formato inválido', () => {
    expect(shiftISODate('03/07/2026', -1)).toBe('03/07/2026');
    expect(shiftISODate('', -1)).toBe('');
  });
});
