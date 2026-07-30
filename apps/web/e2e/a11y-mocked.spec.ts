/**
 * DoD de a11y da Sprint 4 num BROWSER de verdade — **sem depender de Postgres,
 * API no ar ou seed**. Escrito pelo QA.
 *
 * **Por que existe, ao lado de `a11y.spec.ts`:** aquela suíte é a verificação
 * ponta a ponta e precisa de `pnpm infra:up` + API + credenciais; sem isso ela
 * faz `test.skip`. Resultado prático: o gate do DoD ("axe-core via Playwright,
 * 0 violações `critical`/`serious`") **nunca rodou** até a revisão de
 * 25/07/2026 — e quando finalmente rodou achou `color-contrast`,
 * `aria-prohibited-attr` e `aria-hidden-focus` reais, além do badge de status
 * que renderizava VAZIO (quase-branco sobre quase-branco).
 *
 * Aqui a API é interceptada no browser (`page.route`), então a suíte roda em
 * qualquer máquina/CI com `next build && next start` + Chromium. O que ela
 * mede é exatamente o que jsdom não vê: **CSS computado** (contraste real,
 * inclusive as CSS vars do tema) e a árvore de acessibilidade da página
 * montada. Dado mockado não enfraquece isso — cor e ARIA não dependem de o
 * número ter vindo do Postgres.
 *
 * Cobre as telas da sprint (R1, R2, R3, R4, R6) em desktop **e** mobile 390px,
 * e trava o contraste do badge de status por medição direta
 * (`getComputedStyle` + fórmula WCAG), porque o axe devolve `incomplete` — não
 * `violation` — quando não consegue determinar o fundo.
 *
 * **Status de execução:** rodado em Chromium real em 27/07/2026 (container
 * `mcr.microsoft.com/playwright`), nos dois projetos do `playwright.config.ts`
 * (`desktop` e `mobile`). Foi ele que pegou o `scrollable-region-focusable`
 * que a suíte só-desktop não conseguia ver. Rode com:
 *
 * ```bash
 * pnpm --filter @auditoria/web build
 * (cd apps/web/.next/standalone/apps/web && PORT=3100 node server.js &)
 * E2E_BASE_URL=http://127.0.0.1:3100 pnpm --filter @auditoria/web \
 *   exec playwright test e2e/a11y-mocked.spec.ts
 * ```
 *
 * Em container: `mcr.microsoft.com/playwright:v1.59.1-noble` já tem as libs.
 *
 * **Última execução como o CI a faz** (27/07/2026, contra a árvore commitada
 * `eb1d713` exportada com `git archive`, não contra o worktree): `Check a11y
 * spec exists` ok → `next build` exit 0 → servidor standalone respondendo em
 * `127.0.0.1:3100` → **30 passed** (15 testes × `desktop`/`mobile`) → guard
 * `expected=30 unexpected=0 skipped=0 flaky=0`.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * REGRA DO GATE (ADR-008-QA / ADR-009-QA — leia antes de acrescentar cenário):
 *
 * 1. **Teste novo só conta quando alguma esteira o executa.** Este arquivo é o
 *    único que o CI mede (`A11Y_SPEC: e2e/a11y-mocked.spec.ts`, job
 *    `web_a11y` do `.github/workflows/ci.yml`). Cenário acrescentado a
 *    `e2e/a11y.spec.ts` **não roda no CI** — aquela suíte exige Postgres + API
 *    + seed e faz `test.skip` sem eles. Ao escrever um teste, cite o job que o
 *    roda; se não existir, o deliverable é o teste **+** o executor.
 * 2. **Teste novo só conta depois de visto VERMELHO** contra o código
 *    defeituoso (mutação: reverta o arquivo de produto e rode).
 * 3. **Revisão de CI parte de `git archive <commit> | tar -x`**, nunca do
 *    worktree — untracked (lockfile, spec) existe no worktree e some no
 *    `checkout` do CI. Foi assim que os dois vermelhos desta sprint apareceram.
 * 4. **Violação ancorada em `#__next_error__` = a página crashou**, nunca
 *    defeito de a11y. Aconteceu quando o mock genérico devolveu
 *    `{data,pagination}` para `/api/v1/omie/lancamentos`, que responde **array
 *    puro** — o axe passou a medir a tela de erro do Next. Rota explícita
 *    abaixo; mantenha-a ao mexer no `fulfillApi`.
 * ────────────────────────────────────────────────────────────────────────────
 */

