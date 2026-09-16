/**
 * Validação 86e2z895j — aba Divergências Omie, antes/depois com a MESMA base.
 * SHOT=<nome> nomeia o print; o storageState é salvo em arquivo para 1 login só.
 */
import { existsSync } from 'node:fs';

import { chromium } from '@playwright/test';

const BASE = process.env.BASE_URL ?? 'http://172.31.106.28:3000';
const OUT = process.env.OUT_DIR ?? '/w/screenshots/pr-shots-86e2z895j';
const SHOT = process.env.SHOT ?? 'shot';
const STORAGE = `${OUT}/storage-state.json`;
const CLIENT_ID = '63ed307c-aac4-4456-b9a5-14ae378f6cf2';
const SESSION_ID = '2fe38045-bfbc-4702-9688-bb95affa415b';

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ...(existsSync(STORAGE) ? { storageState: STORAGE } : {}),
  });
  const page = await ctx.newPage();

  if (!existsSync(STORAGE)) {
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    await page.getByRole('textbox', { name: /e-?mail/i }).fill('gerente@padaria.com.br');
    await page.getByRole('textbox', { name: 'Senha' }).fill('Sprint6Valida!2026');
    await page.getByRole('button', { name: /entrar/i }).click();
    await page.waitForURL('**/clientes**', { timeout: 30000 });
    await ctx.storageState({ path: STORAGE });
    console.log('[ok] login feito e sessão salva');
  }

  await page.goto(
    `${BASE}/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}?tab=divergencias`,
    { waitUntil: 'networkidle' },
  );
  // Espera o DADO, não a tabela: no cold start a repopulação (ListarExtrato)
  // ainda está em voo quando o skeleton some — a data vem do banco e está
  // presente em qualquer desfecho.
  await page.getByText('05/04/2026').first().waitFor({ state: 'visible', timeout: 45000 });
  await esperar(600);

  // Evidência textual além do pixel: fornecedor/valor da 1ª linha de dados.
  const primeiraLinha = page.getByRole('row').nth(1);
  console.log(`[${SHOT}] primeira linha:`, (await primeiraLinha.innerText()).replace(/\n/g, ' | '));

  await page.screenshot({ path: `${OUT}/${SHOT}.png`, fullPage: true });
  console.log(`[fim] ${OUT}/${SHOT}.png`);
} finally {
  await browser.close();
}
