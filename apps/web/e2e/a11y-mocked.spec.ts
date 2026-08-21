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
 * **Sprint 6 (03/08/2026) — MEDIDO em Chromium real.** A FRONT 06.6 acrescentou
 * o bloco "Glossário do cliente" e a FRONT 06.7 os cenários da revisão (selo,
 * veredito, teclado, erro do servidor). O `playwright install --with-deps`
 * continua indisponível no sandbox (precisa de apt/root) e o Chromium local não
 * sobe (`libnspr4.so`), mas o **container resolve**: `docker` existe nesta
 * máquina e a imagem `mcr.microsoft.com/playwright:v1.59.1-noble` está em cache.
 * É o caminho que o QA usou para reprovar a 1ª entrega desta task, e é o que
 * vale daqui pra frente — a afirmação anterior ("não há `docker` nesta distro
 * WSL") estava ERRADA e mandava o próximo agent não medir.
 *
 * Execução contra o build standalone com a correção do toast:
 * **`expected=118 unexpected=0 skipped=0 flaky=0`** (59 testes × `desktop`/`mobile`).
 *
 * **Visto vermelho antes (ADR-008-QA), duas mutações:**
 * - `<Toaster richColors>` de volta → `expected=114 unexpected=4`,
 *   `serious/color-contrast` em `div[data-title=""]`, `#008a2e` sobre `#ecfdf3`
 *   = **4.25:1** — o mesmo achado da reprovação do QA.
 * - par do toast de ERRO trocado para `text-destructive-foreground` (branco
 *   sobre quase-branco) → os 4 cenários de erro falham com contraste medido
 *   **1.048**. Essa mutação **passa** pelo `analyze()` sozinho (o toast de erro
 *   já saiu da tela quando o axe roda), e é por isso que aqueles cenários
 *   carregam `measuredContrast` além do axe.
 *
 * Comando:
 *
 * ```bash
 * INTERNAL_API_URL=http://127.0.0.1:8000 pnpm --filter @auditoria/web build
 * cp -r apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static
 * docker run --rm --network none -v "$PWD:/w" -w /w/apps/web \
 *   -e E2E_BASE_URL=http://127.0.0.1:3100 -e HOME=/tmp -e CI=1 \
 *   mcr.microsoft.com/playwright:v1.59.1-noble bash -lc '
 *     PORT=3100 HOSTNAME=127.0.0.1 NODE_ENV=production \
 *       node /w/apps/web/.next/standalone/apps/web/server.js & sleep 5
 *     ./node_modules/.bin/playwright test e2e/a11y-mocked.spec.ts --retries=0'
 * ```
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
import { expect, test, type Locator, type Page, type Route } from '@playwright/test';

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

/**
 * Defeito 86e2u4nxg — estado MUTÁVEL, resetado no `beforeEach` como
 * `sessionUsedGlossary`. Com `true` a lista devolve conciliações demais para
 * caber na altura da tela, que é a condição do defeito: o container dos cards
 * encolhe abaixo do conteúdo e, sem rolagem própria, o conteúdo vaza por baixo
 * da barra de paginação (opaca). Com poucas conciliações nada disso aparece —
 * é por isso que os cenários anteriores nunca o pegaram.
 */
let listOverflows = false;

/** 12 linhas: transborda com folga em 900px de altura e em 390×844. */
const OVERFLOW_SESSIONS = Array.from({ length: 12 }, (_, i) =>
  session({ id: `22222222-2222-4222-8222-3000000000${String(i).padStart(2, '0')}` }),
);

/**
 * Defeito 86e2uca1d — mesmo estado mutável, para as TRÊS telas de tabela.
 *
 * O `<Table>` embrulha o conteúdo num wrapper rolável, mas com altura de
 * CONTEÚDO: ele rola no horizontal e nunca no vertical. Com linhas suficientes,
 * a tabela vaza do `min-h-0 flex-1` e a barra de paginação (opaca) cobre o que
 * vazou — exatamente como acontecia na Lista de Conciliações. 20 linhas
 * transbordam com folga em 1440×900.
 */
let tableListsOverflow = false;

const MANY_ACCOUNTS = Array.from({ length: 20 }, (_, i) => ({
  id: `44444444-4444-4444-8444-4000000000${String(i).padStart(2, '0')}`,
  omie_conta_id: 100 + i,
  name: `Conta ${String(i).padStart(2, '0')} do Banco Exemplo`,
  bank_name: 'Banco Exemplo',
  account_type: i % 2 === 0 ? 'CC' : 'CR',
  synced_at: '2026-07-20T12:00:00Z',
}));

const MANY_CLIENT_USERS = Array.from({ length: 20 }, (_, i) => ({
  id: `dddddddd-dddd-4ddd-8ddd-d00000000d${String(i).padStart(2, '0')}`,
  name: `Pessoa ${String(i).padStart(2, '0')}`,
  email: `pessoa${i}@cliente-exemplo.com.br`,
  role: i % 2 === 0 ? 'client_operator' : 'client_manager',
  scope: 'client',
  client_id: CLIENT_ID,
  active: i % 3 !== 0,
  created_at: '2026-07-01T12:00:00Z',
  updated_at: '2026-07-01T12:00:00Z',
}));

const MANY_GLOSSARY_ENTRIES = Array.from({ length: 20 }, (_, i) => ({
  id: `ffffffff-ffff-4fff-8fff-f00000000f${String(i).padStart(2, '0')}`,
  kind: (['categoria', 'regra', 'fornecedor'] as const)[i % 3],
  code: i % 3 === 0 ? `3.1.${String(i).padStart(2, '0')}` : null,
  name: `Entrada ${String(i).padStart(2, '0')} do glossário`,
  description: `Descrição de uso da entrada ${i}.`,
  decryptFailed: false,
}));

const DETAIL = {
  session_id: SESSION_ID,
  client_id: CLIENT_ID,
  omie_conta_id: 10,
  account_type: 'CR',
  reference_month: '2026-06-01',
  status: 'reviewing',
  total_file_entries: 30,
  conciliated_count: 25,
  // 86e2u513b — o card explica a soma ("inclui N com data divergente");
  // com >0 o subtexto novo entra nas telas medidas pelo axe.
  conciliated_divergent_count: 2,
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
  // 86e2u513f — somas da sessão inteira, computadas no backend. Sem eles o
  // Resumo renderizaria "R$ NaN" (Number(undefined)) em qualquer cenário que
  // abra a aba.
  credits_total: '350.00',
  debits_total: '1850.00',
  card_charges_total: '42.90',
  anomalies_critical: 1,
  anomalies_moderate: 0,
  anomalies_info: 0,
  anomalies_resolved: 0,
};

/**
 * Sprint 6 / R4 — estado MUTÁVEL por teste, resetado no `beforeEach` junto do
 * `sessionUser`. Fica fora do `DETAIL` de propósito: com `false` como default,
 * todos os cenários anteriores à Sprint 6 continuam medindo exatamente a mesma
 * tela (o selo não aparece), e só os testes do selo ligam a chave.
 */
let sessionUsedGlossary = false;

/**
 * Sprint 7 / R1 — a sessão vira CARTÃO só nos cenários de lançamento.
 *
 * Fica fora do `DETAIL` pelo mesmo motivo do selo: com `null`, todos os
 * cenários anteriores medem exatamente a mesma tela (sem coluna de seleção,
 * sem ação de lançamento). O valor é o do CONTRATO (`credit_card`), que é o
 * que o front compara — o `'CR'` do `DETAIL` é o código cru do Omie e nunca
 * ligou o modo cartão nesta suíte.
 */
let sessionAccountType: string | null = null;

/**
 * Linhas `sem_omie` de uma fatura de cartão: são as únicas lançáveis. A
 * terceira é IGNORADA de propósito — é ela que prova, no browser, que a ação
 * fica indisponível (e a caixa de seleção também) sem sumir da tela.
 */
const CARD_FILE_ENTRIES = [
  {
    id: '99999999-9999-4999-8999-999999999801',
    transaction_date: '2026-06-03',
    description: 'Posto Shell 1234',
    amount: '-150.50',
    balance: null,
    situation: 'sem_omie',
    user_action: null,
    user_note: null,
    omie_lancamento_id: null,
  },
  {
    id: '99999999-9999-4999-8999-999999999802',
    transaction_date: '2026-06-07',
    description: 'Assinatura de software',
    amount: '-89.90',
    balance: null,
    situation: 'sem_omie',
    user_action: null,
    user_note: null,
    omie_lancamento_id: null,
  },
  {
    id: '99999999-9999-4999-8999-999999999803',
    transaction_date: '2026-06-09',
    description: 'Compra ignorada na revisão',
    amount: '-12.00',
    balance: null,
    situation: 'ignorado',
    user_action: 'ignore',
    user_note: null,
    omie_lancamento_id: null,
  },
];

/**
 * Uma anomalia da Camada 1 (o único tipo que aceita veredito) e uma
 * estrutural. `reviewVerdict` é mutável para o PATCH do teste refletir na
 * lista — é assim que se verifica "a lista reflete a mudança sem reload".
 */
let reviewVerdict: string | null = null;

/**
 * Caminho de FALHA do PATCH do veredito. Existe para o gate medir o toast de
 * ERRO, não só o de sucesso: os dois usam pares de token diferentes
 * (`destructive/destructive-muted` × `success/success-muted`) e o de sucesso já
 * reprovou uma vez — afirmar que o outro passa sem medir é o que a reprovação
 * anterior proibiu.
 */
let patchAnomalyFails = false;

function anomalies() {
  return [
    {
      id: 'aaaa1111-aaaa-4aaa-8aaa-aaaaaaaa1111',
      anomaly_type: {
        id: 'tttt1111-tttt-4ttt-8ttt-tttttttt1111',
        code: 'qualificacao_suspeita',
        name: 'Classificação suspeita',
        severity: 'moderate',
      },
      detected_by: 'ai',
      resolved: false,
      review_verdict: reviewVerdict,
      context: 'IOF classificado como juros.',
      resolution_note: null,
      created_at: '2026-07-01T12:00:00Z',
      related_file_entry: null,
      related_omie_entry: null,
    },
    {
      id: 'aaaa2222-aaaa-4aaa-8aaa-aaaaaaaa2222',
      anomaly_type: {
        id: 'tttt2222-tttt-4ttt-8ttt-tttttttt2222',
        code: 'saldo_divergente',
        name: 'Saldo divergente',
        severity: 'critical',
      },
      detected_by: 'ai',
      resolved: false,
      review_verdict: null,
      context: null,
      resolution_note: null,
      created_at: '2026-07-01T11:00:00Z',
      related_file_entry: null,
      related_omie_entry: null,
    },
  ];
}

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

/**
 * Glossário do tenant (Sprint 6 / BACK 06.3). Envelope REAL da rota:
 * `{ data: { entries, version }, pagination }` — o `data` é um OBJETO com a
 * versão dentro, não o array. O fallback genérico do fim devolveria
 * `{data: [], pagination}` e `data.data.entries` seria `undefined`: a listagem
 * cairia no error boundary e o axe passaria a medir a tela de erro (foi
 * exatamente o que aconteceu com `/omie/lancamentos` em 27/07). Por isso a rota
 * é EXPLÍCITA aqui — mantenha-a ao mexer no `fulfillApi`.
 */
const GLOSSARY_ENTRIES = [
  {
    id: 'ffffffff-ffff-4fff-8fff-000000000001',
    kind: 'categoria',
    code: '3.1.02',
    name: 'Taxas bancárias',
    description: 'Tarifas do banco. Nunca classificar como juros.',
    decryptFailed: false,
  },
  {
    id: 'ffffffff-ffff-4fff-8fff-000000000002',
    kind: 'regra',
    code: null,
    name: 'IOF nunca é juros',
    description: 'IOF vai para despesa financeira própria.',
    decryptFailed: false,
  },
  {
    id: 'ffffffff-ffff-4fff-8fff-000000000003',
    kind: 'fornecedor',
    code: null,
    // Entrada indecifrável: o backend devolve o placeholder no campo, e a tela
    // precisa sinalizar o estado por badge (não só pelo texto).
    name: '[indecifrável]',
    description: null,
    decryptFailed: true,
  },
];

/** Categorias do Omie (BACK 07.3) — a lista COMPLETA que o combobox filtra. */
const OMIE_CATEGORIAS = [
  { codigo: '1.01.01', descricao: 'Combustível' },
  { codigo: '2.02.02', descricao: 'Serviços de software' },
  { codigo: '3.03.03', descricao: 'Alimentação' },
];

/**
 * Resposta do lote (BACK 07.4) — PARCIAL de propósito: uma lançada e uma com
 * erro do Omie. É o caso que a tela precisa saber contar (resumo por linha +
 * toast de aviso), e é o único em que o toast NÃO é verde.
 */
const POSTING_BATCH_PARTIAL = {
  lines: [
    {
      file_entry_id: '99999999-9999-4999-8999-999999999801',
      status: 'lancada',
      reason: null,
      message: null,
      omie_lancamento_id: 5001,
    },
    {
      file_entry_id: '99999999-9999-4999-8999-999999999802',
      status: 'erro',
      reason: 'erro_omie',
      message: 'Categoria informada nao existe para o cliente.',
      omie_lancamento_id: null,
    },
  ],
  lancadas: 1,
  bloqueadas: 0,
  com_erro: 1,
};

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
  // Glossário do tenant (S6/R2) — rota literal ANTES do fallback paginado.
  if (path === `/api/v1/clients/${CLIENT_ID}/glossary`) {
    const entries = tableListsOverflow ? MANY_GLOSSARY_ENTRIES : GLOSSARY_ENTRIES;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        data: { entries, version: 7 },
        pagination: {
          page: 1,
          pageSize: 20,
          total: entries.length,
          totalPages: 1,
        },
      }),
    });
  }
  // Usuários DO tenant (BACK 05.5). Rota literal antes de qualquer fallback.
  if (path === `/api/v1/clients/${CLIENT_ID}/users`) {
    const users = tableListsOverflow ? MANY_CLIENT_USERS : CLIENT_USERS;
    return json({
      data: users,
      pagination: { page: 1, pageSize: 20, total: users.length, totalPages: 1 },
    });
  }
  if (path === '/api/v1/notifications/unread-count') return json({ unread: 2 });
  if (path === '/api/v1/notifications')
    return json({ data: NOTIFICATIONS, pagination: PAGINATION });
  if (path.endsWith('/read')) return json({ already_read: false, read_at: '2026-07-26T13:00:00Z' });
  if (path === '/api/v1/clients') return json({ data: [CLIENT_DETAIL], pagination: PAGINATION });
  if (path === `/api/v1/clients/${CLIENT_ID}`) {
    // As contas bancárias da tela R6 vêm DAQUI (paginação client-side sobre
    // `detail.accounts`), não de uma rota própria.
    return json(tableListsOverflow ? { ...CLIENT_DETAIL, accounts: MANY_ACCOUNTS } : CLIENT_DETAIL);
  }
  // O tenant alheio RESPONDE 200 de propósito: se o front pedir e renderizar,
  // o vazamento aparece no teste. Um 403 aqui esconderia o defeito atrás do
  // backend, e o que se verifica no front é que ele nem chega a pedir.
  if (path === `/api/v1/clients/${OTHER_CLIENT_ID}`) {
    return json({ ...CLIENT_DETAIL, id: OTHER_CLIENT_ID, name: 'Cliente de Outro Tenant' });
  }
  if (path === `/api/v1/clients/${CLIENT_ID}/sync-accounts`) return json(CLIENT_DETAIL);
  if (path === `/api/v1/clients/${CLIENT_ID}/reconciliations`) {
    const list = listOverflows ? OVERFLOW_SESSIONS : SESSIONS;
    return json({ data: list, pagination: { ...PAGINATION, total: list.length } });
  }
  if (path === `/api/v1/reconciliations/${SESSION_ID}`) {
    return json({
      ...DETAIL,
      ...(sessionAccountType === null ? {} : { account_type: sessionAccountType }),
      qualification_used_glossary: sessionUsedGlossary,
    });
  }
  // Anomalias (BACK 9.7) + veredito do revisor (BACK 06.5). Rota literal antes
  // do fallback: o PATCH grava no estado do módulo para a lista refletir a
  // mudança no refetch, que é o que o teste precisa observar.
  if (path === `/api/v1/reconciliations/${SESSION_ID}/anomalies`) {
    const list = anomalies();
    return json({
      data: list,
      pagination: { page: 1, pageSize: 20, total: list.length, totalPages: 1 },
    });
  }
  if (/\/api\/v1\/reconciliations\/[^/]+\/anomalies\/[^/]+$/.test(path)) {
    if (patchAnomalyFails) {
      // Envelope de erro do §7 da API — é dele que sai o `userMessage` que o
      // `ApiError` entrega ao `toast.error`.
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'INTERNAL_ERROR',
            message: 'boom',
            userMessage: 'Não foi possível registrar o veredito. Tente novamente.',
          },
        }),
      });
    }
    const body = route.request().postDataJSON() as { review_verdict?: string | null } | null;
    if (body?.review_verdict != null) reviewVerdict = body.review_verdict;
    return json(anomalies()[0]);
  }
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
    // Em sessão de cartão a tela pede DUAS listas: a paginada da aba e a de
    // `situation=sem_omie` (que a aba de Anomalias usa para saber quem ainda
    // pode ser lançada). O filtro é do servidor — aqui ele é aplicado no mock,
    // senão a lista "sem_omie" traria a linha ignorada e a tela ofereceria
    // lançar o que o backend recusa.
    const rows = sessionAccountType === 'credit_card' ? CARD_FILE_ENTRIES : FILE_ENTRIES;
    const situation = url.searchParams.get('situation');
    const filtered = situation === null ? rows : rows.filter((r) => r.situation === situation);
    return json({
      data: filtered,
      pagination: { page: 1, pageSize: 20, total: filtered.length, totalPages: 1 },
    });
  }
  // Sprint 7 (BACK 07.3): envelope `{data, total}` — DUAS chaves, então o front
  // NÃO desempacota. Rota literal antes de qualquer fallback: o genérico
  // devolveria `{data:[]}` sem `total` e o combobox abriria vazio.
  if (path === '/api/v1/omie/categorias') {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ data: OMIE_CATEGORIAS, total: OMIE_CATEGORIAS.length }),
    });
  }
  // Sprint 7 (BACK 07.4): o lote responde 200 mesmo com falha parcial — quem
  // conta o desfecho é `lines[]`.
  if (path === `/api/v1/reconciliations/${SESSION_ID}/omie-postings`) {
    return json(POSTING_BATCH_PARTIAL);
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
/**
 * Espera a animação de entrada do PRÓPRIO elemento terminar.
 *
 * `toBeVisible()` resolve assim que o nó entra na árvore — mas a gaveta do
 * Radix entra deslizando (`translate-x`), e uma `boundingBox()` medida no meio
 * do trajeto devolve coordenadas fora da viewport que não são defeito nenhum.
 * Sem `{ subtree: true }` de propósito: um spinner de carregamento lá dentro é
 * animação infinita e o `finished` dele nunca resolveria.
 */
async function aguardarAnimacao(locator: Locator): Promise<void> {
  await locator.evaluate(async (el) => {
    await Promise.all(el.getAnimations().map((a) => a.finished));
  });
}

/** O título do toast do Sonner — o nó que o axe reprovou em `#008a2e`/`#ecfdf3`. */
const TOAST_TITLE = '[data-sonner-toast] [data-title]';

/**
 * O toast do Sonner ENTRA animando (lift + fade). Medir no meio do trajeto
 * mede cor MESCLADA: no CI o axe pegou `#25894b`/`#eefdf4` = 4.25:1 num par
 * cujos tokens puros (`success`/`success-muted`) dão 4.75:1 — vermelho flaky
 * sem defeito nenhum (run 32485181657, PR #89). Mesma lição da gaveta
 * (`aguardarAnimacao`): visível não é estável; medir só depois da animação.
 */
async function aguardarToastEstavel(page: Page): Promise<void> {
  await aguardarAnimacao(page.locator('[data-sonner-toast]').first());
}

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

/**
 * Conteúdo que está POR BAIXO da barra de paginação (defeitos 86e2u4nxg e
 * 86e2uca1d), amostrando 9 pontos do retângulo da barra.
 *
 * `elementsFromPoint` é a ferramenta certa aqui porque devolve todos os
 * elementos daquele ponto, inclusive os cobertos — mas **não** devolve o que um
 * ancestral com `overflow` recortou. É exatamente a distinção entre "vazou da
 * caixa e ficou escondido atrás da barra opaca" (defeito) e "foi recortado pela
 * região rolável" (correto). Comparar `boundingBox` não serviria: depois da
 * correção a última linha continua geometricamente abaixo da barra, só que
 * recortada.
 *
 * `seletor` é o que conta como "conteúdo" na tela medida: o card da Lista de
 * Conciliações (`article[role="link"]`) ou a linha de corpo das telas de tabela
 * (`tbody tr`).
 */
async function conteudoAtrasDaBarra(page: Page, seletor: string): Promise<string[]> {
  return page.evaluate((sel) => {
    const barra = document.querySelector('nav[aria-label^="Paginação de"]');
    if (barra === null) return ['barra de paginação ausente'];
    const r = barra.getBoundingClientRect();
    const encontrados = new Set<string>();
    for (const fx of [0.15, 0.5, 0.85]) {
      for (const fy of [0.2, 0.5, 0.8]) {
        for (const el of document.elementsFromPoint(r.left + r.width * fx, r.top + r.height * fy)) {
          const alvo = el.closest(sel);
          if (alvo !== null) {
            encontrados.add(
              alvo.getAttribute('aria-label') ?? (alvo.textContent ?? '?').trim().slice(0, 50),
            );
          }
        }
      }
    }
    return [...encontrados];
  }, seletor);
}

/**
 * Roda a sonda nas DUAS posições possíveis da barra: onde a tela abre e depois
 * de rolar o `<main>` (o único container rolável do shell) até o fim.
 * `elementsFromPoint` só enxerga dentro da viewport, então uma posição só
 * deixaria o cenário vazio — sem medir nada — justamente no viewport em que a
 * barra não começa visível. Em desktop a barra abre no lugar e a 2ª medição é
 * redundante; em 390px é o contrário.
 */
async function conteudoCobertoEmQualquerRolagem(page: Page, seletor: string): Promise<string[]> {
  const achados = new Set(await conteudoAtrasDaBarra(page, seletor));
  await page.locator('main').evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
  for (const item of await conteudoAtrasDaBarra(page, seletor)) achados.add(item);
  return [...achados];
}

test.beforeEach(async ({ page, context, baseURL }) => {
  // Volta ao admin: os cenários de papel da S5 trocam este estado de módulo.
  sessionUser = USER;
  // Sprint 6: sessão SEM glossário e flag não julgado são o estado de partida.
  sessionUsedGlossary = false;
  // Sprint 7: conta corrente é o estado de partida — só os cenários de
  // lançamento ligam o cartão.
  sessionAccountType = null;
  reviewVerdict = null;
  patchAnomalyFails = false;
  listOverflows = false;
  tableListsOverflow = false;
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

    /**
     * Defeito 86e2u4nxg — a barra de paginação era desenhada POR CIMA dos cards.
     *
     * A causa não é z-index: o container dos cards é `min-h-0 flex-1`, ou seja,
     * está explicitamente autorizado a encolher abaixo da altura do conteúdo, e
     * não tinha `overflow`. O conteúdo vazava da caixa e a `PaginationBar`, que
     * é opaca (`bg-card`), pintava por cima do que vazou.
     *
     * A trava é `elementsFromPoint` dentro do retângulo da barra: ele devolve
     * TODOS os elementos naquele ponto, inclusive os que estão por baixo — mas
     * **não** devolve o que um ancestral com `overflow` recortou. É exatamente a
     * diferença entre "vazou e ficou escondido atrás" (defeito) e "foi recortado
     * pela região rolável" (correto). Medir só a `boundingBox` do último card
     * não serviria: depois da correção ele continua geometricamente abaixo da
     * barra, só que recortado.
     */
    test('a barra de paginação NUNCA cobre um card (86e2u4nxg)', async ({ page }) => {
      listOverflows = true;
      await page.goto(`/clientes/${CLIENT_ID}`);
      await expect(page.getByRole('heading', { name: 'Conciliações', level: 2 })).toBeVisible();
      const bar = page.getByRole('navigation', { name: 'Paginação de conciliações' });
      await expect(bar).toBeVisible();
      await shot(page, `lista-conciliacoes-transbordo-${vp.label.replace(/\s+/g, '-')}`);

      const cobertos = await conteudoCobertoEmQualquerRolagem(page, 'article[role="link"]');
      expect(cobertos, `cards escondidos atrás da barra de paginação (${vp.label})`).toEqual([]);

      // ...e a barra precisa ser ALCANÇÁVEL: em desktop ela fica ancorada no
      // rodapé; em 390px chega-se a ela rolando o `<main>`.
      await expect(bar).toBeInViewport();

      await analyze(page, `lista de conciliações com transbordo (${vp.label})`);
    });

    /**
     * O contraponto do teste acima: recortar não pode virar "sumiu".
     *
     * Só em desktop, de propósito. A partir de `lg` o shell fixa a altura
     * (`h-dvh` + `lg:flex-row`) e a lista precisa ter rolagem PRÓPRIA, com a
     * barra ancorada no rodapé. Abaixo de `lg` o mesmo shell vira coluna, a
     * altura deixa de ser fixa e quem rola é o `<main>` — MEDIDO em 390×844:
     * `clientHeight` 779 × `scrollHeight` 3327. São dois layouts, não um layout
     * quebrado. Exigir rolagem interna em 390px travaria um comportamento que a
     * tela não tem, e obtê-lo exigiria constranger a altura de todas as telas do
     * cliente — o que empurraria este mesmo defeito para as três telas de tabela.
     */
    if (vp.label === 'desktop') {
      test('a lista rola dentro da própria área, com a barra ancorada (86e2u4nxg)', async ({
        page,
      }) => {
        listOverflows = true;
        await page.goto(`/clientes/${CLIENT_ID}`);
        const lista = page.getByRole('region', { name: 'Lista de conciliações' });
        await expect(lista).toBeVisible();

        // Região rolável PRECISA ser focável, senão o conteúdo só é alcançável
        // arrastando o mouse — `scrollable-region-focusable` (SERIOUS).
        expect(await lista.evaluate((el) => el.tabIndex)).toBe(0);
        expect(
          await lista.evaluate((el) => el.scrollHeight - el.clientHeight),
          'a lista precisa ter rolagem própria quando as conciliações não cabem',
        ).toBeGreaterThan(0);

        const bar = page.getByRole('navigation', { name: 'Paginação de conciliações' });
        await expect(bar).toBeInViewport();
        await lista.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
        // Rolar a lista alcança o último card E não move a barra do rodapé.
        await expect(page.getByRole('link', { name: /Abrir conciliação/ }).last()).toBeInViewport();
        await expect(bar).toBeInViewport();
      });
    }

    test('Detalhe da conciliação (R3)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);
      await expect(
        page.getByRole('region', { name: 'Totalizadores da conciliação' }),
      ).toBeVisible();
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
      expect(
        ratio,
        `badge "Processada" (${vp.label}): ${ratio.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(4.5);
    });
  });
}

/**
 * Defeito 86e2uca1d — as TRÊS telas que combinam `PaginationBar` com `<Table>`.
 *
 * A análise da 86e2u4nxg dizia que elas estavam a salvo porque o `<Table>` já
 * embrulha o conteúdo numa região rolável. Medindo, não: aquele wrapper tem
 * altura de CONTEÚDO, então rola no horizontal e nunca no vertical. Com 20
 * linhas em 1440×900, a barra cobria `Conta 18`, `Pessoa 6`/`Pessoa 7` e
 * `Entrada 7` — o mesmo defeito da lista de cards, por baixo do mesmo mecanismo.
 *
 * O gate é o mesmo dos cards, com `tbody tr` no lugar de `article[role="link"]`.
 */
const TELAS_COM_TABELA = [
  {
    key: 'contas',
    titulo: 'Contas Bancárias',
    rota: `/clientes/${CLIENT_ID}/contas`,
    regiao: 'Contas bancárias (rolável)',
    usuario: (): Record<string, unknown> => USER,
  },
  {
    key: 'usuarios',
    titulo: 'Usuários',
    rota: `/clientes/${CLIENT_ID}/usuarios`,
    regiao: 'Usuários do cliente (rolável)',
    usuario: (): Record<string, unknown> => CLIENT_MANAGER_USER,
  },
  {
    key: 'glossario',
    titulo: 'Glossário',
    rota: `/clientes/${CLIENT_ID}/glossario`,
    regiao: 'Glossário do cliente (rolável)',
    usuario: (): Record<string, unknown> => CLIENT_MANAGER_USER,
  },
] as const;

for (const vp of VIEWPORTS) {
  test.describe(`Telas de tabela com paginação — ${vp.label}`, () => {
    test.use({ viewport: vp.size });

    for (const tela of TELAS_COM_TABELA) {
      test(`${tela.key}: a barra de paginação NUNCA cobre uma linha (86e2uca1d)`, async ({
        page,
      }) => {
        tableListsOverflow = true;
        sessionUser = tela.usuario();
        await page.goto(tela.rota);
        await expect(page.getByRole('heading', { name: tela.titulo, level: 2 })).toBeVisible();
        await expect(page.getByRole('navigation', { name: /^Paginação de/ })).toBeVisible();
        await shot(page, `tabela-${tela.key}-transbordo-${vp.label.replace(/\s+/g, '-')}`);

        const cobertas = await conteudoCobertoEmQualquerRolagem(page, 'tbody tr');
        expect(cobertas, `linhas escondidas atrás da barra (${tela.key}, ${vp.label})`).toEqual([]);

        await expect(page.getByRole('navigation', { name: /^Paginação de/ })).toBeInViewport();
        await analyze(page, `${tela.key} com transbordo (${vp.label})`);
      });

      /**
       * Contraponto: recortar não pode virar "sumiu". Só em desktop, pelo mesmo
       * motivo da lista de cards — abaixo de `lg` o shell vira coluna, a altura
       * deixa de ser fixa e quem rola é o `<main>`.
       */
      if (vp.label === 'desktop') {
        test(`${tela.key}: a tabela rola dentro da própria área (86e2uca1d)`, async ({ page }) => {
          tableListsOverflow = true;
          sessionUser = tela.usuario();
          await page.goto(tela.rota);
          const regiao = page.getByRole('region', { name: tela.regiao });
          await expect(regiao).toBeVisible();

          expect(await regiao.evaluate((el) => el.tabIndex)).toBe(0);
          expect(
            await regiao.evaluate((el) => el.scrollHeight - el.clientHeight),
            `a tabela de ${tela.key} precisa ter rolagem própria quando as linhas não cabem`,
          ).toBeGreaterThan(0);

          const bar = page.getByRole('navigation', { name: /^Paginação de/ });
          await expect(bar).toBeInViewport();
          await regiao.evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
          await expect(page.getByRole('row').last()).toBeInViewport();
          // Rolar a tabela não pode arrastar a barra do rodapé junto.
          await expect(bar).toBeInViewport();
        });
      }
    }
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
 * Sprint 6 / R4 (FRONT 06.7) — revisão: selo do glossário e veredito do flag.
 *
 * Os DOIS casos do selo são medidos (com e sem glossário), porque o critério de
 * aceite é simétrico: quando o backend informa `true`, a revisão mostra o selo;
 * quando não, a tela fica **idêntica** ao comportamento anterior à sprint.
 *
 * O veredito é medido no browser porque o que interessa é o comportamento
 * completo: clicar → PATCH → invalidação → a lista refletir o novo estado sem
 * reload manual. Em jsdom o hook é mockado; aqui o ciclo roda inteiro.
 */
for (const vp of VIEWPORTS) {
  const slugR = vp.label.replace(/\s+/g, '-');
  test.describe(`Revisão com glossário — ${vp.label}`, () => {
    test.use({ viewport: vp.size });

    test('selo aparece quando a análise considerou o glossário (R4)', async ({ page }) => {
      sessionUsedGlossary = true;
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);

      await expect(page.getByText('Considerou o glossário do cliente')).toBeVisible();
      // O selo não pode empurrar nada para fora da viewport em 390px.
      const box = await page.getByText('Considerou o glossário do cliente').boundingBox();
      expect(box, 'o selo precisa ter caixa visível').not.toBeNull();
      expect(
        (box?.x ?? 0) + (box?.width ?? 0),
        `selo do glossário cortado fora da viewport (${vp.label})`,
      ).toBeLessThanOrEqual(vp.size.width);

      await expect(page.locator('#__next_error__')).toHaveCount(0);
      await shot(page, `revisao-selo-glossario-${slugR}`);
      await analyze(page, `revisão com selo do glossário (${vp.label})`);
    });

    test('cliente SEM glossário: nada de selo, nada de espaço morto (R4)', async ({ page }) => {
      sessionUsedGlossary = false;
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);

      await expect(
        page.getByRole('region', { name: 'Totalizadores da conciliação' }),
      ).toBeVisible();
      // Escopo no SELO, não em `/glossário/i` solto: o shell do cliente tem o
      // item de menu "Glossário" (FRONT 06.6), que deve continuar lá.
      await expect(page.getByText('Considerou o glossário do cliente')).toHaveCount(0);
      await analyze(page, `revisão sem glossário (${vp.label})`);
    });

    test('operador marca o flag como improcedente e a lista reflete (R4)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}?tab=anomalias`);

      const row = page.getByRole('row', { name: /Classificação suspeita/ });
      await expect(row).toBeVisible();
      // Estado de partida VISÍVEL — "não avaliei" precisa ser legível.
      await expect(row.getByText('Não avaliado')).toBeVisible();

      await page
        .getByRole('button', { name: 'Marcar "Classificação suspeita" como improcedente' })
        .click();

      // Sem reload manual: a invalidação do TanStack refaz a lista.
      await expect(
        page.getByRole('button', { name: 'Marcar "Classificação suspeita" como improcedente' }),
      ).toHaveAttribute('aria-pressed', 'true');
      await expect(row.getByText('Não avaliado')).toHaveCount(0);

      // O toast de SUCESSO precisa estar na tela quando o axe roda — é ele que
      // reprovou a 1ª entrega (`richColors` do Sonner, 4.25:1 em `[data-title]`).
      // Sem esta espera, o axe podia medir a tela depois de o toast sumir e o
      // gate ficaria verde sem ver o defeito.
      await expect(page.getByText('Flag marcado como improcedente.')).toBeVisible();
      await aguardarToastEstavel(page);
      // ...e MEDIDO, não só "presente": o toast tem `duration` de 4 s e o axe
      // só reprova o que estiver na tela no instante em que roda. A medição
      // direta falha se o toast sumiu (o `$eval` não acha o seletor) e falha se
      // a cor regrediu — as duas maneiras de este cenário virar teatro.
      expect(await measuredContrast(page, TOAST_TITLE)).toBeGreaterThanOrEqual(4.5);

      await shot(page, `revisao-veredito-${slugR}`);
      await analyze(page, `veredito do flag marcado + toast de sucesso (${vp.label})`);
    });

    test('erro do servidor no veredito: mensagem legível e toast medido (R4)', async ({ page }) => {
      patchAnomalyFails = true;
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}?tab=anomalias`);

      const alvo = page.getByRole('button', {
        name: 'Marcar "Classificação suspeita" como procedente',
      });
      await expect(alvo).toBeVisible();
      await alvo.click();

      // `userMessage` do envelope da API, em português — nunca o erro cru.
      await expect(
        page.getByText('Não foi possível registrar o veredito. Tente novamente.'),
      ).toBeVisible();
      // A falha NÃO pode deixar o botão marcado: o veredito não foi gravado.
      await expect(alvo).toHaveAttribute('aria-pressed', 'false');
      await expect(
        page.getByRole('row', { name: /Classificação suspeita/ }).getByText('Não avaliado'),
      ).toBeVisible();

      // O par `destructive`/`destructive-muted` do toast de ERRO, que nenhum
      // cenário exercitava. Aqui a medição direta é a trava PRINCIPAL: com o
      // toast de erro o `analyze()` sozinho não basta — ele passou verde contra
      // um par mutado para branco-sobre-quase-branco (o toast já tinha saído da
      // tela quando o axe rodou). `measuredContrast` deu 1.048 no mesmo build.
      await aguardarToastEstavel(page);
      expect(await measuredContrast(page, TOAST_TITLE)).toBeGreaterThanOrEqual(4.5);

      await shot(page, `revisao-veredito-erro-${slugR}`);
      await analyze(page, `erro do veredito + toast destrutivo (${vp.label})`);
    });

    test('a ação do veredito é alcançável por TECLADO (WCAG 2.1.1)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}?tab=anomalias`);
      const alvo = page.getByRole('button', {
        name: 'Marcar "Classificação suspeita" como procedente',
      });
      await expect(alvo).toBeVisible();

      let alcancado = false;
      for (let i = 0; i < 60 && !alcancado; i++) {
        await page.keyboard.press('Tab');
        alcancado = await alvo.evaluate((el) => el === document.activeElement);
      }
      expect(alcancado, 'o botão de veredito precisa ser alcançável por Tab').toBe(true);

      await page.keyboard.press('Enter');
      await expect(alvo).toHaveAttribute('aria-pressed', 'true');
    });

    test('tipo que o servidor não julga não ganha a ação (R4)', async ({ page }) => {
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}?tab=anomalias`);
      await expect(page.getByRole('row', { name: /Saldo divergente/ })).toBeVisible();
      await expect(
        page.getByRole('button', { name: /Marcar "Saldo divergente" como/ }),
      ).toHaveCount(0);
    });
  });
}

/**
 * Sprint 7 / R1 (FRONT 07.6) — lançamento no Omie a partir da revisão.
 *
 * O que só o browser mede: a tabela com a coluna de seleção montada (a caixa
 * nativa e o seu nome acessível), a ação inerte por `aria-disabled` com o
 * motivo em `aria-describedby` e a barra de lote **presente** no instante do
 * `analyze()` — estado transitório é tela (ADR-013-QA).
 *
 * A simetria é o ponto: a MESMA sessão em conta corrente não pode exibir nada
 * disso. É o critério "conta corrente não exibe a ação em lugar nenhum", e ele
 * só vale se for medido nos dois lados.
 */
for (const vp of VIEWPORTS) {
  const slugP = vp.label.replace(/\s+/g, '-');
  test.describe(`Lançamento no Omie — ${vp.label}`, () => {
    test.use({ viewport: vp.size });

    test('cartão: compra sem_omie oferece a ação e entra no lote (R1)', async ({ page }) => {
      sessionAccountType = 'credit_card';
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);

      const linha = page.getByRole('row', { name: /Posto Shell 1234/ });
      await expect(linha).toBeVisible();
      await expect(linha.getByRole('button', { name: /Lançar no Omie/ })).toBeVisible();

      // A linha IGNORADA continua na tela, com a ação inerte e o motivo
      // acessível — não some sem explicação.
      const ignorada = page.getByRole('row', { name: /Compra ignorada na revisão/ });
      const acaoInerte = ignorada.getByRole('button', { name: /Lançar no Omie/ });
      await expect(acaoInerte).toHaveAttribute('aria-disabled', 'true');
      await expect(ignorada.getByRole('checkbox', { name: /Selecionar a compra/ })).toBeDisabled();

      // Seleciona e mede COM a barra de lote montada.
      await linha.getByRole('checkbox', { name: /Selecionar a compra/ }).check();
      await expect(page.getByRole('button', { name: /Lançar 1 compra no Omie/ })).toBeVisible();

      // O VALOR não pode quebrar entre o hífen e o número. O navegador quebra
      // depois do `-`, e a Sprint 7 estreitou esta coluna ao acrescentar duas
      // (seleção + ação): `-R$ 150,50` virava `-` numa linha e `R$ 150,50` na
      // outra — um débito que se lê como crédito. `getClientRects()` sobre o
      // conteúdo conta LINHAS renderizadas; o axe não vê isto (ADR-014-QA).
      const linhasDoValor = await linha
        .locator('td')
        .filter({ hasText: /R\$\s*150,50/ })
        .evaluate((el) => {
          const r = document.createRange();
          r.selectNodeContents(el);
          return r.getClientRects().length;
        });
      expect(linhasDoValor, 'valor monetário quebrado em mais de uma linha').toBe(1);

      await expect(page.locator('#__next_error__')).toHaveCount(0);
      await shot(page, `lancamento-selecao-${slugP}`);
      await analyze(page, `revisão de cartão com lote selecionado (${vp.label})`);
    });

    test('gaveta: classifica em lote, confirma e lê o resumo parcial (R2/R5)', async ({ page }) => {
      sessionAccountType = 'credit_card';
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);

      await page
        .getByRole('checkbox', { name: /Selecionar todas as compras desta página/ })
        .check();
      await page.getByRole('button', { name: /Lançar 2 compras no Omie/ }).click();

      const gaveta = page.getByRole('dialog', { name: 'Lançar no Omie' });
      await expect(gaveta).toBeVisible();
      await aguardarAnimacao(gaveta);
      // Cancelar à ESQUERDA da ação primária (contrato da gaveta).
      const cancelar = await gaveta.getByRole('button', { name: 'Cancelar' }).boundingBox();
      const primaria = await gaveta
        .getByRole('button', { name: /Confirmar e lançar/ })
        .boundingBox();
      expect(cancelar?.x ?? 0, 'Cancelar precisa ficar à esquerda da ação primária').toBeLessThan(
        primaria?.x ?? 0,
      );

      // Nenhum dos dois pode passar da borda da viewport. O axe não mede
      // transbordo e o jsdom não tem layout: em 390px o rodapé com TRÊS
      // elementos (Cancelar + texto auxiliar + ação primária) clipava o botão
      // que grava na contabilidade do cliente. Medido aqui, no estado inicial
      // da gaveta — que é justamente o que tem compra sem categoria.
      const larguraViewport = page.viewportSize()?.width ?? 0;
      expect(
        (primaria?.x ?? 0) + (primaria?.width ?? 0),
        'ação primária da gaveta cortada pela borda da viewport',
      ).toBeLessThanOrEqual(larguraViewport);
      expect(
        (cancelar?.x ?? 0) + (cancelar?.width ?? 0),
        'Cancelar da gaveta cortado pela borda da viewport',
      ).toBeLessThanOrEqual(larguraViewport);

      // Os valores das DUAS compras do lote têm de terminar na mesma borda
      // direita. Em 390px o valor mais largo caía para a linha de baixo
      // alinhado à ESQUERDA, enquanto o curto ficava à direita — duas linhas
      // do mesmo lote alinhadas de formas diferentes. Mede as bordas, não o CSS.
      const bordas = await gaveta
        .locator('li span.tabular-nums')
        .filter({ hasText: /R\$/ })
        .evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().right)));
      expect(bordas.length, 'esperava um valor por compra do lote').toBe(2);
      expect(
        Math.abs((bordas[0] ?? 0) - (bordas[1] ?? 0)),
        'valores do lote não terminam na mesma borda direita',
      ).toBeLessThanOrEqual(1);

      // Sem categoria, o envio não é oferecido.
      await expect(
        gaveta.getByRole('button', { name: /Confirmar e lançar 0 de 2/ }),
      ).toBeDisabled();

      // COMBOBOX ABERTO no instante da medição (ADR-013-QA): é o estado que o
      // axe nunca vê se o cenário medir só a tela em repouso.
      await gaveta.getByRole('button', { name: /Categoria para todas as compras/ }).click();
      const lista = page.getByRole('listbox', { name: /Categoria para todas as compras/ });
      await expect(lista).toBeVisible();
      await page.getByRole('combobox').fill('combust');
      await expect(lista.getByRole('option')).toHaveCount(1);
      await shot(page, `lancamento-combobox-${slugP}`);
      await analyze(page, `gaveta de lançamento com o combobox aberto (${vp.label})`);

      await lista.getByRole('option', { name: /Combustível/ }).click();
      await gaveta.getByRole('button', { name: 'Aplicar a 2 compras' }).click();
      await gaveta.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }).click();

      // Resumo por linha, com a mensagem VERBATIM do provedor na que falhou.
      await expect(gaveta.getByText('Lançada no Omie')).toBeVisible();
      await expect(gaveta.getByText(/lançamento nº 5001/)).toBeVisible();
      await expect(
        gaveta.getByText(/Categoria informada nao existe para o cliente\./),
      ).toBeVisible();
      // A gaveta NÃO fecha no parcial: é dela que o operador reexecuta.
      await expect(gaveta.getByRole('button', { name: /Tentar novamente 1 de 1/ })).toBeVisible();

      // TOAST montado e MEDIDO — parcial é aviso, e a cor vem dos tokens.
      await expect(
        page.getByText('1 de 2 compras lançadas. Veja o motivo das demais.'),
      ).toBeVisible();
      await aguardarToastEstavel(page);
      expect(await measuredContrast(page, TOAST_TITLE)).toBeGreaterThanOrEqual(4.5);

      await expect(page.locator('#__next_error__')).toHaveCount(0);
      await shot(page, `lancamento-resumo-${slugP}`);
      await analyze(page, `resumo parcial do lote + toast de aviso (${vp.label})`);
    });

    test('conta corrente: nem seleção nem ação, em nenhuma aba (R1)', async ({ page }) => {
      // `sessionAccountType` fica no default (o `DETAIL` de conta corrente).
      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}`);

      await expect(page.getByRole('row', { name: /Pagamento fornecedor X/ })).toBeVisible();
      await expect(page.getByRole('button', { name: /Lançar no Omie/ })).toHaveCount(0);
      await expect(page.getByRole('checkbox', { name: /Selecionar a compra/ })).toHaveCount(0);

      await page.goto(`/clientes/${CLIENT_ID}/conciliacao/${SESSION_ID}?tab=anomalias`);
      await expect(page.getByRole('button', { name: /Lançar no Omie/ })).toHaveCount(0);
      await analyze(page, `revisão de conta corrente sem lançamento (${vp.label})`);
    });
  });
}

