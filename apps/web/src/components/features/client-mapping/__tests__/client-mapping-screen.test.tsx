/**
 * Testes da tela "De-para" do cliente (FRONT 12.7 / R0 · R2 · R4 · R5 · R6 · R7).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Os cenários que precisam de browser real (CSS
 * computado, contraste, 390px) ficam em `e2e/a11y-mocked.spec.ts`.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - POR PAPEL: `client_operator` vê a lista e a prévia e NÃO vê editar, lote,
 *     iniciar, importar, materializar nem sincronizar; `manager` (o contador
 *     parceiro, a armadilha do R6) vê todas as ações de edição;
 *   - filtro e busca chegam ao SERVIDOR (params do hook → query string), e a
 *     busca é rotulada "por código";
 *   - as quatro situações têm selos distintos, com nome acessível; a
 *     divergência com a origem tem nome acessível próprio;
 *   - alterar pede competência de início com padrão = corrente do servidor e diz
 *     que cria vigência nova; a retroativa mostra as competências afetadas e só
 *     então confirma com `confirmRetroactive`;
 *   - o lote mostra a quantidade afetada que veio do servidor, manda o recorte
 *     por código da lista, reconta ao trocar o mês de início e só existe no
 *     destino que herda;
 *   - sem destino na URL a tela abre no demonstrativo contábil;
 *   - as versões materializadas vêm da rota (autor, data, cobertura, selo);
 *   - o estado da base que falha tem "Tentar novamente"; a instrução da base
 *     nunca sincronizada só cita o botão quando ele existe;
 *   - a prévia mostra o estado da base e as quatro situações; base nunca
 *     sincronizada mostra a instrução (e nem pede a prévia); materializar com
 *     valor sem decisão exige a confirmação extra;
 *   - a importação mostra a prévia com as linhas recusadas antes de aplicar;
 *   - (86e3f55bd) os contadores por situação vêm do envelope e filtram pela URL
 *     (ativo exato, `aria-pressed`, desfazer), o `Select` de situação saiu, os
 *     filtros ativos viram etiquetas removíveis, a página rola e a tabela não,
 *     50 por página, e clicar na linha abre a gaveta do item certo para quem
 *     edita (e nada para o operador);
 *   - axe-core sem `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/clientes/c1/de-para',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

function mutationState() {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    data: undefined as unknown,
  };
}

const destinationsState = {
  data: undefined as MappingDestination[] | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const listState = {
  data: undefined as MappingListResponse | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const targetsState = { data: [] as MappingTarget[], isLoading: false, isError: false };
const syncStateQuery = {
  data: undefined as MovementsSyncState | undefined,
  isLoading: false,
  isError: false,
  refetch: vi.fn(),
};
const materializationsState = {
  data: undefined as MaterializationSummary[] | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const previewState = {
  data: undefined as MappingPreview | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
let previewEnabled: boolean | undefined;
let lastListParams: ListClientMappingParams | undefined;

const writeState = mutationState();
const confirmInheritedState = mutationState();
const inheritState = mutationState();
const materializeState = mutationState();
const syncMovementsState = mutationState();
const exportState = mutationState();
const importPreviewState = mutationState();
const importApplyState = mutationState();
const createTargetsState = mutationState();
const originPreviewState = {
  data: undefined as OriginTargetsPreview | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

vi.mock('@/hooks/use-client-mapping', () => ({
  useMappingDestinations: () => destinationsState,
  useMappingTargets: () => targetsState,
  useClientMappingList: (
    _clientId: string,
    _destinationType: string,
    params: ListClientMappingParams,
  ) => {
    lastListParams = params;
    return listState;
  },
  useMovementsSyncState: () => syncStateQuery,
  useMappingMaterializations: () => materializationsState,
  useMappingPreview: (_c: string, _d: string, _k: string, options: { enabled?: boolean }) => {
    previewEnabled = options.enabled;
    return options.enabled === false ? { ...previewState, data: undefined } : previewState;
  },
  useSyncMovements: () => syncMovementsState,
  useWriteMappingDecision: () => writeState,
  useConfirmInheritedDecisions: () => confirmInheritedState,
  useInheritMapping: () => inheritState,
  useMaterializeMapping: () => materializeState,
  useExportClientMapping: () => exportState,
  usePreviewMappingImport: () => importPreviewState,
  useApplyMappingImport: () => importApplyState,
  useOriginTargetsPreview: () => originPreviewState,
  useCreateMappingTargets: () => createTargetsState,
}));

// S16: `AccountingPlanNotice` (lista) e `AccountingDecisionSheet` (gaveta) usam
// esta sonda incondicionalmente quando o destino é `conta_contabil` — sem o
// mock, `useQuery` explode por falta de `QueryClientProvider` (este arquivo
// não envolve `render()` num provider de verdade, como os outros hooks acima).
// `total: 1` = "tem plano" (padrão); o destino padrão (86e3n70pn) também lê a
// sonda, e os testes dele trocam o total.
const accountingProbe = { total: 1 };
vi.mock('@/hooks/use-client-accounting-chart', () => ({
  useAccountingChartList: () => ({
    data: { data: [], pagination: { page: 1, pageSize: 1, total: accountingProbe.total } },
    isLoading: false,
  }),
}));

const clientDetailState = {
  data: undefined as
    | {
        closed_at: string | null;
        origin_status: string;
        organization: { id: string; name: string };
        connections?: ClientConnection[];
      }
    | undefined,
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => clientDetailState,
}));

const authState = { user: null as AuthenticatedUser | null };

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

// Permissões NEGADAS à força, por cima da matriz real: `upload_client_file` é dos
// cinco papéis, e só assim o teste prova que o link do envio pergunta ao helper
// (§4.9) em vez de supor a célula.
const deniedPermissions = new Set<string>();

vi.mock('@/lib/authz', async (importOriginal) => {
  const actual = await importOriginal<typeof AuthzModule>();
  return {
    ...actual,
    hasPermission: (...args: Parameters<typeof actual.hasPermission>) =>
      !deniedPermissions.has(args[1]) && actual.hasPermission(...args),
  };
});

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import {
  ClientMappingScreen,
  DEFAULT_PAGE_SIZE,
  defaultDestinationType,
} from '@/components/features/client-mapping/client-mapping-screen';
import {
  isMappingCountActive,
  mappingCountFilterSituation,
  type MappingCountKey,
} from '@/components/features/client-mapping/mapping-situation-counts';
import { ApiError } from '@/lib/api/client';
import { buildClientMappingQuery, type ListClientMappingParams } from '@/lib/api/client-mapping';
import type * as AuthzModule from '@/lib/authz';
import type {
  AuthenticatedUser,
  ClientConnection,
  MappingDestination,
  MappingListItem,
  MappingListResponse,
  MappingPreview,
  MappingTarget,
  MaterializationSummary,
  MovementsSyncState,
  OriginTargetsPreview,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ORG = '0706eeb5-9718-4d03-bcda-ef615789e6ac';
const TENANT = '11111111-1111-4111-8111-111111111111';

const manager: AuthenticatedUser = {
  id: 'm',
  email: 'contador@parceiro.com.br',
  name: 'Contador Parceiro',
  role: 'manager',
  scope: 'system',
  client_id: null,
  organization_id: ORG,
  organization_name: 'Hologram',
};
const orgAdmin: AuthenticatedUser = {
  id: 'a',
  email: 'admin@prospecta.com.br',
  name: 'Admin da Organização',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: ORG,
  organization_name: 'Hologram',
};
const clientOperator: AuthenticatedUser = {
  id: 'co',
  email: 'operador@cliente.com.br',
  name: 'Operador',
  role: 'client_operator',
  scope: 'client',
  client_id: TENANT,
  organization_id: ORG,
  organization_name: 'Hologram',
};

function destination(overrides: Partial<MappingDestination> = {}): MappingDestination {
  return {
    id: 'd-contabil',
    type: 'demonstrativo_contabil',
    name: 'Demonstrativo contábil',
    active: true,
    organizationId: ORG,
    targetsCount: 14,
    ...overrides,
  };
}

function item(overrides: Partial<MappingListItem> = {}): MappingListItem {
  return {
    sourceType: 'omie',
    categoryCode: '2.01.01',
    categoryName: 'Aluguel',
    categoryNameResolved: true,
    situation: 'herdada',
    decision: 'alvo',
    targetCode: '3.1',
    targetName: 'Despesas administrativas',
    effectiveFrom: '2026-09',
    divergent: false,
    originDreCode: '3.1',
    // S16: campo novo do contrato (sempre presente; `true` só em legado do `conta_contabil`).
    requiresRedo: false,
    ...overrides,
  };
}

function preview(overrides: Partial<MappingPreview> = {}): MappingPreview {
  return {
    competence: '2026-06',
    destination: 'demonstrativo_contabil',
    baseState: { syncedAt: '2026-09-25T12:00:00Z', syncFailedAt: null },
    situations: {
      alvo: { amount: '98200.00', count: 310 },
      naoMapear: { amount: '1200.00', count: 20 },
      semDecisao: { amount: '12400.00', count: 3 },
      semCategoria: { amount: '0.00', count: 0 },
    },
    coveragePct: '88.9',
    naoMapearPct: '1.1',
    coverageNumerator: '99400.00',
    coverageDenominator: '111800.00',
    undecidedCategories: [
      { sourceType: 'omie', categoryCode: '1.04.02', amount: '12400.00', count: 3 },
    ],
    previewToken: 'tok-1',
    latestVersion: 2,
    ...overrides,
  };
}

function materialization(overrides: Partial<MaterializationSummary> = {}): MaterializationSummary {
  return {
    id: 'mat-2',
    competence: '2026-06',
    version: 2,
    createdAt: '2026-09-26T14:30:00Z',
    author: { name: 'Contador Parceiro', email: 'contador@parceiro.com.br' },
    partialCoverageConfirmed: false,
    coveragePct: '88.9',
    mappedAmount: '98200.00',
    mappedCount: 310,
    notMappedAmount: '1200.00',
    notMappedCount: 20,
    undecidedAmount: '12400.00',
    undecidedCount: 3,
    uncategorizedAmount: '0.00',
    uncategorizedCount: 0,
    undecidedCategories: 1,
    decisionsUsed: 40,
    ...overrides,
  };
}

const brl = (valor: string) => new RegExp(`R\\$\\s*${valor.replace(/\./g, '\\.')}`);

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentSearch = '';
  deniedPermissions.clear();
  replaceMock.mockClear();
  lastListParams = undefined;
  previewEnabled = undefined;
  authState.user = manager;
  accountingProbe.total = 1;
  originPreviewState.data = undefined;
  originPreviewState.isLoading = false;
  originPreviewState.isError = false;
  createTargetsState.mutateAsync.mockReset();
  clientDetailState.data = {
    closed_at: null,
    origin_status: 'ativa',
    organization: { id: ORG, name: 'Hologram' },
  };
  destinationsState.data = [
    destination(),
    destination({
      id: 'd-caixa',
      type: 'fluxo_de_caixa',
      name: 'Fluxo de caixa',
      targetsCount: 8,
    }),
  ];
  destinationsState.isLoading = false;
  destinationsState.isError = false;
  listState.data = {
    data: [
      item(),
      item({
        categoryCode: '2.01.02',
        categoryName: 'Energia',
        situation: 'confirmada',
        targetCode: '3.2',
        targetName: 'Utilidades',
        divergent: true,
        originDreCode: '3.9',
      }),
      item({
        categoryCode: '1.09.01',
        categoryName: 'Transferência entre contas',
        situation: 'nao_mapear',
        decision: 'nao_mapear',
        targetCode: null,
        targetName: null,
      }),
      item({
        categoryCode: '1.04.02',
        categoryName: null,
        categoryNameResolved: false,
        situation: 'sem_decisao',
        decision: null,
        targetCode: null,
        targetName: null,
        effectiveFrom: null,
      }),
    ],
    pagination: { page: 1, pageSize: 20, total: 4, totalPages: 1 },
    competence: '2026-09',
    // O universo INTEIRO do destino (servidor): maior que a página de 4 linhas
    // de propósito, para provar que os contadores não vêm da página.
    counts: { total: 60, herdada: 20, confirmada: 12, naoMapear: 10, semDecisao: 18 },
  };
  listState.isLoading = false;
  listState.isError = false;
  targetsState.data = [
    { id: 't1', code: '3.1', name: 'Despesas administrativas', active: true },
    { id: 't2', code: '3.2', name: 'Utilidades', active: true },
  ];
  syncStateQuery.data = {
    competence: '2026-09',
    neverSynced: false,
    syncedAt: '2026-09-25T12:00:00Z',
    syncFailedAt: null,
  };
  syncStateQuery.isLoading = false;
  syncStateQuery.isError = false;
  syncStateQuery.refetch = vi.fn();
  materializationsState.data = [
    materialization(),
    materialization({
      id: 'mat-1',
      version: 1,
      createdAt: '2026-09-20T10:00:00Z',
      author: { name: 'Equipe Hologram', email: null },
      partialCoverageConfirmed: true,
      coveragePct: '71.5',
    }),
  ];
  materializationsState.isLoading = false;
  materializationsState.isError = false;
  materializationsState.error = null;
  previewState.data = preview();
  previewState.isLoading = false;
  previewState.isError = false;
  previewState.error = null;
  for (const state of [
    writeState,
    confirmInheritedState,
    inheritState,
    materializeState,
    syncMovementsState,
    exportState,
    importPreviewState,
    importApplyState,
  ]) {
    state.mutate = vi.fn();
    state.mutateAsync = vi.fn().mockResolvedValue({});
    state.reset = vi.fn();
    state.isPending = false;
    state.data = undefined;
  }
});

describe('ClientMappingScreen — gating por papel (R6)', () => {
  it('client_operator vê a lista e NÃO vê editar, lote, iniciar nem importar', () => {
    authState.user = clientOperator;
    render(<ClientMappingScreen clientId={TENANT} />);

    expect(screen.getByRole('table')).toBeVisible();
    expect(screen.getByText('2.01.01')).toBeVisible();
    // Exportar é de todos que leem.
    expect(screen.getByRole('button', { name: /Exportar/ })).toBeVisible();

    for (const name of [
      /Alterar/,
      /Decidir/,
      /Confirmar herdadas/,
      /Iniciar de-para/,
      /Importar/,
    ]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    // Nem a coluna de ações existe.
    expect(screen.queryByText('Ações')).not.toBeInTheDocument();
  });

  it('client_operator na prévia: lê a base e as situações, sem sincronizar nem materializar', () => {
    authState.user = clientOperator;
    currentSearch = 'view=previa';
    render(<ClientMappingScreen clientId={TENANT} />);

    expect(screen.getByTestId('mapping-base-state')).toHaveTextContent(/Sincronizada em/);
    expect(screen.getByTestId('mapping-preview')).toBeVisible();
    expect(
      screen.queryByRole('button', { name: /Sincronizar competência/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Materializar/ })).not.toBeInTheDocument();
  });

  it('manager (contador parceiro) vê todas as ações de edição', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getAllByRole('button', { name: /^Alterar a categoria/ })).toHaveLength(3);
    expect(screen.getByRole('button', { name: 'Decidir a categoria 1.04.02' })).toBeVisible();
    expect(screen.getByRole('button', { name: /Confirmar herdadas/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /Iniciar de-para/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /Importar/ })).toBeVisible();
  });

  it('manager na prévia: sincronizar e materializar visíveis', () => {
    currentSearch = 'view=previa';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('button', { name: /Sincronizar competência/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /Materializar/ })).toBeVisible();
  });

  it('"Iniciar de-para" e "Confirmar herdadas" só existem no destino que herda', () => {
    currentSearch = 'destination=fluxo_de_caixa';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.queryByRole('button', { name: /Iniciar de-para/ })).not.toBeInTheDocument();
    // Validação humana da S12: no destino sem herança o botão abria um diálogo
    // com "Confirmar" desabilitado. Agora não existe.
    expect(screen.queryByRole('button', { name: /Confirmar herdadas/ })).not.toBeInTheDocument();
    // A edição individual e a importação continuam.
    expect(screen.getByRole('button', { name: 'Decidir a categoria 1.04.02' })).toBeVisible();
    expect(screen.getByRole('button', { name: /Importar/ })).toBeVisible();
  });

  it('cliente encerrado: nenhuma escrita, com o motivo dito na tela', () => {
    clientDetailState.data = { ...clientDetailState.data!, closed_at: '2026-09-01T00:00:00Z' };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(
      screen.getByText(/Cliente encerrado: o de-para fica disponível só para leitura/),
    ).toBeVisible();
    expect(screen.queryByRole('button', { name: /Importar/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Confirmar herdadas/ })).not.toBeInTheDocument();
  });

  it('"Importar" também existe no destino Conta contábil (86e3fxqqe)', () => {
    // Até a task 86e3fxqqe, a S16 escondia o botão de propósito nesse destino
    // (a planilha não levava o histórico cifrado). Agora a importação leva a
    // coluna e a ação volta a existir, gatilhada só por `manage_client_mapping`
    // — a MESMA permissão de qualquer outro destino, sem célula própria.
    destinationsState.data = [
      destination({ id: 'd-conta', type: 'conta_contabil', name: 'Conta contábil' }),
      destination(),
    ];
    currentSearch = 'destination=conta_contabil';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('button', { name: /Importar/ })).toBeVisible();
  });
});

describe('ClientMappingScreen — destino padrão', () => {
  it('sem destino na URL abre no demonstrativo contábil, mesmo que não seja o primeiro', () => {
    destinationsState.data = [
      destination({
        id: 'd-conta',
        type: 'conta_contabil',
        name: 'Conta contábil',
        targetsCount: 0,
      }),
      destination(),
    ];
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('combobox', { name: 'Destino' })).toHaveTextContent(
      'Demonstrativo contábil',
    );
    expect(
      screen.queryByText('O catálogo de alvos deste destino está vazio'),
    ).not.toBeInTheDocument();
  });

  it('sem demonstrativo no catálogo, cai no primeiro', () => {
    destinationsState.data = [
      destination({ id: 'd-caixa', type: 'fluxo_de_caixa', name: 'Fluxo de caixa' }),
      destination({ id: 'd-conta', type: 'conta_contabil', name: 'Conta contábil' }),
    ];
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('combobox', { name: 'Destino' })).toHaveTextContent('Fluxo de caixa');
  });

  it('o destino da URL vence o padrão', () => {
    currentSearch = 'destination=fluxo_de_caixa';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('combobox', { name: 'Destino' })).toHaveTextContent('Fluxo de caixa');
  });

  // 86e3n70pn: escritório novo, demonstrativo sem alvos, plano contábil já importado.
  const semAlvos = () => [
    destination({ targetsCount: 0 }),
    destination({ id: 'd-conta', type: 'conta_contabil', name: 'Conta contábil', targetsCount: 0 }),
  ];

  it('demonstrativo sem alvos e cliente COM plano contábil: abre na conta contábil', () => {
    destinationsState.data = semAlvos();
    accountingProbe.total = 120;
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('combobox', { name: 'Destino' })).toHaveTextContent('Conta contábil');
  });

  it('demonstrativo sem alvos e cliente SEM plano contábil: abre no demonstrativo', () => {
    destinationsState.data = semAlvos();
    accountingProbe.total = 0;
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('combobox', { name: 'Destino' })).toHaveTextContent(
      'Demonstrativo contábil',
    );
  });

  it('defaultDestinationType: os dois casos da exceção, e o demonstrativo com alvos', () => {
    const withPlan = { clientHasAccountingChart: true };
    expect(defaultDestinationType(semAlvos(), withPlan)).toBe('conta_contabil');
    expect(defaultDestinationType(semAlvos(), { clientHasAccountingChart: false })).toBe(
      'demonstrativo_contabil',
    );
    expect(defaultDestinationType(semAlvos())).toBe('demonstrativo_contabil');
    expect(
      defaultDestinationType(
        [destination(), destination({ id: 'd-conta', type: 'conta_contabil', name: 'Conta' })],
        withPlan,
      ),
    ).toBe('demonstrativo_contabil');
    expect(defaultDestinationType([], withPlan)).toBe('');
  });
});

describe('ClientMappingScreen — catálogo vazio e alvos da origem (86e3n70pn)', () => {
  it('quem administra o catálogo vê "Cadastrar alvos" apontando o destino na tela nova', () => {
    authState.user = orgAdmin;
    destinationsState.data = [destination({ targetsCount: 0 })];
    render(<ClientMappingScreen clientId={TENANT} />);
    const notice = screen.getByTestId('mapping-empty-catalog');
    expect(within(notice).queryByText(/Peça ao administrador/)).not.toBeInTheDocument();
    expect(within(notice).getByRole('link', { name: 'Cadastrar alvos' })).toHaveAttribute(
      'href',
      '/configuracoes/destinos-de-para?destino=d-contabil',
    );
    expect(
      within(notice).getByRole('button', {
        name: 'Criar alvos a partir das contas de demonstrativo deste cliente',
      }),
    ).toBeVisible();
  });

  it('fora do demonstrativo, o aviso não oferece criar a partir da origem', () => {
    authState.user = orgAdmin;
    destinationsState.data = [
      destination({
        id: 'd-caixa',
        type: 'fluxo_de_caixa',
        name: 'Fluxo de caixa',
        targetsCount: 0,
      }),
    ];
    render(<ClientMappingScreen clientId={TENANT} />);
    const notice = screen.getByTestId('mapping-empty-catalog');
    expect(within(notice).getByRole('link', { name: 'Cadastrar alvos' })).toBeVisible();
    expect(within(notice).queryByRole('button', { name: /a partir/ })).not.toBeInTheDocument();
  });

  it('o gerente (sem a permissão do catálogo) lê o texto de sempre, sem botão', () => {
    destinationsState.data = [destination({ targetsCount: 0 })];
    render(<ClientMappingScreen clientId={TENANT} />);
    const notice = screen.getByTestId('mapping-empty-catalog');
    expect(within(notice).getByText(/Peça ao administrador da organização/)).toBeVisible();
    expect(within(notice).queryByRole('link')).not.toBeInTheDocument();
    expect(within(notice).queryByRole('button')).not.toBeInTheDocument();
  });

  it('a prévia mostra o que entra e cria SÓ os faltantes, depois de confirmar', async () => {
    const user = userEvent.setup();
    authState.user = orgAdmin;
    destinationsState.data = [destination({ targetsCount: 0 })];
    originPreviewState.data = {
      state: 'ok',
      destinationId: 'd-contabil',
      namesResolved: false,
      candidates: [
        {
          code: '1.01',
          name: 'Receita bruta',
          categories: 3,
          exists: false,
          active: null,
          creatable: true,
        },
        {
          code: '2.01',
          name: 'Despesas',
          categories: 1,
          exists: true,
          active: true,
          creatable: false,
        },
        { code: '2.02', name: null, categories: 2, exists: false, active: null, creatable: false },
      ],
    };
    createTargetsState.mutateAsync.mockResolvedValue([
      { id: 't1', code: '1.01', name: 'Receita bruta', active: true },
    ]);
    render(<ClientMappingScreen clientId={TENANT} />);

    await user.click(
      screen.getByRole('button', {
        name: 'Criar alvos a partir das contas de demonstrativo deste cliente',
      }),
    );
    const dialog = await screen.findByRole('dialog', { name: 'Criar alvos a partir da origem' });
    expect(within(dialog).getByTestId('origin-targets-summary')).toHaveTextContent(
      '1 conta nova será criada como alvo; 2 já existem ou ficam de fora.',
    );
    expect(within(dialog).getByText(/não respondeu agora com o nome/)).toBeVisible();
    expect(within(dialog).getByText('Já no catálogo')).toBeVisible();
    expect(within(dialog).getByText('Sem nome agora')).toBeVisible();
    // Nada foi gravado só por abrir: a criação espera o clique.
    expect(createTargetsState.mutateAsync).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Criar alvo' }));
    expect(createTargetsState.mutateAsync).toHaveBeenCalledWith({
      targets: [{ code: '1.01', name: 'Receita bruta' }],
    });
  });

  it('cliente sem as categorias do Omie sincronizadas: aponta a tela de categorias', async () => {
    const user = userEvent.setup();
    authState.user = orgAdmin;
    destinationsState.data = [destination({ targetsCount: 0 })];
    originPreviewState.data = {
      state: 'sem_plano_de_contas',
      destinationId: 'd-contabil',
      namesResolved: true,
      candidates: [],
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /a partir das contas de demonstrativo/ }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByRole('link', { name: 'Ir para Categorias do Omie' }),
    ).toHaveAttribute('href', `/clientes/${TENANT}/plano-de-contas`);
    expect(within(dialog).getByRole('button', { name: 'Criar alvo' })).toBeDisabled();
  });

  it('com alvos no catálogo, a ação fica na barra ao lado de "Iniciar de-para"', () => {
    authState.user = orgAdmin;
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('button', { name: 'Criar alvos a partir da origem' })).toBeVisible();
    expect(screen.queryByTestId('mapping-empty-catalog')).not.toBeInTheDocument();
  });

  it('o gerente não vê a ação na barra', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(
      screen.queryByRole('button', { name: 'Criar alvos a partir da origem' }),
    ).not.toBeInTheDocument();
  });
});

describe('ClientMappingScreen — "Como funciona" (86e3n70pn)', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('explica destino, alvo, decisão e herdada, e recolhe lembrando a escolha', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    const block = screen.getByRole('region', { name: 'Como funciona' });
    for (const term of ['Destino', 'Alvo', 'Decisão', 'Herdada']) {
      expect(within(block).getByText(term, { selector: 'dt' })).toBeVisible();
    }
    await user.click(screen.getByRole('button', { name: 'Ocultar como funciona' }));
    expect(screen.getByRole('button', { name: 'Mostrar como funciona' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(window.localStorage.getItem('adl:totais-recolhidos:de-para-como-funciona')).toBe('1');
  });
});

describe('ClientMappingScreen — lista, filtros e busca (R6)', () => {
  // 86e3f55bd: o `pageSize` padrão foi de 20 para 50 (a página rola agora);
  // os dois testes abaixo mudaram de propósito só nesse número.
  it('filtro e busca vão ao SERVIDOR, com page/pageSize sempre presentes', () => {
    currentSearch = 'situation=herdada&code=2.01';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(lastListParams).toEqual({ page: 1, pageSize: 50, situation: 'herdada', code: '2.01' });
    expect(buildClientMappingQuery(lastListParams!)).toBe(
      'page=1&pageSize=50&situation=herdada&code=2.01',
    );
  });

  it('situação inválida na URL degrada para "sem filtro" (nunca 400 na carga)', () => {
    currentSearch = 'situation=qualquer';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(lastListParams?.situation).toBeNull();
    expect(buildClientMappingQuery(lastListParams!)).toBe('page=1&pageSize=50');
  });

  it('a busca é rotulada "por código" e diz que o nome não é pesquisável', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    const campo = screen.getByLabelText('Buscar por código da categoria');
    expect(campo).toHaveAccessibleDescription(/só pelo código.*não é pesquisável/);
  });

  it('as quatro situações têm selos distintos, e a divergência tem nome acessível', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    const rows = screen.getAllByRole('row');
    expect(within(rows[1]!).getByText('Herdada da origem')).toBeVisible();
    expect(within(rows[2]!).getByText('Confirmada')).toBeVisible();
    expect(within(rows[3]!).getByText('Não mapear')).toBeVisible();
    expect(within(rows[4]!).getByText('Sem decisão')).toBeVisible();
    expect(
      within(rows[2]!).getByRole('img', {
        name: /Divergente da origem \(categoria 2\.01\.02\).*3\.9/,
      }),
    ).toBeVisible();
    // Nome não resolvido: o código segue na célula, nunca vazio.
    expect(within(rows[4]!).getByText('1.04.02')).toBeVisible();
    expect(within(rows[4]!).getByText('Nome indisponível agora')).toBeVisible();
  });

  it('universo vazio sem filtro: estado explicativo apontando as categorias do Omie', () => {
    listState.data = {
      ...listState.data!,
      data: [],
      pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 },
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByText('Ainda não há categorias para classificar')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Ir para Categorias do Omie' })).toHaveAttribute(
      'href',
      `/clientes/${TENANT}/plano-de-contas`,
    );
  });

  it('catálogo de alvos vazio: orienta o administrador da organização', () => {
    destinationsState.data = [destination({ targetsCount: 0 })];
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByText('O catálogo de alvos deste destino está vazio')).toBeVisible();
    expect(screen.getByText(/Peça ao administrador da organização/)).toBeVisible();
  });

  it('erro na lista: mensagem e "Tentar novamente"', () => {
    listState.isError = true;
    listState.error = new ApiError(500, {
      code: 'INTERNAL_ERROR',
      message: 'x',
      userMessage: 'Ocorreu um erro inesperado. Tente novamente.',
    });
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Não foi possível carregar o de-para');
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });
});

describe('ClientMappingScreen — contadores que filtram (86e3f55bd)', () => {
  it('a tabela contador → situação, e o ativo é exato', () => {
    expect(mappingCountFilterSituation('total')).toBeNull();
    const keys: MappingCountKey[] = ['total', 'herdada', 'confirmada', 'nao_mapear', 'sem_decisao'];
    for (const key of keys) {
      if (key !== 'total') expect(mappingCountFilterSituation(key)).toBe(key);
      const situacao = mappingCountFilterSituation(key);
      expect(keys.filter((k) => isMappingCountActive(situacao, k))).toEqual([key]);
    }
  });

  it('os cinco contadores vêm do envelope (universo), não da página', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    const esperado: Array<[string, string]> = [
      ['Categorias', '60'],
      ['Herdadas da origem', '20'],
      ['Confirmadas', '12'],
      ['Não mapear', '10'],
      ['Sem decisão', '18'],
    ];
    for (const [rotulo, valor] of esperado) {
      const termo = screen.getByText(rotulo, { selector: 'dt' });
      expect(termo.parentElement).toHaveTextContent(valor);
    }
    expect(
      screen.getByText(/^Contagens do destino inteiro, na competência corrente/),
    ).toBeVisible();
  });

  it('botão no <dd>, nome com rótulo + valor, e aria-pressed pelo recorte da URL', () => {
    currentSearch = 'situation=sem_decisao';
    render(<ClientMappingScreen clientId={TENANT} />);
    const semDecisao = screen.getByRole('button', {
      name: 'Sem decisão: 18 categorias. Filtrar a lista',
    });
    expect(semDecisao.closest('dd')).not.toBeNull();
    expect(semDecisao).toHaveAttribute('aria-pressed', 'true');
    expect(semDecisao).toHaveClass('bg-accent', 'text-accent-foreground', 'ring-2');
    expect(screen.getByRole('button', { name: /^Categorias:/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('clicar aplica a situação e zera a página; o código fica', async () => {
    const user = userEvent.setup();
    currentSearch = 'page=3&code=2.01';
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /^Herdadas da origem:/ }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('situation=herdada');
    expect(url).toContain('code=2.01');
    expect(url).not.toContain('page=3');
  });

  it('segundo clique desfaz; "Categorias" limpa a situação', async () => {
    const user = userEvent.setup();
    currentSearch = 'situation=confirmada';
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /^Confirmadas:/ }));
    expect(String(replaceMock.mock.calls.at(-1)?.[0])).not.toContain('situation=');
    await user.click(screen.getByRole('button', { name: /^Categorias:/ }));
    expect(String(replaceMock.mock.calls.at(-1)?.[0])).not.toContain('situation=');
  });

  it('o Select de situação saiu da barra', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.queryByLabelText('Situação')).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Situação' })).toBeNull();
  });

  it('etiquetas removíveis: uma por parâmetro, remover limpa só aquele', async () => {
    const user = userEvent.setup();
    currentSearch = 'situation=sem_decisao&code=2.01&page=2';
    render(<ClientMappingScreen clientId={TENANT} />);
    const lista = screen.getByRole('list', { name: 'Filtros ativos' });
    expect(
      within(lista)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(['Sem decisão', 'Código 2.01']);

    await user.click(screen.getByRole('button', { name: 'Remover filtro Código 2.01' }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).not.toContain('code=');
    expect(url).toContain('situation=sem_decisao');
    expect(url).not.toContain('page=2');
    expect(screen.getByLabelText('Buscar por código da categoria')).toHaveValue('');
  });

  it('a página rola e a tabela não: sem fill, cabeçalho grudado na rolagem da página', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    const region = screen.getByRole('region', { name: 'Categorias do de-para (rolável)' });
    expect(region).toHaveClass('overflow-auto', 'xl:overflow-clip');
    expect(region).not.toHaveClass('min-h-0');
    expect(region.className).toContain('[&_thead_th]:xl:sticky');
    const card = region.parentElement;
    expect(card).toHaveClass('overflow-clip');
    const area = region.closest('[aria-busy]');
    expect(area).not.toHaveClass('flex-1', 'min-h-[24rem]', 'lg:min-h-[8rem]');
    expect(region.closest('section')).not.toHaveClass('h-full');
    // 86e3gkd80: a raiz declara o padrão, e é isso que solta a altura fixa do shell.
    expect(screen.getByRole('heading', { level: 1 }).closest('section')).toHaveAttribute(
      'data-page-scroll',
    );
    const barra = screen.getByRole('navigation', { name: 'Paginação de categorias' });
    expect(region).not.toContainElement(barra);
    expect(region.compareDocumentPosition(barra) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('50 categorias por página por padrão', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(DEFAULT_PAGE_SIZE).toBe(50);
    expect(lastListParams).toMatchObject({ pageSize: 50 });
  });

  it('clicar na linha abre a gaveta do item certo para quem edita (sem tabIndex/role)', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    const linha = screen.getAllByRole('row')[4]!; // 1.04.02, sem decisão
    expect(linha).not.toHaveAttribute('tabindex');
    expect(linha).not.toHaveAttribute('role', 'button');
    expect(linha).toHaveClass('cursor-pointer');

    await user.click(within(linha).getByText('Nome indisponível agora'));
    const gaveta = await screen.findByRole('dialog');
    expect(within(gaveta).getByRole('heading', { name: 'Decisão do de-para' })).toBeVisible();
    expect(gaveta).toHaveTextContent('1.04.02');
    expect(gaveta).not.toHaveTextContent('2.01.01');
  });

  it('o botão da coluna não abre a gaveta duas vezes', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: 'Alterar a categoria 2.01.01' }));
    expect(await screen.findAllByRole('dialog')).toHaveLength(1);
  });

  it('para o operador a linha não é clicável e nada abre', async () => {
    const user = userEvent.setup();
    authState.user = clientOperator;
    render(<ClientMappingScreen clientId={TENANT} />);
    const linha = screen.getAllByRole('row')[1]!;
    expect(linha).not.toHaveClass('cursor-pointer');
    await user.click(within(linha).getByText('Aluguel'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('ClientMappingScreen — decisão com vigência (R4)', () => {
  it('pede a competência de início com padrão = corrente e diz que cria vigência nova', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: 'Alterar a categoria 2.01.01' }));

    const gaveta = await screen.findByRole('dialog');
    expect(within(gaveta).getByLabelText('Competência de início')).toHaveValue('2026-09');
    expect(within(gaveta).getByTestId('vigencia-explanation')).toHaveTextContent(
      /Cria uma vigência nova a partir de Setembro de 2026.*continua valendo até Agosto de 2026/,
    );
    // "Não mapear" é oferecido como DECISÃO, distinta de "sem decisão".
    expect(within(gaveta).getByText(/"Não mapear" é uma decisão registrada/)).toBeVisible();
  });

  it('retroativa: mostra as competências afetadas e só então confirma', async () => {
    const user = userEvent.setup();
    writeState.mutateAsync = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError(409, {
          code: 'RETROATIVA_REQUER_CONFIRMACAO',
          message: 'x',
          userMessage: 'Confirme.',
          details: { competences: '2026-07,2026-08' },
        }),
      )
      .mockResolvedValueOnce({ effectiveFrom: '2026-07', created: 1, resolved: 0, unchanged: 0 });

    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: 'Alterar a categoria 2.01.01' }));
    const gaveta = await screen.findByRole('dialog');
    const mes = within(gaveta).getByLabelText('Competência de início');
    await user.clear(mes);
    await user.type(mes, '2026-07');
    await user.click(within(gaveta).getByRole('button', { name: 'Gravar decisão' }));

    const aviso = await within(gaveta).findByRole('alert');
    expect(aviso).toHaveTextContent(/Julho de 2026, Agosto de 2026/);
    expect(writeState.mutateAsync).toHaveBeenLastCalledWith(
      expect.objectContaining({ effectiveFrom: '2026-07', confirmRetroactive: false }),
    );

    await user.click(
      within(gaveta).getByRole('button', { name: 'Confirmar alteração retroativa' }),
    );
    await waitFor(() =>
      expect(writeState.mutateAsync).toHaveBeenLastCalledWith(
        expect.objectContaining({
          categoryCode: '2.01.01',
          decision: 'alvo',
          targetCode: '3.1',
          effectiveFrom: '2026-07',
          confirmRetroactive: true,
        }),
      ),
    );
  });

  it('competência materializada: recusa com as competências nomeadas, sem confirmar', async () => {
    const user = userEvent.setup();
    writeState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'COMPETENCIA_MATERIALIZADA',
        message: 'x',
        userMessage: 'Esta alteração atingiria uma competência que já teve o de-para aplicado.',
        details: { competences: '2026-06' },
      }),
    );
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: 'Alterar a categoria 2.01.01' }));
    const gaveta = await screen.findByRole('dialog');
    await user.click(within(gaveta).getByRole('button', { name: 'Gravar decisão' }));
    expect(await within(gaveta).findByRole('alert')).toHaveTextContent(
      /já teve o de-para aplicado.*Materializadas: Junho de 2026/,
    );
    expect(within(gaveta).getByRole('button', { name: 'Gravar decisão' })).toBeDisabled();
  });
});

describe('ClientMappingScreen — lote das herdadas (R6)', () => {
  it('conta no servidor (confirm=false) e mostra a quantidade afetada', async () => {
    const user = userEvent.setup();
    confirmInheritedState.data = { affected: 3, applied: false, result: null };
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Confirmar herdadas/ }));

    // A contagem já sai para a competência de início padrão (a corrente).
    expect(confirmInheritedState.mutate).toHaveBeenCalledWith(
      { confirm: false, confirmRetroactive: false, effectiveFrom: '2026-09' },
      expect.anything(),
    );
    const dialogo = await screen.findByRole('dialog');
    expect(within(dialogo).getByTestId('confirm-inherited-count')).toHaveTextContent(
      '3 decisões herdadas serão confirmadas.',
    );
    await user.click(within(dialogo).getByRole('button', { name: 'Confirmar 3' }));
    await waitFor(() =>
      expect(confirmInheritedState.mutateAsync).toHaveBeenCalledWith({
        confirm: true,
        effectiveFrom: '2026-09',
        confirmRetroactive: false,
      }),
    );
  });

  it('manda o recorte por código da lista e diz isso no diálogo', async () => {
    const user = userEvent.setup();
    currentSearch = 'code=2.01';
    confirmInheritedState.data = { affected: 2, applied: false, result: null };
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Confirmar herdadas/ }));

    expect(confirmInheritedState.mutate).toHaveBeenCalledWith(
      { confirm: false, confirmRetroactive: false, effectiveFrom: '2026-09', code: '2.01' },
      expect.anything(),
    );
    const dialogo = await screen.findByRole('dialog');
    expect(within(dialogo).getByTestId('confirm-inherited-count')).toHaveTextContent(
      '2 decisões herdadas serão confirmadas (código começando por "2.01").',
    );
    await user.click(within(dialogo).getByRole('button', { name: 'Confirmar 2' }));
    await waitFor(() =>
      expect(confirmInheritedState.mutateAsync).toHaveBeenCalledWith({
        confirm: true,
        effectiveFrom: '2026-09',
        confirmRetroactive: false,
        code: '2.01',
      }),
    );
  });

  it('filtro de situação ativo: avisa que o lote ignora o filtro (só herdadas)', async () => {
    const user = userEvent.setup();
    currentSearch = 'situation=confirmada';
    confirmInheritedState.data = { affected: 3, applied: false, result: null };
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Confirmar herdadas/ }));
    const dialogo = await screen.findByRole('dialog');
    expect(dialogo).toHaveTextContent(/O filtro de situação não se aplica/);
  });

  it('trocar a competência de início reconta no servidor (a vigência muda o lote)', async () => {
    const user = userEvent.setup();
    confirmInheritedState.data = { affected: 3, applied: false, result: null };
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Confirmar herdadas/ }));
    const dialogo = await screen.findByRole('dialog');
    const mes = within(dialogo).getByLabelText('Competência de início');
    await user.clear(mes);
    await user.type(mes, '2026-07');
    await waitFor(() =>
      expect(confirmInheritedState.mutate).toHaveBeenLastCalledWith(
        { confirm: false, confirmRetroactive: false, effectiveFrom: '2026-07' },
        expect.anything(),
      ),
    );
  });

  it('"Iniciar de-para" não diz que "a decisão atual continua valendo"', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Iniciar de-para/ }));
    const dialogo = await screen.findByRole('dialog');
    const explicacao = within(dialogo).getByTestId('vigencia-explanation');
    expect(explicacao).toHaveTextContent(/Só as categorias sem decisão recebem a herdada/);
    expect(explicacao).not.toHaveTextContent(/continua valendo/);
  });
});

describe('ClientMappingScreen — prévia da competência (R0 · R5)', () => {
  it('mostra o estado da base e as quatro situações em BRL e quantidade', () => {
    currentSearch = 'view=previa&competence=2026-06';
    render(<ClientMappingScreen clientId={TENANT} />);
    const previa = screen.getByTestId('mapping-preview');
    for (const rotulo of ['Com alvo', 'Não mapear', 'Sem decisão', 'Sem categoria de origem']) {
      expect(within(previa).getByText(rotulo, { selector: 'dt' })).toBeVisible();
    }
    expect(
      within(previa).getByText('Sem decisão', { selector: 'dt' }).parentElement,
    ).toHaveTextContent(brl('12.400,00'));
    expect(within(previa).getByText('88,9%')).toBeVisible();
    expect(within(previa).getByText('1,1%')).toBeVisible();
  });

  it('versões materializadas vêm da rota: autor, data, cobertura e o selo de parcial', () => {
    currentSearch = 'view=previa&competence=2026-06';
    render(<ClientMappingScreen clientId={TENANT} />);
    const versoes = screen.getByTestId('mapping-versions');
    const linhas = within(versoes).getAllByRole('listitem');
    expect(linhas).toHaveLength(2);
    expect(linhas[0]).toHaveTextContent(/Versão 2/);
    expect(linhas[0]).toHaveTextContent(/mais recente/);
    expect(linhas[0]).toHaveTextContent(/Cobertura 88,9%/);
    expect(linhas[0]).not.toHaveTextContent(/cobertura parcial/);
    // Autor com e-mail: nome na tela, e-mail na dica acessível (86e2n39f1).
    expect(
      within(linhas[0]!).getByRole('img', { name: 'Contador Parceiro — contador@parceiro.com.br' }),
    ).toBeVisible();
    // Autor MASCARADO pelo servidor: texto simples, sem dica.
    expect(linhas[1]).toHaveTextContent(/Versão 1/);
    expect(linhas[1]).toHaveTextContent(/Equipe Hologram/);
    expect(within(linhas[1]!).queryByRole('img')).not.toBeInTheDocument();
    expect(linhas[1]).toHaveTextContent(/cobertura parcial/);
    expect(linhas[1]).toHaveTextContent(/Cobertura 71,5%/);
  });

  it('sem versão materializada: diz isso, sem lista', () => {
    currentSearch = 'view=previa&competence=2026-06';
    materializationsState.data = [];
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByText('Nenhuma versão materializada nesta competência.')).toBeVisible();
    expect(
      within(screen.getByTestId('mapping-versions')).queryByRole('list'),
    ).not.toBeInTheDocument();
  });

  it('estado da base com erro: "Tentar novamente", e a prévia nem é pedida', async () => {
    const user = userEvent.setup();
    currentSearch = 'view=previa&competence=2026-06';
    syncStateQuery.data = undefined;
    syncStateQuery.isError = true;
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(previewEnabled).toBe(false);
    const alerta = screen.getByRole('alert');
    expect(alerta).toHaveTextContent(/Não foi possível ler o estado da base/);
    await user.click(within(alerta).getByRole('button', { name: 'Tentar novamente' }));
    expect(syncStateQuery.refetch).toHaveBeenCalledTimes(1);
  });

  it('base nunca sincronizada: instrução de sincronizar, e a prévia nem é pedida', () => {
    currentSearch = 'view=previa';
    syncStateQuery.data = {
      competence: '2026-09',
      neverSynced: true,
      syncedAt: null,
      syncFailedAt: null,
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(previewEnabled).toBe(false);
    expect(screen.getByText(/ainda não tem base de movimentos/)).toBeVisible();
    expect(screen.getByText(/Sincronize a competência \(botão acima\)/)).toBeVisible();
    expect(screen.queryByTestId('mapping-preview')).not.toBeInTheDocument();
  });

  it('base nunca sincronizada: "botão acima" só quando o botão existe', () => {
    currentSearch = 'view=previa';
    syncStateQuery.data = {
      competence: '2026-09',
      neverSynced: true,
      syncedAt: null,
      syncFailedAt: null,
    };
    // Operador: sem permissão de sincronizar, a instrução manda pedir.
    authState.user = clientOperator;
    const { unmount } = render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.queryByText(/botão acima/)).not.toBeInTheDocument();
    expect(screen.getByText(/Peça a alguém da equipe/)).toBeVisible();
    unmount();

    // Manager com a origem sem conexão: o botão não existe; a instrução aponta a origem.
    authState.user = manager;
    clientDetailState.data = { ...clientDetailState.data!, origin_status: 'sem_origem' };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(
      screen.queryByRole('button', { name: /Sincronizar competência/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/botão acima/)).not.toBeInTheDocument();
    expect(screen.getByText(/depende de uma origem conectada e ativa/)).toBeVisible();
  });

  it('409 BASE_NAO_SINCRONIZADA vira a instrução, não um erro genérico', () => {
    currentSearch = 'view=previa';
    previewState.data = undefined;
    previewState.isError = true;
    previewState.error = new ApiError(409, {
      code: 'BASE_NAO_SINCRONIZADA',
      message: 'x',
      userMessage: 'Sincronize.',
    });
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByText(/ainda não tem base de movimentos/)).toBeVisible();
    expect(screen.queryByText('Não foi possível calcular a prévia')).not.toBeInTheDocument();
  });

  it('origem por ARQUIVO (S14): sem "Sincronizar competência", com "Enviar arquivo do mês"', () => {
    currentSearch = 'view=previa&competence=2026-06';
    clientDetailState.data = {
      ...clientDetailState.data!,
      connections: [
        {
          id: 'arq-1',
          provider_type: 'arquivo',
          label: 'Arquivo',
          status: 'ativa',
          last_checked_at: null,
          accounts_synced_at: null,
          capabilities: ['listar_lancamentos'],
        },
      ],
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    // O servidor responderia 409 `ORIGEM_POR_ARQUIVO`: a ação some (§4.9)…
    expect(
      screen.queryByRole('button', { name: /Sincronizar competência/ }),
    ).not.toBeInTheDocument();
    // …e no lugar entra o link para a aba de envio, já com a competência da prévia.
    expect(screen.getByRole('link', { name: /Enviar arquivo do mês/ })).toHaveAttribute(
      'href',
      `/clientes/${TENANT}/origem-arquivo?competence=2026-06`,
    );
    // 86e3g9ua7: a PALAVRA acompanha a origem. "Sincronizada em" aqui mandaria
    // procurar um botão "Sincronizar" que esta mesma tela acabou de esconder.
    const base = screen.getByTestId('mapping-base-state');
    expect(base).toHaveTextContent(/Arquivo processado em/);
    expect(base).not.toHaveTextContent(/incroniz/);
  });

  it('origem por ARQUIVO com base nunca sincronizada: a instrução fala do envio, não do sync', () => {
    currentSearch = 'view=previa';
    syncStateQuery.data = {
      competence: '2026-09',
      neverSynced: true,
      syncedAt: null,
      syncFailedAt: null,
    };
    clientDetailState.data = {
      ...clientDetailState.data!,
      connections: [
        {
          id: 'arq-1',
          provider_type: 'arquivo',
          label: 'Arquivo',
          status: 'ativa',
          last_checked_at: null,
          accounts_synced_at: null,
          capabilities: ['listar_lancamentos'],
        },
      ],
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByText(/alimentada pelo envio do arquivo do mês/)).toBeVisible();
    expect(screen.queryByText(/Sincronize a competência/)).not.toBeInTheDocument();
  });

  it('origem por ARQUIVO sem `upload_client_file`: nem o link de envio, e a instrução diz a quem pedir', () => {
    deniedPermissions.add('upload_client_file');
    currentSearch = 'view=previa';
    syncStateQuery.data = {
      competence: '2026-09',
      neverSynced: true,
      syncedAt: null,
      syncFailedAt: null,
    };
    clientDetailState.data = {
      ...clientDetailState.data!,
      connections: [
        {
          id: 'arq-1',
          provider_type: 'arquivo',
          label: 'Arquivo',
          status: 'ativa',
          last_checked_at: null,
          accounts_synced_at: null,
          capabilities: ['listar_lancamentos'],
        },
      ],
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.queryByRole('link', { name: /Enviar arquivo do mês/ })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Sincronizar competência/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Peça a alguém da equipe com acesso de envio/)).toBeVisible();
  });

  it('cliente Omie segue como antes: "Sincronizar competência" e nenhum link de envio (regressão)', () => {
    currentSearch = 'view=previa';
    clientDetailState.data = {
      ...clientDetailState.data!,
      connections: [
        {
          id: 'omie-1',
          provider_type: 'omie',
          label: 'Omie',
          status: 'ativa',
          last_checked_at: '2026-09-22T12:00:00Z',
          accounts_synced_at: '2026-09-22T12:00:00Z',
          capabilities: [
            'verificar_credencial',
            'listar_contas',
            'listar_lancamentos',
            'escrever',
            'listar_titulos_em_aberto',
          ],
        },
      ],
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('button', { name: /Sincronizar competência/ })).toBeVisible();
    expect(screen.queryByRole('link', { name: /Enviar arquivo do mês/ })).not.toBeInTheDocument();
  });

  it('sincronizar chama o servidor com a competência da tela', async () => {
    const user = userEvent.setup();
    currentSearch = 'view=previa&competence=2026-06';
    syncMovementsState.mutateAsync = vi.fn().mockResolvedValue({
      movimentos: 80,
      semCategoria: 0,
      contas: 6,
      ausentes: 0,
      state: syncStateQuery.data,
    });
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Sincronizar competência/ }));
    expect(syncMovementsState.mutateAsync).toHaveBeenCalledWith('2026-06');
  });

  it('materializar com valor sem decisão exige a confirmação extra', async () => {
    const user = userEvent.setup();
    currentSearch = 'view=previa&competence=2026-06';
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Materializar/ }));

    const dialogo = await screen.findByRole('alertdialog');
    const confirmar = within(dialogo).getByRole('button', { name: 'Materializar versão 3' });
    expect(confirmar).toBeDisabled();
    expect(dialogo).toHaveTextContent(/R\$\s*12\.400,00 em 3 movimentos sem decisão/);

    await user.click(within(dialogo).getByRole('switch'));
    expect(confirmar).toBeEnabled();
    await user.click(confirmar);
    await waitFor(() =>
      expect(materializeState.mutateAsync).toHaveBeenCalledWith({
        competence: '2026-06',
        previewToken: 'tok-1',
        confirmPartialCoverage: true,
      }),
    );
  });

  it('materializar com cobertura total não pede a confirmação extra', async () => {
    const user = userEvent.setup();
    currentSearch = 'view=previa&competence=2026-06';
    previewState.data = preview({
      situations: { ...preview().situations, semDecisao: { amount: '0.00', count: 0 } },
      undecidedCategories: [],
    });
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Materializar/ }));
    const dialogo = await screen.findByRole('alertdialog');
    expect(within(dialogo).queryByRole('switch')).not.toBeInTheDocument();
    expect(within(dialogo).getByRole('button', { name: 'Materializar versão 3' })).toBeEnabled();
  });
});

describe('ClientMappingScreen — importação com prévia (R6)', () => {
  it('mostra criadas · alteradas · ignoradas e as linhas recusadas ANTES de aplicar', async () => {
    const user = userEvent.setup();
    importPreviewState.mutateAsync = vi.fn().mockResolvedValue({
      effectiveFrom: '2026-09',
      created: 5,
      altered: 2,
      altersConfirmed: 1,
      ignored: 10,
      rejected: [
        { line: 7, categoryCode: '9.99', targetCode: '3.1', reason: 'categoria_inexistente' },
      ],
    });
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Importar/ }));
    const gaveta = await screen.findByRole('dialog');

    const arquivo = new File(['PK'], 'de-para.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    await user.upload(within(gaveta).getByLabelText('Planilha (.xlsx)'), arquivo);
    await user.click(within(gaveta).getByRole('button', { name: 'Gerar prévia' }));

    const resumo = await within(gaveta).findByTestId('mapping-import-preview');
    expect(within(resumo).getByText('Criadas').parentElement).toHaveTextContent('5');
    expect(within(resumo).getByText('Alteradas').parentElement).toHaveTextContent('2');
    expect(within(resumo).getByText('Ignoradas').parentElement).toHaveTextContent('10');
    expect(within(gaveta).getByText('Categoria inexistente no cliente')).toBeVisible();
    expect(importApplyState.mutateAsync).not.toHaveBeenCalled();

    // Altera 1 confirmada: aplicar só depois da confirmação explícita.
    const aplicar = within(gaveta).getByRole('button', { name: 'Aplicar importação' });
    expect(aplicar).toBeDisabled();
    await user.click(within(gaveta).getByRole('switch'));
    await user.click(aplicar);
    await waitFor(() =>
      expect(importApplyState.mutateAsync).toHaveBeenCalledWith({
        file: arquivo,
        effectiveFrom: '2026-09',
        confirmRetroactive: false,
      }),
    );
  });
});

describe('ClientMappingScreen — a11y', () => {
  it('lista com edição sem violações critical/serious', async () => {
    const { container } = render(<ClientMappingScreen clientId={TENANT} />);
    await assertNoA11yViolations(container);
  });

  it('prévia sem violações critical/serious', async () => {
    currentSearch = 'view=previa&competence=2026-06';
    const { container } = render(<ClientMappingScreen clientId={TENANT} />);
    await assertNoA11yViolations(container);
  });
});
