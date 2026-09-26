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
 *   - o lote mostra a quantidade afetada que veio do servidor;
 *   - a prévia mostra o estado da base e as quatro situações; base nunca
 *     sincronizada mostra a instrução (e nem pede a prévia); materializar com
 *     valor sem decisão exige a confirmação extra;
 *   - a importação mostra a prévia com as linhas recusadas antes de aplicar;
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
}));

const clientDetailState = {
  data: undefined as
    | {
        closed_at: string | null;
        origin_status: string;
        organization: { id: string; name: string };
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

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import { ClientMappingScreen } from '@/components/features/client-mapping/client-mapping-screen';
import { ApiError } from '@/lib/api/client';
import { buildClientMappingQuery, type ListClientMappingParams } from '@/lib/api/client-mapping';
import type {
  AuthenticatedUser,
  MappingDestination,
  MappingListItem,
  MappingListResponse,
  MappingPreview,
  MappingTarget,
  MovementsSyncState,
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

const brl = (valor: string) => new RegExp(`R\\$\\s*${valor.replace(/\./g, '\\.')}`);

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentSearch = '';
  replaceMock.mockClear();
  lastListParams = undefined;
  previewEnabled = undefined;
  authState.user = manager;
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

  it('"Iniciar de-para" só existe no destino que herda', () => {
    currentSearch = 'destination=fluxo_de_caixa';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.queryByRole('button', { name: /Iniciar de-para/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Confirmar herdadas/ })).toBeVisible();
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
});

describe('ClientMappingScreen — lista, filtros e busca (R6)', () => {
  it('filtro e busca vão ao SERVIDOR, com page/pageSize sempre presentes', () => {
    currentSearch = 'situation=herdada&code=2.01';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(lastListParams).toEqual({ page: 1, pageSize: 20, situation: 'herdada', code: '2.01' });
    expect(buildClientMappingQuery(lastListParams!)).toBe(
      'page=1&pageSize=20&situation=herdada&code=2.01',
    );
  });

  it('situação inválida na URL degrada para "sem filtro" (nunca 400 na carga)', () => {
    currentSearch = 'situation=qualquer';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(lastListParams?.situation).toBeNull();
    expect(buildClientMappingQuery(lastListParams!)).toBe('page=1&pageSize=20');
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

  it('universo vazio sem filtro: estado explicativo apontando o plano de contas', () => {
    listState.data = {
      ...listState.data!,
      data: [],
      pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 },
    };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByText('Ainda não há categorias para classificar')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Ir para o plano de contas' })).toHaveAttribute(
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

    expect(confirmInheritedState.mutate).toHaveBeenCalledWith(
      { confirm: false, confirmRetroactive: false },
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
    // Versões materializadas, só-leitura.
    expect(within(previa).getByText('Versão 2 (mais recente)')).toBeVisible();
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
    expect(screen.getByText(/Sincronize a competência/)).toBeVisible();
    expect(screen.queryByTestId('mapping-preview')).not.toBeInTheDocument();
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
