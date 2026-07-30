/**
 * Testes da tela "Usuários" do cliente (FRONT 05.6 / R5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Não é o `web_a11y` — aquele roda só
 * `e2e/a11y-mocked.spec.ts`.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - lista paginada com colunas nome/e-mail/papel/status;
 *   - estados loading, vazio (com CTA real) e erro (com "Tentar novamente");
 *   - gaveta de criação: papel só com `client_manager`/`client_operator`,
 *     senha mínima de 10 caracteres com a mensagem do backend, e o request
 *     **sem** `client_id` no body;
 *   - operador do cliente não vê a tela (nem a ação de criar);
 *   - desativar passa por confirmação `role="alertdialog"`;
 *   - axe-core sem violações `critical`/`serious`.
 *
 * Os componentes Radix REAIS são usados (nada de stub de `ui/select`/`ui/sheet`):
 * é o markup real que precisa passar no axe. jsdom não implementa as APIs de
 * ponteiro que o Radix consulta, então o `beforeAll` abaixo as preenche.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/clientes/c1/usuarios',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const listState = {
  data: undefined as
    | { data: ClientUserResponse[]; pagination: Record<string, number> }
    | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

/** Último `params` que a tela mandou para o hook — prova o "URL → request". */
let lastQueryParams: ListClientUsersParams | undefined;
const createMock = vi.fn();
const updateMock = vi.fn();
const setActiveMock = vi.fn();

