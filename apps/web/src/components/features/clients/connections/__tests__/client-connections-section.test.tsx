/**
 * Testes da seção "Origens de dado" do cliente (Sprint 9 / R4 · R5 · R7).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Layout e contraste são do `web_a11y`, em browser
 * real — aqui se mede comportamento, estado e gating.
 *
 * Critérios cobertos:
 *   - os TRÊS estados de origem com copy própria, e `erro` dizendo "origem com
 *     erro" e nunca "sem origem" (o defeito que o R7 nomeia);
 *   - gating por papel: gerente da organização VÊ as quatro ações, operador do
 *     cliente vê o estado e a lista e NENHUMA ação;
 *   - cliente encerrado é só-leitura (o servidor negaria com 409);
 *   - listar, testar novamente (com o desfecho certo por `status` de volta) e
 *     remover por `AlertDialog`;
 *   - estados loading / vazio / erro;
 *   - axe-core sem violações `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

// A querystring é regravável por teste: `?conectar=<tipo>` manda a gaveta abrir
// já no tipo que outra tela sabe que falta (86e3fqnc9).
let searchParams = '';
const routerReplace = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  usePathname: () => '/clientes/c1',
  useSearchParams: () => new URLSearchParams(searchParams),
}));

const listState = {
  data: undefined as ClientConnection[] | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

const testMock = vi.fn();
const deleteMock = vi.fn();

vi.mock('@/hooks/use-client-connections', () => ({
  useClientConnections: () => listState,
  useTestStoredConnection: () => ({ mutateAsync: testMock, isPending: false }),
  useDeleteConnection: () => ({ mutateAsync: deleteMock, isPending: false }),
  useCreateConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('@/hooks/use-clients', () => ({
  useTestConnection: () => ({ mutateAsync: vi.fn(), reset: vi.fn(), isPending: false }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Imports do SUT DEPOIS dos `vi.mock`.
import { ClientConnectionsSection } from '@/components/features/clients/connections/client-connections-section';
import type { AuthenticatedUser, ClientConnection } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const CLIENT_ID = 'c1';

function connection(over: Partial<ClientConnection> = {}): ClientConnection {
  return {
    id: 'conn-1',
    provider_type: 'omie',
    label: 'Omie',
    status: 'ativa',
    last_checked_at: '2026-09-22T12:00:00Z',
    accounts_synced_at: '2026-09-22T12:00:00Z',
    capabilities: ['verificar_credencial', 'listar_contas', 'listar_lancamentos', 'escrever'],
    ...over,
  };
}

function actor(over: Partial<AuthenticatedUser> = {}): AuthenticatedUser {
  return {
    id: 'me',
    email: 'gerente@hologram.com.br',
    name: 'Gerente da Organização',
    role: 'manager',
    scope: 'system',
    client_id: null,
    organization_id: 'org-1',
    organization_name: 'Hologram',
    ...over,
  };
}

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date('2026-09-22T14:00:00Z'));
});

beforeEach(() => {
  searchParams = '';
  routerReplace.mockReset();
  listState.data = [connection()];
  listState.isLoading = false;
  listState.isFetching = false;
  listState.isError = false;
  listState.error = null;
  testMock.mockReset();
  deleteMock.mockReset();
  authState.user = actor();
});

/** O bloco de estado da origem, pelo `data-origin-status` que ele carrega. */
function originBlock(): HTMLElement {
  const block = document.querySelector('[data-origin-status]');
  if (block === null) throw new Error('bloco de estado de origem não renderizado');
  return block as HTMLElement;
}

describe('Origens — os três estados (R7)', () => {
  it('sem origem: convida a conectar', () => {
    listState.data = [];
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    expect(within(originBlock()).getByText('Sem origem conectada')).toBeVisible();
    expect(screen.getAllByRole('button', { name: /Conectar origem/ }).length).toBeGreaterThan(0);
  });

  it('origem ativa: nenhum chamado para ação corretiva', () => {
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);
    expect(within(originBlock()).getByText('Origem ativa')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Reconectar' })).toBeNull();
  });

  it('origem em erro: diz "Origem com erro" e oferece Reconectar — NUNCA "sem origem"', () => {
    listState.data = [connection({ status: 'erro' })];
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="erro" isClosed={false} />);

    expect(within(originBlock()).getByText('Origem com erro')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Reconectar' })).toBeVisible();
    // O defeito que o PRD nomeia: mandar criar uma conexão que já existe.
    expect(screen.queryByText('Sem origem conectada')).toBeNull();
  });
});

