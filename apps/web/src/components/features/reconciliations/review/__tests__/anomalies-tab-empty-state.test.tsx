/**
 * Estado vazio da aba Anomalias distingue "não há" de "o filtro não achou"
 * (86e2u513j). Quem filtra por "Críticas" numa conciliação só com moderadas
 * lia "Nenhuma anomalia registrada" e concluía que estava tudo limpo — a
 * frase afirmava sobre a conciliação inteira o que só valia para o recorte.
 * Mesmo padrão da aba Movimentações; harness espelhado do teste de veredito.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const listState = {
  data: undefined as { data: AnomalyItem[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
};

vi.mock('@/hooks/use-reconciliations', () => ({
  useAnomalies: () => listState,
  usePatchAnomaly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAnomalyTypes: () => ({ data: [], isLoading: false }),
  useAllSemOmieEntries: () => ({ data: undefined }),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: null }) => unknown) => selector({ user: null }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Imports do SUT DEPOIS dos `vi.mock`.
import { AnomaliesTab } from '@/components/features/reconciliations/review/anomalies-tab';
import type { AnomalyItem } from '@/lib/api/reconciliations';

function setEmptyList() {
  listState.data = {
    data: [],
    pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 },
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
  listState.isLoading = false;
  setEmptyList();
});

describe('Anomalias — estado vazio', () => {
  it('sem filtro, afirma sobre a conciliação', () => {
    render(<AnomaliesTab sessionId="s1" isCard={false} />);
    expect(screen.getByText('Nenhuma anomalia registrada.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Limpar filtros' })).not.toBeInTheDocument();
  });

  it('com filtro de severidade, cita o filtro e oferece a saída', async () => {
    const ui = userEvent.setup();
    render(<AnomaliesTab sessionId="s1" isCard={false} />);

    await ui.click(screen.getByRole('combobox', { name: 'Severidade' }));
    await ui.click(screen.getByRole('option', { name: 'Críticas' }));

    expect(
      screen.getByText('Nenhuma anomalia encontrada com os filtros selecionados.'),
    ).toBeVisible();
    expect(screen.queryByText('Nenhuma anomalia registrada.')).not.toBeInTheDocument();

    // "Limpar filtros" é a saída: volta para a listagem completa.
    await ui.click(screen.getByRole('button', { name: 'Limpar filtros' }));
    expect(screen.getByText('Nenhuma anomalia registrada.')).toBeVisible();
  });
});