import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page, type Route } from '@playwright/test';

const CLIENT_ID = '11111111-1111-4111-8111-111111111111';
/**
 * Tenant ALHEIO (FRONT 05.7). O mock responde com um nome distinto de
 * propósito: se a tela renderizar qualquer coisa dele, o teste vê o vazamento.
 */
const OTHER_CLIENT_ID = '99999999-9999-4999-8999-999999999999';
const SESSION_ID = '22222222-2222-4222-8222-222222222222';

/** Impactos que reprovam o DoD. */
const BLOCKING = ['critical', 'serious'];

/**
 * Sprint 5 (R2): o payload da sessão passou a carregar `scope`/`client_id` —
 * é deles que o gating de UI (`src/lib/authz.ts`) deriva. A fixture reflete o
 * contrato real; sem `scope`, o front trataria o admin como papel sem escopo.
 */
const USER = {
  id: '33333333-3333-4333-8333-333333333333',
  email: 'admin@hologram.com.br',
  name: 'Admin QA',
  role: 'admin',
  scope: 'system',
  client_id: null,
};

/** Usuário da sessão corrente — trocado por teste nos cenários de papel (S5). */
let sessionUser: Record<string, unknown> = USER;

const CLIENT_MANAGER_USER = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  email: 'gerente@cliente-exemplo.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: CLIENT_ID,
};

const CLIENT_OPERATOR_USER = {
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  email: 'operador@cliente-exemplo.com.br',
  name: 'Operador do Cliente',
  role: 'client_operator',
  scope: 'client',
  client_id: CLIENT_ID,
};

const SYSTEM_MANAGER_USER = {
  id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  email: 'manager@hologram.com.br',
  name: 'Gerente Hologram',
  role: 'manager',
  scope: 'system',
  client_id: null,
};

/** Usuários DO tenant, devolvidos por `/clients/{id}/users` (BACK 05.5). */
const CLIENT_USERS = [
  {
    id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    name: 'Joana Prado',
    email: 'joana@cliente-exemplo.com.br',
    role: 'client_operator',
    scope: 'client',
    client_id: CLIENT_ID,
    active: true,
    created_at: '2026-07-01T12:00:00Z',
    updated_at: '2026-07-01T12:00:00Z',
  },
  {
    id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
    name: 'Rui Sales',
    email: 'rui@cliente-exemplo.com.br',
    role: 'client_manager',
    scope: 'client',
    client_id: CLIENT_ID,
    active: false,
    created_at: '2026-07-02T12:00:00Z',
    updated_at: '2026-07-02T12:00:00Z',
  },
];

/**
 * Screenshots de conferência visual (desktop + mobile, por perfil). Só saem
 * com `E2E_SHOTS=1` — o CI não precisa delas e `test-results/` é gitignored.
 */
async function shot(page: Page, name: string): Promise<void> {
  if (process.env.E2E_SHOTS !== '1') return;
  await page.screenshot({ path: `test-results/screenshots/${name}.png`, fullPage: true });
}

const ACCOUNTS = [
  {
    id: '44444444-4444-4444-8444-444444444444',
    omie_conta_id: 10,
    name: 'Cartão Itaú',
    bank_name: 'Itaú Unibanco',
    account_type: 'CR',
    synced_at: '2026-07-20T12:00:00Z',
  },
  {
    id: '55555555-5555-4555-8555-555555555555',
    omie_conta_id: 11,
    name: 'Conta Corrente Bradesco',
    bank_name: 'Bradesco',
    account_type: 'CC',
    synced_at: '2026-07-20T12:00:00Z',
  },
];

const CLIENT_DETAIL = {
  id: CLIENT_ID,
  // Fixture fictícia de propósito: nome de cliente real não entra em arquivo
  // versionado (CLAUDE.md §4.5 — razão social é dado identificável).
  name: 'Cliente Exemplo Ltda',
  active: true,
  created_at: '2026-05-01T12:00:00Z',
  updated_at: '2026-07-20T12:00:00Z',
  responsible_manager: null,
  reconciliation_count: 3,
  accounts: ACCOUNTS,
  accounts_synced_at: '2026-07-20T12:00:00Z',
};

