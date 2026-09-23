/**
 * Testes da tela "Plano de Contas" do cliente (FRONT 10.5 / R3 · R4).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Não é o `web_a11y` — aquele roda só
 * `e2e/a11y-mocked.spec.ts`, onde ficam os cenários que precisam de CSS
 * computado e de browser real.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - o bloco de cobertura exibe as CINCO contagens vindas do servidor
 *     (50 · 50 · 37 · 13 · 6, o cenário da fixture real);
 *   - lista paginada com `pageSize` lido da URL, filtro por situação e por
 *     hierarquia, e busca por CÓDIGO — e **não existe** busca por nome na UI;
 *   - `client_operator` LÊ mas **não** vê a ação de sincronizar (oculta, não
 *     desabilitada); `client_manager` vê;
 *   - estado vazio de "nunca sincronizou" com a ação de sincronizar; falha da
 *     última tentativa mostra a data da última BEM-SUCEDIDA;
 *   - cliente encerrado: ação indisponível **com o motivo**;
 *   - loading, erro com "Tentar novamente", e axe-core sem `critical`/`serious`.
 *
 * Os componentes Radix REAIS são usados (nada de stub de `ui/select`): é o
 * markup real que precisa passar no axe. jsdom não implementa as APIs de
 * ponteiro que o Radix consulta, então o `beforeAll` abaixo as preenche.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/clientes/c1/plano-de-contas',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const listState = {
  data: undefined as
    | { data: ChartOfAccountEntry[]; pagination: Record<string, number> }
    | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

const coverageState = {
  data: undefined as ChartOfAccountsCoverage | undefined,
  isLoading: false,
  isError: false,
};

const syncState = { mutateAsync: vi.fn(), isPending: false };

/** Último `params` que a tela mandou para o hook — prova o "URL → request". */
let lastQueryParams: ListChartOfAccountsParams | undefined;

vi.mock('@/hooks/use-client-chart-of-accounts', () => ({
  useChartOfAccountsList: (_clientId: string, params: ListChartOfAccountsParams) => {
    lastQueryParams = params;
    return listState;
  },
  useChartOfAccountsCoverage: () => coverageState,
  useSyncChartOfAccounts: () => syncState,
}));

const clientDetailState = {
  data: undefined as { closed_at: string | null; origin_status: string } | undefined,
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => clientDetailState,
}));

