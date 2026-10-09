/**
 * "Cliquei e não sei o que aconteceu" (86e3n70qj, reunião de 08/10/2026).
 *
 * O que estes testes travam:
 *   - enquanto a gaveta abre (categorias a caminho), o botão clicado fica em
 *     carregando e não aceita outro clique;
 *   - depois do envio, a linha da aba Movimentações E a linha da anomalia mostram
 *     "Lançado no Omie · nº X" VISÍVEL, não só numa dica ou no texto da resolução;
 *   - a gaveta diz, no topo e em cada linha, quantas entraram e ONDE (conta do
 *     cartão, data da compra).
 *
 * O estado "lançado nesta visita" e a abertura são do hook (testado em
 * `hooks/__tests__/use-omie-postings.test.tsx`); aqui eles são o mock mutável.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const fileEntriesState = {
  data: undefined as { data: unknown[]; pagination: unknown } | undefined,
  isLoading: false,
};
const anomaliesState = {
  data: undefined as { data: AnomalyItem[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
};
const semOmieState = { data: [] as FileEntryItem[] };

vi.mock('@/hooks/use-reconciliations', () => ({
  useFileEntries: () => fileEntriesState,
  useOmieLancamentos: () => ({ data: [], isLoading: false }),
  useAllSessionAnomalies: () => ({ data: [], isLoading: false }),
  useAllSemOmieEntries: () => semOmieState,
  usePatchFileEntry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAnomalies: () => anomaliesState,
  usePatchAnomaly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAnomalyTypes: () => ({ data: [], isLoading: false }),
}));

const postedState: { value: Record<string, number | null> } = { value: {} };
const openingState: { value: unknown[] | null } = { value: null };
const openMock = vi.fn();
const postMock = vi.fn();
const postState = { mutateAsync: postMock, isPending: false };

vi.mock('@/hooks/use-omie-postings', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  usePostOmieLancamentos: () => postState,
  useOmieCategorias: () => ({
    data: { data: [{ codigo: '2.01.03', descricao: 'Combustível' }], total: 1 },
    isLoading: false,
    isError: false,
    isFetching: false,
  }),
  usePostedInSession: () => postedState.value,
  useOpenLancarDrawer: (_sessionId: string, onReady: (targets: unknown[]) => void) => ({
    open: async (targets: unknown[]) => {
      openMock(targets);
      onReady(targets);
    },
    openingTargets: openingState.value,
  }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// Imports do SUT DEPOIS dos `vi.mock`.
import { AnomaliesTab } from '@/components/features/reconciliations/review/anomalies-tab';
import { LancarNoOmieDrawer } from '@/components/features/reconciliations/review/lancar-no-omie-drawer';
import { MovementsTab } from '@/components/features/reconciliations/review/movements-tab';
import { LancadaNoOmieBadge } from '@/components/features/reconciliations/review/situation-badge';
import type { AnomalyItem, FileEntryItem } from '@/lib/api/reconciliations';
import type { AuthenticatedUser } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const SESSION_ID = 's1';

function entry(over: Partial<FileEntryItem> = {}): FileEntryItem {
  return {
    id: 'e1',
    transaction_date: '2026-06-10',
    description: 'Posto Shell 1234',
    amount: '-150.50',
    balance: null,
    situation: 'sem_omie',
    user_action: null,
    user_note: null,
    omie_lancamento_id: null,
    ...over,
  };
}

function missingInOmie(over: Partial<AnomalyItem> = {}): AnomalyItem {
  return {
    id: 'a1',
    anomaly_type: {
      id: 't1',
      code: 'missing_in_omie',
      name: 'Sem lançamento no Omie',
      severity: 'critical',
    },
    detected_by: 'ai',
    resolved: false,
    review_verdict: null,
    context: null,
    resolution_note: null,
    created_at: '2026-07-01T12:00:00Z',
    related_file_entry: {
      id: 'e1',
      transaction_date: '2026-06-10',
      description: 'Posto Shell 1234',
      amount: '-150.50',
    },
    related_omie_entry: null,
    ...over,
  };
}

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  postedState.value = {};
  openingState.value = null;
  postState.isPending = false;
  semOmieState.data = [];
  authState.user = {
    id: 'op',
    email: 'operador@cliente-exemplo.com.br',
    name: 'Operador do Cliente',
    role: 'client_operator',
    scope: 'client',
    client_id: 'c1',
  } as AuthenticatedUser;
  fileEntriesState.data = {
    data: [entry()],
    pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
  };
  anomaliesState.data = {
    data: [missingInOmie()],
    pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
  };
});

describe('Selo "Lançado no Omie · nº X"', () => {
  it('o número fica visível no próprio selo, e a dica diz onde entrou', async () => {
    const { container } = render(<LancadaNoOmieBadge omieLancamentoId={5001} />);
    const selo = screen.getByRole('img', { name: /Lançado no Omie · nº 5001/ });
    expect(selo).toHaveTextContent('Lançado no Omie · nº 5001');
    expect(selo).toHaveAccessibleName(/na conta do cartão no Omie, na data da compra/);
    await assertNoA11yViolations(container);
  });

  it('sem número na resposta, o selo não inventa um', () => {
    render(<LancadaNoOmieBadge omieLancamentoId={null} />);
    expect(screen.getByRole('img', { name: /^Lançado no Omie\./ })).toHaveTextContent(
      /^Lançado no Omie$/,
    );
  });
});

describe('Aba Movimentações', () => {
  it('linha lançada nesta visita mostra o selo com o número', () => {
    fileEntriesState.data = {
      data: [entry({ situation: 'conciliado', omie_lancamento_id: 5001 })],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    postedState.value = { e1: 5001 };
    render(<MovementsTab sessionId={SESSION_ID} isCard canPostToOmie />);
    expect(screen.getByText('Lançado no Omie · nº 5001')).toBeVisible();
  });

  it('clicar em "Lançar no Omie" pede a abertura com a linha', async () => {
    const ui = userEvent.setup();
    render(<MovementsTab sessionId={SESSION_ID} isCard canPostToOmie />);
    await ui.click(screen.getByRole('button', { name: /Lançar no Omie/ }));
    expect(openMock).toHaveBeenCalledWith([expect.objectContaining({ id: 'e1' })]);
  });

  it('enquanto a gaveta abre, o botão da linha fica em carregando e não aceita clique', async () => {
    openingState.value = [entry()];
    const ui = userEvent.setup({ pointerEventsCheck: 0 });
    render(<MovementsTab sessionId={SESSION_ID} isCard canPostToOmie />);
    const botao = screen.getByRole('button', { name: /Lançar no Omie/ });
    expect(botao).toBeDisabled();
    await ui.click(botao);
    expect(openMock).not.toHaveBeenCalled();
  });
});

describe('Aba Anomalias', () => {
  it('a anomalia da linha lançada mostra o selo com o número, fora do texto da resolução', () => {
    postedState.value = { e1: 5001 };
    render(<AnomaliesTab sessionId={SESSION_ID} isCard canPostToOmie />);
    const linha = screen.getByRole('row', { name: /Sem lançamento no Omie/ });
    expect(within(linha).getByText('Lançado no Omie · nº 5001')).toBeVisible();
  });

  it('linha não lançada não ganha selo', () => {
    render(<AnomaliesTab sessionId={SESSION_ID} isCard canPostToOmie />);
    expect(screen.queryByText(/Lançado no Omie/)).toBeNull();
  });
});

describe('Gaveta depois do envio', () => {
  it('diz no topo quantas entraram e, em cada linha, o número e onde entrou', async () => {
    postMock.mockResolvedValue({
      lines: [
        {
          file_entry_id: 'e1',
          status: 'lancada',
          reason: null,
          message: null,
          omie_lancamento_id: 5001,
        },
      ],
      lancadas: 1,
      bloqueadas: 0,
      com_erro: 0,
    });
    const ui = userEvent.setup();
    const { container } = render(
      <LancarNoOmieDrawer
        sessionId={SESSION_ID}
        entries={[entry()]}
        open
        onOpenChange={vi.fn()}
        onPosted={vi.fn()}
      />,
    );
    await ui.click(screen.getByRole('button', { name: /Categoria da compra/ }));
    await ui.click(await screen.findByRole('option', { name: /Combustível/ }));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));

    const resumo = await screen.findByRole('status');
    expect(resumo).toHaveTextContent('1 compra lançada no Omie.');
    expect(resumo).toHaveTextContent(/na conta do cartão, na data da compra/);
    expect(screen.getByText(/lançamento nº 5001/)).toBeVisible();
    expect(
      screen.getByText('Na conta do cartão no Omie, na data da compra (10/06/2026).'),
    ).toBeVisible();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Concluir' })).toBeVisible());
    await assertNoA11yViolations(container);
  });
});