/** Uma linha por status — é o badge que precisa estar legível nos três casos. */
function session(over: Record<string, unknown> = {}) {
  return {
    id: SESSION_ID,
    omie_conta_id: 10,
    account_type: 'CR',
    reference_month: '2026-06-01',
    status: 'reviewing',
    created_at: '2026-07-01T12:00:00Z',
    total_file_entries: 30,
    conciliated_count: 25,
    sem_omie_count: 3,
    omie_sem_arquivo_count: 2,
    anomaly_count: 1,
    error_message: null,
    error_code: null,
    total_files: 3,
    ...over,
  };
}

const SESSIONS = [
  session(),
  session({ id: '22222222-2222-4222-8222-222222222223', status: 'processing' }),
  session({
    id: '22222222-2222-4222-8222-222222222224',
    status: 'error',
    error_code: 'ADL-PARSE-LIMIT',
  }),
];

const DETAIL = {
  session_id: SESSION_ID,
  client_id: CLIENT_ID,
  omie_conta_id: 10,
  account_type: 'CR',
  reference_month: '2026-06-01',
  status: 'reviewing',
  total_file_entries: 30,
  conciliated_count: 25,
  sem_omie_count: 3,
  omie_sem_arquivo_count: 2,
  anomaly_count: 1,
  error_message: null,
  error_code: null,
  balance_start: '1000.00',
  balance_end_file: '1500.00',
  balance_end_omie: '1500.00',
  balance_difference: '0.00',
  total_files: 3,
};

const PAGINATION = { page: 1, pageSize: 20, total: 3, totalPages: 1 };

/**
 * Uma movimentação `conciliado` — é o único `situation` cujo menu de ações
 * oferece "Trocar lançamento", que é a porta do modal do follow-up 86e2gy1n0.
 */
const FILE_ENTRIES = [
  {
    id: '99999999-9999-4999-8999-999999999991',
    transaction_date: '2026-06-10',
    description: 'Pagamento fornecedor X',
    amount: '-150.50',
    balance: '1000.00',
    situation: 'conciliado',
    user_action: null,
    user_note: null,
    omie_lancamento_id: 9001,
  },
];

const OMIE_CANDIDATES = [
  {
    omie_id: 9001,
    transaction_date: '2026-06-11',
    description: 'NF 123 - Fornecedor X',
    supplier: 'Fornecedor X LTDA',
    category: 'Serviços',
    amount: '-150.50',
    status: 'Conciliado',
  },
  {
    omie_id: 9002,
    transaction_date: '2026-06-12',
    description: 'NF 124 - Fornecedor Y',
    supplier: 'Fornecedor Y ME',
    category: 'Materiais',
    amount: '-150.50',
    status: 'Previsto',
  },
];

const NOTIFICATIONS = [
  {
    id: '66666666-6666-4666-8666-666666666666',
    session_id: SESSION_ID,
    client_id: CLIENT_ID,
    tipo: 'processada',
    omie_conta_id: 10,
    reference_month: '2026-06-01',
    error_code: null,
    read_at: null,
    created_at: '2026-07-26T12:00:00Z',
  },
  {
    id: '77777777-7777-4777-8777-777777777777',
    session_id: SESSION_ID,
    client_id: CLIENT_ID,
    tipo: 'erro',
    omie_conta_id: 11,
    reference_month: '2026-06-01',
    error_code: 'ADL-PARSE-LIMIT',
    read_at: null,
    created_at: '2026-07-26T11:00:00Z',
  },
];