vi.mock('@/hooks/use-client-users', () => ({
  useClientUsersList: (_clientId: string, params: ListClientUsersParams) => {
    lastQueryParams = params;
    return listState;
  },
  useCreateClientUser: () => ({ mutateAsync: createMock, isPending: false }),
  useUpdateClientUser: () => ({ mutateAsync: updateMock, isPending: false }),
  useSetClientUserActive: () => ({ mutateAsync: setActiveMock, isPending: false }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import { ClientUsersScreen } from '@/components/features/client-users/client-users-screen';
import type { ListClientUsersParams } from '@/lib/api/client-users';
import type { AuthenticatedUser, ClientUserResponse } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const CLIENT_ID = 'c1';

function user(over: Partial<ClientUserResponse> = {}): ClientUserResponse {
  return {
    id: 'u1',
    name: 'Joana Prado',
    email: 'joana@austral.com.br',
    role: 'client_operator',
    scope: 'client',
    client_id: CLIENT_ID,
    active: true,
    created_at: '2026-07-01T12:00:00Z',
    updated_at: '2026-07-01T12:00:00Z',
    ...over,
  };
}

function actor(over: Partial<AuthenticatedUser> = {}): AuthenticatedUser {
  return {
    id: 'me',
    email: 'gerente@austral.com.br',
    name: 'Gerente Austral',
    role: 'client_manager',
    scope: 'client',
    client_id: CLIENT_ID,
    ...over,
  };
}

function setList(rows: ClientUserResponse[], total = rows.length) {
  listState.data = {
    data: rows,
    pagination: { page: 1, pageSize: 20, total, totalPages: Math.max(1, Math.ceil(total / 20)) },
  };
}

beforeAll(() => {
  // Radix consulta APIs de ponteiro que o jsdom não implementa.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  currentSearch = '';
  authState.user = actor();
  listState.isLoading = false;
  listState.isFetching = false;
  listState.isError = false;
  listState.error = null;
  setList([user()]);
});

describe('ClientUsersScreen — lista', () => {
  it('mostra nome, e-mail, papel e status de cada usuário do tenant', () => {
    setList([
      user({ id: 'u1', name: 'Joana Prado', role: 'client_operator', active: true }),
      user({
        id: 'u2',
        name: 'Rui Sales',
        email: 'rui@austral.com.br',
        role: 'client_manager',
        active: false,
      }),
    ]);
    render(<ClientUsersScreen clientId={CLIENT_ID} />);

    const joana = screen.getByRole('row', { name: /Joana Prado/ });
    expect(within(joana).getByText('joana@austral.com.br')).toBeInTheDocument();
    expect(within(joana).getByText('Operador do cliente')).toBeInTheDocument();
    expect(within(joana).getByText('Ativo')).toBeInTheDocument();

    const rui = screen.getByRole('row', { name: /Rui Sales/ });
    expect(within(rui).getByText('Gerente do cliente')).toBeInTheDocument();
    expect(within(rui).getByText('Inativo')).toBeInTheDocument();
  });

  it('lê a paginação da URL e a repassa ao request', () => {
    currentSearch = 'page=3&pageSize=50';
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    expect(lastQueryParams).toMatchObject({ page: 3, pageSize: 50 });
  });

  it('buscar escreve na URL e volta para a página 1', async () => {
    const ui = userEvent.setup();
    currentSearch = 'page=4';
    render(<ClientUsersScreen clientId={CLIENT_ID} />);

    await ui.type(screen.getByLabelText('Buscar usuários deste cliente'), 'rui');

    await waitFor(() => expect(replaceMock).toHaveBeenCalled());
    const lastUrl = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(lastUrl).toContain('busca=');
    expect(lastUrl).not.toContain('page=4');
  });

  it('empty-state traz um botão real para o primeiro cadastro', async () => {
    const ui = userEvent.setup();
    setList([], 0);
    render(<ClientUsersScreen clientId={CLIENT_ID} />);

    expect(screen.getByText('Nenhum usuário cadastrado neste cliente')).toBeInTheDocument();
    const cta = screen.getAllByRole('button', { name: 'Novo usuário' });
    await ui.click(cta[cta.length - 1] as HTMLElement);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('erro mostra a mensagem amigável do backend e "Tentar novamente"', async () => {
    const ui = userEvent.setup();
    listState.isError = true;
    listState.error = new (class extends Error {
      userMessage = 'Você não tem permissão para acessar este recurso.';
    })();
    render(<ClientUsersScreen clientId={CLIENT_ID} />);

    // Sem `ApiError` real a tela cai no fallback — o que importa é NÃO vazar
    // `error.message` cru e oferecer o retry.
    expect(screen.getByRole('alert')).toBeInTheDocument();
    await ui.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(listState.refetch).toHaveBeenCalled();
  });

  it('mostra skeleton enquanto carrega, sem tabela vazia', () => {
    listState.isLoading = true;
    listState.data = undefined;
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    expect(screen.queryByText('Nenhum usuário cadastrado neste cliente')).not.toBeInTheDocument();
  });
});

describe('ClientUsersScreen — gating por papel', () => {
  it('operador do cliente não vê a tela nem a ação de criar', () => {
    authState.user = actor({ id: 'op', role: 'client_operator' });
    render(<ClientUsersScreen clientId={CLIENT_ID} />);

    expect(screen.queryByRole('button', { name: 'Novo usuário' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Você não tem acesso a esta página');
    expect(screen.getByRole('link', { name: 'Voltar para as conciliações' })).toHaveAttribute(
      'href',
      `/clientes/${CLIENT_ID}`,
    );
  });

  it('gerente do SISTEMA (carteira) também não administra usuários do tenant', () => {
    authState.user = actor({ id: 'mgr', role: 'manager', scope: 'system', client_id: null });
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    expect(screen.queryByRole('button', { name: 'Novo usuário' })).not.toBeInTheDocument();
  });

  it('admin do sistema administra os usuários do tenant', () => {
    authState.user = actor({ id: 'adm', role: 'admin', scope: 'system', client_id: null });
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    expect(screen.getByRole('button', { name: 'Novo usuário' })).toBeInTheDocument();
  });
});

describe('ClientUsersScreen — gaveta de criação', () => {
  async function openDrawer() {
    const ui = userEvent.setup();
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    await ui.click(screen.getByRole('button', { name: 'Novo usuário' }));
    await screen.findByRole('dialog');
    return ui;
  }

  it('oferece apenas os papéis de cliente — nunca admin/gerente do sistema', async () => {
    const ui = await openDrawer();
    await ui.click(screen.getByRole('combobox', { name: /Papel/ }));

    const options = await screen.findAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([
      'Gerente do cliente',
      'Operador do cliente',
    ]);
  });

  it('exige senha de 10 caracteres com a mesma regra do backend', async () => {
    const ui = await openDrawer();
    const dialog = screen.getByRole('dialog');

    await ui.type(within(dialog).getByLabelText('Nome completo'), 'João Austral');
    await ui.type(within(dialog).getByLabelText('E-mail'), 'joao@austral.com.br');
    await ui.type(within(dialog).getByLabelText('Senha inicial'), 'curta123');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar usuário' }));

    expect(
      await within(dialog).findByText('A senha precisa ter pelo menos 10 caracteres.'),
    ).toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it('cria o usuário SEM mandar client_id no body', async () => {
    const ui = await openDrawer();
    const dialog = screen.getByRole('dialog');

    await ui.type(within(dialog).getByLabelText('Nome completo'), 'João Austral');
    await ui.type(within(dialog).getByLabelText('E-mail'), 'joao@austral.com.br');
    await ui.type(within(dialog).getByLabelText('Senha inicial'), 'senha-bem-longa');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar usuário' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(payload).toEqual({
      name: 'João Austral',
      email: 'joao@austral.com.br',
      password: 'senha-bem-longa',
      role: 'client_operator',
    });
    expect(payload).not.toHaveProperty('client_id');
    expect(payload).not.toHaveProperty('scope');
  });

  it('a senha tem alternância mostrar/ocultar e nasce oculta', async () => {
    const ui = await openDrawer();
    const dialog = screen.getByRole('dialog');
    const field = within(dialog).getByLabelText('Senha inicial');

    expect(field).toHaveAttribute('type', 'password');
    await ui.click(within(dialog).getByRole('button', { name: /mostrar senha/i }));
    expect(field).toHaveAttribute('type', 'text');
  });

  it('Cancelar fica à ESQUERDA da ação primária no rodapé da gaveta', async () => {
    await openDrawer();
    const dialog = screen.getByRole('dialog');
    const cancel = within(dialog).getByRole('button', { name: 'Cancelar' });
    const submit = within(dialog).getByRole('button', { name: 'Criar usuário' });
    expect(cancel.compareDocumentPosition(submit) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('ClientUsersScreen — desativar', () => {
  it('pede confirmação em alertdialog antes de desativar', async () => {
    const ui = userEvent.setup();
    setList([user({ id: 'u1', name: 'Joana Prado', active: true })]);
    render(<ClientUsersScreen clientId={CLIENT_ID} />);

    await ui.click(screen.getByRole('button', { name: 'Desativar Joana Prado' }));

    const confirm = await screen.findByRole('alertdialog');
    expect(setActiveMock).not.toHaveBeenCalled();
    await ui.click(within(confirm).getByRole('button', { name: 'Desativar' }));
    await waitFor(() =>
      expect(setActiveMock).toHaveBeenCalledWith({ userId: 'u1', active: false }),
    );
  });

  it('não oferece desativar a si mesmo (o backend devolve 403)', () => {
    authState.user = actor({ id: 'u1' });
    setList([user({ id: 'u1', name: 'Joana Prado' })]);
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    expect(screen.queryByRole('button', { name: 'Desativar Joana Prado' })).not.toBeInTheDocument();
  });
});

describe('ClientUsersScreen — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core na lista', async () => {
    setList([user(), user({ id: 'u2', name: 'Rui Sales', role: 'client_manager', active: false })]);
    const { container } = render(<ClientUsersScreen clientId={CLIENT_ID} />);
    await assertNoA11yViolations(container);
  });

  it('não tem violações critical/serious no estado vazio', async () => {
    setList([], 0);
    const { container } = render(<ClientUsersScreen clientId={CLIENT_ID} />);
    await assertNoA11yViolations(container);
  });

  it('não tem violações critical/serious com a gaveta aberta', async () => {
    const ui = userEvent.setup();
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    await ui.click(screen.getByRole('button', { name: 'Novo usuário' }));
    await screen.findByRole('dialog');
    // A gaveta vai para um portal fora do container do render — o axe precisa
    // olhar o `document.body` inteiro, senão mede o lado errado da página.
    await assertNoA11yViolations(document.body);
  });

  it('não tem violações critical/serious na confirmação de desativar', async () => {
    const ui = userEvent.setup();
    setList([user({ id: 'u1', name: 'Joana Prado', active: true })]);
    render(<ClientUsersScreen clientId={CLIENT_ID} />);
    await ui.click(screen.getByRole('button', { name: 'Desativar Joana Prado' }));
    await screen.findByRole('alertdialog');
    await assertNoA11yViolations(document.body);
  });
});