/**
 * Sprint 6 / R2 (FRONT 06.6) — tela "Glossário" do cliente, por PAPEL.
 *
 * O que só um browser mede aqui: o CSS computado das badges de tipo (tokens
 * `info`/`warning`/`muted` do tema) e da badge "Indecifrável" (`destructive`),
 * e a árvore de acessibilidade da gaveta e do `alertdialog` montados de
 * verdade, em desktop e em 390px.
 *
 * A diferença de gating em relação a "Usuários": aqui a ROTA é liberada para
 * todo papel com acesso ao cliente (o operador lê o glossário como referência)
 * — o que some para ele são as ações de escrita. Um `AccessDenied` para o
 * operador seria defeito, não segurança.
 */
for (const vp of VIEWPORTS) {
  const slugG = vp.label.replace(/\s+/g, '-');
  test.describe(`Glossário do cliente — ${vp.label}`, () => {
    test.use({ viewport: vp.size });

    test('gerente do cliente vê a lista e as ações de escrita (R2)', async ({ page }) => {
      sessionUser = CLIENT_MANAGER_USER;
      await page.goto(`/clientes/${CLIENT_ID}/glossario`);

      await expect(page.getByRole('heading', { name: 'Glossário', level: 2 })).toBeVisible();
      await expect(page.getByRole('row', { name: /Taxas bancárias/ })).toBeVisible();
      await expect(page.getByText('Regra de auditoria').first()).toBeVisible();
      await expect(page.getByText('Fornecedor típico').first()).toBeVisible();
      // Entrada indecifrável não pode virar célula silenciosamente vazia.
      // `exact: true` NÃO é decoração: sem ele o `getByText` casa substring de
      // forma case-insensitive e o próprio nome `[indecifrável]` satisfaria a
      // asserção — o teste passaria com a badge ausente.
      await expect(page.getByText('Indecifrável', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Nova entrada' })).toBeVisible();
      // Se a página tivesse caído no error boundary, o axe mediria a tela de erro.
      await expect(page.locator('#__next_error__')).toHaveCount(0);
      await shot(page, `glossario-gerente-${slugG}`);
      await analyze(page, `glossário — gerente (${vp.label})`);
    });

    test('gaveta de criação: três tipos e Cancelar à esquerda (R2)', async ({ page }) => {
      sessionUser = CLIENT_MANAGER_USER;
      await page.goto(`/clientes/${CLIENT_ID}/glossario`);
      await page.getByRole('button', { name: 'Nova entrada' }).first().click();

      const drawer = page.getByRole('dialog');
      await expect(drawer).toBeVisible();
      await expect(drawer.getByLabel('Nome', { exact: true })).toBeVisible();
      await shot(page, `glossario-gaveta-${slugG}`);
      await analyze(page, `gaveta do glossário (${vp.label})`);

      await drawer.getByRole('combobox', { name: /Tipo/ }).click();
      const options = page.getByRole('option');
      await expect(options).toHaveCount(3);
      await expect(options.nth(0)).toHaveText('Categoria');
      await expect(options.nth(1)).toHaveText('Fornecedor típico');
      await expect(options.nth(2)).toHaveText('Regra de auditoria');
    });

    test('remover passa por alertdialog com foco inicial no Cancelar (R2)', async ({ page }) => {
      sessionUser = CLIENT_MANAGER_USER;
      await page.goto(`/clientes/${CLIENT_ID}/glossario`);
      await page.getByRole('button', { name: 'Remover Taxas bancárias' }).click();

      const confirm = page.getByRole('alertdialog');
      await expect(confirm).toBeVisible();
      // `Enter` reflexo não pode apagar entrada do glossário.
      await expect(confirm.getByRole('button', { name: 'Cancelar' })).toBeFocused();
      await shot(page, `glossario-confirmacao-${slugG}`);
      await analyze(page, `confirmação de remoção do glossário (${vp.label})`);
    });

    test('operador do cliente LÊ, e nenhuma ação de escrita existe (R2/R4)', async ({ page }) => {
      sessionUser = CLIENT_OPERATOR_USER;
      await page.goto(`/clientes/${CLIENT_ID}/glossario`);

      // A rota NÃO é negada para ele — o glossário é referência na revisão.
      await expect(page.getByRole('heading', { name: 'Glossário', level: 2 })).toBeVisible();
      await expect(page.getByRole('row', { name: /Taxas bancárias/ })).toBeVisible();
      await expect(
        page.getByRole('heading', { name: 'Você não tem acesso a esta página' }),
      ).toHaveCount(0);

      // Ações OCULTAS (não desabilitadas) — inclusive a coluna inteira.
      await expect(page.getByRole('button', { name: 'Nova entrada' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Editar Taxas bancárias' })).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Remover Taxas bancárias' })).toHaveCount(0);
      await expect(page.getByRole('columnheader', { name: 'Ações' })).toHaveCount(0);

      await shot(page, `glossario-operador-${slugG}`);
      await analyze(page, `glossário — operador somente leitura (${vp.label})`);
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
        // "Glossário" (S6/R2) aparece para os QUATRO papéis: ler é de todo mundo
        // com acesso ao cliente; quem pede permissão é a escrita, dentro da tela.
        await expect(clientNav.getByText('Glossário')).toHaveCount(1);
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
