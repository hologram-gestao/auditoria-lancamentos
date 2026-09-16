/**
 * Validação visual da 86e2xmug9 contra o AMBIENTE REAL (banco auditoria_s7val,
 * Omie mockado). Roda no container Playwright; servidores no host WSL.
 *
 * 1 login só (rate limit 5/5min) — sessão reusada via storageState em memória.
 * Percorre CC (aba Anomalias, veredito + resolver com nota) e CARTÃO
 * (checkbox de lançamento, clamp longo/curto/nulo, resolvida com nota,
 * travessão, badge Informativa), em 1440px e 390px.
 */
import { chromium } from '@playwright/test';

const BASE = process.env.BASE_URL ?? 'http://172.31.106.28:3000';
const OUT = process.env.OUT_DIR ?? '/w/screenshots/pr-shots-86e2xmug9';
const CLIENT_ID = '63ed307c-aac4-4456-b9a5-14ae378f6cf2';
const CC_SESSION = 'aafa13ab-e701-4637-9178-f6653511cd5d';
const CARD_SESSION = '03cbcc24-8d39-4a6a-9984-8198c8f91b34';

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

async function abrirAnomalias(page, sessionId) {
  await page.goto(`${BASE}/clientes/${CLIENT_ID}/conciliacao/${sessionId}?tab=anomalias`, {
    waitUntil: 'networkidle',
  });
  await page.getByRole('table').first().waitFor({ state: 'visible', timeout: 30000 });
  await esperar(800); // animações/refetch
}

const browser = await chromium.launch();
try {
  // ---- login único (desktop) ----
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
  await page.getByRole('textbox', { name: /e-?mail/i }).fill('gerente@padaria.com.br');
  await page.getByRole('textbox', { name: 'Senha' }).fill('Sprint6Valida!2026');
  await page.getByRole('button', { name: /entrar/i }).click();
  await page.waitForURL('**/clientes**', { timeout: 30000 });
  const storage = await ctx.storageState();
  console.log('[ok] login feito, sessão salva');

  // ---- CC desktop: estado inicial ----
  await abrirAnomalias(page, CC_SESSION);
  await page.screenshot({ path: `${OUT}/01-cc-desktop-inicial.png`, fullPage: true });

  // veredito na primeira anomalia (fluxo real) — idempotente entre execuções:
  // se já está improcedente, marca procedente (reenviar o mesmo valor não
  // dispara request nem toast).
  const improc = page.getByRole('button', { name: /como improcedente/ }).first();
  const jaImproc = (await improc.getAttribute('aria-pressed')) === 'true';
  const alvoVeredito = jaImproc
    ? page.getByRole('button', { name: /como procedente/ }).first()
    : improc;
  await alvoVeredito.click();
  await page.getByText(/Flag marcado como (im)?procedente/).waitFor({ timeout: 15000 });
  await esperar(1200);

  // resolver a segunda com nota (fluxo real do dialog)
  await page.getByRole('button', { name: 'Marcar como resolvida' }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.waitFor({ timeout: 10000 });
  await dialog
    .getByRole('textbox')
    .fill('Conferido com a contabilidade: fornecedor correto é o do Omie.');
  await dialog.getByRole('button', { name: 'Marcar como resolvida' }).click();
  await esperar(1500);
  await page.screenshot({ path: `${OUT}/02-cc-desktop-verdito-e-resolvida.png`, fullPage: true });
  console.log('[ok] CC desktop: veredito + resolvida com nota');

  // ---- CARTÃO desktop ----
  await abrirAnomalias(page, CARD_SESSION);
  await page.screenshot({ path: `${OUT}/03-cartao-desktop.png`, fullPage: true });

  // tooltip do contexto clampado aberto por FOCO (padrão a09a7c3)
  const clampado = page.getByText(/ATACADAO SA[\s\S]*CD OSASCO/).first();
  await clampado.focus();
  await esperar(600);
  await page.screenshot({ path: `${OUT}/04-cartao-desktop-tooltip-contexto.png` });
  console.log('[ok] cartão desktop + tooltip por foco');

  // seleção de lote (checkbox) para ver a barra
  const caixas = page.getByRole('checkbox');
  await caixas.nth(1).check();
  await esperar(400);
  await page.screenshot({ path: `${OUT}/05-cartao-desktop-lote.png` });
  await page.close();

  // ---- 390px (mobile) — mesmo login via storageState ----
  const mCtx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    storageState: storage,
  });
  const mPage = await mCtx.newPage();
  await abrirAnomalias(mPage, CARD_SESSION);
  // no mobile quem rola é o <main> — leva a tabela para o viewport antes do print
  await mPage.getByRole('table').first().scrollIntoViewIfNeeded();
  await esperar(400);
  await mPage.screenshot({ path: `${OUT}/06-cartao-390.png` });
  // rola a região da tabela até o fim para ver as colunas da direita
  await mPage.evaluate(() => {
    for (const el of document.querySelectorAll('[tabindex="0"]')) {
      if (el.scrollWidth > el.clientWidth) el.scrollLeft = el.scrollWidth;
    }
  });
  await esperar(400);
  await mPage.screenshot({ path: `${OUT}/07-cartao-390-scroll-direita.png` });

  await abrirAnomalias(mPage, CC_SESSION);
  await mPage.getByRole('table').first().scrollIntoViewIfNeeded();
  await esperar(400);
  await mPage.screenshot({ path: `${OUT}/08-cc-390.png` });
  console.log('[ok] mobile 390 (cartão + CC)');
  await mCtx.close();
} finally {
  await browser.close();
}
console.log('[fim] screenshots em', OUT);