const authState = { user: null as AuthenticatedUser | null };

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import { ChartOfAccountsScreen } from '@/components/features/chart-of-accounts/chart-of-accounts-screen';
import type { ListChartOfAccountsParams } from '@/lib/api/client-chart-of-accounts';
import type {
  AuthenticatedUser,
  ChartOfAccountEntry,
  ChartOfAccountsCoverage,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const TENANT = '11111111-1111-4111-8111-111111111111';

const clientManager: AuthenticatedUser = {
  id: 'cm',
  email: 'gerente@cliente.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: TENANT,
  organization_id: '0706eeb5-9718-4d03-bcda-ef615789e6ac',
  organization_name: 'Hologram',
};
const clientOperator: AuthenticatedUser = { ...clientManager, id: 'co', role: 'client_operator' };

/** Uma categoria COM destino — o caso que o de-para aproveita pronto. */
function entry(overrides: Partial<ChartOfAccountEntry> = {}): ChartOfAccountEntry {
  return {
    categoryCode: '1.01.01',
    name: 'BPO Controller - RB',
    parentCode: '1.01',
    dreCode: '1.01.01',
    dreName: 'Receita Bruta de Vendas',
    dreLevel: 3,
    dreSign: '+',
    contaContabilCode: '31101',
    totalizadora: false,
    transferencia: false,
    naoExibir: false,
    status: 'ativa',
    syncedAt: '2026-09-23T09:00:00Z',
    ...overrides,
  };
}

/** O cenário da fixture real: 50 · 50 · 37 · 13 · 6. */
function coverage(overrides: Partial<ChartOfAccountsCoverage> = {}): ChartOfAccountsCoverage {
  return {
    total: 50,
    ativas: 50,
    comDestino: 37,
    semDestino: 13,
    comContaContabil: 6,
    syncedAt: '2026-09-23T09:00:00Z',
    syncFailedAt: null,
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
  currentSearch = '';
  replaceMock.mockClear();
  lastQueryParams = undefined;
  authState.user = clientManager;
  clientDetailState.data = { closed_at: null, origin_status: 'ativa' };
  syncState.mutateAsync = vi.fn().mockResolvedValue(coverage());
  syncState.isPending = false;
  listState.data = {
    // A 2ª linha é o caso "sem destino declarado" da amostra real: uma
    // categoria de TRANSFERÊNCIA, que por definição contábil não tem conta de
    // demonstrativo própria.
    data: [
      entry(),
      entry({
        categoryCode: '0.01',
        name: 'Transferência entre contas',
        parentCode: null,
        dreCode: null,
        dreName: null,
        dreLevel: null,
        dreSign: null,
        contaContabilCode: null,
        transferencia: true,
      }),
    ],
    pagination: { page: 1, pageSize: 20, total: 2, totalPages: 1 },
  };
  listState.isLoading = false;
  listState.isFetching = false;
  listState.isError = false;
  coverageState.data = coverage();
  coverageState.isLoading = false;
  coverageState.isError = false;
});

describe('ChartOfAccountsScreen — bloco de cobertura (R3)', () => {
  it('exibe as CINCO contagens vindas do servidor', () => {
    render(<ChartOfAccountsScreen clientId="c1" />);

    const expected: Array<[string, string]> = [
      ['Categorias', '50'],
      ['Ativas', '50'],
      ['Com destino', '37'],
      ['Sem destino declarado', '13'],
      ['Com conta contábil', '6'],
    ];
    for (const [label, value] of expected) {
      // `selector: 'dt'` não é preciosismo: "Sem destino declarado" é TAMBÉM o
      // texto da célula da tabela, e sem o recorte o locator casa os dois.
      const term = screen.getByText(label, { selector: 'dt' });
      // O valor é o `<dd>` irmão do `<dt>` — prova o PAR, não só a presença do
      // número em algum canto da tela.
      expect(term.parentElement).toHaveTextContent(value);
    }
  });

  it('não soma nada no navegador: a cobertura não vem da página', () => {
    // A página traz 1 linha; a cobertura continua dizendo 50. Se alguém
    // trocar o servidor pelo `rows.length`, este teste cai.
    listState.data = {
      data: [entry()],
      pagination: { page: 1, pageSize: 20, total: 2, totalPages: 1 },
    };
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(screen.getByText('Categorias', { selector: 'dt' }).parentElement).toHaveTextContent(
      '50',
    );
  });

  it('carregando a cobertura mostra o skeleton proporcional', () => {
    coverageState.isLoading = true;
    coverageState.data = undefined;
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(screen.getByLabelText('Carregando a cobertura do plano de contas')).toBeInTheDocument();
  });
});

describe('ChartOfAccountsScreen — lista', () => {
  it('mostra código, nome, destino, conta contábil e situação', () => {
    render(<ChartOfAccountsScreen clientId="c1" />);
    const rows = screen.getAllByRole('row');
    // 1 header + 2 categorias.
    expect(rows).toHaveLength(3);
    const first = within(rows[1]!);
    expect(first.getByText('1.01.01', { selector: 'td' })).toBeVisible();
    expect(first.getByText('BPO Controller - RB')).toBeVisible();
    expect(first.getByText('Receita Bruta de Vendas')).toBeVisible();
    expect(first.getByText('31101')).toBeVisible();
    expect(first.getByText('Ativa')).toBeVisible();
  });

  it('destino ausente é "sem destino declarado", nunca inferido', () => {
    render(<ChartOfAccountsScreen clientId="c1" />);
    const rows = screen.getAllByRole('row');
    expect(within(rows[2]!).getByText('Sem destino declarado')).toBeVisible();
  });

  it('nome não resolvido não vira o código repetido', () => {
    listState.data = {
      data: [entry({ name: null })],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(screen.getByText('Nome não disponível')).toBeVisible();
  });

  it('a tabela é o scroller vertical da área, e a barra de paginação fica fora', () => {
    render(<ChartOfAccountsScreen clientId="c1" />);
    const region = screen.getByRole('region', {
      name: 'Categorias do plano de contas (rolável)',
    });
    expect(region).toHaveClass('overflow-auto', 'min-h-0');
    expect(region).not.toContainElement(
      screen.getByRole('navigation', { name: 'Paginação de categorias' }),
    );
  });

  it('lê page e pageSize da URL e os manda SEMPRE para o servidor', () => {
    currentSearch = 'page=2&pageSize=50';
    listState.data = {
      data: [entry()],
      pagination: { page: 2, pageSize: 50, total: 60, totalPages: 2 },
    };
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(lastQueryParams).toMatchObject({ page: 2, pageSize: 50 });
    const footer = screen.getByRole('navigation', { name: 'Paginação de categorias' });
    expect(within(footer).getByText('51–60 de 60')).toBeVisible();
  });
});

describe('ChartOfAccountsScreen — filtros e busca (R3)', () => {
  it('a busca é por CÓDIGO: não existe campo de busca por nome', () => {
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(screen.getByLabelText('Buscar por código')).toBeVisible();
    expect(screen.queryByLabelText(/buscar por nome/i)).toBeNull();
  });

  it('o código digitado vira `code` na URL e volta para a página 1', async () => {
    const user = userEvent.setup();
    currentSearch = 'page=3';
    render(<ChartOfAccountsScreen clientId="c1" />);

    await user.type(screen.getByLabelText('Buscar por código'), '1.01');
    await waitFor(() => expect(replaceMock).toHaveBeenCalled());
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('code=1.01');
    expect(url).not.toContain('page=3');
  });

  it('o filtro de situação e o de hierarquia chegam ao servidor', () => {
    currentSearch = 'status=inativa&parentCode=1.01';
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(lastQueryParams).toMatchObject({ status: 'inativa', parentCode: '1.01' });
    expect(screen.getByLabelText('Filhas do código')).toHaveValue('1.01');
  });

  it('situação fora do vocabulário do servidor degrada para "sem filtro"', () => {
    // URL editada à mão não pode virar 422 na carga inicial.
    currentSearch = 'status=qualquer-coisa';
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(lastQueryParams?.status).toBeNull();
  });

  it('"Limpar filtros" derruba os três de uma vez', async () => {
    const user = userEvent.setup();
    currentSearch = 'status=ativa&code=1.01&parentCode=1';
    render(<ChartOfAccountsScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: 'Limpar filtros' }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).not.toContain('status=');
    expect(url).not.toContain('code=');
    expect(url).not.toContain('parentCode=');
  });
});

describe('ChartOfAccountsScreen — sincronizar (R4, §4.9)', () => {
  it('gerente do cliente vê a ação e ela força a sincronização', async () => {
    const user = userEvent.setup();
    render(<ChartOfAccountsScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Sincronizar agora' }));
    expect(syncState.mutateAsync).toHaveBeenCalledTimes(1);
  });

  it('operador do cliente LÊ a lista e NÃO vê a ação (oculta, não desabilitada)', () => {
    authState.user = clientOperator;
    render(<ChartOfAccountsScreen clientId="c1" />);

    // Lê: a tabela e a cobertura continuam lá.
    expect(screen.getByText('BPO Controller - RB')).toBeVisible();
    expect(screen.getByText('Com destino', { selector: 'dt' }).parentElement).toHaveTextContent(
      '37',
    );
    // E não há botão nenhum de sincronizar — nem desabilitado.
    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
  });

  it('em andamento fica desabilitado com spinner (bloqueia duplo-clique)', () => {
    syncState.isPending = true;
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(screen.getByRole('button', { name: 'Sincronizando…' })).toBeDisabled();
  });

  it('avisa por toast ao concluir', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    render(<ChartOfAccountsScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Sincronizar agora' }));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith('Plano de contas sincronizado.'),
    );
  });

  it('reabilita e avisa em erro', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    syncState.mutateAsync = vi.fn().mockRejectedValue(new Error('boom'));
    render(<ChartOfAccountsScreen clientId="c1" />);
    const button = screen.getByRole('button', { name: 'Sincronizar agora' });
    await user.click(button);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(button).toBeEnabled();
  });

  it('cliente encerrado: ação indisponível COM o motivo', () => {
    clientDetailState.data = { closed_at: '2026-09-01T12:00:00Z', origin_status: 'ativa' };
    render(<ChartOfAccountsScreen clientId="c1" />);

    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
    expect(screen.getByText(/Cliente encerrado: a sincronização está indisponível/)).toBeVisible();
  });

  it('sem origem conectada: explica o estado e não oferece a ação', () => {
    clientDetailState.data = { closed_at: null, origin_status: 'sem_origem' };
    render(<ChartOfAccountsScreen clientId="c1" />);

    expect(screen.getByText('Este cliente não tem origem conectada')).toBeVisible();
    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
  });
});

