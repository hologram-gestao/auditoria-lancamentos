/**
 * Trava de regressão de CONTRASTE nos tokens do tema.
 *
 * **Por que este teste existe:** a camada de a11y que roda no `pnpm test` é o
 * `axe.run()` em jsdom (`src/test/a11y.ts`), e ela desliga `color-contrast` de
 * propósito — jsdom não calcula cor computada (ADR-004-FE-03). Resultado: um
 * par fundo/texto ilegível passava batido aqui e só reprovava no Playwright, no
 * fim da esteira. Foi exatamente o que aconteceu com o badge de status
 * (`bg-success-muted` + `text-success-foreground` ≈ 1.05:1 — pílula verde
 * VAZIA na lista e no detalhe).
 *
 * Este teste não substitui o axe em browser (ele não vê o que a UI de fato
 * combina); ele trava a CAMADA DE BAIXO: os pares de token que o design-system
 * declara como válidos precisam passar o AA de 4.5:1 (texto normal). Se alguém
 * clarear um token, o vermelho aparece aqui, em milissegundos, e não três
 * telas depois.
 *
 * A fonte é o `globals.css` de verdade (parseado), não uma cópia dos valores —
 * uma cópia sairia de sincronia no primeiro ajuste de paleta.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { AA_NORMAL_TEXT, contrast, hslToRgb, parseCssVariables, type Rgb } from '@/test/contrast';

const CSS = readFileSync(path.resolve(__dirname, '../globals.css'), 'utf8');

function parseTokens(selector: ':root' | '.dark' | '.hologram'): Record<string, string> {
  return parseCssVariables(CSS, selector);
}

/**
 * Pares que a UI de fato usa, por tema.
 *
 * A regra do design-system: `-foreground` é o texto do token SÓLIDO; sobre a
 * variante `-muted` o texto é o SÓLIDO. Os dois sentidos estão cobertos.
 */
const PAIRS: ReadonlyArray<{ text: string; bg: string; where: string }> = [
  // Badge de status da conciliação (lista + detalhe) — o defeito de origem.
  // São TAMBÉM os quatro pares do `<Toaster>` (`app/providers.tsx`): o toast
  // deixou de usar a paleta `richColors` do Sonner, que media 4.25:1 no verde.
  { text: 'success', bg: 'success-muted', where: 'badge "Processada" · toast de sucesso' },
  { text: 'info', bg: 'info-muted', where: 'badge "Em processamento" · toast informativo' },
  { text: 'warning', bg: 'warning-muted', where: 'banner de atenção · toast de atenção' },
  { text: 'destructive', bg: 'destructive-muted', where: 'toast de erro' },
  // Texto/ícone semântico sobre o fundo da página e do card (ambos brancos no
  // claro, mas o token é a fonte da verdade).
  { text: 'success', bg: 'background', where: 'contadores "conciliados"' },
  { text: 'warning', bg: 'background', where: 'contadores "sem Omie"' },
  { text: 'info', bg: 'background', where: 'links/ícones informativos' },
  { text: 'destructive', bg: 'background', where: 'erro em 14px, botões Excluir/Cancelar' },
  { text: 'destructive', bg: 'card', where: 'erro dentro de card (detalhe, painel de arquivos)' },
  { text: 'muted-foreground', bg: 'background', where: 'texto secundário' },
  { text: 'muted-foreground', bg: 'muted', where: 'aba INATIVA do TabsList' },
  // Item ativo do menu e hover de dropdown/select — o tint da marca (86e2ukrc9).
  { text: 'accent-foreground', bg: 'accent', where: 'item ativo do sidebar · hover de menu' },
  // Texto sobre os preenchimentos sólidos.
  { text: 'destructive-foreground', bg: 'destructive', where: 'botão destrutivo, badge do sino' },
  // O estado de HOVER é um par próprio, e faltava (86e36ed1d): enquanto ele
  // era `bg-destructive/90`, a cor medida era uma MISTURA com a superfície —
  // não um token, logo intravável aqui. Passou despercebido até o axe medir
  // o botão com o ponteiro parado em cima: 3,95:1 no escuro. Agora é sólido.
  {
    text: 'destructive-foreground',
    bg: 'destructive-hover',
    where: 'botão/badge destrutivo em hover',
  },
  { text: 'primary-foreground', bg: 'primary', where: 'botão primário' },
  // Verde da marca (86e3h1h75): o botão `variant="brand"` da landing e do login,
  // em repouso e em hover (token sólido, como o destrutivo).
  { text: 'brand-foreground', bg: 'brand', where: 'botão brand (landing, login)' },
  { text: 'brand-foreground', bg: 'brand-hover', where: 'botão brand em hover' },
  { text: 'success-foreground', bg: 'success', where: 'preenchimento de sucesso' },
  { text: 'warning-foreground', bg: 'warning', where: 'preenchimento de atenção' },
  { text: 'info-foreground', bg: 'info', where: 'preenchimento informativo' },
];