/** Backend inteiro em memória, resolvido por padrão de URL. */
async function fulfillApi(route: Route): Promise<void> {
  const url = new URL(route.request().url());
  const path = url.pathname;
  const json = async (data: unknown): Promise<void> =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data }),
    });

  if (path === '/api/v1/auth/refresh') return json({ user: sessionUser });
  // Usuários DO tenant (BACK 05.5). Rota literal antes de qualquer fallback.
  if (path === `/api/v1/clients/${CLIENT_ID}/users`) {
    return json({
      data: CLIENT_USERS,
      pagination: { page: 1, pageSize: 20, total: CLIENT_USERS.length, totalPages: 1 },
    });
  }
  if (path === '/api/v1/notifications/unread-count') return json({ unread: 2 });
  if (path === '/api/v1/notifications') return json({ data: NOTIFICATIONS, pagination: PAGINATION });
  if (path.endsWith('/read')) return json({ already_read: false, read_at: '2026-07-26T13:00:00Z' });
  if (path === '/api/v1/clients') return json({ data: [CLIENT_DETAIL], pagination: PAGINATION });
  if (path === `/api/v1/clients/${CLIENT_ID}`) return json(CLIENT_DETAIL);
  // O tenant alheio RESPONDE 200 de propósito: se o front pedir e renderizar,
  // o vazamento aparece no teste. Um 403 aqui esconderia o defeito atrás do
  // backend, e o que se verifica no front é que ele nem chega a pedir.
  if (path === `/api/v1/clients/${OTHER_CLIENT_ID}`) {
    return json({ ...CLIENT_DETAIL, id: OTHER_CLIENT_ID, name: 'Cliente de Outro Tenant' });
  }
  if (path === `/api/v1/clients/${CLIENT_ID}/sync-accounts`) return json(CLIENT_DETAIL);
  if (path === `/api/v1/clients/${CLIENT_ID}/reconciliations`) {
    return json({ data: SESSIONS, pagination: PAGINATION });
  }
  if (path === `/api/v1/reconciliations/${SESSION_ID}`) return json(DETAIL);
  if (path === `/api/v1/reconciliations/${SESSION_ID}/files`) {
    return json({
      session_id: SESSION_ID,
      total_files: 3,
      files: [1, 2, 3].map((n) => ({
        file_id: `88888888-8888-4888-8888-00000000000${n}`,
        filename: `fatura-parte-${n}.pdf`,
        status: 'processed',
        error_code: null,
        entry_count: 10,
        created_at: '2026-07-01T12:00:00Z',
      })),
    });
  }
  if (path === `/api/v1/reconciliations/${SESSION_ID}/status`) {
    return json({ session_id: SESSION_ID, status: 'reviewing', error_code: null });
  }
  if (path === `/api/v1/reconciliations/${SESSION_ID}/file-entries`) {
    return json({
      data: FILE_ENTRIES,
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    });
  }
  if (path === `/api/v1/reconciliations/${SESSION_ID}/available-omie-entries`) {
    return json(OMIE_CANDIDATES);
  }
  // ⚠️ Devolve ARRAY puro (`apiGet<OmieLancamentoItem[]>`), não `{data,pagination}`.
  // O fallback paginado do fim faz `omieLookupQuery.data?.forEach` explodir com
  // "e.forEach is not a function" e a página inteira cai no error boundary do
  // Next — foi o que aconteceu na 1ª execução deste teste.
  if (path === '/api/v1/omie/lancamentos') {
    return json(
      OMIE_CANDIDATES.map((c) => ({
        omie_id: c.omie_id,
        transaction_date: c.transaction_date,
        description: c.description,
        supplier: c.supplier,
        category: c.category,
        amount: c.amount,
        status: c.status,
      })),
    );
  }
  if (path.startsWith('/api/v1/usage-events')) return json({ recorded: true });
  // Listas das abas de revisão (movimentações / Omie / anomalias) e o resto.
  return json({ data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } });
}

async function analyze(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  const blocking = results.violations.filter((v) => BLOCKING.includes(v.impact ?? ''));
  expect(
    blocking,
    `${label}: ${blocking
      .map((v) => `${v.impact}/${v.id} [${v.nodes.map((n) => n.target.join(' ')).join(' | ')}]`)
      .join(', ')}`,
  ).toHaveLength(0);
}

/**
 * Contraste MEDIDO do elemento (cor computada do texto vs. primeiro ancestral
 * com fundo opaco) — o axe devolve `incomplete` neste caso e `incomplete` não
 * entra em `violations`. Foi assim que o badge vazio passou por todos os gates.
 */
