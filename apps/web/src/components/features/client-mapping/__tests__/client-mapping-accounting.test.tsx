/**
 * Testes do de-para no destino `conta_contabil` (FRONT 16.6 / R2 · R3 · R4).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). No worktree do agent: `--cache=false`.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - TROCA DE SELETOR POR DESTINO: no `conta_contabil` a gaveta escolhe entre
 *     contas do plano do cliente e tem o histórico padrão; nos outros destinos a
 *     gaveta e a lista são as da S12 (sem histórico, sem seletor do plano, sem
 *     coluna nova) — regressão;
 *   - histórico com contador até o teto do contrato; acima dele, erro de
 *     validação e NENHUM request; o 400 do servidor vira erro do campo;
 *   - decisão LEGADA só-leitura, com o selo "Refazer no plano do cliente" e a
 *     ação que cria a vigência nova a partir dela;
 *   - cliente sem plano: estado que orienta a importar, com o link;
 *   - prévia: conta, histórico (dica acessível, sem `title`), incompletude por
 *     categoria, completude agregada (nula = "—", nunca 0%) e as contas de
 *     origem pendentes com o caminho para associar;
 *   - 409 `CONTA_DO_BANCO_PENDENTE` vira ESTADO com as contas, nunca toast;
 *   - importar a planilha some no `conta_contabil`; exportar fica;
 *   - axe-core sem `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/clientes/c1/de-para',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    info: vi.fn(),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

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

const writeState = mutationState();
const materializeState = mutationState();
const exportState = mutationState();

vi.mock('@/hooks/use-client-mapping', () => ({
  useMappingDestinations: () => destinationsState,
  useMappingTargets: () => targetsState,
  useClientMappingList: () => listState,
  useMovementsSyncState: () => syncStateQuery,
  useMappingMaterializations: () => materializationsState,
  useMappingPreview: () => previewState,
  useSyncMovements: () => mutationState(),
  useWriteMappingDecision: () => writeState,
  useConfirmInheritedDecisions: () => mutationState(),
  useInheritMapping: () => mutationState(),
  useMaterializeMapping: () => materializeState,
  useExportClientMapping: () => exportState,
  usePreviewMappingImport: () => mutationState(),
  useApplyMappingImport: () => mutationState(),
}));

/** Total do plano contábil do cliente (a sonda de 1 linha) e as contas do seletor. */
let planTotal = 3;
const planAccounts: AccountingAccount[] = [];
vi.mock('@/hooks/use-client-accounting-chart', () => ({
  useAccountingChartList: (_clientId: string, params: { pageSize?: number }) =>
    params.pageSize === 1
      ? {
          data: { data: [], pagination: { page: 1, pageSize: 1, total: planTotal, totalPages: 1 } },
          isLoading: false,
        }
      : {
          data: {
            data: planAccounts,
            pagination: { page: 1, pageSize: 100, total: planAccounts.length, totalPages: 1 },
          },
          isLoading: false,
          isFetching: false,
          isError: false,
        },
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
  AccountingAccount,
  AuthenticatedUser,
  MappingDestination,
  MappingListItem,
  MappingListResponse,
  MappingPreview,
  MappingTarget,
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

function item(overrides: Partial<MappingListItem> = {}): MappingListItem {
  return {
    sourceType: 'omie',
    categoryCode: '2.01.01',
    categoryName: 'Aluguel',
    categoryNameResolved: true,
    situation: 'confirmada',
    decision: 'alvo',
    targetCode: null,
    targetName: null,
    effectiveFrom: '2026-09',
    divergent: false,
    originDreCode: null,
    accountingAccountId: 'acc-5101',
    accountingAccountCode: '5101',
    accountingAccountName: 'Despesa de aluguel',
    history: 'Pagamento de aluguel do imóvel da sede administrativa conforme contrato',
    requiresRedo: false,
    ...overrides,
  };
}

const LEGACY = item({
  categoryCode: '2.01.02',
  categoryName: 'Energia',
  targetCode: '662',
  targetName: 'Utilidades (catálogo)',
  accountingAccountId: null,
  accountingAccountCode: null,
  accountingAccountName: null,
  history: null,
  requiresRedo: true,
});

function listWith(items: MappingListItem[]): MappingListResponse {
  return {
    data: items,
    pagination: { page: 1, pageSize: 50, total: items.length, totalPages: 1 },
    competence: '2026-09',
    counts: {
      total: items.length,
      herdada: 0,
      confirmada: items.length,
      naoMapear: 0,
      semDecisao: 0,
    },
  };
}

function preview(overrides: Partial<MappingPreview> = {}): MappingPreview {
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
    latestVersion: 1,
    partidaCompleteness: { completeAmount: '600.00', targetAmount: '1000.00', pct: '60.00' },
    pendingSourceAccounts: [{ sourceType: 'omie', sourceAccountId: '4455' }],
    accountingCategories: [
      {
        sourceType: 'omie',
        categoryCode: '2.01.01',
        amount: '600.00',
        count: 2,
        accountingAccountId: 'acc-5101',
        accountingAccountCode: '5101',
        accountingAccountName: 'Despesa de aluguel',
        history: 'Pagamento de aluguel do imóvel da sede administrativa conforme contrato',
        historyMissing: false,
        requiresRedo: false,
        completeAmount: '600.00',
        completeCount: 2,
        pendingSourceAccounts: [],
      },
      {
        sourceType: 'omie',
        categoryCode: '2.01.03',
        amount: '300.00',
        count: 1,
        accountingAccountId: 'acc-5102',
        accountingAccountCode: '5102',
        accountingAccountName: 'Despesa bancária',
        history: null,
        historyMissing: true,
        requiresRedo: false,
        completeAmount: '0.00',
        completeCount: 0,
        pendingSourceAccounts: [{ sourceType: 'omie', sourceAccountId: '4455' }],
      },
      {
        sourceType: 'omie',
        categoryCode: '2.01.02',
        amount: '100.00',
        count: 1,
        accountingAccountId: null,
        accountingAccountCode: null,
        accountingAccountName: null,
        history: null,
        historyMissing: true,
        requiresRedo: true,
        completeAmount: '0.00',
        completeCount: 0,
        pendingSourceAccounts: [],
      },
    ],
    ...overrides,
  };
}

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentSearch = 'destination=conta_contabil';
  authState.user = manager;
  clientDetailState.data = {
    closed_at: null,
    origin_status: 'ativa',
    organization: { id: ORG, name: 'Hologram' },
    accounts: [{ omie_conta_id: 4455, name: 'Itaú — Conta movimento' }],
  };
  destinationsState.data = [DEMONSTRATIVO, CONTA_CONTABIL];
  listState.data = listWith([item(), LEGACY]);
  listState.isLoading = false;
  listState.isError = false;
  targetsState.data = [{ id: 't1', code: '3.1', name: 'Despesas administrativas', active: true }];
  syncStateQuery.data = {
    competence: '2026-06',
    neverSynced: false,
    syncedAt: '2026-09-25T12:00:00Z',
    syncFailedAt: null,
  };
  materializationsState.data = [];
  previewState.data = preview();
  previewState.refetch = vi.fn();
  planTotal = 3;
  planAccounts.splice(0, planAccounts.length, {
    id: 'acc-5101',
    code: '5101',
    classification: '4.1.01',
    name: 'Despesa de aluguel',
    nameResolved: true,
    type: 'analitica',
    active: true,
    postable: true,
    updatedAt: '2026-09-28T10:00:00Z',
  });
  for (const state of [writeState, materializeState, exportState]) {
    state.mutateAsync = vi.fn().mockResolvedValue({
      created: 1,
      resolved: 0,
      unchanged: 0,
      effectiveFrom: '2026-09',
    });
    state.isPending = false;
  }
  toastError.mockClear();
});

