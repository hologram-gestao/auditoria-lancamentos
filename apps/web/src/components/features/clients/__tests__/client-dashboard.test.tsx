/**
 * Testes do painel do cliente (86e3k1q54): os cinco blocos a partir do resumo,
 * da carteira e do fluxo previsto, com os hooks mockados.
 *
 * Cobre: fechamento do mês (contas, anomalias com nome do catálogo, compras do
 * cartão, de-para com cobertura nula = "—"), carteira e fluxo, carteira negada
 * pelo servidor, `neverSynced`, zero compras, cliente sem conciliação (estado
 * vazio com a regra de origem), cliente encerrado, erro por bloco e axe.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/clientes/c1',
  useSearchParams: () => new URLSearchParams(''),
}));

interface QueryState<T> {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  dataUpdatedAt: number;
  refetch: ReturnType<typeof vi.fn>;
}

function queryState<T>(): QueryState<T> {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    dataUpdatedAt: 0,
    refetch: vi.fn(),
  };
}

const detailState = queryState<Record<string, unknown>>();
const monthState = queryState<Record<string, unknown>>();
const summaryState = queryState<ClientSummary>();
const titlesState = queryState<TitlesSummary>();
const reportState = queryState<ReceivablesReport>();
const flowState = queryState<TitlesFlow>();

const authState: { user: AuthenticatedUser | null } = { user: null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

vi.mock('@/hooks/use-clients', () => ({
  useSetFavorite: () => ({ mutate: vi.fn(), isPending: false }),
  useClientDetail: () => detailState,
  useReconciliationsList: () => monthState,
  useTestConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/hooks/use-client-summary', () => ({
  useClientSummary: () => summaryState,
}));

vi.mock('@/hooks/use-client-titles', () => ({
  useClientTitlesSummary: (_id: string, options: { enabled?: boolean } = {}) =>
    options.enabled === false ? queryState() : titlesState,
  useReceivablesReport: (_id: string, options: { enabled?: boolean } = {}) =>
    options.enabled === false ? queryState() : reportState,
  useClientTitlesFlow: (_id: string, options: { enabled?: boolean } = {}) =>
    options.enabled === false ? queryState() : flowState,
  useSyncClientTitles: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/hooks/use-anomaly-types', () => ({
  useAnomalyTypesList: () => ({
    data: {
      data: [{ id: 't1', code: 'wrong_date', name: 'Data divergente', active: true }],
      pagination: { page: 1, pageSize: 100, total: 1, totalPages: 1 },
    },
  }),
}));

vi.mock('@/hooks/use-client-mapping', () => ({
  useMappingDestinations: () => ({
    data: [
      {
        id: 'd1',
        type: 'conta_contabil',
        name: 'Conta contábil',
        active: true,
        organizationId: 'o1',
        targetsCount: 0,
      },
    ],
  }),
}));

vi.mock('@/hooks/use-glossary', () => ({
  useGlossaryList: () => ({
    data: {
      data: { entries: [], version: 7 },
      pagination: { page: 1, pageSize: 1, total: 42, totalPages: 42 },
    },
  }),
}));

const openDrawer = vi.fn();
vi.mock('@/components/features/reconciliations/create/use-create-reconciliation-drawer', () => ({
  useCreateReconciliationDrawer: () => ({ open: openDrawer, drawer: null }),
}));

// A seção de origens tem suíte própria; aqui ela só precisa montar.
vi.mock('@/hooks/use-client-connections', () => ({
  useClientConnections: () => ({
    data: [],
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useTestStoredConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

import { ClientDashboard } from '@/components/features/clients/client-dashboard';
import { ApiError } from '@/lib/api/client';
import type {
  AuthenticatedUser,
  ClientSummary,
  ReceivablesReport,
  TitlesFlow,
  TitlesSummary,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ADMIN: AuthenticatedUser = {
  id: 'u-admin',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
};

const OPERATOR: AuthenticatedUser = {
  id: 'u-op',
  email: 'operador@cliente.com.br',
  name: 'Operador',
  role: 'client_operator',
  scope: 'client',
  client_id: 'c1',
};

function session(over: Record<string, unknown> = {}) {
  return {
    id: 's1',
    omie_conta_id: 10,
    account_type: 'checking',
    reference_month: '2026-10-01',
    status: 'done',
    created_at: '2026-10-05T14:32:00Z',
    total_file_entries: 30,
    conciliated_count: 25,
    sem_omie_count: 3,
    omie_sem_arquivo_count: 2,
    anomaly_count: 0,
    total_files: 1,
    ...over,
  };
}

const SUMMARY: ClientSummary = {
  referenceMonth: '2026-10',
  reconciliations: {
    accountsTotal: 3,
    accountsWithSession: 2,
    byStatus: { processing: 0, reviewing: 1, done: 1, error: 0 },
    // A conta 30 (aplicação) está no cache, mas ninguém a concilia: não é meta.
    habitualAccountIds: [10, 20],
  },
  anomalies: {
    openTotal: 7,
    byType: [
      { code: 'tipo_sem_nome', count: 2 },
      { code: 'wrong_date', count: 5 },
    ],
    resolvedInMonth: 4,
  },
  cardPurchasesToPost: { count: 3, totalAmount: '-420.00' },
  mapping: [
    {
      destinationCode: 'conta_contabil',
      withoutDecision: 4,
      coveragePct: '81.2',
      materialized: false,
    },
    {
      destinationCode: 'fluxo_de_caixa',
      withoutDecision: 0,
      coveragePct: null,
      materialized: true,
    },
  ],
  titles: {
    overdueCount: 12,
    overdueTotal: '87245.50',
    aPagar: { overdueCount: 3, overdueTotal: '4380.00' },
    aReceber: { overdueCount: 9, overdueTotal: '82865.50' },
    syncedAt: '2026-10-07T09:10:00Z',
    neverSynced: false,
  },
  latestSession: {
    id: 's2',
    referenceMonth: '2026-10-01',
    status: 'reviewing',
    accountType: 'credit_card',
    createdAt: '2026-10-06T10:00:00Z',
  },
};

function aging(over: Partial<TitlesSummary['aReceber']> = {}): TitlesSummary['aReceber'] {
  return {
    totalEmAberto: '107413.10',
    totalAVencer: '24547.60',
    totalVencido: '82865.50',
    bucket1a30: '0.00',
    bucket31a60: '0.00',
    bucket61a90: '0.00',
    bucket90Mais: '82865.50',
    qtdEmAberto: 148,
    qtdAVencer: 20,
    qtdVencido: 128,
    ...over,
  };
}

const TITLES: TitlesSummary = {
  aReceber: aging(),
  aPagar: aging({
    totalEmAberto: '9380.00',
    totalAVencer: '5000.00',
    totalVencido: '4380.00',
    bucket1a30: '4380.00',
    bucket90Mais: '0.00',
  }),
  neverSynced: false,
  syncedAt: '2026-10-07T09:10:00Z',
  syncFailedAt: null,
  referenceDate: '2026-10-07',
};

function group(total: string, qtd: number) {
  return {
    total,
    bucket1a30: '0.00',
    bucket31a60: '0.00',
    bucket61a90: '0.00',
    bucket90Mais: total,
    qtd,
  };
}

const REPORT: ReceivablesReport = {
  referenceDate: '2026-10-07',
  aReceber: { inadimplencia: group('60000.00', 30), vencidoComContexto: group('22865.50', 98) },
  aPagar: { inadimplencia: group('0.00', 0), vencidoComContexto: group('0.00', 0) },
};

function side(total: string, count: number) {
  return { total, count };
}

const FLOW: TitlesFlow = {
  referenceDate: '2026-10-07',
  syncedAt: '2026-10-07T09:10:00Z',
  neverSynced: false,
  buckets: [
    {
      bucket: 'vencidos',
      aReceber: side('82865.50', 128),
      aPagar: side('4380.00', 3),
      net: '78485.50',
    },
    { bucket: 'ate_7', aReceber: side('3000.00', 4), aPagar: side('1200.00', 2), net: '1800.00' },
    { bucket: '8_30', aReceber: side('9000.00', 6), aPagar: side('3800.00', 1), net: '5200.00' },
    { bucket: '31_60', aReceber: side('12547.60', 10), aPagar: side('0.00', 0), net: '12547.60' },
    { bucket: '61_90', aReceber: side('0.00', 0), aPagar: side('0.00', 0), net: '0.00' },
    { bucket: '90_mais', aReceber: side('0.00', 0), aPagar: side('0.00', 0), net: '0.00' },
  ],
};

beforeAll(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date('2026-10-07T12:00:00Z'));
});

beforeEach(() => {
  authState.user = ADMIN;
  openDrawer.mockClear();
  for (const state of [
    detailState,
    monthState,
    summaryState,
    titlesState,
    reportState,
    flowState,
  ]) {
    state.isLoading = false;
    state.isError = false;
    state.error = null;
  }
  detailState.data = {
    name: 'Cliente Exemplo Ltda',
    organization: { id: 'o1', name: 'Hologram' },
    accounts: [
      { id: 'a1', omie_conta_id: 10, name: 'Itaú CC', bank_name: 'Itaú', account_type: 'CC' },
      { id: 'a2', omie_conta_id: 20, name: 'Cartão Inter', bank_name: 'Inter', account_type: 'CR' },
      { id: 'a3', omie_conta_id: 30, name: 'BB Aplicação', bank_name: 'BB', account_type: 'CA' },
    ],
    origin_status: 'ativa',
    connections: [],
  };
  monthState.data = {
    data: [
      session({
        id: 's2',
        omie_conta_id: 20,
        account_type: 'credit_card',
        status: 'reviewing',
        anomaly_count: 5,
      }),
      session(),
    ],
    pagination: { page: 1, pageSize: 100, total: 2, totalPages: 1 },
  };
  summaryState.data = SUMMARY;
  summaryState.dataUpdatedAt = new Date('2026-10-07T12:00:00Z').getTime();
  titlesState.data = TITLES;
  reportState.data = REPORT;
  flowState.data = FLOW;
});

function card(name: string): HTMLElement {
  return screen.getByRole('region', { name });
}

describe('ClientDashboard — blocos', () => {
  it('cabeçalho: título, cliente, mês do resumo e as duas ações', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<ClientDashboard clientId="c1" />);

    expect(screen.getByRole('heading', { level: 1, name: 'Painel' })).toBeVisible();
    expect(
      screen.getByText(/Cliente Exemplo Ltda · fechamento de outubro de 2026 · atualizado às/),
    ).toBeVisible();
    expect(screen.getByRole('link', { name: 'Ver conciliações' })).toHaveAttribute(
      'href',
      '/clientes/c1',
    );
    await user.click(screen.getByRole('button', { name: 'Nova conciliação' }));
    expect(openDrawer).toHaveBeenCalledTimes(1);
  });

  it('conciliações do mês: meta pelas contas HABITUAIS, uma fatia por habitual e as outras recolhidas', () => {
    render(<ClientDashboard clientId="c1" />);
    const block = card('Conciliações do mês');

    expect(within(block).getByTestId('closing-headline')).toHaveTextContent(
      '1 de 2 contas habituais concluídas',
    );
    expect(block).toHaveTextContent('Habituais: contas conciliadas nos últimos 3 meses');
    const states = Array.from(block.querySelectorAll('[data-testid="closing-segment"]')).map((el) =>
      el.getAttribute('data-state'),
    );
    // Quem pede atenção primeiro: em revisão antes de concluída.
    expect(states).toEqual(['reviewing', 'done']);
    const items = within(within(block).getByTestId('closing-accounts'))
      .getAllByRole('listitem')
      .map((li) => li.textContent);
    expect(items).toEqual(['Cartão InterEm revisão', 'Itaú CCConcluída']);
    const others = within(block).getByTestId('closing-other-accounts');
    expect(others.tagName).toBe('DETAILS');
    expect(others).not.toHaveAttribute('open');
    expect(others).toHaveTextContent('Outras 1 conta sem conciliação neste mês');
    expect(others).toHaveTextContent('BB Aplicação');
    expect(others).not.toHaveTextContent('Sem conciliação');
  });

  it('conciliações do mês: 22 contas no cache e 3 habituais, ordenadas por status', () => {
    const accounts = Array.from({ length: 22 }, (_, i) => ({
      id: `acc-${i + 1}`,
      omie_conta_id: i + 1,
      name: `Conta ${String(i + 1).padStart(2, '0')}`,
      bank_name: 'Banco',
      account_type: 'CC',
    }));
    detailState.data = { ...detailState.data!, accounts };
    summaryState.data = {
      ...SUMMARY,
      reconciliations: { ...SUMMARY.reconciliations, habitualAccountIds: [3, 7, 15] },
    };
    monthState.data = {
      data: [
        session({ id: 'sa', omie_conta_id: 3, status: 'done', anomaly_count: 0 }),
        session({ id: 'sb', omie_conta_id: 15, status: 'processing', anomaly_count: 0 }),
      ],
      pagination: { page: 1, pageSize: 100, total: 2, totalPages: 1 },
    };
    render(<ClientDashboard clientId="c1" />);
    const block = card('Conciliações do mês');

    expect(within(block).getByTestId('closing-headline')).toHaveTextContent(
      '1 de 3 contas habituais concluídas',
    );
    expect(block.querySelectorAll('[data-testid="closing-segment"]')).toHaveLength(3);
    const items = within(within(block).getByTestId('closing-accounts'))
      .getAllByRole('listitem')
      .map((li) => li.textContent);
    expect(items).toEqual([
      'Conta 15Em processamento',
      'Conta 03Concluída',
      'Conta 07Sem conciliação',
    ]);
    const others = within(block).getByTestId('closing-other-accounts');
    expect(others).not.toHaveAttribute('open');
    expect(others).toHaveTextContent('Outras 19 contas sem conciliação neste mês');
    expect(within(others).getAllByRole('listitem', { hidden: true })).toHaveLength(19);
  });

  it('conciliações do mês: sem habituais e sem sessão no mês, nada de "0 de N"', () => {
    summaryState.data = {
      ...SUMMARY,
      reconciliations: { ...SUMMARY.reconciliations, habitualAccountIds: [] },
    };
    monthState.data = {
      data: [],
      pagination: { page: 1, pageSize: 100, total: 0, totalPages: 0 },
    };
    render(<ClientDashboard clientId="c1" />);
    const block = card('Conciliações do mês');

    expect(within(block).getByTestId('closing-headline')).toHaveTextContent(
      'Nenhuma conciliação neste mês ainda',
    );
    expect(block).not.toHaveTextContent(/\d+ de \d+/);
    expect(block.querySelectorAll('[data-testid="closing-segment"]')).toHaveLength(0);
    expect(within(block).getByTestId('closing-other-accounts')).toHaveTextContent(
      'Outras 3 contas sem conciliação neste mês',
    );
  });

  it('anomalias: total em destaque, nome do catálogo (sem nome fica o código), resolvidas e link', () => {
    render(<ClientDashboard clientId="c1" />);
    const block = card('Anomalias em aberto');

    expect(within(block).getByText('7')).toHaveClass('text-warning');
    expect(within(block).getByText('Data divergente')).toBeVisible();
    expect(within(block).getByText('tipo_sem_nome')).toBeVisible();
    expect(block).toHaveTextContent('Resolvidas no mês: 4');
    expect(within(block).getByRole('link', { name: 'Revisar anomalias' })).toHaveAttribute(
      'href',
      '/clientes/c1/conciliacao/s2?tab=anomalias',
    );
  });

  it('compras do cartão: contagem, total com sinal e link para a fatura do mês', () => {
    render(<ClientDashboard clientId="c1" />);
    const block = card('Compras do cartão a lançar');

    expect(block).toHaveTextContent('3 compras sem lançamento no Omie');
    expect(within(block).getByText(/−R\$\s*420,00/)).toHaveClass('text-destructive');
    expect(within(block).getByRole('link', { name: 'Abrir a fatura' })).toHaveAttribute(
      'href',
      '/clientes/c1/conciliacao/s2',
    );
    // A flag do lançamento no Omie não é conhecida no front: a frase não aparece.
    expect(block).not.toHaveTextContent(/desligado/);
  });

  it('compras do cartão: zero compras, e mês sem fatura de cartão', () => {
    summaryState.data = { ...SUMMARY, cardPurchasesToPost: { count: 0, totalAmount: '0.00' } };
    const { unmount } = render(<ClientDashboard clientId="c1" />);
    expect(card('Compras do cartão a lançar')).toHaveTextContent('0 compras sem lançamento');
    unmount();

    monthState.data = {
      data: [session()],
      pagination: { page: 1, pageSize: 100, total: 1, totalPages: 1 },
    };
    render(<ClientDashboard clientId="c1" />);
    const block = card('Compras do cartão a lançar');
    expect(block).toHaveTextContent('Nenhuma fatura de cartão neste mês.');
    expect(within(block).queryByRole('link')).toBeNull();
  });

  it('de-para: nome do destino, cobertura, "—" quando nula, sem decisão e materialização', () => {
    render(<ClientDashboard clientId="c1" />);
    const block = card('De-para do mês');
    const [contabil, fluxo] = within(block).getAllByRole('listitem');

    expect(contabil).toHaveTextContent('Conta contábil');
    expect(contabil).toHaveTextContent('81,2%');
    expect(within(contabil!).getByText('4')).toHaveClass('text-warning');
    expect(contabil).toHaveTextContent('Materialização: Pendente');
    // Destino sem nome no catálogo: o código. Cobertura nula: "—", nunca "0%".
    expect(fluxo).toHaveTextContent('fluxo_de_caixa');
    expect(fluxo).toHaveTextContent('—');
    expect(fluxo).not.toHaveTextContent('0%');
    expect(fluxo).toHaveTextContent('Materialização: Concluída');
    expect(within(block).getByRole('link', { name: 'Decidir no de-para' })).toHaveAttribute(
      'href',
      '/clientes/c1/de-para?view=previa&competence=2026-10',
    );
  });

  it('carteira: os dois lados, vencido em destaque e os rodapés', () => {
    render(<ClientDashboard clientId="c1" />);
    const receber = card('A receber');
    const pagar = card('A pagar');

    expect(within(receber).getByText(/R\$\s*107\.413,10/)).toBeVisible();
    expect(within(receber).getAllByText(/R\$\s*82\.865,50/)[0]).toHaveClass('text-destructive');
    expect(receber).toHaveTextContent('Inadimplência real (sem contexto)');
    expect(within(receber).getByText(/R\$\s*60\.000,00/)).toBeVisible();
    expect(receber).toHaveTextContent('(30 títulos)');
    expect(pagar).toHaveTextContent('Vence nos próximos 7 dias');
    expect(within(pagar).getByText(/R\$\s*1\.200,00/)).toBeVisible();
    expect(screen.getByText(/aging com referência em 07\/10\/2026/)).toBeVisible();
  });

  it('fluxo: gráfico, vencidos fora das barras e o rodapé "não é saldo de conta"', () => {
    render(<ClientDashboard clientId="c1" />);
    const block = card('Fluxo previsto pelos vencimentos');

    expect(within(block).getByRole('img', { name: /Fluxo previsto por faixa/ })).toBeVisible();
    expect(within(block).getByTestId('flow-overdue-line')).toHaveTextContent(
      /Já vencidos: \+R\$\s*82\.865,50 a receber · −R\$\s*4\.380,00 a pagar/,
    );
    expect(block).toHaveTextContent('Não é saldo de conta');
  });

  it('atividade: origem, última conciliação do resumo e o glossário', () => {
    render(<ClientDashboard clientId="c1" />);
    const block = card('Atividade');
    expect(within(block).getByRole('link', { name: /Abrir a última conciliação/ })).toHaveAttribute(
      'href',
      '/clientes/c1/conciliacao/s2',
    );
    expect(block).toHaveTextContent('Glossário');
    expect(within(block).getByTestId('dashboard-glossary-line')).toHaveTextContent(
      '42 termos · versão 7',
    );
  });

  it('fluxo em largura inteira, ANTES da faixa de atividade', () => {
    render(<ClientDashboard clientId="c1" />);
    const flow = card('Fluxo previsto pelos vencimentos');
    const activity = card('Atividade');
    // Irmãos diretos do mesmo contêiner, sem coluna lateral.
    expect(flow.parentElement).toBe(activity.parentElement);
    expect(flow.compareDocumentPosition(activity) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<ClientDashboard clientId="c1" />);
    await assertNoA11yViolations(container);
  });
});

describe('ClientDashboard — origem', () => {
  const OMIE_ATIVA = {
    id: 'omie-1',
    provider_type: 'omie',
    label: 'Omie principal',
    status: 'ativa',
    capabilities: ['listar_contas', 'listar_lancamentos', 'verificar_credencial'],
    last_checked_at: '2026-10-04T12:00:00Z',
    accounts_synced_at: '2026-10-04T12:00:00Z',
  };

  it('origem ativa: só a linha de estado e o link para Contas Bancárias, sem a seção', () => {
    detailState.data = { ...detailState.data!, connections: [OMIE_ATIVA] };
    render(<ClientDashboard clientId="c1" />);

    expect(screen.queryByRole('heading', { name: 'Origens de dado' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Conectar origem' })).toBeNull();
    const line = screen.getByTestId('dashboard-origin-line');
    expect(line).toHaveTextContent('Omie · Ativa · verificada há 3 dias');
    expect(within(line).getByRole('link', { name: /Gerir origens/ })).toHaveAttribute(
      'href',
      '/clientes/c1/contas',
    );
  });

  it('quem não gere conexões lê "Ver origens" no link', () => {
    authState.user = OPERATOR;
    detailState.data = { ...detailState.data!, connections: [OMIE_ATIVA] };
    render(<ClientDashboard clientId="c1" />);
    expect(
      within(screen.getByTestId('dashboard-origin-line')).getByRole('link', {
        name: /Ver origens/,
      }),
    ).toBeVisible();
  });

  it('sem origem: a seção completa aparece antes do fechamento do mês', () => {
    detailState.data = { ...detailState.data!, origin_status: 'sem_origem', connections: [] };
    render(<ClientDashboard clientId="c1" />);

    const heading = screen.getByRole('heading', { name: 'Origens de dado' });
    const closing = screen.getByRole('heading', { name: 'Fechamento do mês' });
    expect(
      heading.compareDocumentPosition(closing) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Conectar origem' }).length).toBeGreaterThan(0);
    expect(screen.getByTestId('dashboard-origin-line')).toHaveTextContent(
      'Nenhuma origem conectada',
    );
  });

  it('origem com erro: a seção completa aparece, com o reconectar', () => {
    detailState.data = {
      ...detailState.data!,
      origin_status: 'erro',
      connections: [{ ...OMIE_ATIVA, status: 'erro' }],
    };
    render(<ClientDashboard clientId="c1" />);
    expect(screen.getByRole('heading', { name: 'Origens de dado' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Reconectar' })).toBeVisible();
    expect(screen.getByTestId('dashboard-origin-line')).toHaveTextContent('Omie · Com erro');
  });

  it('cliente encerrado: nem seção nem link, só a linha de estado', () => {
    detailState.data = {
      ...detailState.data!,
      origin_status: 'sem_origem',
      connections: [],
      closed_at: '2026-09-01T00:00:00Z',
    };
    render(<ClientDashboard clientId="c1" />);
    expect(screen.queryByRole('heading', { name: 'Origens de dado' })).toBeNull();
    const line = screen.getByTestId('dashboard-origin-line');
    expect(line).toHaveTextContent('Nenhuma origem conectada');
    expect(within(line).queryByRole('link')).toBeNull();
  });
});

describe('ClientDashboard — estados', () => {
  it('carteira negada pelo servidor (403): somem carteira e fluxo, o resto segue', () => {
    titlesState.isError = true;
    titlesState.data = undefined;
    titlesState.error = new ApiError(403, {
      code: 'FORBIDDEN',
      message: 'forbidden',
      userMessage: 'Sem acesso.',
    });
    render(<ClientDashboard clientId="c1" />);

    expect(screen.queryByRole('heading', { name: 'Carteira em aberto' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Fluxo previsto pelos vencimentos' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Fechamento do mês' })).toBeVisible();
  });

  it('carteira nunca sincronizada: sem zeros, e o convite a sincronizar', () => {
    titlesState.data = { ...TITLES, neverSynced: true, syncedAt: null };
    render(<ClientDashboard clientId="c1" />);

    expect(screen.getByTestId('dashboard-portfolio-never-synced')).toHaveTextContent(
      'ainda não foi sincronizada',
    );
    expect(screen.getByRole('button', { name: 'Sincronizar agora' })).toBeVisible();
    expect(screen.queryByRole('region', { name: 'A receber' })).toBeNull();
    expect(screen.queryByRole('region', { name: 'Fluxo previsto pelos vencimentos' })).toBeNull();
  });

  it('operador (sem sincronizar) vê o texto e o caminho para a Carteira, sem o botão', () => {
    authState.user = OPERATOR;
    titlesState.data = { ...TITLES, neverSynced: true, syncedAt: null };
    render(<ClientDashboard clientId="c1" />);

    expect(screen.queryByRole('button', { name: 'Sincronizar agora' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Ir para a Carteira' })).toHaveAttribute(
      'href',
      '/clientes/c1/carteira',
    );
  });

  it('cliente sem conciliação nenhuma: o card mostra o estado vazio pela regra de origem', () => {
    summaryState.data = { ...SUMMARY, latestSession: null };
    monthState.data = {
      data: [],
      pagination: { page: 1, pageSize: 100, total: 0, totalPages: 0 },
    };
    render(<ClientDashboard clientId="c1" />);

    const vazio = screen.getByTestId('dashboard-no-reconciliations');
    expect(vazio).toHaveAttribute('data-state', 'pronto');
    expect(within(vazio).getByRole('link', { name: /Ir para conciliações/ })).toHaveAttribute(
      'href',
      '/clientes/c1',
    );
    expect(card('Atividade')).toHaveTextContent('Nenhuma conciliação ainda.');
  });

  it('cliente por arquivo sem conciliação: manda para Origem por arquivo e não oferece criar', () => {
    summaryState.data = { ...SUMMARY, latestSession: null };
    detailState.data = {
      ...detailState.data!,
      connections: [
        {
          id: 'arq-1',
          provider_type: 'arquivo',
          label: 'Arquivo',
          status: 'ativa',
          capabilities: ['listar_lancamentos'],
        },
      ],
    };
    render(<ClientDashboard clientId="c1" />);

    expect(screen.getByTestId('dashboard-no-reconciliations')).toHaveAttribute(
      'data-state',
      'arquivo',
    );
    expect(screen.queryByRole('button', { name: 'Nova conciliação' })).toBeNull();
  });

  it('cliente sem origem sem conciliação: explica, sem ação que a lista não oferece', () => {
    summaryState.data = { ...SUMMARY, latestSession: null };
    detailState.data = { ...detailState.data!, origin_status: 'sem_origem', connections: [] };
    render(<ClientDashboard clientId="c1" />);

    const vazio = screen.getByTestId('dashboard-no-reconciliations');
    expect(vazio).toHaveAttribute('data-state', 'sem-origem');
    expect(within(vazio).queryByRole('link')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Nova conciliação' })).toBeNull();
  });

  it('cliente encerrado: só leitura, sem nova conciliação nem sincronizar', () => {
    detailState.data = { ...detailState.data!, closed_at: '2026-09-01T00:00:00Z' };
    titlesState.data = { ...TITLES, neverSynced: true, syncedAt: null };
    render(<ClientDashboard clientId="c1" />);

    expect(screen.queryByRole('button', { name: 'Nova conciliação' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Sincronizar agora' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Ver conciliações' })).toBeVisible();
  });

  it('cada bloco falha sozinho, com o próprio "Tentar novamente"', () => {
    flowState.isError = true;
    flowState.data = undefined;
    render(<ClientDashboard clientId="c1" />);

    const alerts = screen.getAllByRole('alert');
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent('Não foi possível carregar o fluxo previsto.');
    expect(within(alerts[0]!).getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
    // O resto do painel continua.
    expect(card('Conciliações do mês')).toBeVisible();
    expect(card('A receber')).toBeVisible();
  });

  it('resumo carregando: skeleton por card do fechamento, a carteira já aparece', () => {
    summaryState.isLoading = true;
    summaryState.data = undefined;
    render(<ClientDashboard clientId="c1" />);
    expect(screen.getByLabelText('Carregando anomalias')).toBeInTheDocument();
    expect(card('A receber')).toBeVisible();
  });
});