async function measuredContrast(page: Page, selector: string): Promise<number> {
  return page.$eval(selector, (el) => {
    const parse = (c: string): [number, number, number, number] => {
      const [r = 0, g = 0, b = 0, a = 1] = (c.match(/[\d.]+/g) ?? []).map(Number);
      return [r, g, b, a];
    };
    const lum = ([r, g, b]: number[]): number => {
      const f = (u = 0): number => {
        const v = u / 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const fg = parse(getComputedStyle(el).color);
    let bg: [number, number, number, number] = [255, 255, 255, 1];
    for (let node: Element | null = el; node !== null; node = node.parentElement) {
      const c = parse(getComputedStyle(node).backgroundColor);
      if (c[3] > 0) {
        bg = c;
        break;
      }
    }
    const l1 = lum(fg);
    const l2 = lum(bg);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  });
}

test.beforeEach(async ({ page, context, baseURL }) => {
  // Volta ao admin: os cenários de papel da S5 trocam este estado de módulo.
  sessionUser = USER;
  await page.route('**/api/v1/**', fulfillApi);
  // O `src/middleware.ts` decide navegação só pela PRESENÇA do cookie
  // `access_token` (a validação real é do backend). Um valor qualquer basta
  // para o middleware deixar passar — quem responde pelos dados é o `route`.
  await context.addCookies([
    { name: 'access_token', value: 'e2e-mock', url: baseURL ?? 'http://localhost:3000' },
  ]);
});

const VIEWPORTS = [
  { label: 'desktop', size: { width: 1440, height: 900 } },
  { label: 'mobile 390px', size: { width: 390, height: 844 } },
];

for (const vp of VIEWPORTS) {
  test.describe(`${vp.label}`, () => {
    test.use({ viewport: vp.size });

    test('Lista de Conciliações (R1)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}`);
      await expect(page.getByRole('heading', { name: 'Conciliações' })).toBeVisible();
      await expect(page.getByText('Processada').first()).toBeVisible();
      await analyze(page, `lista de conciliações (${vp.label})`);
    });

    test('Detalhe da conciliação (R3)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);
      await expect(page.getByRole('region', { name: 'Totalizadores da conciliação' })).toBeVisible();
      await analyze(page, `detalhe da conciliação (${vp.label})`);
    });

    test('Contas Bancárias (R6)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}/contas`);
      await expect(page.getByRole('heading', { name: 'Contas Bancárias' })).toBeVisible();
      await analyze(page, `contas bancárias (${vp.label})`);
    });

    test('Gaveta de criação (R2)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}`);
      await page.getByRole('button', { name: 'Criar conciliação' }).first().click();
      await expect(page.getByRole('dialog')).toBeVisible();
      await analyze(page, `gaveta de criação (${vp.label})`);
    });

    test('Sino de notificações aberto (R4)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}`);
      await page.getByRole('button', { name: /Notificações/ }).click();
      await expect(page.getByRole('menu')).toBeVisible();
      await analyze(page, `sino de notificações (${vp.label})`);
    });

    /**
     * Follow-up 86e2gy1n0. A tela é PRÉ-EXISTENTE (S12/S13) e não estava sob
     * nenhuma suíte de a11y — foi por isso que a seleção só-mouse sobreviveu.
     *
     * ⚠️ Nenhuma regra do axe reprova "onClick em `<tr>` não-focável", e o
     * `scrollable-region-focusable` que disparava aqui **silenciou por efeito
     * colateral** do fix do `ui/table.tsx` (b43f0f5) sem que o defeito fosse
     * corrigido. Por isso o gate desta tela é COMPORTAMENTAL: operar o modal
     * só com teclado. O axe fica como rede para o resto.
     */
    test('Modal "Trocar lançamento" — operável só por teclado (86e2gy1n0)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);
      await page.getByRole('button', { name: 'Abrir ações' }).first().click();
      await page.getByRole('menuitem', { name: 'Trocar lançamento' }).click();

      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      await analyze(page, `modal trocar lançamento (${vp.label})`);

      const primeiro = dialog.getByRole('radio').first();
      await expect(primeiro).toBeVisible();

      // Tab até o candidato: prova a operabilidade que faltava (WCAG 2.1.1).
      let alcancado = false;
      for (let i = 0; i < 40 && !alcancado; i++) {
        await page.keyboard.press('Tab');
        alcancado = await primeiro.evaluate((el) => el === document.activeElement);
      }
      expect(alcancado, 'o radio do candidato precisa ser alcançável por Tab').toBe(true);

      // Enter é a tecla que o operador tenta primeiro; Espaço é a nativa.
      await page.keyboard.press('Enter');
      await expect(primeiro).toBeChecked();
      await expect(dialog.getByRole('button', { name: 'Confirmar' })).toBeEnabled();

      // `aria-selected` em `role="row"` fora de grid/treegrid não pode voltar.
      expect(await page.locator('tr[aria-selected]').count()).toBe(0);

      // Com um candidato selecionado o estado visual muda — reanalisa.
      await analyze(page, `modal trocar lançamento, selecionado (${vp.label})`);
    });

    test('badge de status é LEGÍVEL (contraste medido ≥ 4.5:1)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}`);
      const badge = page.getByText('Processada').first();
      await expect(badge).toBeVisible();
      // O rótulo tem que existir como texto visível — badge vazio não é badge.
      await expect(badge).toHaveText(/Processada/);
      const ratio = await measuredContrast(page, 'text=Processada >> nth=0');
      expect(ratio, `badge "Processada" (${vp.label}): ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    });
  });
}

/**
 * Sprint 5 / R5 (FRONT 05.6) — tela "Usuários" do cliente, por PAPEL.
 *
 * O que só um browser mede aqui: o CSS computado das badges de papel/status
 * (tokens `info`/`success`/`destructive` do tema) e a árvore de acessibilidade
 * da gaveta e do `alertdialog` montados de verdade, em desktop e em 390px.
 *
 * O gating é presentacional — a autoridade é o backend. O que se verifica é
 * que a UI **não oferece** o que o servidor nega, e que o deep link do papel
 * sem permissão degrada com mensagem em português + caminho de volta, em vez
 * de tela branca.
 */
for (const vp of VIEWPORTS) {
  test.describe(`Usuários do cliente — ${vp.label}`, () => {
    test.use({ viewport: vp.size });

    test('gerente do cliente vê a lista do tenant (R5)', async ({ page }) => {
      sessionUser = CLIENT_MANAGER_USER;
      await page.goto(`/clientes/${CLIENT_ID}/usuarios`);

      await expect(page.getByRole('heading', { name: 'Usuários', level: 2 })).toBeVisible();
      await expect(page.getByRole('row', { name: /Joana Prado/ })).toBeVisible();
      await expect(page.getByText('Operador do cliente').first()).toBeVisible();
      await expect(page.getByText('Inativo').first()).toBeVisible();
      await shot(page, `client-users-gerente-${vp.label.replace(/\s+/g, '-')}`);
      await analyze(page, `usuários do cliente — gerente (${vp.label})`);
    });

    test('gaveta de criação: papel restrito e senha com toggle (R5)', async ({ page }) => {
      sessionUser = CLIENT_MANAGER_USER;
      await page.goto(`/clientes/${CLIENT_ID}/usuarios`);
      await page.getByRole('button', { name: 'Novo usuário' }).first().click();

      const drawer = page.getByRole('dialog');
      await expect(drawer).toBeVisible();
      await expect(drawer.getByLabel('Senha inicial')).toHaveAttribute('type', 'password');
      await shot(page, `client-users-gaveta-${vp.label.replace(/\s+/g, '-')}`);
      await analyze(page, `gaveta de usuário do cliente (${vp.label})`);

      // O select de papel não pode oferecer papel de SISTEMA.
      await drawer.getByRole('combobox', { name: /Papel/ }).click();
      const options = page.getByRole('option');
      await expect(options).toHaveCount(2);
      await expect(options.nth(0)).toHaveText('Gerente do cliente');
      await expect(options.nth(1)).toHaveText('Operador do cliente');
    });

    test('desativar passa por alertdialog com Cancelar à esquerda (R5)', async ({ page }) => {
      sessionUser = CLIENT_MANAGER_USER;
      await page.goto(`/clientes/${CLIENT_ID}/usuarios`);
      await page.getByRole('button', { name: 'Desativar Joana Prado' }).click();

      const confirm = page.getByRole('alertdialog');
      await expect(confirm).toBeVisible();
      // Foco inicial no Cancelar: `Enter` reflexo não pode desativar ninguém.
      await expect(confirm.getByRole('button', { name: 'Cancelar' })).toBeFocused();
      await shot(page, `client-users-confirmacao-${vp.label.replace(/\s+/g, '-')}`);
      await analyze(page, `confirmação de desativar (${vp.label})`);
    });

    test('operador do cliente não vê a tela nem o item de menu (R4)', async ({ page }) => {
      sessionUser = CLIENT_OPERATOR_USER;
      await page.goto(`/clientes/${CLIENT_ID}/usuarios`);

      // `getByRole('alert')` sozinho é ambíguo: o Next injeta o
      // `#__next-route-announcer__`, que também é `role="alert"`.
      await expect(
        page.getByRole('heading', { name: 'Você não tem acesso a esta página' }),
      ).toBeVisible();
      await expect(page.getByRole('button', { name: 'Novo usuário' })).toHaveCount(0);
      // Item de menu ausente — a UI não mostra o que a rota bloqueia.
      await expect(
        page.getByRole('navigation', { name: 'Seções do cliente' }).getByText('Usuários'),
      ).toHaveCount(0);
      await shot(page, `client-users-operador-${vp.label.replace(/\s+/g, '-')}`);
      await analyze(page, `usuários do cliente — operador negado (${vp.label})`);
    });

    test('gerente do SISTEMA opera a carteira mas não gere usuários do tenant (R4)', async ({
      page,
    }) => {
      sessionUser = SYSTEM_MANAGER_USER;
      await page.goto(`/clientes/${CLIENT_ID}/usuarios`);
      // `getByRole('alert')` sozinho é ambíguo: o Next injeta o
      // `#__next-route-announcer__`, que também é `role="alert"`.
      await expect(
        page.getByRole('heading', { name: 'Você não tem acesso a esta página' }),
      ).toBeVisible();
      await expect(
        page.getByRole('navigation', { name: 'Seções do cliente' }).getByText('Usuários'),
      ).toHaveCount(0);
    });
  });
}