describe.each(['root', 'dark', 'hologram'] as const)(
  'tokens do tema (%s) — contraste AA',
  (theme) => {
    const tokens = parseTokens(
      theme === 'root' ? ':root' : theme === 'dark' ? '.dark' : '.hologram',
    );
    const rgb = (name: string): Rgb => {
      const value = tokens[name];
      if (value === undefined) throw new Error(`token \`--${name}\` não existe no tema ${theme}`);
      return hslToRgb(value);
    };

    it.each(PAIRS)('$text sobre $bg ($where) passa 4.5:1', ({ text, bg }) => {
      expect(contrast(rgb(text), rgb(bg))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    });

    // Não há mais caso para `bg-destructive/10` (86e3dxund). Ele compunha os 10%
    // sobre `background` e PASSAVA nos três temas enquanto a tela reprovava: o
    // badge vive em linha de tabela, e em hover (`hover:bg-muted/50`) o que fica
    // embaixo é outro — 4,23:1 no escuro e 4,44:1 no Hologram, medido no e2e.
    // Fundo com alfa depende da superfície de baixo, e este teste não sabe qual
    // é. Por isso fundo destrutivo de badge ou de hover é `destructive-muted`
    // (opaco, travado no par "toast de erro" acima), nunca `/N`.
  },
);

/**
 * O verde da marca (86e3h1h75; era o `--lp-brand` da landing, 86e3h0xcr). É a cor da
 * MARCA, não do tema: o mesmo valor nos três blocos. Como cor de primeiro plano ele só
 * aparece nas páginas públicas, que fixam o tema Hologram, então os pares "verde sobre
 * a superfície" são medidos só contra o bloco `.hologram`.
 */
describe('verde da marca (--brand)', () => {
  const blocks = {
    root: parseTokens(':root'),
    dark: parseTokens('.dark'),
    hologram: parseTokens('.hologram'),
  };
  const rgbOf = (tokens: Record<string, string>, name: string): Rgb => {
    const value = tokens[name];
    if (value === undefined) throw new Error(`token \`--${name}\` não existe`);
    return hslToRgb(value);
  };
  const brand = rgbOf(blocks.hologram, 'brand');

  it.each(['brand', 'brand-foreground', 'brand-hover'])(
    '`--%s` tem o mesmo valor nos três temas',
    (name) => {
      expect(blocks.dark[name]).toBe(blocks.root[name]);
      expect(blocks.hologram[name]).toBe(blocks.root[name]);
    },
  );

  it('é o #05d1bf amostrado do site da Hologram (hsl 175 95% 42%)', () => {
    const [r, g, b] = brand.map((channel) => Math.round(channel * 255));
    // A conversão de `175 95% 42%` cai a 1 unidade do pixel (5, 209, 191).
    expect(Math.abs((r ?? 0) - 5)).toBeLessThanOrEqual(1);
    expect(Math.abs((g ?? 0) - 209)).toBeLessThanOrEqual(1);
    expect(Math.abs((b ?? 0) - 191)).toBeLessThanOrEqual(1);
  });

  it('o hover é o verde com 10 % de preto (`color-mix(in srgb, verde 90%, black)`)', () => {
    const hover = rgbOf(blocks.root, 'brand-hover').map((c) => Math.round(c * 255));
    const mix = brand.map((c) => Math.round(c * 0.9 * 255));
    for (const [indice, canal] of hover.entries()) {
      expect(Math.abs(canal - (mix[indice] ?? 0))).toBeLessThanOrEqual(2);
    }
  });

  it('o texto sobre o verde é a cópia literal do navy da marca (`--primary` do tema claro)', () => {
    expect(blocks.root['brand-foreground']).toBe(blocks.root['primary']);
  });

  it.each([
    ['verde sobre o fundo Hologram (check dos bullets, linha do passo)', 'background'],
    ['verde sobre o card Hologram', 'card'],
  ])('%s passa 4.5:1', (_where, bg) => {
    expect(contrast(brand, rgbOf(blocks.hologram, bg))).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
  });

  it('branco (o `--primary` do Hologram) sobre o verde REPROVA: nunca texto branco no verde', () => {
    expect(contrast(rgbOf(blocks.hologram, 'primary'), brand)).toBeLessThan(AA_NORMAL_TEXT);
  });
});