async function openDecision(name: string) {
  const user = userEvent.setup();
  render(<ClientMappingScreen clientId={TENANT} />);
  await user.click(screen.getByRole('button', { name }));
  const dialog = await screen.findByRole('dialog');
  return { user, dialog };
}

describe('troca de seletor por destino', () => {
  it('conta_contabil: conta do plano do cliente + histórico padrão, sem catálogo', async () => {
    const { dialog } = await openDecision('Alterar a categoria 2.01.01');

    expect(
      within(dialog).getByRole('button', { name: /Conta do plano contábil do cliente: 5101/ }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: /Alvo do catálogo/ }),
    ).not.toBeInTheDocument();
    const historico = within(dialog).getByLabelText('Histórico padrão');
    expect(historico).toHaveValue(item().history);
    expect(within(dialog).getByTestId('history-counter')).toHaveTextContent(
      `${item().history?.length}/500`,
    );
    expect(
      within(dialog).getByText(/Trocar a conta ou só o histórico cria uma vigência nova/),
    ).toBeInTheDocument();
  });

  it('REGRESSÃO — demonstrativo: a gaveta e a lista da S12, sem histórico nem plano', async () => {
    currentSearch = 'destination=demonstrativo_contabil';
    listState.data = listWith([
      item({
        targetCode: '3.1',
        targetName: 'Despesas administrativas',
        accountingAccountId: null,
        accountingAccountCode: null,
        accountingAccountName: null,
        history: null,
      }),
    ]);
    const { dialog } = await openDecision('Alterar a categoria 2.01.01');

    expect(within(dialog).getByRole('button', { name: /Alvo do catálogo/ })).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Histórico padrão')).not.toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: /Conta do plano contábil/ }),
    ).not.toBeInTheDocument();
    const headers = screen
      .getAllByRole('columnheader', { hidden: true })
      .map((th) => th.textContent)
      .filter((text) => text !== 'Ações');
    expect(headers).toEqual(['Categoria', 'Situação', 'Decisão', 'Vigente desde']);
    expect(screen.queryByTestId('mapping-legacy-badge')).not.toBeInTheDocument();
  });

  it('grava accountingAccountId e o histórico aparado — nunca targetCode', async () => {
    const { user, dialog } = await openDecision('Alterar a categoria 2.01.01');
    const historico = within(dialog).getByLabelText('Histórico padrão');
    await user.clear(historico);
    await user.type(historico, '  Aluguel da sede  ');
    await user.click(within(dialog).getByRole('button', { name: 'Gravar decisão' }));

    await waitFor(() => expect(writeState.mutateAsync).toHaveBeenCalledTimes(1));
    const payload = writeState.mutateAsync.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(payload).toMatchObject({
      categoryCode: '2.01.01',
      decision: 'alvo',
      accountingAccountId: 'acc-5101',
      history: 'Aluguel da sede',
      effectiveFrom: '2026-09',
    });
    expect(payload).not.toHaveProperty('targetCode');
  });
});