/**
 * Sprint 5 / R4 (FRONT 05.7) — gating de navegação e ações por papel.
 *
 * Cada perfil abre a MESMA rota e a UI mostra só o que a matriz permite. A
 * conferência é por screenshot em desktop **e** mobile 390px nos QUATRO perfis,
 * porque `grep` prova "existe em algum lugar", não "em todos os contextos".
 *
 * A barra lateral do shell é `hidden md:block` — por isso as asserções sobre
 * ela só correm no viewport desktop; a nav DENTRO do cliente é verificada nos
 * dois.
 */
const PROFILES = [
  { key: 'admin', user: () => USER, systemArea: true, clientUsers: true, editClient: true },
  {
    key: 'manager-sistema',
    user: () => SYSTEM_MANAGER_USER,
    systemArea: true,
    clientUsers: false,
    editClient: false,
  },
  {
    key: 'gerente-cliente',
    user: () => CLIENT_MANAGER_USER,
    systemArea: false,
    clientUsers: true,
    editClient: false,
  },
  {
    key: 'operador-cliente',
    user: () => CLIENT_OPERATOR_USER,
    systemArea: false,
    clientUsers: false,
    editClient: false,
  },
] as const;

for (const vp of VIEWPORTS) {
  const slug = vp.label.replace(/\s+/g, '-');
  test.describe(`Gating por perfil — ${vp.label}`, () => {
    test.use({ viewport: vp.size });

    for (const profile of PROFILES) {
      test(`${profile.key}: navegação e ações conforme a matriz (R4)`, async ({ page }) => {
        sessionUser = profile.user();
        await page.goto(`/clientes/${CLIENT_ID}`);
        await expect(page.getByRole('heading', { name: 'Conciliações', level: 2 })).toBeVisible();

        const clientNav = page.getByRole('navigation', { name: 'Seções do cliente' });
        // Conciliação / contas / painel: liberados para os QUATRO papéis.
        await expect(clientNav.getByText('Conciliações')).toBeVisible();
        await expect(clientNav.getByText('Contas Bancárias')).toBeVisible();
        // "Usuários" só para quem administra usuários do tenant.
        await expect(clientNav.getByText('Usuários')).toHaveCount(profile.clientUsers ? 1 : 0);
        // §9 (editar dados do cliente, credenciais Omie) é só do admin.
        await expect(page.getByRole('button', { name: 'Editar cliente' })).toHaveCount(
          profile.editClient ? 1 : 0,
        );
        // Criar conciliação vale para todo papel (matriz: ✅ nas 4 colunas).
        await expect(page.getByRole('button', { name: 'Criar conciliação' })).toBeVisible();

        if (vp.label === 'desktop') {
          const sidebar = page.getByRole('navigation').first();
          // Lista GLOBAL de clientes e configurações do sistema: só equipe Hologram.
          await expect(sidebar.getByRole('link', { name: 'Clientes' })).toHaveCount(
            profile.systemArea ? 1 : 0,
          );
          await expect(page.getByRole('link', { name: 'Tipos de Anomalia' })).toHaveCount(
            profile.key === 'admin' ? 1 : 0,
          );
        }

        // O chrome compartilhado (header) precisa caber nos DOIS viewports: em
        // 390px o "Sair" estava sendo cortado fora da tela.
        const sair = page.getByRole('button', { name: 'Sair' });
        await expect(sair).toBeVisible();
        const box = await sair.boundingBox();
        expect(box, 'o botão Sair precisa ter caixa visível').not.toBeNull();
        expect(
          (box?.x ?? 0) + (box?.width ?? 0),
          `"Sair" cortado fora da viewport (${vp.label})`,
        ).toBeLessThanOrEqual(vp.size.width);

        await shot(page, `gating-${profile.key}-${slug}`);
        await analyze(page, `gating ${profile.key} (${vp.label})`);
      });
    }

    test('deep link em configurações do sistema degrada em português (R4)', async ({ page }) => {
      sessionUser = CLIENT_OPERATOR_USER;
      await page.goto('/configuracoes/usuarios');

      await expect(
        page.getByRole('heading', { name: 'Você não tem acesso a este recurso' }),
      ).toBeVisible();
      // Caminho de volta é a CASA do papel, não a lista global (que ele também
      // não vê) — senão o "voltar" cai num segundo beco sem saída.
      await expect(page.getByRole('link', { name: 'Voltar para o início' })).toHaveAttribute(
        'href',
        `/clientes/${CLIENT_ID}`,
      );
      // Nada do tenant alheio, e nenhum vazamento do erro do framework.
      await expect(page.locator('#__next_error__')).toHaveCount(0);
      await shot(page, `deeplink-configuracoes-negado-${slug}`);
      await analyze(page, `deep link em configurações negado (${vp.label})`);
    });

    test('deep link em OUTRO tenant degrada sem mostrar dado do alvo (R4)', async ({ page }) => {
      sessionUser = CLIENT_MANAGER_USER;
      await page.goto(`/clientes/${OTHER_CLIENT_ID}`);

      await expect(
        page.getByRole('heading', { name: 'Você não tem acesso a este recurso' }),
      ).toBeVisible();
      // O nome do outro cliente NÃO pode aparecer — nem vindo de uma resposta
      // 403/404 renderizada por engano.
      await expect(page.getByText('Cliente de Outro Tenant')).toHaveCount(0);
      await expect(page.locator('#__next_error__')).toHaveCount(0);
      await shot(page, `deeplink-outro-tenant-${slug}`);
      await analyze(page, `deep link cross-tenant negado (${vp.label})`);
    });

    test('usuário de tenant não para na lista global — vai para a casa dele', async ({ page }) => {
      sessionUser = CLIENT_OPERATOR_USER;
      await page.goto('/clientes');
      await page.waitForURL(`**/clientes/${CLIENT_ID}`);
      await expect(page.getByRole('heading', { name: 'Conciliações', level: 2 })).toBeVisible();
    });
  });
}

test('Login (defeito 86e2ggm7r: senha sem nome acessível)', async ({ page, context }) => {
  // Com cookie o middleware manda para `/clientes` — a tela de login só existe
  // deslogado.
  await context.clearCookies();
  await page.goto('/login');
  // O input de senha precisa ser alcançável PELO RÓTULO — era o que faltava.
  await expect(page.getByLabel('Senha', { exact: true })).toHaveAttribute('type', 'password');
  await analyze(page, 'login');
});
