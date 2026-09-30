/**
 * Contraste WCAG 2.1 a partir de tokens de CSS no formato `H S% L%` (o que o
 * Tailwind embrulha em `hsl()`). Usado pelas travas de contraste do tema
 * (`theme-contrast.test.ts`), que parseiam o CSS de verdade em vez de copiar valores.
 */

export type Rgb = [number, number, number];

/** Mínimo do WCAG 2.1 AA para texto normal (< 18.66px bold / < 24px). */
export const AA_NORMAL_TEXT = 4.5;

/**
 * As variáveis `--nome: valor;` do PRIMEIRO bloco `selector {` do CSS, até a primeira
 * `}`. Os blocos lidos aqui são planos (nenhuma regra aninhada dentro deles).
 */
export function parseCssVariables(css: string, selector: string): Record<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`bloco \`${selector}\` não encontrado`);
  const block = css.slice(start, css.indexOf('}', start));
  const tokens: Record<string, string> = {};
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    if (name !== undefined && value !== undefined) tokens[name] = value.trim();
  }
  return tokens;
}

/** `"142.4 71.8% 29.2%"` → RGB 0–1. */
export function hslToRgb(value: string): Rgb {
  const match = /^([\d.]+)\s+([\d.]+)%\s+([\d.]+)%$/.exec(value);
  if (match === null) throw new Error(`token fora do formato \`H S% L%\`: "${value}"`);
  const h = Number(match[1]);
  const s = Number(match[2]) / 100;
  const l = Number(match[3]) / 100;

  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = h / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const m = l - c / 2;
  const sextant: Rgb[] = [
    [c, x, 0],
    [x, c, 0],
    [0, c, x],
    [0, x, c],
    [x, 0, c],
    [c, 0, x],
  ];
  const base = sextant[Math.min(5, Math.floor(hp))] ?? [0, 0, 0];
  return [base[0] + m, base[1] + m, base[2] + m];
}

/** Luminância relativa (WCAG 2.1, §relative luminance). */
export function luminance([r, g, b]: Rgb): number {
  const lin = (v: number): number => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

export function contrast(fg: Rgb, bg: Rgb): number {
  const [lighter, darker] = [luminance(fg), luminance(bg)].sort((a, b) => b - a) as [
    number,
    number,
  ];
  return (lighter + 0.05) / (darker + 0.05);
}
