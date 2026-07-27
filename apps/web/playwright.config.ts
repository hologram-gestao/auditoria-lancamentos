import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright — suíte E2E/a11y do `apps/web`.
 *
 * A esteira de a11y do DoD (axe-core, 0 violações `critical`/`serious`) precisa
 * de um BROWSER de verdade: contraste, foco e ordem de leitura não existem em
 * jsdom. A contraparte em jsdom (`src/test/a11y.ts`, roda no `pnpm test`) pega
 * a maioria das regressões no componente; esta aqui é a verificação na tela
 * montada, autenticada, com CSS aplicado.
 *
 * Pré-requisitos para rodar (ver `e2e/README.md`):
 *   - API + Postgres no ar e seed aplicado;
 *   - `E2E_BASE_URL` (default http://localhost:3000) e as credenciais de teste;
 *   - `pnpm exec playwright install chromium` uma vez, para baixar o browser.
 */
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:3000';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL,
    trace: 'on-first-retry',
  },
  /**
   * Desktop **e** mobile. O defeito 86e2gwuxn (`scrollable-region-focusable`,
   * SERIOUS) só existia em viewport estreito: em 390px a tabela transborda e o
   * wrapper vira região rolável. Rodando só `Desktop Chrome`, nada transbordava
   * e a suíte passava com o defeito no lugar — a cobertura mobile é o que torna
   * o gate honesto para o design mobile-first.
   */
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 5'] } },
  ],
});
