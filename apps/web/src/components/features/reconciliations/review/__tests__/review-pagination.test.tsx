/**
 * Paginação das 3 abas da revisão (86e2u512z).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * As três abas tinham cada uma a sua paginação escrita à mão — o mesmo bloco
 * copiado em triplicata, sem rótulo nos botões e sem itens-por-página. O que
 * estes testes travam:
 *
 *   - as três usam a `PaginationBar` do design-system (e nenhuma mantém a
 *     versão artesanal, cuja assinatura era o texto "Mostrando x–y de N");
 *   - dá para trocar itens-por-página nas três, e o novo valor chega ao
 *     request — o que só passou a ser verdade depois de o backend aceitar o
 *     alias `pageSize` (as 3 rotas da revisão liam `page_size`, então o valor
 *     escolhido na tela era descartado e o servidor devolvia 20 em silêncio);
 *   - trocar o tamanho volta para a página 1, senão quem está na página 8 com
 *     10 por página e escolhe 100 cai numa página que não existe mais.
 *
 * jsdom não implementa as APIs de ponteiro que o Radix consulta (o seletor de
 * itens-por-página é um `Select` real, porque é o markup real que precisa
 * responder ao teclado e ao axe), então o `beforeAll` as preenche.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

/** Último `params` que cada aba mandou ao seu hook — é a prova do "UI → request". */
let fileEntriesParams: Record<string, unknown> | undefined;
let omieEntriesParams: Record<string, unknown> | undefined;
let anomaliesParams: Record<string, unknown> | undefined;

const paginationOf = (total: number, pageSize: number) => ({
  page: 1,
  pageSize,
  total,
  totalPages: Math.max(1, Math.ceil(total / pageSize)),
});

vi.mock('@/hooks/use-reconciliations', () => ({
  useFileEntries: (_id: string, params: Record<string, unknown>) => {
    fileEntriesParams = params;
    return {
      data: { data: [], pagination: paginationOf(45, params.pageSize as number) },
      isLoading: false,
    };
  },
  useOmieEntries: (_id: string, params: Record<string, unknown>) => {
    omieEntriesParams = params;
    return {
      data: { data: [], pagination: paginationOf(45, params.pageSize as number) },
      isLoading: false,
    };
  },
  useAnomalies: (_id: string, params: Record<string, unknown>) => {
    anomaliesParams = params;
    return {
      data: { data: [], pagination: paginationOf(45, params.pageSize as number) },
      isLoading: false,
    };
  },
  useAllSessionAnomalies: () => ({ data: [], isLoading: false }),
  useOmieLancamentos: () => ({ data: [], isLoading: false }),
  usePatchFileEntry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchOmieEntry: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchAnomaly: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useAnomalyTypes: () => ({ data: [], isLoading: false }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import { AnomaliesTab } from '@/components/features/reconciliations/review/anomalies-tab';
import { MovementsTab } from '@/components/features/reconciliations/review/movements-tab';
import { OmieDivergencesTab } from '@/components/features/reconciliations/review/omie-divergences-tab';
import type { AuthenticatedUser } from '@/lib/contracts';

const SESSION_ID = 's1';

const ABAS = [
  {
    nome: 'Movimentações',
    rotuloBarra: 'Paginação de movimentações',
    render: () => render(<MovementsTab sessionId={SESSION_ID} isCard={false} />),
    params: () => fileEntriesParams,
  },
  {
    nome: 'Divergências Omie',
    rotuloBarra: 'Paginação de lançamentos Omie',
    render: () => render(<OmieDivergencesTab sessionId={SESSION_ID} />),
    params: () => omieEntriesParams,
  },
  {
    nome: 'Anomalias',
    rotuloBarra: 'Paginação de anomalias',
    render: () => render(<AnomaliesTab sessionId={SESSION_ID} />),
    params: () => anomaliesParams,
  },
] as const;

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  fileEntriesParams = undefined;
  omieEntriesParams = undefined;
  anomaliesParams = undefined;
  authState.user = {
    id: 'me',
    email: 'admin@hologram.com.br',
    name: 'Admin',
    role: 'admin',
    scope: 'system',
    client_id: null,
  };
});

describe.each(ABAS)('$nome — paginação do design-system', (aba) => {
  it('usa a PaginationBar, com x–y de N e a página atual', () => {
    aba.render();

    const barra = screen.getByRole('navigation', { name: aba.rotuloBarra });
    expect(within(barra).getByText('1–20 de 45')).toBeVisible();
    expect(within(barra).getByText('Página 1 de 3')).toBeVisible();
    // Os botões passam a ter RÓTULO escrito, não só a seta.
    expect(within(barra).getByRole('button', { name: 'Próxima página' })).toBeVisible();
  });

  it('não sobrou paginação artesanal (o "Mostrando x–y de N" antigo)', () => {
    aba.render();

    expect(screen.queryByText(/^Mostrando /)).toBeNull();
  });

  it('dá para escolher itens por página, e o valor chega ao request', async () => {
    const user = userEvent.setup();
    aba.render();

    await user.click(screen.getByRole('combobox', { name: 'Itens por página' }));
    await user.click(screen.getByRole('option', { name: '50' }));

    await waitFor(() => expect(aba.params()?.pageSize).toBe(50));
    // E volta para a página 1 — senão a pessoa cai numa página inexistente.
    expect(aba.params()?.page).toBe(1);
  });
});
