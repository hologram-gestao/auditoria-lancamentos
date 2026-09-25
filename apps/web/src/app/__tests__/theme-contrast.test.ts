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

const CSS = readFileSync(path.resolve(__dirname, '../globals.css'), 'utf8');

/** Mínimo do WCAG 2.1 AA para texto normal (< 18.66px bold / < 24px). */
const AA_NORMAL_TEXT = 4.5;

type Rgb = [number, number, number];

function parseTokens(selector: ':root' | '.dark' | '.hologram'): Record<string, string> {
  // O bloco vai do seletor até a primeira `}` — os dois blocos de tema são
  // planos (não há regra aninhada dentro deles).
  const start = CSS.indexOf(`${selector} {`);
  if (start === -1) throw new Error(`bloco \`${selector}\` não encontrado em globals.css`);
  const block = CSS.slice(start, CSS.indexOf('}', start));
  const tokens: Record<string, string> = {};
  for (const [, name, value] of block.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    if (name !== undefined && value !== undefined) tokens[name] = value.trim();
  }
  return tokens;
}

/** `"142.4 71.8% 29.2%"` (o formato que o Tailwind embrulha em `hsl()`) → RGB 0–1. */
function hslToRgb(value: string): Rgb {
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
function luminance([r, g, b]: Rgb): number {
  const lin = (v: number): number => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(fg: Rgb, bg: Rgb): number {
  const [lighter, darker] = [luminance(fg), luminance(bg)].sort((a, b) => b - a) as [
    number,
    number,
  ];
  return (lighter + 0.05) / (darker + 0.05);
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
