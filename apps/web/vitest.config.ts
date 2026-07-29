/**
 * Vitest config — primeira esteira de testes do `apps/web` (S15 FRONT 11.2).
 *
 * Decisões:
 *   - `environment: jsdom` — necessário para RTL e qualquer teste que toque
 *     DOM/clipboard.
 *   - Sem `@vitejs/plugin-react` instalado: vitest 2 transforma JSX via esbuild
 *     com `jsx: 'automatic'`, suficiente para nossos componentes funcionais.
 *   - Alias `@/*` espelha `tsconfig.json`.
 *   - Setup file injeta jest-dom matchers em todos os testes.
 */
import path from 'node:path';

import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: {
    jsx: 'automatic',
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    css: false,
    // `e2e/` é do Playwright (precisa de browser + app no ar). O include
    // default do vitest pega `*.spec.ts` — sem esta exclusão, `pnpm test`
    // tentaria rodar a suíte E2E em jsdom e quebraria.
    exclude: ['**/node_modules/**', '**/dist/**', '**/.next/**', 'e2e/**'],
  },
});