describe('Origens — gating por papel (R5)', () => {
  it('gerente da ORGANIZAÇÃO vê as quatro ações', () => {
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);
    expect(screen.getByRole('button', { name: /^Conectar origem$/ })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Testar Omie novamente' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Editar Omie' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Remover Omie' })).toBeVisible();
  });

  it('operador do cliente vê o ESTADO e a lista, e nenhuma ação', () => {
    authState.user = actor({
      role: 'client_operator',
      scope: 'client',
      client_id: CLIENT_ID,
      organization_id: 'org-1',
    });
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);

    // Ele precisa SABER por que a conciliação dele não roda.
    expect(within(originBlock()).getByText('Origem ativa')).toBeVisible();
    expect(screen.getAllByText('Omie').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Conectar origem/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Testar/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Editar/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Remover/ })).toBeNull();
    // A coluna "Ações" não existe: anunciar uma coluna sempre vazia é ruído.
    expect(screen.queryByRole('columnheader', { name: 'Ações' })).toBeNull();
  });

  it('gerente do CLIENTE não gere conexões (célula ❌ da matriz)', () => {
    authState.user = actor({ role: 'client_manager', scope: 'client', client_id: CLIENT_ID });
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);
    expect(screen.queryByRole('button', { name: /Conectar origem/ })).toBeNull();
  });

  it('cliente ENCERRADO é só-leitura mesmo para quem tem a permissão', () => {
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed />);
    expect(screen.queryByRole('button', { name: /Conectar origem/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Remover/ })).toBeNull();
  });
});

