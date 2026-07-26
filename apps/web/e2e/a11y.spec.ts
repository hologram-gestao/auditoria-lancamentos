/**
 * DoD de acessibilidade da Sprint 4 — axe-core via Playwright, **0 violações
 * `critical`/`serious`** nas telas tocadas.
 *
 * Telas cobertas:
 *   - Lista de Conciliações do cliente (R1) — incl. gaveta de criação (R2);
 *   - Contas Bancárias (R6);
 *   - Detalhe da conciliação (R3);
 *   - Sino de notificações no header (R4).
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

test('Sino de notificações', async ({ page }) => {
  await page.goto('/clientes');
  await page.getByRole('button', { name: /Notificações/ }).click();
  await analyze(page, 'sino de notificações');
});