describe('ChartOfAccountsScreen — estados (R3)', () => {
  it('nunca sincronizou: estado vazio com a ação de sincronizar', () => {
    coverageState.data = coverage({
      total: 0,
      ativas: 0,
      comDestino: 0,
      semDestino: 0,
      comContaContabil: 0,
      syncedAt: null,
    });
    listState.data = {
      data: [],
      pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 },
    };
    render(<ChartOfAccountsScreen clientId="c1" />);

    expect(screen.getByText('Este cliente ainda não sincronizou o plano de contas')).toBeVisible();
    // Duas ocorrências: o botão do cabeçalho e o do estado vazio.
    expect(screen.getAllByRole('button', { name: 'Sincronizar agora' })).toHaveLength(2);
  });

  it('operador no estado vazio não ganha a ação, e o texto muda', () => {
    authState.user = clientOperator;
    coverageState.data = coverage({
      total: 0,
      ativas: 0,
      comDestino: 0,
      semDestino: 0,
      comContaContabil: 0,
      syncedAt: null,
    });
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ChartOfAccountsScreen clientId="c1" />);

    expect(screen.getByText(/Quando alguém da equipe sincronizar/)).toBeVisible();
    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
  });

  it('última tentativa falhou: aviso com a data da última BEM-SUCEDIDA', () => {
    coverageState.data = coverage({
      syncedAt: '2026-09-20T12:30:00Z',
      syncFailedAt: '2026-09-23T09:00:00Z',
    });
    render(<ChartOfAccountsScreen clientId="c1" />);

    expect(screen.getByText('A última tentativa de sincronização falhou')).toBeVisible();
    // A data da última boa é o que diz se o dado abaixo é de ontem ou de meses
    // atrás. O horário depende do fuso do runner — o dia não.
    expect(screen.getByText(/última sincronização bem-sucedida, de 20\/09\/2026/)).toBeVisible();
  });

  it('filtro sem resultado oferece limpar, não "nunca sincronizou"', () => {
    currentSearch = 'code=9.99';
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ChartOfAccountsScreen clientId="c1" />);

    expect(screen.getByText('Nenhuma categoria encontrada para este recorte.')).toBeVisible();
    expect(screen.queryByText('Este cliente ainda não sincronizou o plano de contas')).toBeNull();
  });

  it('carregando mostra o skeleton da tabela', () => {
    listState.isLoading = true;
    listState.data = undefined;
    const { container } = render(<ChartOfAccountsScreen clientId="c1" />);
    // As linhas do skeleton são `aria-hidden` de propósito (não são conteúdo),
    // então `getAllByRole('row')` só enxerga o cabeçalho — a contagem tem de
    // ser sobre o DOM.
    expect(container.querySelectorAll('tr[aria-hidden="true"]')).toHaveLength(5);
    expect(screen.getAllByRole('row')).toHaveLength(1);
  });

  it('erro oferece "Tentar novamente"', () => {
    listState.isError = true;
    listState.data = undefined;
    render(<ChartOfAccountsScreen clientId="c1" />);
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });
});

describe('ChartOfAccountsScreen — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<ChartOfAccountsScreen clientId="c1" />);
    await assertNoA11yViolations(container);
  });
});
