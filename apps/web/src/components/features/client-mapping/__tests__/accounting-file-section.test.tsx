/**
 * "Gerar arquivo" e o histórico de gerações no de-para (FRONT 13.6).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). No worktree do agent: `--cache=false`.
 *
 * Cobre, em jsdom, os critérios de aceite:
 *   - a seção (e a ação por versão) existe SÓ no `conta_contabil` e SÓ com
 *     `generate_accounting_file`: plataforma, admin e gerente veem; gerente do
 *     cliente e operador não (teste por papel);
 *   - um layout → gera direto; mais de um → diálogo com seletor; nenhum →
 *     estado que orienta conforme `manage_export_layouts`;
 *   - sem materialização → ação desabilitada com a instrução;
 *   - cada recusa 409 vira ESTADO com a `userMessage` e os códigos (as
 *     parcelas na partição; campo e motivo no texto) — nunca toast;
 *   - histórico: data, versão, layout, linhas, total; "Baixar" pelo BFF com o
 *     nome do servidor; `ARQUIVO_DIVERGENTE` como erro claro;
 *   - cliente encerrado: sem gerar e sem baixar, histórico visível;
 *   - axe-core sem `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let currentSearch = '';
const replaceMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/clientes/c1/de-para',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const { toastError, toastSuccess, downloadSpy } = vi.hoisted(() => ({
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  downloadSpy: vi.fn(),
}));
vi.mock('sonner', () => ({
  toast: { success: toastSuccess, info: vi.fn(), error: toastError },
}));
vi.mock('@/lib/download', () => ({ triggerBrowserDownload: downloadSpy }));

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
const syncStateQuery = {
  data: undefined as MovementsSyncState | undefined,
  isLoading: false,
  isError: false,
  refetch: vi.fn(),
};
const materializationsState = {
  data: [] as MaterializationSummary[],
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

vi.mock('@/hooks/use-client-mapping', () => ({
  useMappingDestinations: () => destinationsState,
  useMappingTargets: () => ({ data: [], isLoading: false, isError: false }),
  useClientMappingList: () => listState,
  useMovementsSyncState: () => syncStateQuery,
  useMappingMaterializations: () => materializationsState,
  useMappingPreview: () => previewState,
  useSyncMovements: () => mutationState(),
  useWriteMappingDecision: () => mutationState(),
  useConfirmInheritedDecisions: () => mutationState(),
  useInheritMapping: () => mutationState(),
  useMaterializeMapping: () => mutationState(),
  useExportClientMapping: () => mutationState(),
  usePreviewMappingImport: () => mutationState(),
  useApplyMappingImport: () => mutationState(),
}));

vi.mock('@/hooks/use-client-accounting-chart', () => ({
  useAccountingChartList: () => ({
    data: { data: [], pagination: { page: 1, pageSize: 1, total: 3, totalPages: 1 } },
    isLoading: false,
    isFetching: false,
    isError: false,
  }),
}));

const layoutsState = {
  data: [] as ExportLayoutItem[] | undefined,
  isLoading: false,
  isSuccess: true,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
let lastLayoutsOrganization: string | null | undefined;
vi.mock('@/hooks/use-export-layouts', () => ({
  useExportLayouts: (organizationId: string | null) => {
    lastLayoutsOrganization = organizationId;
    return layoutsState;
  },
}));

const filesState = {
  data: undefined as AccountingFileGenerationListResponse | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const generateState = mutationState();
const downloadState = mutationState();
vi.mock('@/hooks/use-accounting-files', () => ({
  useAccountingFiles: () => filesState,
  useGenerateAccountingFile: () => generateState,
  useDownloadAccountingFile: () => downloadState,
}));

const clientDetailState = {
  data: undefined as
    | {
        closed_at: string | null;
        origin_status: string;
        organization: { id: string; name: string };
        accounts: { omie_conta_id: number; name: string }[];
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

import { ClientMappingScreen } from '@/components/features/client-mapping/client-mapping-screen';
import { ApiError } from '@/lib/api/client';
import type {
  AccountingFileGenerationItem,
  AccountingFileGenerationListResponse,
  AuthenticatedUser,
  ExportLayoutItem,
  MappingDestination,
  MappingListResponse,
  MappingPreview,
  MaterializationSummary,
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
const admin: AuthenticatedUser = { ...manager, id: 'a', role: 'admin' };
const platform: AuthenticatedUser = {
  id: 'p',
  email: 'plataforma@hologram.com.br',
  name: 'Plataforma',
  role: 'platform_admin',
  scope: 'platform',
  client_id: null,
  organization_id: null,
  organization_name: null,
};
const clientManager: AuthenticatedUser = {
  id: 'cm',
  email: 'gerente@cliente.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: TENANT,
  organization_id: ORG,
  organization_name: 'Hologram',
};
const clientOperator: AuthenticatedUser = { ...clientManager, id: 'co', role: 'client_operator' };

const DEMONSTRATIVO: MappingDestination = {
  id: 'd-dre',
  type: 'demonstrativo_contabil',
  name: 'Demonstrativo contábil',
  active: true,
  organizationId: ORG,
  targetsCount: 14,
};
const CONTA_CONTABIL: MappingDestination = {
  id: 'd-conta',
  type: 'conta_contabil',
  name: 'Conta contábil',
  active: true,
  organizationId: ORG,
  targetsCount: 0,
};

const LAYOUT_DOMINIO: ExportLayoutItem = {
  id: 'lay-1',
  name: 'Domínio: lançamentos contábeis (CSV)',
  targetSystem: 'Domínio',
  organizationId: ORG,
  latestVersion: 2,
  createdAt: '2026-09-20T12:00:00Z',
  updatedAt: '2026-09-28T12:00:00Z',
};
const LAYOUT_FILIAL: ExportLayoutItem = { ...LAYOUT_DOMINIO, id: 'lay-2', name: 'Domínio filial' };

function materialization(over: Partial<MaterializationSummary> = {}): MaterializationSummary {
  return {
    id: 'mat-2',
    competence: '2026-06',
    version: 2,
    createdAt: '2026-09-27T14:30:00Z',
    author: { name: 'Contador Parceiro', email: 'contador@parceiro.com.br' },
    partialCoverageConfirmed: false,
    coveragePct: '100.00',
    mappedAmount: '1000.00',
    mappedCount: 4,
    notMappedAmount: '0.00',
    notMappedCount: 0,
    undecidedAmount: '0.00',
    undecidedCount: 0,
    uncategorizedAmount: '0.00',
    uncategorizedCount: 0,
    undecidedCategories: 0,
    decisionsUsed: 3,
    partidaCompleteness: { completeAmount: '1000.00', targetAmount: '1000.00', pct: '100.00' },
    ...over,
  };
}

function generation(
  over: Partial<AccountingFileGenerationItem> = {},
): AccountingFileGenerationItem {
  return {
    id: 'gen-1',
    competence: '2026-06',
    materializationId: 'mat-2',
    materializationVersion: 2,
    layoutId: 'lay-1',
    layoutName: 'Domínio: lançamentos contábeis (CSV)',
    layoutVersion: 2,
    lines: 32,
    totalAmount: '154321.87',
    sha256: 'a'.repeat(64),
    fileName: 'lancamentos_2026-06_v2.csv',
    author: { name: 'Contador Parceiro', email: 'contador@parceiro.com.br' },
    createdAt: '2026-09-28T15:00:00Z',
    ...over,
  };
}

function preview(): MappingPreview {
  return {
    competence: '2026-06',
    destination: 'conta_contabil',
    baseState: { syncedAt: '2026-09-25T12:00:00Z', syncFailedAt: null },
    situations: {
      alvo: { amount: '1000.00', count: 4 },
      naoMapear: { amount: '0.00', count: 0 },
      semDecisao: { amount: '0.00', count: 0 },
      semCategoria: { amount: '0.00', count: 0 },
    },
    coveragePct: '100.00',
    naoMapearPct: '0.00',
    coverageNumerator: '1000.00',
    coverageDenominator: '1000.00',
    undecidedCategories: [],
    previewToken: 'tok-9',
    latestVersion: 2,
    partidaCompleteness: { completeAmount: '1000.00', targetAmount: '1000.00', pct: '100.00' },
    pendingSourceAccounts: [],
    accountingCategories: [],
  };
}

function refusal(code: string, userMessage: string, details: Record<string, unknown> = {}) {
  return new ApiError(409, { code, message: code, userMessage, details });
}

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentSearch = 'destination=conta_contabil&view=previa&competence=2026-06';
  replaceMock.mockReset();
  authState.user = manager;
  clientDetailState.data = {
    closed_at: null,
    origin_status: 'ativa',
    organization: { id: ORG, name: 'Hologram' },
    accounts: [],
  };
  destinationsState.data = [DEMONSTRATIVO, CONTA_CONTABIL];
  listState.data = {
    data: [],
    pagination: { page: 1, pageSize: 50, total: 0, totalPages: 0 },
    competence: '2026-09',
    counts: { total: 0, herdada: 0, confirmada: 0, naoMapear: 0, semDecisao: 0 },
  };
  syncStateQuery.data = {
    competence: '2026-06',
    neverSynced: false,
    syncedAt: '2026-09-25T12:00:00Z',
    syncFailedAt: null,
  };
  materializationsState.data = [
    materialization(),
    materialization({ id: 'mat-1', version: 1, createdAt: '2026-09-26T14:30:00Z' }),
  ];
  previewState.data = preview();
  layoutsState.data = [LAYOUT_DOMINIO];
  layoutsState.isLoading = false;
  layoutsState.isSuccess = true;
  layoutsState.isError = false;
  lastLayoutsOrganization = undefined;
  filesState.data = {
    data: [generation()],
    pagination: { page: 1, pageSize: 100, total: 1, totalPages: 1 },
  };
  filesState.isLoading = false;
  filesState.isError = false;
  generateState.mutateAsync = vi.fn().mockResolvedValue(generation());
  generateState.isPending = false;
  downloadState.mutateAsync = vi
    .fn()
    .mockResolvedValue({ blob: new Blob(['x']), filename: 'lancamentos_2026-06_v2.csv' });
  toastError.mockClear();
  toastSuccess.mockClear();
  downloadSpy.mockClear();
});

function section() {
  return screen.getByTestId('accounting-file-section');
}

describe('visibilidade por papel e destino', () => {
  it.each([
    ['gerente da organização', manager],
    ['admin', admin],
    ['plataforma', platform],
  ])('%s vê a seção, "Gerar arquivo" e a ação por versão', (_label, user) => {
    authState.user = user;
    render(<ClientMappingScreen clientId={TENANT} />);

    expect(
      within(section()).getByRole('heading', { name: 'Arquivo contábil de Junho de 2026' }),
    ).toBeInTheDocument();
    expect(within(section()).getByRole('button', { name: 'Gerar arquivo' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Gerar arquivo da versão 1' })).toBeInTheDocument();
  });

  it.each([
    ['gerente do cliente', clientManager],
    ['operador do cliente', clientOperator],
  ])('%s NÃO vê a seção, nem o histórico, nem a ação por versão', (_label, user) => {
    authState.user = user;
    render(<ClientMappingScreen clientId={TENANT} />);

    expect(screen.getByTestId('mapping-preview')).toBeInTheDocument();
    expect(screen.queryByTestId('accounting-file-section')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Gerar arquivo/ })).not.toBeInTheDocument();
  });

  it('no demonstrativo não há arquivo contábil, nem para o admin', () => {
    authState.user = admin;
    currentSearch = 'destination=demonstrativo_contabil&view=previa&competence=2026-06';
    previewState.data = { ...preview(), destination: 'demonstrativo_contabil' };
    render(<ClientMappingScreen clientId={TENANT} />);

    expect(screen.queryByTestId('accounting-file-section')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Gerar arquivo/ })).not.toBeInTheDocument();
  });

  it('a plataforma lê os layouts da organização DO CLIENTE; o staff, os da própria', () => {
    authState.user = platform;
    const { unmount } = render(<ClientMappingScreen clientId={TENANT} />);
    expect(lastLayoutsOrganization).toBe(ORG);
    unmount();

    authState.user = manager;
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(lastLayoutsOrganization).toBeNull();
  });
});

describe('gerar — escolha do layout', () => {
  it('um layout só: gera direto a ÚLTIMA materialização, sem diálogo', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);

    expect(section()).toHaveTextContent('Layout: Domínio: lançamentos contábeis (CSV)');
    await user.click(within(section()).getByRole('button', { name: 'Gerar arquivo' }));

    expect(generateState.mutateAsync).toHaveBeenCalledWith({
      layoutId: 'lay-1',
      competence: '2026-06',
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
  });

  it('a ação da versão gera AQUELA versão (materializationId)', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);

    await user.click(screen.getByRole('button', { name: 'Gerar arquivo da versão 1' }));
    expect(generateState.mutateAsync).toHaveBeenCalledWith({
      layoutId: 'lay-1',
      competence: '2026-06',
      materializationId: 'mat-1',
    });
  });

  it('mais de um layout: diálogo com seletor, escolha obrigatória', async () => {
    layoutsState.data = [LAYOUT_DOMINIO, LAYOUT_FILIAL];
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);

    await user.click(within(section()).getByRole('button', { name: 'Gerar arquivo' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Gerar arquivo' }));
    expect(await within(dialog).findByText('Escolha o layout do arquivo.')).toBeVisible();
    expect(generateState.mutateAsync).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('combobox', { name: 'Layout do arquivo' }));
    await user.click(await screen.findByRole('option', { name: 'Domínio filial (v2)' }));
    await user.click(within(dialog).getByRole('button', { name: 'Gerar arquivo' }));

    await waitFor(() =>
      expect(generateState.mutateAsync).toHaveBeenCalledWith({
        layoutId: 'lay-2',
        competence: '2026-06',
      }),
    );
  });

  it('nenhum layout, admin: estado com o link para Configurações', () => {
    authState.user = admin;
    layoutsState.data = [];
    render(<ClientMappingScreen clientId={TENANT} />);

    const state = screen.getByTestId('accounting-file-no-layout');
    expect(
      within(state).getByRole('link', { name: /Ir para Layouts de exportação/ }),
    ).toHaveAttribute('href', '/configuracoes/layouts-exportacao');
    expect(within(section()).queryByRole('button', { name: 'Gerar arquivo' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Gerar arquivo da versão/ })).toBeNull();
  });

  it('nenhum layout, gerente (sem manage_export_layouts): "peça ao administrador", sem link', () => {
    layoutsState.data = [];
    render(<ClientMappingScreen clientId={TENANT} />);

    const state = screen.getByTestId('accounting-file-no-layout');
    expect(state).toHaveTextContent('Peça ao administrador da organização');
    expect(within(state).queryByRole('link')).not.toBeInTheDocument();
  });

  it('sem materialização: a ação aparece desabilitada com a instrução', () => {
    materializationsState.data = [];
    render(<ClientMappingScreen clientId={TENANT} />);

    const gerar = within(section()).getByRole('button', { name: 'Gerar arquivo' });
    expect(gerar).toBeDisabled();
    expect(gerar).toHaveAccessibleDescription(/Materialize a prévia desta competência/);
  });

  it('desabilitada durante a geração', () => {
    generateState.isPending = true;
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(within(section()).getByRole('button', { name: 'Gerar arquivo' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Gerar arquivo da versão 1' })).toBeDisabled();
  });
});

describe('recusas 409 como ESTADO, nunca toast', () => {
  async function generateAndGetRefusal() {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(within(section()).getByRole('button', { name: 'Gerar arquivo' }));
    const state = await screen.findByTestId('accounting-file-refusal');
    expect(state).toHaveAttribute('role', 'alert');
    expect(toastError).not.toHaveBeenCalled();
    return { state, user };
  }

  it('cobertura parcial: mensagem e os códigos das categorias', async () => {
    generateState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('ARQUIVO_COBERTURA_PARCIAL', 'Decida as categorias sem decisão.', {
        categoryCodes: ['2.01.07', '2.03.01'],
      }),
    );
    const { state } = await generateAndGetRefusal();

    expect(state).toHaveAttribute('data-refusal-code', 'ARQUIVO_COBERTURA_PARCIAL');
    expect(state).toHaveTextContent('Decida as categorias sem decisão.');
    const list = within(state).getByRole('list', { name: 'Categorias a corrigir' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(list).toHaveTextContent('2.01.07');
    expect(list).toHaveTextContent('2.03.01');
  });

  it('partida incompleta: códigos e o caminho para a lista de decisões', async () => {
    generateState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('ARQUIVO_PARTIDA_INCOMPLETA', 'Complete o de-para.', {
        categoryCodes: ['2.01.03'],
        completenessPct: '75.00',
      }),
    );
    const { state, user } = await generateAndGetRefusal();

    await user.click(
      within(state).getByRole('button', {
        name: 'Corrigir a categoria 2.01.03 na lista de decisões',
      }),
    );
    // Vai para a aba Decisões (sem `view`) filtrada pela categoria.
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('code=2.01.03');
    expect(url).not.toContain('view=');
  });

  it('partição que não fecha: mostra as parcelas em reais', async () => {
    generateState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('ARQUIVO_PARTICAO_NAO_FECHA', 'Os totais não fecham.', {
        competenceAmount: '1000.00',
        withAccountAmount: '900.00',
        notMappedAmount: '50.00',
        undecidedAmount: '0.00',
        uncategorizedAmount: '0.00',
      }),
    );
    const { state } = await generateAndGetRefusal();

    const parcels = within(state).getByLabelText('Parcelas da competência');
    expect(parcels).toHaveTextContent('Total da competência');
    expect(parcels).toHaveTextContent(/R\$\s*1\.000,00/);
    expect(parcels).toHaveTextContent(/R\$\s*900,00/);
    expect(parcels).toHaveTextContent('Não mapear');
  });

  it('texto que não cabe: categoria, campo e motivo legível', async () => {
    generateState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('ARQUIVO_TEXTO_NAO_CABE', 'Edite o histórico.', {
        categories: [
          { categoryCode: '2.01.01', field: 'historico', reason: 'contem_separador' },
          { categoryCode: '2.01.05', field: 'historico', reason: 'fora_da_codificacao' },
          { categoryCode: '2.01.06', field: 'historico', reason: 'inicio_de_formula' },
          { categoryCode: '2.01.08', field: 'historico', reason: 'quebra_de_linha' },
        ],
      }),
    );
    const { state } = await generateAndGetRefusal();

    const problems = within(state).getByRole('list', { name: 'Textos que não cabem' });
    expect(problems).toHaveTextContent('2.01.01: Histórico contém o separador de colunas');
    expect(problems).toHaveTextContent('2.01.05: Histórico tem caractere que a codificação');
    expect(problems).toHaveTextContent('2.01.06: Histórico começa com =, +, - ou @');
    expect(problems).toHaveTextContent('2.01.08: Histórico tem quebra de linha');
    expect(within(state).getByRole('list', { name: 'Categorias a corrigir' })).toHaveTextContent(
      '2.01.05',
    );
  });

  it.each([
    ['ARQUIVO_SEM_MATERIALIZACAO', 'Confira a prévia e aplique o de-para antes de gerar.'],
    ['ARQUIVO_DESTINO_INVALIDO', 'Só a partir do destino Conta contábil.'],
  ])('%s: estado com a userMessage', async (code, message) => {
    generateState.mutateAsync = vi.fn().mockRejectedValue(refusal(code, message));
    const { state } = await generateAndGetRefusal();
    expect(state).toHaveAttribute('data-refusal-code', code);
    expect(state).toHaveTextContent(message);
  });

  it('a recusa da versão diz qual versão foi recusada', async () => {
    generateState.mutateAsync = vi
      .fn()
      .mockRejectedValue(
        refusal('ARQUIVO_COBERTURA_PARCIAL', 'Cobertura parcial.', { categoryCodes: ['2.09'] }),
      );
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: 'Gerar arquivo da versão 1' }));
    expect(await screen.findByTestId('accounting-file-refusal')).toHaveTextContent('(versão 1)');
  });
});

describe('histórico de gerações e download', () => {
  it('lista data, versão, layout, linhas e total; baixa com o nome do servidor', async () => {
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);

    const table = within(section()).getByRole('table');
    const row = within(table).getAllByRole('row')[1]!;
    expect(row).toHaveTextContent('28/09/2026');
    expect(row).toHaveTextContent('Versão 2');
    expect(row).toHaveTextContent('Domínio: lançamentos contábeis (CSV) (v2)');
    expect(row).toHaveTextContent('32');
    expect(row).toHaveTextContent(/R\$\s*154\.321,87/);

    await user.click(
      within(row).getByRole('button', { name: 'Baixar lancamentos_2026-06_v2.csv' }),
    );
    expect(downloadState.mutateAsync).toHaveBeenCalledWith('gen-1');
    await waitFor(() =>
      expect(downloadSpy).toHaveBeenCalledWith(expect.any(Blob), 'lancamentos_2026-06_v2.csv'),
    );
  });

  it('SHA divergente (409 ARQUIVO_DIVERGENTE) aparece como erro claro', async () => {
    downloadState.mutateAsync = vi
      .fn()
      .mockRejectedValue(refusal('ARQUIVO_DIVERGENTE', 'A equipe foi avisada; gere de novo.'));
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);

    await user.click(within(section()).getByRole('button', { name: /^Baixar / }));
    const error = await screen.findByTestId('accounting-file-download-error');
    expect(error).toHaveAttribute('role', 'alert');
    expect(error).toHaveTextContent('não pôde ser reproduzido exatamente como foi gerado');
    expect(error).toHaveTextContent('A equipe foi avisada; gere de novo.');
    expect(downloadSpy).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('vazio: "Nenhum arquivo gerado nesta competência"', () => {
    filesState.data = { data: [], pagination: { page: 1, pageSize: 100, total: 0, totalPages: 0 } };
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(section()).toHaveTextContent('Nenhum arquivo gerado nesta competência');
  });

  it('carregando: nem vazio nem linhas', () => {
    filesState.data = undefined;
    filesState.isLoading = true;
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(section()).not.toHaveTextContent('Nenhum arquivo gerado');
    expect(within(section()).queryByRole('button', { name: /^Baixar / })).toBeNull();
  });

  it('erro: userMessage e "Tentar novamente"', async () => {
    filesState.data = undefined;
    filesState.isError = true;
    filesState.error = new ApiError(500, {
      code: 'INTERNAL',
      message: 'x',
      userMessage: 'Serviço fora do ar.',
    });
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(section()).toHaveTextContent('Serviço fora do ar.');
    await user.click(within(section()).getByRole('button', { name: 'Tentar novamente' }));
    expect(filesState.refetch).toHaveBeenCalled();
  });
});

describe('cliente encerrado', () => {
  it('sem gerar e sem baixar; histórico visível só leitura, com o motivo', () => {
    authState.user = admin;
    clientDetailState.data = {
      closed_at: '2026-09-20T12:00:00Z',
      origin_status: 'ativa',
      organization: { id: ORG, name: 'Hologram' },
      accounts: [],
    };
    render(<ClientMappingScreen clientId={TENANT} />);

    expect(screen.getByTestId('accounting-file-closed')).toHaveTextContent('Cliente encerrado');
    expect(screen.queryByRole('button', { name: /Gerar arquivo/ })).not.toBeInTheDocument();
    expect(within(section()).queryByRole('button', { name: /^Baixar/ })).toBeNull();
    // O histórico continua lá.
    expect(within(section()).getByRole('table')).toHaveTextContent('Versão 2');
  });
});

describe('acessibilidade', () => {
  it('seção com histórico e recusa: axe sem critical/serious', async () => {
    generateState.mutateAsync = vi
      .fn()
      .mockRejectedValue(
        refusal('ARQUIVO_PARTIDA_INCOMPLETA', 'Complete o de-para.', { categoryCodes: ['2.01'] }),
      );
    const user = userEvent.setup();
    const { container } = render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(within(section()).getByRole('button', { name: 'Gerar arquivo' }));
    await screen.findByTestId('accounting-file-refusal');
    await assertNoA11yViolations(container);
  });
});
