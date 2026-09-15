/**
 * Validação — tema Hologram como PADRÃO (decisão do Pedro, 25/08, sem task).
 * Contexto novo = localStorage vazio = o que um usuário novo vê.
 */
import { chromium } from '@playwright/test';

const BASE = process.env.BASE_URL ?? 'http://172.31.106.28:3000';
const OUT = process.env.OUT_DIR ?? '/w/screenshots/hologram-default-theme';

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

async function classeDoHtml(page) {
  return page.evaluate(() => document.documentElement.className);
}

const browser = await chromium.launch();
try {
  // 1. Usuário NOVO (sem localStorage): login já nasce Hologram, 1440 e 390.
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await esperar(500);
  console.log('[01] html.class =', await classeDoHtml(page));
  await page.screenshot({ path: `${OUT}/01-login-default-1440.png` });

  const mCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const mPage = await mCtx.newPage();
  await mPage.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await esperar(500);
  console.log('[02] html.class =', await classeDoHtml(mPage));
  await mPage.screenshot({ path: `${OUT}/02-login-default-390.png` });
  await mCtx.close();

  // 2. Único login: dentro do app o padrão segue Hologram e o toggle marca
  //    "Hologram" como ativo mesmo SEM nada no localStorage.
  await page.getByRole('textbox', { name: /e-?mail/i }).fill('gerente@padaria.com.br');
  await page.getByRole('textbox', { name: 'Senha' }).fill('Sprint6Valida!2026');
  await page.getByRole('button', { name: /entrar/i }).click();
  await page.waitForURL('**/clientes**', { timeout: 30000 });
  await esperar(800);
  console.log('[03] html.class =', await classeDoHtml(page));
  await page.screenshot({ path: `${OUT}/03-app-default-1440.png` });

  await page.getByRole('button', { name: 'Alterar tema' }).click();
  await esperar(400);
  const ativo = await page
    .getByRole('menuitemradio')
    .evaluateAll((els) =>
      els.filter((e) => e.getAttribute('aria-checked') === 'true').map((e) => e.textContent),
    );
  console.log('[04] opção ativa no toggle =', JSON.stringify(ativo));
  await page.screenshot({ path: `${OUT}/04-toggle-hologram-ativo.png` });

  // 3. Escolha manual SOBREVIVE: muda para Claro, F5, continua claro.
  await page.getByRole('menuitemradio', { name: 'Claro' }).click();
  await esperar(600);
  await page.reload({ waitUntil: 'networkidle' });
  await esperar(600);
  console.log('[05] html.class pós-escolha+F5 =', await classeDoHtml(page));
  await page.screenshot({ path: `${OUT}/05-escolha-claro-sobrevive-f5.png` });
} finally {
  await browser.close();
}
console.log('[fim]', OUT);
