/**
 * A linha conciliada mostra a situação do lançamento NO OMIE (86e3t4vpq).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que estes testes travam:
 *   - "Conciliado" e "Não conciliado" aparecem como vieram da Omie, com o tom
 *     neutro só para "Conciliado";
 *   - sem `cSituacao` (o cache grava `""`), a linha fica como sempre foi: sem
 *     selo, nunca um "Conciliado" inventado;
 *   - o selo só existe na linha COM par (sem Omie não tem situação lá dentro);
 *   - o selo e o bloco do topo explicam que a marcação do Omie não decide o
 *     cruzamento, pelo nome acessível (não só pelo tooltip aberto).
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let rows: FileEntryItem[] = [];
let lookup: OmieLancamentoItem[] = [];

vi.mock('@/hooks/use-omie-postings', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  usePostedInSession: () => ({}),
  useOpenLancarDrawer: (_sessionId: string, onReady: (targets: unknown[]) => void) => ({
    open: async (targets: unknown[]) => onReady(targets),
    openingTargets: null,
  }),
}));

vi.mock('@/hooks/use-reconciliations', () => ({
  useFileEntries: () => ({
    data: { data: rows, pagination: { page: 1, pageSize: 20, total: rows.length, totalPages: 1 } },
    isLoading: false,
  }),
  useAllSessionAnomalies: () => ({ data: [], isLoading: false }),
  useAllSemOmieEntries: () => ({ data: [], isLoading: false }),
  useOmieLancamentos: () => ({ data: lookup, isLoading: false }),
  usePatchFileEntry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchAnomaly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAnomalyTypes: () => ({ data: [], isLoading: false }),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: unknown }) => unknown) =>
    selector({
      user: {
        id: 'me',
        email: 'admin@hologram.com.br',
        name: 'Admin',
        role: 'admin',
        scope: 'system',
        client_id: null,
      },
    }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { MovementsTab } from '@/components/features/reconciliations/review/movements-tab';
import { OMIE_STATUS_EXPLANATION } from '@/components/features/reconciliations/review/situation-badge';
import type { FileEntryItem, OmieLancamentoItem } from '@/lib/api/reconciliations';
import { TEST_POSTING_CONTEXT } from '@/test/posting-context';

function entry(over: Partial<FileEntryItem> = {}): FileEntryItem {
  return {
    id: 'e1',
    transaction_date: '2026-06-10',
    description: 'PIX FORNECEDOR X',
    amount: '-150.50',
    balance: null,
    situation: 'conciliado',
    user_action: null,
    user_note: null,
    omie_lancamento_id: 9001,
    omie_posting: null,
    ...over,
  };
}

function omie(status: string, omieId = 9001): OmieLancamentoItem {
  return {
    omie_id: omieId,
    transaction_date: '2026-06-10',
    description: 'NF 123',
    supplier: 'Fornecedor X LTDA',
    category: 'Serviços',
    amount: '-150.50',
    status,
  };
}

function renderTab() {
  return render(
    <MovementsTab
      sessionId="s1"
      isCard={false}
      canPostToOmie
      postingContext={TEST_POSTING_CONTEXT}
    />,
  );
}

function rowOf(description: string): HTMLElement {
  const row = screen.getByText(description).closest('tr');
  if (row === null) throw new Error(`linha "${description}" não encontrada`);
  return row;
}

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  window.localStorage.clear();
  rows = [entry()];
  lookup = [];
});

describe('MovementsTab — situação do lançamento no Omie (86e3t4vpq)', () => {
  it('"Conciliado": selo neutro com a explicação no nome acessível', () => {
    lookup = [omie('Conciliado')];
    renderTab();

    const selo = within(rowOf('PIX FORNECEDOR X')).getByRole('img', {
      name: /^No Omie: Conciliado\./,
    });
    expect(selo).toHaveTextContent('No Omie: Conciliado');
    expect(selo).toHaveAccessibleName(`No Omie: Conciliado. ${OMIE_STATUS_EXPLANATION}`);
    expect(selo).toHaveClass('bg-muted', 'text-muted-foreground');
    expect(selo).not.toHaveClass('text-warning');
    // Alcance por teclado: o tooltip do Radix abre no foco.
    expect(selo).toHaveAttribute('tabindex', '0');
    expect(selo).not.toHaveAttribute('title');
  });

  it('"Não conciliado": selo em atenção, texto como a Omie devolveu, e o par continua', () => {
    lookup = [omie('Não conciliado')];
    renderTab();

    const linha = rowOf('PIX FORNECEDOR X');
    const selo = within(linha).getByRole('img', { name: /^No Omie: Não conciliado\./ });
    expect(selo).toHaveTextContent('No Omie: Não conciliado');
    expect(selo).toHaveClass('bg-warning-muted', 'text-warning');
    // A situação do cruzamento não muda: a linha segue conciliada.
    expect(within(linha).getByText('Conciliado', { exact: true })).toBeVisible();
  });

  it('valor fora dos dois conhecidos é mostrado como veio, em atenção', () => {
    lookup = [omie('Previsto')];
    renderTab();

    const selo = within(rowOf('PIX FORNECEDOR X')).getByRole('img', {
      name: /^No Omie: Previsto\./,
    });
    expect(selo).toHaveClass('text-warning');
  });

  it('sem situação na resposta, a linha fica como sempre foi (sem selo)', () => {
    lookup = [omie('')];
    renderTab();

    const linha = rowOf('PIX FORNECEDOR X');
    expect(within(linha).getByText('Fornecedor X LTDA')).toBeVisible();
    expect(within(linha).queryByText(/No Omie:/)).toBeNull();
  });

  it('linha sem par não ganha selo, mesmo com o mesmo lançamento na resposta', () => {
    rows = [entry({ situation: 'sem_omie', omie_lancamento_id: null })];
    lookup = [omie('Não conciliado')];
    renderTab();

    expect(screen.queryByText(/No Omie:/)).toBeNull();
  });

  it('linha com data divergente também mostra o selo', () => {
    rows = [entry({ situation: 'conciliado_data_divergente' })];
    lookup = [omie('Não conciliado')];
    renderTab();

    expect(
      within(rowOf('PIX FORNECEDOR X')).getByRole('img', { name: /^No Omie: Não conciliado\./ }),
    ).toBeVisible();
  });

  it('o topo da aba explica uma vez, e a pessoa recolhe e a tela lembra', async () => {
    const user = userEvent.setup();
    const { unmount } = renderTab();

    const bloco = screen.getByRole('region', { name: 'Conciliado aqui e conciliado no Omie' });
    expect(within(bloco).getByText(OMIE_STATUS_EXPLANATION)).toBeVisible();

    await user.click(screen.getByRole('button', { name: 'Ocultar a explicação' }));
    expect(screen.getByRole('button', { name: 'Mostrar a explicação' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    unmount();

    renderTab();
    expect(await screen.findByRole('button', { name: 'Mostrar a explicação' })).toBeVisible();
  });
});
