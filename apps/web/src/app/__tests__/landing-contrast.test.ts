/**
 * Trava de contraste do verde da Hologram na landing (86e3h0xcr).
 *
 * O verde (`--lp-brand`) é a única cor da landing que não é token do tema, então os
 * pares dele não estão no `theme-contrast.test.ts`. Aqui eles são lidos do
 * `landing.css` e do `globals.css` de verdade (parseados), como lá: uma cópia dos
 * valores sairia de sincronia no primeiro ajuste.
 *
 * A landing tem o tema Hologram FIXO (wrapper `.hologram`), então o fundo é o do
 * bloco `.hologram`.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { AA_NORMAL_TEXT, contrast, hslToRgb, parseCssVariables, type Rgb } from '@/test/contrast';

const GLOBALS = readFileSync(path.resolve(__dirname, '../globals.css'), 'utf8');
const LANDING = readFileSync(path.resolve(__dirname, '../(public)/landing.css'), 'utf8');

const landing = parseCssVariables(LANDING, '.landing');
const light = parseCssVariables(GLOBALS, ':root');
const hologram = parseCssVariables(GLOBALS, '.hologram');

function rgbOf(tokens: Record<string, string>, name: string): Rgb {
  const value = tokens[name];
  if (value === undefined) throw new Error(`\`--${name}\` não existe`);
  return hslToRgb(value);
}

const brand = rgbOf(landing, 'lp-brand');
const brandFg = rgbOf(landing, 'lp-brand-fg');
/** `color-mix(in srgb, verde 90%, black)`: cada canal sRGB vezes 0,9. */
const brandHover = brand.map((channel) => channel * 0.9) as Rgb;

describe('verde da Hologram na landing: contraste AA', () => {
  it('o verde é o #05d1bf amostrado do site (hsl 175 95% 42%)', () => {
    const [r, g, b] = brand.map((channel) => Math.round(channel * 255));
    // A conversão de `175 95% 42%` cai a 1 unidade do pixel (5, 209, 191).
    expect(Math.abs((r ?? 0) - 5)).toBeLessThanOrEqual(1);
    expect(Math.abs((g ?? 0) - 209)).toBeLessThanOrEqual(1);
    expect(Math.abs((b ?? 0) - 191)).toBeLessThanOrEqual(1);
  });

  it('o texto sobre o verde é a cópia literal do navy da marca (`--primary` do tema claro)', () => {
    expect(landing['lp-brand-fg']).toBe(light['primary']);
  });

  it('o hover do botão é o verde com 10 % de preto (o que este teste calcula)', () => {
    expect(LANDING).toContain('color-mix(in srgb, hsl(var(--lp-brand)) 90%, black)');
  });

  it.each([
    ['navy sobre o verde (botão primário da landing)', brandFg, brand],
    ['navy sobre o verde em hover', brandFg, brandHover],
    [
      'verde sobre o fundo Hologram (check dos bullets, linha do passo)',
      brand,
      rgbOf(hologram, 'background'),
    ],
    ['verde sobre o card Hologram', brand, rgbOf(hologram, 'card')],
  ] as const)('%s passa 4.5:1', (_where, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });

  it('branco (o `--primary` do Hologram) sobre o verde REPROVA: nunca texto branco no verde', () => {
    expect(contrast(rgbOf(hologram, 'primary'), brand)).toBeLessThan(AA_NORMAL_TEXT);
  });
});