describe('histórico padrão — limite do contrato', () => {
  it('acima de 500 caracteres: erro de validação e nenhum request', async () => {
    const { user, dialog } = await openDecision('Alterar a categoria 2.01.01');
    const historico = within(dialog).getByLabelText('Histórico padrão');
    await user.clear(historico);
    await user.click(historico);
    await user.paste('x'.repeat(501));

    expect(within(dialog).getByTestId('history-counter')).toHaveTextContent('501/500');
    await user.click(within(dialog).getByRole('button', { name: 'Gravar decisão' }));
    expect(
      await within(dialog).findByText('O histórico padrão tem no máximo 500 caracteres.'),
    ).toBeInTheDocument();
    expect(writeState.mutateAsync).not.toHaveBeenCalled();
  });

  it('o 400 do servidor vira erro do CAMPO histórico, sem toast', async () => {
    writeState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(400, {
        code: 'VALIDATION_ERROR',
        message: 'x',
        userMessage: 'Dados inválidos.',
      }),
    );
    const { user, dialog } = await openDecision('Alterar a categoria 2.01.01');
    await user.click(within(dialog).getByRole('button', { name: 'Gravar decisão' }));

    expect(
      await within(dialog).findByText(/O servidor recusou o histórico: no máximo 500 caracteres/),
    ).toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('422 de conta não lançável vira erro do campo da conta', async () => {
    writeState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(422, {
        code: 'CONTA_CONTABIL_NAO_LANCAVEL',
        message: 'x',
        userMessage: 'Esta conta não recebe lançamento.',
        details: { reason: 'sintetica', accountId: 'acc-5101' },
      }),
    );
    const { user, dialog } = await openDecision('Alterar a categoria 2.01.01');
    await user.click(within(dialog).getByRole('button', { name: 'Gravar decisão' }));

    expect(await within(dialog).findByText(/Esta conta é sintética/)).toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe('decisão legada do catálogo', () => {
  it('na lista: só-leitura com o selo, o código do catálogo e a ação "Refazer"', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    const linha = screen.getByRole('row', { name: /2\.01\.02/ });

    expect(within(linha).getByTestId('mapping-legacy-badge')).toHaveTextContent(
      'Refazer no plano do cliente',
    );
    expect(within(linha).getByText(/Catálogo: 662 — Utilidades \(catálogo\)/)).toBeInTheDocument();
    expect(
      within(linha).getByRole('button', { name: 'Refazer a categoria 2.01.02' }),
    ).toBeInTheDocument();
  });

  it('na gaveta: a decisão legada aparece só-leitura e gravar refaz no plano', async () => {
    const { dialog } = await openDecision('Refazer a categoria 2.01.02');

    const legado = within(dialog).getByTestId('accounting-legacy-decision');
    expect(legado).toHaveTextContent(/aponta o catálogo da organização \(662 — Utilidades/);
    // Sem conta herdada: a pessoa escolhe no plano do cliente.
    expect(
      within(dialog).getByRole('button', { name: 'Conta do plano contábil do cliente' }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: 'Refazer no plano do cliente' }),
    ).toBeInTheDocument();
  });
});

describe('cliente sem plano contábil', () => {
  it('lista e gaveta orientam a importar, com o link, e "Gravar" fica travado', async () => {
    planTotal = 0;
    const { dialog } = await openDecision('Alterar a categoria 2.01.01');

    expect(screen.getByTestId('mapping-accounting-no-plan')).toHaveTextContent(
      'Este cliente ainda não tem plano contábil',
    );
    const semPlano = within(dialog).getByTestId('accounting-no-plan');
    expect(within(semPlano).getByRole('link', { name: 'Ir para Plano contábil' })).toHaveAttribute(
      'href',
      `/clientes/${TENANT}/plano-contabil`,
    );
    // Nada de "Importar" aqui: importar é da tela do plano, com outra permissão.
    expect(within(dialog).queryByRole('button', { name: /importar/i })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Gravar decisão' })).toBeDisabled();
  });
});

describe('portabilidade', () => {
  it('conta_contabil: Importar some, Exportar fica', () => {
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('button', { name: 'Exportar' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Importar' })).not.toBeInTheDocument();
  });

  it('REGRESSÃO — demonstrativo: Importar e Exportar', () => {
    currentSearch = 'destination=demonstrativo_contabil';
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByRole('button', { name: 'Exportar' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Importar' })).toBeInTheDocument();
  });
});

describe('prévia no conta_contabil', () => {
  beforeEach(() => {
    currentSearch = 'destination=conta_contabil&view=previa&competence=2026-06';
  });

  it('conta, histórico com dica acessível, incompletude e completude agregada', async () => {
    const { container } = render(<ClientMappingScreen clientId={TENANT} />);
    const secao = screen.getByTestId('mapping-accounting-preview');

    const completude = within(secao).getByTestId('partida-completeness');
    expect(completude).toHaveTextContent('60%');
    expect(completude).toHaveTextContent(/R\$\s*600,00/);
    expect(completude).toHaveTextContent(/de R\$\s*1\.000,00 com conta/);

    const linhaCompleta = within(secao).getByRole('row', { name: /2\.01\.01/ });
    expect(within(linhaCompleta).getByText('Completa')).toBeInTheDocument();
    const dica = within(linhaCompleta).getByRole('img', { name: /^Histórico padrão: Pagamento/ });
    expect(dica).toHaveAttribute('tabindex', '0');
    expect(dica).not.toHaveAttribute('title');

    const linhaIncompleta = within(secao).getByRole('row', { name: /2\.01\.03/ });
    expect(within(linhaIncompleta).getByText('Falta histórico')).toBeInTheDocument();
    expect(within(linhaIncompleta).getByText('Conta do banco pendente')).toBeInTheDocument();
    const linhaLegado = within(secao).getByRole('row', { name: /2\.01\.02/ });
    expect(within(linhaLegado).getByTestId('mapping-legacy-badge')).toBeInTheDocument();

    const pendentes = within(secao).getByTestId('bank-pending-notice');
    expect(pendentes).toHaveTextContent('Itaú — Conta movimento');
    expect(
      within(pendentes).getByRole('link', { name: /Associar conta do banco/ }),
    ).toHaveAttribute('href', `/clientes/${TENANT}/plano-contabil#conta-do-banco`);
    await assertNoA11yViolations(container);
  });

  it('completude sem linha com conta aparece como "—", nunca 0%', () => {
    previewState.data = preview({
      partidaCompleteness: { completeAmount: '0.00', targetAmount: '0.00', pct: null },
      accountingCategories: [],
      pendingSourceAccounts: [],
    });
    render(<ClientMappingScreen clientId={TENANT} />);
    const completude = screen.getByTestId('partida-completeness');
    expect(completude).toHaveTextContent('—');
    expect(completude).not.toHaveTextContent('0%');
    expect(completude).toHaveTextContent(/não há partida para medir/);
  });

  it('409 CONTA_DO_BANCO_PENDENTE vira ESTADO com as contas pendentes, sem toast', async () => {
    materializeState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'CONTA_DO_BANCO_PENDENTE',
        message: 'x',
        userMessage: 'Há movimentos de contas de origem sem a conta contábil do banco associada.',
        details: {
          pendingSourceAccounts: [
            { sourceType: 'omie', sourceAccountId: '4455' },
            { sourceType: 'arquivo', sourceAccountId: null },
          ],
        },
      }),
    );
    const user = userEvent.setup();
    render(<ClientMappingScreen clientId={TENANT} />);
    await user.click(screen.getByRole('button', { name: /Materializar/ }));
    const dialogo = await screen.findByRole('alertdialog');
    await user.click(within(dialogo).getByRole('button', { name: 'Materializar versão 2' }));

    const recusa = await screen.findByTestId('bank-pending-refusal');
    expect(recusa).toHaveAttribute('role', 'alert');
    expect(recusa).toHaveTextContent('nada foi gravado');
    const lista = within(recusa).getByRole('list', { name: 'Contas de origem sem conta do banco' });
    expect(lista).toHaveTextContent('Itaú — Conta movimento');
    expect(lista).toHaveTextContent('Conta padrão (arquivo sem coluna de conta)');
    expect(toastError).not.toHaveBeenCalled();
    expect(previewState.refetch).toHaveBeenCalled();
  });

  it('versões materializadas mostram a completude de partida', () => {
    materializationsState.data = [
      {
        id: 'mat-1',
        competence: '2026-06',
        version: 1,
        createdAt: '2026-09-26T14:30:00Z',
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
      },
    ];
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByTestId('mapping-versions')).toHaveTextContent('Partida completa 100%');
  });

  it('REGRESSÃO — demonstrativo: sem seção de partida', () => {
    currentSearch = 'destination=demonstrativo_contabil&view=previa&competence=2026-06';
    previewState.data = preview({
      destination: 'demonstrativo_contabil',
      partidaCompleteness: null,
      pendingSourceAccounts: null,
      accountingCategories: null,
    });
    render(<ClientMappingScreen clientId={TENANT} />);
    expect(screen.getByTestId('mapping-preview')).toBeInTheDocument();
    expect(screen.queryByTestId('mapping-accounting-preview')).not.toBeInTheDocument();
    expect(screen.queryByTestId('bank-pending-notice')).not.toBeInTheDocument();
  });
});

describe('a11y', () => {
  it('lista do conta_contabil com legado e histórico, sem violações critical/serious', async () => {
    const { container } = render(<ClientMappingScreen clientId={TENANT} />);
    await assertNoA11yViolations(container);
  });
});
