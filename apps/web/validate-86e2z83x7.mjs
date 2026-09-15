/**
 * Validação 86e2z83x7 — botões do veredito com largura fixa igual.
 * 1 login (storageState em arquivo). Shots em light e hologram, 1440 e 390,
 * nas sessões CC (1001) e cartão (1002) do ambiente demo.
 */
import { existsSync } from 'node:fs';

import { chromium } from '@playwright/test';

const BASE = process.env.BASE_URL ?? 'http://172.31.106.28:3000';
const OUT = process.env.OUT_DIR ?? '/w/screenshots/pr-shots-86e2z83x7';
const STORAGE = `${OUT}/storage-state.json`;
const CLIENT_ID = '63ed307c-aac4-4456-b9a5-14ae378f6cf2';
const CC_SESSION = 'aafa13ab-e701-4637-9178-f6653511cd5d';
const CARD_SESSION = '03cbcc24-8d39-4a6a-9984-8198c8f91b34';

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

async function medirPar(page, rotulo) {
  const proc = await page
    .getByRole('button', { name: /como procedente/ })
    .first()
    .boundingBox();
  const improc = await page
    .getByRole('button', { name: /como improcedente/ })
    .first()
    .boundingBox();
  console.log(
    `[${rotulo}] procedente w=${proc?.width} x=${proc?.x} · improcedente w=${improc?.width} x=${improc?.x}`,
  );
}

async function abrirAnomalias(page, sessionId) {
  await page.goto(`${BASE}/clientes/${CLIENT_ID}/conciliacao/${sessionId}?tab=anomalias`, {
    waitUntil: 'networkidle',
  });
  await page
    .getByRole('button', { name: /como procedente/ })
    .first()
    .waitFor({ state: 'visible', timeout: 30000 });
  await esperar(600);
}

const browser = await chromium.launch();
try {
  // ---- login único ----
  if (!existsSync(STORAGE)) {
    const loginCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await loginCtx.newPage();
    await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' });
    await page.getByRole('textbox', { name: /e-?mail/i }).fill('gerente@padaria.com.br');
    await page.getByRole('textbox', { name: 'Senha' }).fill('Sprint6Valida!2026');
    await page.getByRole('button', { name: /entrar/i }).click();
    await page.waitForURL('**/clientes**', { timeout: 30000 });
    await loginCtx.storageState({ path: STORAGE });
    await loginCtx.close();
    console.log('[ok] login feito e sessão salva');
  }

  const cenarios = [
    { tema: 'light', width: 1440, height: 900, sessao: CC_SESSION, shot: '01-cc-light-1440' },
    { tema: 'light', width: 390, height: 844, sessao: CC_SESSION, shot: '02-cc-light-390' },
    {
      tema: 'hologram',
      width: 1440,
      height: 900,
      sessao: CC_SESSION,
      shot: '03-cc-hologram-1440',
    },
    {
      tema: 'hologram',
      width: 390,
      height: 844,
      sessao: CARD_SESSION,
      shot: '04-cartao-hologram-390',
    },
  ];

  for (const c of cenarios) {
    const ctx = await browser.newContext({
      viewport: { width: c.width, height: c.height },
      storageState: STORAGE,
    });
    const page = await ctx.newPage();
    // next-themes lê o localStorage ANTES da hidratação.
    await page.addInitScript((tema) => window.localStorage.setItem('theme', tema), c.tema);
    await abrirAnomalias(page, c.sessao);
    if (c.width < 1000) {
      // no mobile quem rola é o <main>; leva o par para o viewport
      await page
        .getByRole('button', { name: /como procedente/ })
        .first()
        .scrollIntoViewIfNeeded();
      // rola a região da tabela até a coluna do veredito
      await page.evaluate(() => {
        for (const el of document.querySelectorAll('[tabindex="0"]')) {
          if (el.scrollWidth > el.clientWidth) el.scrollLeft = el.scrollWidth * 0.62;
        }
      });
      await esperar(400);
    }
    await medirPar(page, c.shot);
    await page.screenshot({ path: `${OUT}/${c.shot}.png` });
    console.log(`[fim] ${c.shot}.png`);
    await ctx.close();
  }
} finally {
  await browser.close();
}
