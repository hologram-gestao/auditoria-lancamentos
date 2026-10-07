/**
 * `<Money>` e `moneyToneClass` (86e3k1q30): sinal do valor e cor do tom, dois
 * eixos que não se misturam. `formatBRL` usa espaço NÃO-quebrável entre o
 * símbolo e o número; por isso as asserções comparam com o próprio `formatBRL`.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Money, formatMoney, moneyToneClass } from '@/components/shared/money';
import { formatBRL } from '@/lib/format';

const MINUS = '\u2212';

/**
 * O `getByText` normaliza espaços do NÓ (o NBSP do `formatBRL` vira espaço
 * comum), mas não o do texto procurado: normalizamos o procurado igual.
 */
function shown(text: string): string {
  return text.replace(/\s+/g, ' ');
}

describe('moneyToneClass', () => {
  it('sign: positivo verde, negativo vermelho, zero sem cor', () => {
    expect(moneyToneClass(1234.56, 'sign')).toBe('text-success');
    expect(moneyToneClass('-980.00', 'sign')).toBe('text-destructive');
    expect(moneyToneClass(0, 'sign')).toBe('');
    expect(moneyToneClass('0.00', 'sign')).toBe('');
  });

  it('sign: valor que arredonda a zero centavo não ganha cor', () => {
    expect(moneyToneClass(-0.004, 'sign')).toBe('');
    expect(moneyToneClass(0.004, 'sign')).toBe('');
  });

  it('situação: overdue e warning colorem independente do sinal; neutral não colore', () => {
    expect(moneyToneClass(500, 'overdue')).toBe('text-destructive');
    expect(moneyToneClass(-500, 'overdue')).toBe('text-destructive');
    expect(moneyToneClass(500, 'warning')).toBe('text-warning');
    expect(moneyToneClass(-500, 'neutral')).toBe('');
  });

  it('valor não numérico não ganha cor de sinal', () => {
    expect(moneyToneClass('abc', 'sign')).toBe('');
  });
});

describe('formatMoney', () => {
  it('sign: + no positivo e menos tipográfico (U+2212) no negativo', () => {
    expect(formatMoney(1234.56, 'sign')).toBe(`+${formatBRL(1234.56)}`);
    expect(formatMoney('-980', 'sign')).toBe(`${MINUS}${formatBRL(980)}`);
    expect(formatMoney('-980', 'sign')).not.toContain('-');
  });

  it('sign: zero sem sinal, inclusive o -0 e o que arredonda a zero', () => {
    expect(formatMoney(0, 'sign')).toBe(formatBRL(0));
    expect(formatMoney(-0, 'sign')).toBe(formatBRL(0));
    expect(formatMoney(-0.004, 'sign')).toBe(formatBRL(0));
  });

  it('fora do sign, o texto é exatamente o do formatBRL (hífen no negativo)', () => {
    expect(formatMoney(-50, 'overdue')).toBe(formatBRL(-50));
    expect(formatMoney('1234.56', 'neutral')).toBe(formatBRL('1234.56'));
  });
});

describe('<Money>', () => {
  it('positivo com tone="sign": +R$ em verde, com tabular-nums e sem quebra', () => {
    render(<Money value="1234.56" tone="sign" />);
    const el = screen.getByText(shown(`+${formatBRL(1234.56)}`));
    expect(el).toHaveClass('text-success', 'tabular-nums', 'whitespace-nowrap');
  });

  it('negativo com tone="sign": −R$ em vermelho', () => {
    render(<Money value={-980} tone="sign" />);
    expect(screen.getByText(shown(`${MINUS}${formatBRL(980)}`))).toHaveClass('text-destructive');
  });

  it('zero com tone="sign": R$ 0,00 sem sinal e sem cor', () => {
    render(<Money value="0" tone="sign" />);
    const el = screen.getByText(shown(formatBRL(0)));
    expect(el).not.toHaveClass('text-success');
    expect(el).not.toHaveClass('text-destructive');
  });

  it('tom padrão é neutral: sem cor', () => {
    render(<Money value={-50} />);
    const el = screen.getByText(shown(formatBRL(-50)));
    expect(el.className).not.toMatch(/text-(success|destructive|warning)/);
  });

  it('overdue e warning colorem pela situação', () => {
    render(
      <>
        <Money value={100} tone="overdue" />
        <Money value={200} tone="warning" />
      </>,
    );
    expect(screen.getByText(shown(formatBRL(100)))).toHaveClass('text-destructive');
    expect(screen.getByText(shown(formatBRL(200)))).toHaveClass('text-warning');
  });

  it('fallback: valor não numérico vira R$ — em muted, em qualquer tom', () => {
    render(<Money value="abc" tone="overdue" />);
    const el = screen.getByText('R$ —');
    expect(el).toHaveClass('text-muted-foreground');
    expect(el).not.toHaveClass('text-destructive');
  });

  it('o className do pai sobrescreve a cor do tom (botão ativo da carteira)', () => {
    render(<Money value={100} tone="overdue" className="text-accent-foreground" />);
    const el = screen.getByText(shown(formatBRL(100)));
    expect(el).toHaveClass('text-accent-foreground');
    expect(el).not.toHaveClass('text-destructive');
  });
});
