// Figuras do manual (86e3gqfmj; recapturadas na 1.2, 86e3mz74x), pela tela real: next dev em
// 3031 → API em 8031, banco novo com os dados de `dados_demo.py`. Roda DENTRO do container
// mcr.microsoft.com/playwright:v1.59.1-noble:
//   LANG=pt_BR.UTF-8 WEB_DIR=<repo>/apps/web SHOTS_OUT=<pasta> \
//     node shots.mjs <padariaId> <horizonteId> [nomes]
// O Chromium COMPLETO (`channel: 'chromium'`), não o headless shell: só ele traz o pacote pt-BR.
// 1440x900 em DPR 2, tema Hologram FORÇADO no localStorage (todas as figuras são do Hologram,
// mesmo se o padrão do produto mudar). Um login só.
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';

const require = createRequire(`${process.env.WEB_DIR}/package.json`);
const { chromium } = require('@playwright/test');

const [PADARIA, HORIZONTE, ONLY] = process.argv.slice(2);
const BASE = process.env.SHOTS_BASE ?? 'http://127.0.0.1:3031';
const OUT = process.env.SHOTS_OUT;
mkdirSync(OUT, { recursive: true });

// O texto do <input type="month"> segue o idioma do PROCESSO, não o `locale` do contexto.
const browser = await chromium.launch({ channel: 'chromium', args: ['--lang=pt-BR'] });
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  reducedMotion: 'reduce',
  // Sem isto o Chromium do container escreve "August 2026" no campo de competência.
  locale: 'pt-BR',
  timezoneId: 'America/Sao_Paulo',
});
await ctx.addInitScript(() => window.localStorage.setItem('theme', 'hologram'));
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
  // O ponteiro fica onde estava o botão Entrar, em cima de um card do painel: o hover do
  // card elevado (86e3h57a5) acenderia na figura.
  await page.mouse.move(0, 0);
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
  // O painel (86e3k1q54) é mais alto que 900: janela alta para caber carteira e fluxo.
  await page.setViewportSize({ width: 1440, height: 1700 });
  await go(`${BASE}/clientes/${PADARIA}/painel`);
  await page.getByText('Atividade').first().waitFor();
  await settle();
  await page.screenshot({ path: `${OUT}/painel.png` });
  await page.setViewportSize({ width: 1440, height: 900 });
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
