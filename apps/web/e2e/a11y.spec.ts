/**
 * DoD de acessibilidade da Sprint 4 — axe-core via Playwright, **0 violações
 * `critical`/`serious`** nas telas tocadas.
 *
 * Telas cobertas:
 *   - Lista de Conciliações do cliente (R1) — incl. gaveta de criação (R2);
 *   - Contas Bancárias (R6);
 *   - Detalhe da conciliação (R3);
 *   - Sino de notificações no header (R4);
 *   - Modal "Trocar lançamento" (tela pré-existente S12/S13, incluída pelo
 *     follow-up 86e2gy1n0 — ela não estava sob nenhuma suíte de a11y).
 *
 * ⚠️ Requer ambiente completo no ar (ver `e2e/README.md`) — não roda no
 * `pnpm test`. A checagem equivalente em jsdom, que roda em toda esteira, está
 * em `src/test/a11y.ts` e nos testes de componente.
 */
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

const EMAIL = process.env.E2E_EMAIL ?? 'admin@hologram.com.br';
const PASSWORD = process.env.E2E_PASSWORD ?? '';
/** UUID de um cliente com pelo menos uma conciliação processada. */
const CLIENT_ID = process.env.E2E_CLIENT_ID ?? '';

/** Impactos que reprovam o DoD. */
const BLOCKING = ['critical', 'serious'];

async function analyze(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  const blocking = results.violations.filter((v) => BLOCKING.includes(v.impact ?? ''));
  expect(
    blocking,
    `${label}: ${blocking.map((v) => `${v.impact}/${v.id}`).join(', ')}`,
  ).toHaveLength(0);
}

test.beforeEach(async ({ page }) => {
  test.skip(PASSWORD === '' || CLIENT_ID === '', 'defina E2E_PASSWORD e E2E_CLIENT_ID');
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(EMAIL);
  // `exact: true`: o botão de olho se chama "Mostrar senha" e o casamento
  // padrão do `getByLabel` é por SUBSTRING — sem isto, "Senha" resolveria para
  // dois elementos e o strict mode do Playwright reprovaria.
  await page.getByLabel('Senha', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: /entrar/i }).click();
  await page.waitForURL('**/clientes');
});

test('Login', async ({ page }) => {
  // O `beforeEach` já autenticou; voltar ao /login exercita a tela em si. Ela
  // é a porta de entrada de TODA a suíte: quando o campo de senha ficou sem
  // nome acessível, a suíte inteira parou no `beforeEach` (defeito 86e2ggm7r).
  await page.goto('/login');
  await expect(page.getByLabel('Senha', { exact: true })).toHaveAttribute('type', 'password');
  await analyze(page, 'login');
});

test('Lista de Conciliações do cliente', async ({ page }) => {
  await page.goto(`/clientes/${CLIENT_ID}`);
  await expect(page.getByRole('heading', { name: 'Conciliações' })).toBeVisible();
  await analyze(page, 'lista de conciliações');
});

test('Gaveta de criação de conciliação', async ({ page }) => {
  await page.goto(`/clientes/${CLIENT_ID}`);
  await page.getByRole('button', { name: 'Criar conciliação' }).first().click();
  await expect(page.getByRole('dialog', { name: /Criar conciliação/ })).toBeVisible();
  await analyze(page, 'gaveta de criação');
});

test('Contas Bancárias', async ({ page }) => {
  await page.goto(`/clientes/${CLIENT_ID}/contas`);
  await expect(page.getByRole('heading', { name: 'Contas Bancárias' })).toBeVisible();
  await analyze(page, 'contas bancárias');
});

test('Detalhe da conciliação', async ({ page }) => {
  await page.goto(`/clientes/${CLIENT_ID}`);
  await page.getByRole('link', { name: /Abrir conciliação/ }).first().click();
  await page.waitForURL('**/conciliacao/**');
  await analyze(page, 'detalhe da conciliação');
});

/**
 * Modal "Trocar lançamento" (follow-up 86e2gy1n0).
 *
 * Além do axe, este teste **opera o modal só com teclado**: era exatamente esse
 * o defeito (linhas com `onClick` e nada focável), e nenhuma regra do axe o
 * detecta. Ele para antes de acionar o "Confirmar" de propósito — pressioná-lo
 * gravaria um `PATCH` no ambiente de teste. O que precisa ser provado é que o
 * controle é alcançável, selecionável e que a ação primária habilita; a
 * gravação em si já é coberta pelo teste de componente.
 */
test('Modal "Trocar lançamento" — a11y e seleção por teclado', async ({ page }) => {
  await page.goto(`/clientes/${CLIENT_ID}`);
  await page.getByRole('link', { name: /Abrir conciliação/ }).first().click();
  await page.waitForURL('**/conciliacao/**');

  // "Trocar lançamento" só existe em linha `conciliado`; percorre os menus até
  // achar uma (em vez de assumir que a 1ª linha da seed é conciliada).
  const menus = page.getByRole('button', { name: 'Abrir ações' });
  const total = Math.min(await menus.count(), 10);
  let aberto = false;
  for (let i = 0; i < total && !aberto; i++) {
    await menus.nth(i).click();
    const item = page.getByRole('menuitem', { name: 'Trocar lançamento' });
    if (await item.isVisible().catch(() => false)) {
      await item.click();
      aberto = true;
    } else {
      await page.keyboard.press('Escape');
    }
  }
  test.skip(!aberto, 'nenhuma movimentação conciliada na seed — nada a trocar');

  const dialog = page.getByRole('dialog', { name: /Selecionar lançamento Omie correto/ });
  await expect(dialog).toBeVisible();
  await analyze(page, 'modal trocar lançamento');

  const radios = dialog.getByRole('radio');
  test.skip((await radios.count()) === 0, 'sem candidatos Omie disponíveis na seed');

  const primeiro = radios.first();
  let alcancado = false;
  for (let i = 0; i < 40 && !alcancado; i++) {
    await page.keyboard.press('Tab');
    alcancado = await primeiro.evaluate((el) => el === document.activeElement);
  }
  expect(alcancado, 'o radio do candidato precisa ser alcançável por Tab').toBe(true);

  await page.keyboard.press('Enter');
  await expect(primeiro).toBeChecked();

  const confirmar = dialog.getByRole('button', { name: 'Confirmar' });
  await expect(confirmar).toBeEnabled();
  for (let i = 0; i < 40; i++) {
    if (await confirmar.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press('Tab');
  }
  await expect(confirmar).toBeFocused();

  // Com um candidato selecionado o axe roda de novo: o estado selecionado é o
  // que antes carregava o `aria-selected` inválido na linha.
  await analyze(page, 'modal trocar lançamento (candidato selecionado)');
});

test('Sino de notificações', async ({ page }) => {
  await page.goto('/clientes');
  await page.getByRole('button', { name: /Notificações/ }).click();
  await analyze(page, 'sino de notificações');
});
