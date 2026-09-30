/**
 * Testes do painel do cliente (FRONT 04.7 / R7 — desejável).
 *
 * Critérios: resumo com conciliações no mês, anomalias, última conciliação
 * (status + data) e contas sincronizadas; estados loading/vazio/erro; axe-core.
 * Tudo a partir de dados já persistidos — nenhuma consulta cara nova.
 */
import { render, screen, within } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// O painel monta a seção de conexões, que lê `?conectar=` pelo `useUrlState`
// (86e3fqnc9) — daí `usePathname`/`useSearchParams` no mock.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/clientes/c1/painel',
  useSearchParams: () => new URLSearchParams(''),
}));

const detailState = {
  data: undefined as Record<string, unknown> | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const monthState = {
  data: undefined as Record<string, unknown> | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const latestState = {
  data: undefined as Record<string, unknown> | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

vi.mock('@/hooks/use-clients', () => ({
  // O ClientShell agora monta o diálogo de exclusão (86e34jd1d).
  // O ClientShell/lista agora renderiza o coração de favorito (86e34jd5a).
  useSetFavorite: () => ({ mutate: vi.fn(), isPending: false }),
  useClientDetail: () => detailState,
  // A 1ª chamada é a do mês (tem `month`), a 2ª é a da última conciliação.
  useReconciliationsList: (_id: string, params: { month?: string }) =>
    params.month !== undefined ? monthState : latestState,
}));

// S9: o painel passou a montar a seção de origens, que busca as conexões por
// conta própria. Sem este mock o `useQuery` real exigiria um QueryClient —
// e o que esta suíte mede é o RESUMO, não a seção (que tem suíte própria).
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
import { assertNoA11yViolations } from '@/test/a11y';

function session(over: Record<string, unknown> = {}) {
  return {
    id: 's1',
    omie_conta_id: 10,
    account_type: 'credit_card',
    reference_month: '2026-06-01',
    status: 'reviewing',
    created_at: '2026-06-12T14:32:00Z',
    total_file_entries: 30,
    conciliated_count: 25,
    sem_omie_count: 3,
    omie_sem_arquivo_count: 2,
    anomaly_count: 4,
    total_files: 2,
    ...over,
  };
}

beforeAll(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date('2026-06-20T12:00:00Z'));
});

beforeEach(() => {
  detailState.data = {
    accounts: [{ id: 'a1' }, { id: 'a2' }],
    accounts_synced_at: '2026-06-20T09:00:00Z',
    origin_status: 'ativa',
    connections: [],
  };
  detailState.isLoading = false;
  detailState.isError = false;
  monthState.data = {
    data: [session(), session({ id: 's2', anomaly_count: 1 })],
    pagination: { page: 1, pageSize: 100, total: 2, totalPages: 1 },
  };
  monthState.isLoading = false;
  monthState.isError = false;
  latestState.data = {
    data: [session()],
    pagination: { page: 1, pageSize: 1, total: 2, totalPages: 2 },
  };
  latestState.isLoading = false;
  latestState.isError = false;
});

describe('ClientDashboard', () => {
  it('resume conciliações do mês, anomalias, contas e a última conciliação', () => {
    render(<ClientDashboard clientId="c1" />);

    const conciliacoes = screen.getByText('Conciliações no mês').closest('div')
      ?.parentElement as HTMLElement;
    expect(within(conciliacoes).getByText('2')).toBeVisible();

    const anomalias = screen.getByText('Anomalias no mês').closest('div')
      ?.parentElement as HTMLElement;
    // 4 + 1 somados das sessões do mês.
    expect(within(anomalias).getByText('5')).toBeVisible();

    const contas = screen.getByText('Contas sincronizadas').closest('div')
      ?.parentElement as HTMLElement;
    expect(within(contas).getByText('2')).toBeVisible();
    expect(within(contas).getByText('Sincronizado há 3 h')).toBeVisible();

    const ultima = screen.getByText('Última conciliação').closest('div')
      ?.parentElement as HTMLElement;
    expect(within(ultima).getByText('Junho de 2026')).toBeVisible();
    expect(within(ultima).getByText('Processada')).toBeVisible();
  });

  // 86e3g9uku: o estado vazio não pode prometer o que a tela de destino não
  // oferece. "Criar conciliação" levava à LISTA, onde a criação já está
  // escondida para quem não pode conciliar.
  function semConciliacoes() {
    monthState.data = {
      data: [],
      pagination: { page: 1, pageSize: 100, total: 0, totalPages: 0 },
    };
    latestState.data = { data: [], pagination: { page: 1, pageSize: 1, total: 0, totalPages: 0 } };
  }

  it('cliente que concilia: convida, com rótulo honesto (o link leva à LISTA)', () => {
    semConciliacoes();
    render(<ClientDashboard clientId="c1" />);
    const vazio = screen.getByTestId('dashboard-no-reconciliations');
    expect(vazio).toHaveAttribute('data-state', 'pronto');
    expect(vazio).toHaveTextContent(/ainda não tem conciliações/);
    const link = screen.getByRole('link', { name: /Ir para conciliações/ });
    expect(link).toHaveAttribute('href', '/clientes/c1');
    expect(screen.queryByRole('link', { name: /Criar conciliação/ })).toBeNull();
  });

  it('cliente por arquivo: diz que não concilia e manda para Origem por arquivo', () => {
    semConciliacoes();
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
    const vazio = screen.getByTestId('dashboard-no-reconciliations');
    expect(vazio).toHaveAttribute('data-state', 'arquivo');
    expect(vazio).toHaveTextContent(/não concilia/);
    expect(screen.getByRole('link', { name: /Ir para Origem por arquivo/ })).toHaveAttribute(
      'href',
      '/clientes/c1/origem-arquivo',
    );
    expect(screen.queryByRole('link', { name: /Ir para conciliações/ })).toBeNull();
  });

  it('cliente sem origem: explica o que falta, sem ação que a lista não oferece', () => {
    semConciliacoes();
    detailState.data = { ...detailState.data!, origin_status: 'sem_origem', connections: [] };
    render(<ClientDashboard clientId="c1" />);
    const vazio = screen.getByTestId('dashboard-no-reconciliations');
    expect(vazio).toHaveAttribute('data-state', 'sem-origem');
    expect(vazio).toHaveTextContent(/origem conectada e ativa/);
    expect(within(vazio).queryByRole('link')).toBeNull();
  });

  it('cliente encerrado: nenhuma ação no estado vazio', () => {
    semConciliacoes();
    detailState.data = { ...detailState.data!, closed_at: '2026-09-01T00:00:00Z' };
    render(<ClientDashboard clientId="c1" />);
    const vazio = screen.getByTestId('dashboard-no-reconciliations');
    expect(vazio).toHaveAttribute('data-state', 'encerrado');
    expect(within(vazio).queryByRole('link')).toBeNull();
  });

  it('carregando mostra skeleton; erro oferece retry', () => {
    monthState.isLoading = true;
    const { unmount } = render(<ClientDashboard clientId="c1" />);
    expect(screen.getByLabelText('Carregando painel')).toBeInTheDocument();
    unmount();

    monthState.isLoading = false;
    monthState.isError = true;
    render(<ClientDashboard clientId="c1" />);
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });

  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<ClientDashboard clientId="c1" />);
    await assertNoA11yViolations(container);
  });
});
