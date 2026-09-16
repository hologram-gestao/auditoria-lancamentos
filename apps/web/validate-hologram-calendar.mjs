/**
 * Validação — ícone do calendário claro no tema Hologram (color-scheme: dark).
 * Compara com o tema escuro (mesmo comportamento) e com o claro (ícone escuro,
 * correto sobre fundo branco). 1 login.
 */
import { chromium } from '@playwright/test';

const BASE = process.env.BASE_URL ?? 'http://172.31.106.28:3000';
const OUT = process.env.OUT_DIR ?? '/w/screenshots/hologram-default-theme';
const CLIENT_ID = '63ed307c-aac4-4456-b9a5-14ae378f6cf2';

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await page.getByRole('textbox', { name: /e-?mail/i }).fill('gerente@padaria.com.br');
  await page.getByRole('textbox', { name: 'Senha' }).fill('Sprint6Valida!2026');
  await page.getByRole('button', { name: /entrar/i }).click();
  await page.waitForURL('**/clientes**', { timeout: 30000 });

  for (const tema of ['hologram', 'dark', 'light']) {
    await page.evaluate((t) => window.localStorage.setItem('theme', t), tema);
    // A lista de conciliações (com o filtro de mês) é a HOME do cliente —
    // `conciliacao/` só tem as subrotas de sessão (404 sem page.tsx próprio).
    await page.goto(`${BASE}/clientes/${CLIENT_ID}`, { waitUntil: 'networkidle' });
    await esperar(800);
    const campo = page.getByLabel(/Mês de referência/i).first();
    await campo.waitFor({ state: 'visible', timeout: 15000 });
    console.log(
      `[${tema}] html.class =`,
      await page.evaluate(() => document.documentElement.className),
      '· color-scheme =',
      await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme),
    );
    // close-up do campo (o ícone nativo é pequeno) + tela inteira
    await campo.screenshot({ path: `${OUT}/06-mes-referencia-${tema}-closeup.png` });
    await page.screenshot({ path: `${OUT}/07-conciliacoes-${tema}.png` });
  }
} finally {
  await browser.close();
}
console.log('[fim]', OUT);
