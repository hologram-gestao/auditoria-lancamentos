// Figuras do manual (task 86e3gqfmj), pela tela real: next dev em 3031 → API em 8031, banco
// `adl_manual`. Roda DENTRO do container mcr.microsoft.com/playwright:v1.59.1-noble com
// --network host:  node shots.mjs <padariaId> <horizonteId>
// 1440x900, tema Hologram (o padrão: localStorage vazio). Um login só.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';

const require = createRequire('/home/phaos93/auditoria-landing/apps/web/package.json');
const { chromium } = require('@playwright/test');

const [PADARIA, HORIZONTE, ONLY] = process.argv.slice(2);
const BASE = process.env.SHOTS_BASE ?? 'http://127.0.0.1:3031';
const OUT = '/home/phaos93/auditoria-manual/screenshots/pr-shots-86e3gqfmj';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
page.setDefaultTimeout(60_000);

await page.goto(`${BASE}/login`);
// O botão só habilita com o formulário válido; preencher antes da hidratação perde os valores.
await page.waitForLoadState('networkidle').catch(() => undefined);
await page.waitForTimeout(4000);
await page.getByLabel('E-mail').fill('platform@hologram.com.br');
await page.getByLabel('Senha', { exact: true }).fill('dev-only-change-me');
await page.getByRole('button', { name: 'Entrar' }).click();
await page.waitForURL((u) => !u.pathname.startsWith('/login'));

async function settle() {
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await page.waitForTimeout(1200);
}
const want = (name) => !ONLY || ONLY.split(',').includes(name);
async function go(url) {
  for (let i = 0; ; i++) {
    try { await page.goto(url); return; } catch (e) {
      if (i >= 3) throw e;
      console.log('goto retry', i + 1, String(e).slice(0, 80));
      await page.waitForTimeout(5000);
    }
  }
}

if (want('painel')) {
  await go(`${BASE}/clientes/${PADARIA}/painel`);
  await page.getByText('Origens de dado').waitFor();
  await settle();
  await page.screenshot({ path: `${OUT}/painel.png` });
}

if (want('recebiveis')) {
  await go(`${BASE}/clientes/${PADARIA}/carteira?view=relatorio`);
  await page.getByText('Inadimplência real').first().waitFor();
  await settle();
  await page.screenshot({ path: `${OUT}/recebiveis.png` });
}

if (want('plano-contabil')) {
  await go(`${BASE}/clientes/${HORIZONTE}/plano-contabil`);
  await page.getByRole('cell', { name: '649', exact: true }).waitFor();
  await settle();
  await page.screenshot({ path: `${OUT}/plano-contabil.png` });
}

if (want('previa') || want('arquivo-contabil')) {
  await go(
    `${BASE}/clientes/${HORIZONTE}/de-para?destination=conta_contabil&view=previa&competence=2026-08`,
  );
  const section = page.getByTestId('accounting-file-section');
  await section.getByText('Gerações nesta competência').waitFor();
  await settle();
  if (want('previa')) {
    // Mesmo enquadramento da figura antiga: as abas Decisões / Prévia no alto da janela.
    await page.getByRole('tab', { name: 'Prévia da competência' }).evaluate((el) => {
      el.scrollIntoView({ block: 'start' });
      const main = document.querySelector('main');
      if (main) main.scrollBy(0, -40);
      else window.scrollBy(0, -40);
    });
    await page.waitForTimeout(600);
    await page.screenshot({ path: `${OUT}/previa.png` });
  }
  if (want('arquivo-contabil')) {
    await section.scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);
    await section.screenshot({ path: `${OUT}/arquivo-contabil.png` });
  }
}

if (want('layouts')) {
  // Só 2 layouts: janela mais baixa para a tabela não deixar um vão vazio na figura.
  await page.setViewportSize({ width: 1440, height: 560 });
  await go(`${BASE}/configuracoes/layouts-exportacao`);
  await page.getByText('Layout Domínio padrão').first().waitFor();
  await settle();
  await page.screenshot({ path: `${OUT}/layouts.png` });
  await page.setViewportSize({ width: 1440, height: 900 });
}

if (want('mapeamento')) {
  await go(`${BASE}/clientes/${HORIZONTE}/origem-arquivo`);
  await page.getByText('Mapeamento de colunas').first().waitFor();
  await settle();
  await page.screenshot({ path: `${OUT}/mapeamento-tela.png` });
  const btn = page.getByRole('button', { name: /mapeamento/i }).first();
  await btn.click();
  await page.getByRole('dialog').waitFor();
  await settle();
  // Mesmo enquadramento da figura antiga: a gaveta rolada até a convenção de sinal.
  await page.getByRole('dialog').evaluate((d) => {
    const sc = [...d.querySelectorAll('*')].find(
      (el) => el.scrollHeight > el.clientHeight + 20 && getComputedStyle(el).overflowY !== 'visible',
    );
    if (sc) sc.scrollTop = sc.scrollHeight;
  });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/mapeamento.png` });
}

await browser.close();
console.log('ok');
