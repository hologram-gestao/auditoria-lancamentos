/**
 * Regressão de LEGIBILIDADE do badge de status da conciliação (Sprint 4, QA).
 *
 * Por que este teste existe, separado do `a11y.spec.ts`: o badge chegou à
 * revisão **invisível** — `bg-success-muted` (verde claríssimo) com
 * `text-success-foreground` (`0 0% 98%`, quase branco), contraste ≈ 1.05:1 — e
 * NENHUM portão automático pegou:
 *
 *   - `tsc`/`lint` não enxergam cor;
 *   - o axe de jsdom (`src/test/a11y.ts`) desliga `color-contrast` de propósito
 *     (jsdom não faz layout nem resolve CSS vars);
 *   - o axe **no browser** também não reprovou: quando não consegue determinar
 *     o fundo com certeza ele devolve `incomplete`, e `incomplete` não entra em
 *     `violations`. Um gate que só olha `violations` fica verde sobre texto
 *     invisível.
 *
 * Então aqui a contagem é feita à mão: lê `getComputedStyle` do badge real,
 * calcula o contraste WCAG e exige AA para texto pequeno (4.5:1). É o único
 * lugar da esteira que reprova "o rótulo está lá no DOM, mas ninguém lê".
 *
 * O badge é o artefato central da sprint ("Em processamento" → "Processada" /
 * "Erro"): se ele não é legível, a feature não existe para quem opera.
 *
 * Pré-requisitos: os mesmos do `a11y.spec.ts` (ver `e2e/README.md`) —
 * `E2E_PASSWORD` e `E2E_CLIENT_ID` de um cliente com ao menos uma conciliação.
 */
import { expect, test, type Page } from '@playwright/test';

const EMAIL = process.env.E2E_EMAIL ?? 'admin@hologram.com.br';
const PASSWORD = process.env.E2E_PASSWORD ?? '';
const CLIENT_ID = process.env.E2E_CLIENT_ID ?? '';

/** Mínimo AA para texto pequeno (o badge é `text-xs`). */
const MIN_RATIO = 4.5;

/** Luminância relativa (WCAG 2.x) a partir de um `rgb()`/`rgba()` computado. */
function luminance(css: string): number {
  const [r, g, b] = (css.match(/[\d.]+/g) ?? ['0', '0', '0']).slice(0, 3).map(Number) as [
    number,
    number,
    number,
  ];
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(fg: string, bg: string): number {
  const [a, b] = [luminance(fg), luminance(bg)].sort((x, y) => y - x) as [number, number];
  return (a + 0.05) / (b + 0.05);
}

/**
 * Cor de fundo EFETIVA: o badge pode ser translúcido (`bg-destructive/10`) ou
 * transparente, e nesse caso quem pinta é um ancestral. Subir a árvore até achar
 * um fundo opaco é o que o próprio axe faz — e é justamente onde ele desiste e
 * devolve `incomplete`.
 */
async function effectiveBackground(page: Page, selector: string): Promise<string> {
  return page.evaluate((sel) => {
    let el: Element | null = document.querySelector(sel);
    while (el) {
      const bg = getComputedStyle(el).backgroundColor;
      const parts = (bg.match(/[\d.]+/g) ?? []).map(Number);
      const alpha = parts.length === 4 ? (parts[3] as number) : 1;
      if (alpha === 1 && bg !== 'transparent') return bg;
      el = el.parentElement;
    }
    return 'rgb(255, 255, 255)';
  }, selector);
}

test.beforeEach(async ({ page }) => {
  test.skip(PASSWORD === '' || CLIENT_ID === '', 'defina E2E_PASSWORD e E2E_CLIENT_ID');
  await page.goto('/login');
  await page.getByLabel('E-mail').pressSequentially(EMAIL);
  // Seletor estrutural de propósito: `getByLabel('Senha')` resolve para o botão
  // "Mostrar senha" enquanto o input de senha não tiver nome acessível.
  await page.locator('input[type="password"]').pressSequentially(PASSWORD);
  await page.getByRole('button', { name: /entrar/i }).click();
  await page.waitForURL('**/clientes');
});

test('badge de status é legível na Lista de Conciliações', async ({ page }) => {
  await page.goto(`/clientes/${CLIENT_ID}`);
  await expect(page.getByRole('heading', { name: 'Conciliações' })).toBeVisible();

  const badge = page
    .locator('span.rounded-full')
    .filter({ hasText: /Em processamento|Processada|Erro/ })
    .first();
  await expect(badge, 'nenhum badge de status na lista — seed sem conciliação?').toBeVisible();

  const label = (await badge.textContent())?.trim() ?? '';
  expect(label, 'o badge renderizou sem rótulo').not.toBe('');

  const fg = await badge.evaluate((el) => getComputedStyle(el).color);
  const bg = await effectiveBackground(page, 'span.rounded-full');
  const ratio = contrast(fg, bg);

  expect(
    ratio,
    `badge "${label}": contraste ${ratio.toFixed(2)}:1 (${fg} sobre ${bg}) — mínimo AA é ${MIN_RATIO}:1. ` +
      'Sobre fundo `-muted` o texto tem de ser a cor FORTE (`text-success`/`text-info`); ' +
      '`--x-foreground` é o par do `--x` SÓLIDO.',
  ).toBeGreaterThanOrEqual(MIN_RATIO);
});

test('badge de status é legível no Detalhe da conciliação', async ({ page }) => {
  await page.goto(`/clientes/${CLIENT_ID}`);
  await page.getByRole('heading', { name: 'Conciliações' }).waitFor();
  await page.locator('a[href*="/conciliacao/"]').first().click();
  await page.waitForURL('**/conciliacao/**');

  const badge = page
    .locator('span.rounded-full')
    .filter({ hasText: /Em processamento|Processada|Erro/ })
    .first();
  await expect(badge).toBeVisible();

  const label = (await badge.textContent())?.trim() ?? '';
  const fg = await badge.evaluate((el) => getComputedStyle(el).color);
  const bg = await effectiveBackground(page, 'span.rounded-full');
  const ratio = contrast(fg, bg);

  expect(
    ratio,
    `badge "${label}" no detalhe: contraste ${ratio.toFixed(2)}:1 (${fg} sobre ${bg}) — mínimo AA é ${MIN_RATIO}:1.`,
  ).toBeGreaterThanOrEqual(MIN_RATIO);
});
