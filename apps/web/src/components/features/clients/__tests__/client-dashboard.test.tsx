/**
 * Testes do painel do cliente (FRONT 04.7 / R7 — desejável).
 *
 * Critérios: resumo com conciliações no mês, anomalias, última conciliação
 * (status + data) e contas sincronizadas; estados loading/vazio/erro; axe-core.
 * Tudo a partir de dados já persistidos — nenhuma consulta cara nova.
 */
import { render, screen, within } from '@testing-library/react';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

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
  useClientDetail: () => detailState,
  // A 1ª chamada é a do mês (tem `month`), a 2ª é a da última conciliação.
  useReconciliationsList: (_id: string, params: { month?: string }) =>
    params.month !== undefined ? monthState : latestState,
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

  it('cliente sem conciliação convida a criar a primeira', () => {
    monthState.data = {
      data: [],
      pagination: { page: 1, pageSize: 100, total: 0, totalPages: 0 },
    };
    latestState.data = { data: [], pagination: { page: 1, pageSize: 1, total: 0, totalPages: 0 } };
    render(<ClientDashboard clientId="c1" />);
    expect(screen.getByText(/ainda não tem conciliações/)).toBeVisible();
    expect(screen.getByRole('link', { name: /Criar conciliação/ })).toBeVisible();
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
