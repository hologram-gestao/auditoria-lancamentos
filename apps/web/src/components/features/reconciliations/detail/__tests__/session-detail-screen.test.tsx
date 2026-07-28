/**
 * Testes do detalhe da conciliação (FRONT 04.7 / R3).
 *
 * Critérios cobertos:
 *   - topo com os SEIS totalizadores (movimentações, conciliados, sem Omie,
 *     Omie sem arquivo, anomalias, nº de arquivos) e o resumo de saldos
 *     (anterior/arquivo/Omie/diferença + status Conferido/Divergente);
 *   - números vindos da FONTE ÚNICA (o payload do detalhe) — o mesmo objeto
 *     alimenta topo e abas, então não há como divergirem;
 *   - `processing` mostra progresso (e diz que dá para sair da tela);
 *   - `error` mostra mensagem genérica + CÓDIGO, sem `error_message` e sem
 *     fundo âmbar;
 *   - aba ativa na URL (`?tab=`);
 *   - axe-core sem violações critical/serious.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const detailState = {
  data: undefined as Record<string, unknown> | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => ({
    data: {
      accounts: [{ id: 'a1', omie_conta_id: 10, name: 'Cartão Itaú', bank_name: 'Itaú' }],
    },
  }),
}));

vi.mock('@/hooks/use-reconciliations', () => ({
  useSessionDetail: () => detailState,
  useSessionFiles: () => ({ data: undefined, isLoading: false, isError: true }),
  useDeleteSessionFile: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReprocessReconciliation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDiscardReconciliation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useExportReconciliation: () => ({ mutate: vi.fn(), isPending: false }),
}));

// As abas fazem suas próprias queries; aqui interessa o TOPO, então viram stubs.
vi.mock('@/components/features/reconciliations/review/movements-tab', () => ({
  MovementsTab: () => <div>stub movimentações</div>,
}));
vi.mock('@/components/features/reconciliations/review/omie-divergences-tab', () => ({
  OmieDivergencesTab: () => <div>stub divergências</div>,
}));
vi.mock('@/components/features/reconciliations/review/anomalies-tab', () => ({
  AnomaliesTab: () => <div>stub anomalias</div>,
}));
vi.mock('@/components/features/reconciliations/review/summary-tab', () => ({
  SummaryTab: (props: { counts: { conciliated: number } }) => (
    <div>stub resumo conciliados={props.counts.conciliated}</div>
  ),
}));

import { SessionDetailScreen } from '@/components/features/reconciliations/detail/session-detail-screen';
import { assertNoA11yViolations } from '@/test/a11y';

function detail(over: Record<string, unknown> = {}) {
  return {
    session_id: 's1',
    client_id: 'c1',
    omie_conta_id: 10,
    account_type: 'credit_card',
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
    ...over,
  };
}

function renderScreen() {
  return render(<SessionDetailScreen clientId="c1" sessionId="s1" />);
}

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  currentSearch = '';
  detailState.data = detail();
  detailState.isLoading = false;
  detailState.isError = false;
  detailState.error = null;
});

describe('Detalhe — totalizadores e resumo', () => {
  it('exibe os seis totalizadores', () => {
    renderScreen();
    const totals = screen.getByRole('region', { name: 'Totalizadores da conciliação' });
    for (const [label, value] of [
      ['Movimentações', '30'],
      ['Conciliados', '25'],
      ['Sem Omie', '3'],
      ['Omie sem arquivo', '2'],
      ['Anomalias', '1'],
      ['Arquivos', '3'],
    ] as const) {
      const card = within(totals).getByText(label).closest('div')?.parentElement;
      expect(card).not.toBeNull();
      expect(within(card as HTMLElement).getByText(value)).toBeVisible();
    }
  });

  it('exibe o resumo de saldos com status "Conferido" dentro da tolerância', () => {
    renderScreen();
    const summary = screen.getByRole('region', { name: 'Resumo geral' });
    expect(within(summary).getByText('R$ 1.000,00')).toBeVisible();
    expect(within(summary).getAllByText('R$ 1.500,00')).toHaveLength(2);
    expect(within(summary).getByText('Conferido')).toBeVisible();
  });

  it('marca "Divergente" quando a diferença passa de R$ 0,01', () => {
    detailState.data = detail({ balance_difference: '12.34' });
    renderScreen();
    expect(screen.getByText('Divergente')).toBeVisible();
  });

  it('saldo ausente (sessão legada) mostra "Indisponível", não R$ 0,00', () => {
    detailState.data = detail({
      balance_start: null,
      balance_end_file: null,
      balance_end_omie: null,
      balance_difference: null,
    });
    renderScreen();
    expect(screen.getAllByText('Indisponível').length).toBeGreaterThan(0);
  });

  it('as abas recebem os MESMOS números do topo (fonte única)', async () => {
    const user = userEvent.setup();
    currentSearch = 'tab=resumo';
    renderScreen();
    await user.click(screen.getByRole('tab', { name: /Resumo/ }));
    expect(screen.getByText('stub resumo conciliados=25')).toBeVisible();
    // O rótulo da aba usa o mesmo contador do card.
    expect(screen.getByRole('tab', { name: 'Movimentações (30)' })).toBeVisible();
  });
});

describe('Detalhe — aba na URL', () => {
  it('lê a aba ativa da querystring', () => {
    currentSearch = 'tab=anomalias';
    renderScreen();
    expect(screen.getByRole('tab', { name: /Anomalias/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('trocar de aba escreve na URL', async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByRole('tab', { name: /Divergências Omie/ }));
    expect(replaceMock).toHaveBeenCalledWith('?tab=divergencias', { scroll: false });
  });

  it('aba inválida na URL cai no default', () => {
    currentSearch = 'tab=inexistente';
    renderScreen();
    expect(screen.getByRole('tab', { name: 'Movimentações (30)' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });
});

describe('Detalhe — estados por status', () => {
  it('em processamento mostra progresso e diz que dá para sair da tela', () => {
    detailState.data = detail({ status: 'processing' });
    renderScreen();
    expect(screen.getByRole('status')).toHaveTextContent('Conciliação em processamento');
    expect(screen.getByText(/pode sair desta tela/)).toBeVisible();
    // Sem abas nem exportação enquanto processa.
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByRole('button', { name: /Exportar/ })).toBeNull();
  });

  it('em erro mostra mensagem genérica + código, e NUNCA a mensagem interna', () => {
    detailState.data = detail({
      status: 'error',
      error_code: 'RECONCILIATION_TIMEOUT',
      error_message: 'asyncio.TimeoutError após 900s',
    });
    renderScreen();
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Não foi possível concluir esta conciliação.');
    expect(within(alert).getByText('RECONCILIATION_TIMEOUT')).toBeVisible();
    expect(screen.queryByText(/asyncio/)).toBeNull();
    // Erro é `destructive`, nunca âmbar/warning.
    expect(alert.className).toContain('destructive');
    expect(alert.className).not.toContain('warning');
  });

  it('erro oferece reprocessar e excluir', () => {
    detailState.data = detail({ status: 'error', error_code: 'PARSE_ERROR' });
    renderScreen();
    expect(screen.getByRole('button', { name: /Tentar novamente/ })).toBeVisible();
    expect(screen.getByRole('button', { name: /Excluir conciliação/ })).toBeVisible();
  });

  it('carregando mostra o skeleton; falha de carga oferece retry', () => {
    detailState.isLoading = true;
    detailState.data = undefined;
    const { unmount } = renderScreen();
    expect(screen.getByLabelText('Carregando conciliação')).toBeInTheDocument();
    unmount();

    detailState.isLoading = false;
    detailState.isError = true;
    renderScreen();
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });
});

describe('Detalhe — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = renderScreen();
    await assertNoA11yViolations(container);
  });

  it('não tem violações critical/serious no estado de erro', async () => {
    detailState.data = detail({ status: 'error', error_code: 'PARSE_ERROR' });
    const { container } = renderScreen();
    await assertNoA11yViolations(container);
  });

  /**
   * Regressão do `aria-prohibited-attr` no SKELETON (reprovação do QA): o
   * esqueleto era uma `<div>` com `aria-busy` + `aria-label`, e `aria-label`
   * em role genérico é proibido — o "Carregando conciliação" nunca chegava a
   * ser anunciado. `role="status"` valida o rótulo E faz o estado ser
   * anunciado. Faltava um teste do estado de LOADING: os dois acima só cobriam
   * a tela já carregada.
   */
  it('não tem violações critical/serious no estado de carregamento', async () => {
    detailState.data = undefined;
    detailState.isLoading = true;
    const { container } = renderScreen();
    expect(screen.getByRole('status', { name: 'Carregando conciliação' })).toBeInTheDocument();
    await assertNoA11yViolations(container);
  });
});