describe('Origens — lista e ações', () => {
  it('mostra rótulo, tipo, situação e última verificação', () => {
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);
    const rows = screen.getAllByRole('row');
    const first = within(rows[1]!);
    // Rótulo e tipo coincidem quando se aceita o padrão ("Omie"): as duas
    // células existem mesmo assim, porque a partir da 2ª origem elas divergem.
    expect(first.getAllByText('Omie')).toHaveLength(2);
    expect(first.getByText('Ativa')).toBeVisible();
    expect(first.getByText(/^Verificada/)).toBeVisible();
  });

  it('"Testar novamente" com credencial ACEITA avisa sucesso', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    testMock.mockResolvedValue(connection({ status: 'ativa' }));
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);

    await user.click(screen.getByRole('button', { name: 'Testar Omie novamente' }));
    await waitFor(() => expect(testMock).toHaveBeenCalledWith('conn-1'));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it('"Testar novamente" com credencial RECUSADA não é sucesso', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    // 200 com a conexão marcada como `erro` — a credencial não foi apagada.
    testMock.mockResolvedValue(connection({ status: 'erro' }));
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);

    await user.click(screen.getByRole('button', { name: 'Testar Omie novamente' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
  });

  it('remover passa por AlertDialog e diz a consequência antes de confirmar', async () => {
    const user = userEvent.setup();
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);

    await user.click(screen.getByRole('button', { name: 'Remover Omie' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('Remover origem')).toBeVisible();
    // Única ativa → o aviso de que o cliente fica sem origem precisa aparecer.
    expect(within(dialog).getByText(/sem origem conectada/)).toBeVisible();
    expect(deleteMock).not.toHaveBeenCalled();
  });
});

describe('Origens — conexão por ARQUIVO (S14 / R1), decidida pela capacidade', () => {
  it('não oferece "Testar novamente" (o servidor responderia 409) e diz "Sem credencial"', () => {
    listState.data = [
      connection(),
      connection({
        id: 'conn-arq',
        provider_type: 'arquivo',
        label: 'Arquivo',
        capabilities: ['listar_lancamentos'],
        accounts_synced_at: null,
      }),
    ];
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);

    // O Omie continua com o teste; o arquivo não.
    expect(screen.getByRole('button', { name: 'Testar Omie novamente' })).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Testar Arquivo novamente' }),
    ).not.toBeInTheDocument();
    // Editar e remover continuam.
    expect(screen.getByRole('button', { name: 'Editar Arquivo' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Remover Arquivo' })).toBeVisible();
    expect(screen.getByText('Sem credencial')).toBeVisible();
    // "Nunca sincronizado" é sobre CONTAS — a origem por arquivo não as lista.
    const rows = screen.getAllByRole('row');
    expect(rows[2]).not.toHaveTextContent('Nunca sincronizado');
  });

  it('Omie em ERRO continua com "Sincronizado há X": o tipo declara, o status não apaga o histórico', () => {
    listState.data = [
      connection({ status: 'erro', accounts_synced_at: null }),
      connection({ id: 'conn-2', label: 'Omie filial', status: 'inativa' }),
    ];
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="erro" isClosed={false} />);
    const rows = screen.getAllByRole('row');
    expect(rows[1]).toHaveTextContent('Nunca sincronizado');
    expect(rows[2]).toHaveTextContent(/Sincronizad/);
  });
});

describe('Origens — estados de carga', () => {
  it('loading mostra skeleton em vez de "nenhuma origem"', () => {
    listState.isLoading = true;
    listState.data = undefined;
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    expect(screen.queryByText('Nenhuma origem conectada')).toBeNull();
  });

  it('erro na lista oferece "Tentar novamente"', () => {
    listState.isError = true;
    listState.data = undefined;
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });

  it('vazio sem permissão explica sem oferecer ação', () => {
    listState.data = [];
    authState.user = actor({ role: 'client_operator', scope: 'client', client_id: CLIENT_ID });
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    expect(screen.getByText('Nenhuma origem conectada')).toBeVisible();
    expect(screen.queryByRole('button', { name: /Conectar/ })).toBeNull();
  });
});

describe('Origens — `?conectar=<tipo>` abre a gaveta no tipo pedido (86e3fqnc9)', () => {
  it('abre a gaveta com o tipo já escolhido e limpa o parâmetro da URL', async () => {
    searchParams = 'conectar=arquivo';
    listState.data = [];
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByRole('combobox', { name: /Tipo/ })).toHaveTextContent('Arquivo');
    // O parâmetro sai da URL: senão o refresh e o voltar reabririam a gaveta.
    expect(routerReplace).toHaveBeenCalledWith('/clientes/c1', { scroll: false });
  });

  it('sem o parâmetro a gaveta fica fechada', () => {
    listState.data = [];
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('quem não pode gerir conexões não recebe gaveta nenhuma, e a URL é limpa igual', () => {
    searchParams = 'conectar=arquivo';
    listState.data = [];
    authState.user = actor({ role: 'client_operator', scope: 'client', client_id: CLIENT_ID });
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(routerReplace).toHaveBeenCalledWith('/clientes/c1', { scroll: false });
  });

  it('tipo desconhecido na URL cai no padrão, nunca num valor que o schema recusa', async () => {
    searchParams = 'conectar=inventado';
    listState.data = [];
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByRole('combobox', { name: /Tipo/ })).toHaveTextContent('Omie');
  });
});

describe('Origens — a gaveta não oferece tipo que o servidor recusaria (86e3g9u3w)', () => {
  it('cliente com Omie: "Arquivo" some do seletor (seria 409 ORIGEM_JA_CONECTADA)', async () => {
    searchParams = 'conectar=arquivo';
    listState.data = [connection()];
    const user = userEvent.setup();
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="ativa" isClosed={false} />);
    const drawer = await screen.findByRole('dialog');
    const combo = within(drawer).getByRole('combobox', { name: /Tipo/ });
    // O `?conectar=arquivo` pedia um tipo que não é oferecível aqui: cai no
    // primeiro disponível em vez de semear um valor fora da lista.
    expect(combo).toHaveTextContent('Omie');
    await user.click(combo);
    const opcoes = within(await screen.findByRole('listbox'))
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(opcoes).toEqual(['Omie']);
  });

  it('cliente sem origem: os dois tipos continuam oferecidos', async () => {
    listState.data = [];
    const user = userEvent.setup();
    render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="sem_origem" isClosed={false} />,
    );
    await user.click(screen.getAllByRole('button', { name: /Conectar/ })[0]!);
    const drawer = await screen.findByRole('dialog');
    await user.click(within(drawer).getByRole('combobox', { name: /Tipo/ }));
    const opcoes = within(await screen.findByRole('listbox'))
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(opcoes).toEqual(['Omie', 'Arquivo (planilha ou extrato)']);
  });

  it('a trava do servidor não olha status: Omie EM ERRO esconde o Arquivo igual', async () => {
    listState.data = [connection({ status: 'erro' })];
    const user = userEvent.setup();
    render(<ClientConnectionsSection clientId={CLIENT_ID} originStatus="erro" isClosed={false} />);
    await user.click(screen.getAllByRole('button', { name: /Conectar/ })[0]!);
    const drawer = await screen.findByRole('dialog');
    await user.click(within(drawer).getByRole('combobox', { name: /Tipo/ }));
    const opcoes = within(await screen.findByRole('listbox'))
      .getAllByRole('option')
      .map((o) => o.textContent);
    expect(opcoes).toEqual(['Omie']);
  });
});

describe('Origens — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(
      <ClientConnectionsSection clientId={CLIENT_ID} originStatus="erro" isClosed={false} />,
    );
    await assertNoA11yViolations(container);
  });
});
