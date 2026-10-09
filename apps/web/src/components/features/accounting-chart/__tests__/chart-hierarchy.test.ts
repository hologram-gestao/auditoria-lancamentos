/**
 * O grau da conta e o recuo por grau (86e3n70p9) — funções puras da tela.
 */
import { describe, expect, it } from 'vitest';

import {
  MAX_INDENT_DEPTH,
  classificationDepth,
  indentClassFor,
} from '@/components/features/accounting-chart/chart-hierarchy';

describe('classificationDepth', () => {
  it.each([
    ['1', 0],
    ['1.1', 1],
    ['1.1.1', 2],
    ['1.1.1.02.001', 4],
    ['1.1.10', 2],
  ])('%s tem grau %i', (classification, depth) => {
    expect(classificationDepth(classification)).toBe(depth);
  });

  it('sem classificação, sem recuo', () => {
    expect(classificationDepth(null)).toBe(0);
    expect(classificationDepth(undefined)).toBe(0);
    expect(classificationDepth('')).toBe(0);
  });
});

describe('indentClassFor', () => {
  it('um nível por grau, com teto e sem valor negativo', () => {
    expect(indentClassFor(0)).toBe('pl-0');
    expect(indentClassFor(1)).toBe('pl-4');
    expect(indentClassFor(4)).toBe('pl-16');
    expect(indentClassFor(MAX_INDENT_DEPTH)).toBe('pl-20');
    expect(indentClassFor(MAX_INDENT_DEPTH + 10)).toBe('pl-20');
    expect(indentClassFor(-3)).toBe('pl-0');
  });
});
