/**
 * A tela de Usuários ciente de ORGANIZAÇÃO (86e36ed1d).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que se prova aqui, sem browser:
 *   - a plataforma vê a coluna "Organização" e o filtro, e o filtro vira
 *     `?organizationId=` na consulta (server-side, não recorte no navegador);
 *   - o admin da organização não vê nem a coluna nem o filtro — toda linha da
 *     lista dele é da mesma organização, e a coluna só repetiria o nome;
 *   - o formulário de criação só mostra o seletor de organização para a
 *     plataforma (o admin cria na própria, pela LINHA dele no servidor);
 *   - o badge de perfil rotula pelo papel, não por "admin ou o resto";
 *   - a aba "Administradores da plataforma" (86e3chrxw) existe SÓ para a
 *     plataforma, vai na URL (`?tab=plataforma`) e é só-leitura por construção.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';

// A aba ativa mora na URL: o mock devolve a querystring que o teste escolher e
// registra o `replace` — o "clique → URL" e o "URL → aba" são provados em
// separado, como na lista de conciliações.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/configuracoes/usuarios',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const listState = {
  data: undefined as { data: User[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
};
/** Último `params` que a tela mandou ao hook — prova o filtro server-side. */
let lastQueryParams: Record<string, unknown> | undefined;
/** O `enabled` da última chamada — prova que a aba da plataforma não paga o request de staff. */
let lastListEnabled: boolean | undefined;
const createMock = vi.fn();
const transferMock = vi.fn();

vi.mock('@/hooks/use-users', () => ({
  useUsersList: (params: Record<string, unknown>, options?: { enabled?: boolean }) => {
    lastQueryParams = params;
    lastListEnabled = options?.enabled;
    return listState;
  },
  useCreateUser: () => ({ mutateAsync: createMock, isPending: false, reset: vi.fn() }),
  useUpdateUser: () => ({ mutateAsync: vi.fn(), isPending: false, reset: vi.fn() }),
  useTransferUser: () => ({ mutateAsync: transferMock, isPending: false, reset: vi.fn() }),
  useActivateUser: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeactivateUser: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const organizationsState = {
  data: undefined as { data: OrganizationItem[] } | undefined,
  isLoading: false,
};
const platformAdminsState = {
  data: undefined as PlatformAdminItem[] | undefined,
  isLoading: false,
  isError: false,
};
vi.mock('@/hooks/use-organizations', () => ({
  useOrganizationsList: () => organizationsState,
  usePlatformAdminsList: () => platformAdminsState,
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

// `vi.hoisted`: a fábrica é avaliada quando o SUT importa `sonner`, antes de
// qualquer `const` do corpo deste arquivo. Sem ele, é TDZ.
const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: toastSuccess, error: toastError } }));

// Imports do SUT DEPOIS dos `vi.mock`.
import UsersPage from '@/app/(app)/configuracoes/usuarios/users-page';
import { ApiError } from '@/lib/api/client';
import type { OrganizationItem, PlatformAdminItem } from '@/lib/api/organizations';
import type { User } from '@/lib/api/users';
import type { AuthenticatedUser } from '@/lib/contracts';

const HOLOGRAM = { id: '0706eeb5-9718-4d03-bcda-ef615789e6ac', name: 'Hologram' };
const PROSPECTA = { id: '11111111-2222-3333-4444-555555555555', name: 'Prospecta' };

const PLATFORM: AuthenticatedUser = {
  id: 'plat',
  name: 'Plataforma',
  email: 'plataforma@hologram.com.br',
  role: 'platform_admin',
  scope: 'platform',
  client_id: null,
  organization_id: null,
  organization_name: null,
};

const ORG_ADMIN: AuthenticatedUser = {
  id: 'adm',
  name: 'Admin',
  email: 'admin@hologram.com.br',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: HOLOGRAM.id,
  organization_name: HOLOGRAM.name,
};

function staff(over: Partial<User> = {}): User {
  return {
    id: 'u1',
    name: 'Bruna R.',
    email: 'bruna@hologram.com.br',
    role: 'manager',
    scope: 'system',
    organization_id: HOLOGRAM.id,
    organization_name: HOLOGRAM.name,
    active: true,
    created_at: '2026-06-01T12:00:00Z',
    updated_at: '2026-06-01T12:00:00Z',
    ...over,
  };
}

function platformAdmin(over: Partial<PlatformAdminItem> = {}): PlatformAdminItem {
  return {
    id: 'pa-1',
    name: 'Pedro H.',
    email: 'pedro@hologramgestao.com',
    active: true,
    created_at: '2026-09-18T12:00:00Z',
    ...over,
  };
}

function organization(over: Partial<OrganizationItem> = {}): OrganizationItem {
  return {
    id: HOLOGRAM.id,
    name: HOLOGRAM.name,
    active: true,
    clients_count: 12,
    users_count: 5,
    created_at: '2026-09-01T12:00:00Z',
    updated_at: '2026-09-10T12:00:00Z',
    ...over,
  };
}

beforeAll(() => {
  // jsdom não implementa as APIs de ponteiro que o Radix (Select) consulta.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  listState.data = {
    data: [
      staff(),
      staff({
        id: 'u2',
        name: 'Carlos P.',
        email: 'carlos@prospecta.com.br',
        role: 'admin',
        organization_id: PROSPECTA.id,
        organization_name: PROSPECTA.name,
      }),
    ],
    pagination: { page: 1, pageSize: 20, total: 2, totalPages: 1 },
  };
  listState.isLoading = false;
  listState.isError = false;
  organizationsState.data = {
    data: [organization(), organization({ id: PROSPECTA.id, name: PROSPECTA.name })],
  };
  organizationsState.isLoading = false;
  platformAdminsState.data = [
    platformAdmin(),
    platformAdmin({ id: 'pa-2', name: 'Laio S.', email: 'laio@hologramgestao.com', active: false }),
  ];
  platformAdminsState.isLoading = false;
  platformAdminsState.isError = false;
  currentSearch = '';
  replaceMock.mockReset();
  lastQueryParams = undefined;
  lastListEnabled = undefined;
  createMock.mockReset().mockResolvedValue(staff());
  transferMock
    .mockReset()
    .mockResolvedValue(staff({ organization_id: PROSPECTA.id, organization_name: PROSPECTA.name }));
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe('Usuários — dimensão de organização (plataforma)', () => {
  it('mostra a coluna Organização com o nome de cada staff', () => {
    authState.user = PLATFORM;
    render(<UsersPage />);

    const table = screen.getByRole('table');
    expect(within(table).getByRole('columnheader', { name: 'Organização' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'Prospecta' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'Hologram' })).toBeInTheDocument();
  });

  it('o filtro vira ?organizationId= na consulta, não um recorte no navegador', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<UsersPage />);

    expect(lastQueryParams).toMatchObject({ organizationId: undefined });

    await ui.click(screen.getByRole('combobox', { name: 'Filtrar por organização' }));
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));

    await waitFor(() => expect(lastQueryParams).toMatchObject({ organizationId: PROSPECTA.id }));
    // E volta para a página 1: filtrar na página 3 traria uma página vazia.
    expect(lastQueryParams).toMatchObject({ page: 1 });
  });

  it('o formulário manda a organização escolhida NO PAYLOAD', async () => {
    // A asserção que importa é esta, e não "o combobox existe": sem a linha que
    // monta `organization_id` no submit, o campo aparece na tela, o usuário
    // escolhe, e o POST sai sem a organização — 400 garantido
    // (`resolve_organization_for_creation`). Era o defeito que o helper datado
    // `canCreateWithoutOrganizationPicker` escondia até esta task.
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<UsersPage />);

    await ui.click(screen.getByRole('button', { name: 'Novo Usuário' }));
    const dialog = await screen.findByRole('dialog');

    await ui.type(within(dialog).getByLabelText('Nome completo'), 'Fulano de Tal');
    await ui.type(within(dialog).getByLabelText('E-mail'), 'fulano@prospecta.com.br');
    await ui.type(within(dialog).getByLabelText('Senha inicial'), 'senha-de-teste');
    await ui.click(within(dialog).getByRole('combobox', { name: 'Organização do usuário' }));
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));
    await ui.click(within(dialog).getByRole('button', { name: 'Criar usuário' }));

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        expect.objectContaining({ organization_id: PROSPECTA.id }),
      ),
    );
  });
});

describe('Usuários — o admin da organização não ganha a dimensão', () => {
  it('sem coluna e sem filtro: toda linha da lista dele é da mesma organização', () => {
    authState.user = ORG_ADMIN;
    render(<UsersPage />);

    const table = screen.getByRole('table');
    expect(
      within(table).queryByRole('columnheader', { name: 'Organização' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('combobox', { name: 'Filtrar por organização' }),
    ).not.toBeInTheDocument();
  });

  it('sem seletor, e o payload sai SEM organização — é a ausência que vale', async () => {
    // Omitir o campo é o que faz o backend usar a organização da LINHA do ator.
    // Mandar uma organização, mesmo a própria repetida, não é neutro: o
    // servidor compara, e divergente é 403.
    authState.user = ORG_ADMIN;
    const ui = userEvent.setup();
    render(<UsersPage />);

    await ui.click(screen.getByRole('button', { name: 'Novo Usuário' }));
    const dialog = await screen.findByRole('dialog');

    expect(
      within(dialog).queryByRole('combobox', { name: 'Organização do usuário' }),
    ).not.toBeInTheDocument();
    // O perfil continua lá: é o que ele escolhe.
    expect(within(dialog).getByRole('combobox', { name: /perfil/i })).toBeInTheDocument();

    await ui.type(within(dialog).getByLabelText('Nome completo'), 'Beltrano');
    await ui.type(within(dialog).getByLabelText('E-mail'), 'beltrano@hologram.com.br');
    await ui.type(within(dialog).getByLabelText('Senha inicial'), 'senha-de-teste');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar usuário' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock.mock.calls[0]?.[0]).not.toHaveProperty('organization_id');
  });
});

describe('UserRoleBadge — rótulo por papel, não "admin ou o resto"', () => {
  it('nomeia cada papel pelo rótulo dele', () => {
    authState.user = PLATFORM;
    render(<UsersPage />);

    const table = screen.getByRole('table');
    expect(within(table).getByText('Gerente')).toBeInTheDocument();
    expect(within(table).getByText('Administrador')).toBeInTheDocument();
  });
});

describe('Transferir de organização (86e3bvbfx) — só a plataforma', () => {
  async function abrirEdicaoDe(ui: ReturnType<typeof userEvent.setup>, nome: string) {
    await ui.click(screen.getByRole('button', { name: `Editar ${nome}` }));
    return await screen.findByRole('dialog', { name: 'Editar Usuário' });
  }

  it('a plataforma vê a ação no editar, com a organização atual dita', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<UsersPage />);

    const dialog = await abrirEdicaoDe(ui, 'Bruna R.');
    expect(within(dialog).getByText('Hologram')).toBeVisible();
    expect(within(dialog).getByRole('button', { name: 'Transferir de organização' })).toBeVisible();
  });

  it('o admin da organização NÃO vê a ação: o servidor negaria com 403', async () => {
    authState.user = ORG_ADMIN;
    const ui = userEvent.setup();
    render(<UsersPage />);

    const dialog = await abrirEdicaoDe(ui, 'Bruna R.');
    expect(
      within(dialog).queryByRole('button', { name: 'Transferir de organização' }),
    ).not.toBeInTheDocument();
  });

  it('abre o diálogo próprio (o de editar fecha antes), exclui a organização atual e manda o destino no payload', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<UsersPage />);

    const edit = await abrirEdicaoDe(ui, 'Bruna R.');
    await ui.click(within(edit).getByRole('button', { name: 'Transferir de organização' }));

    const transfer = await screen.findByRole('dialog', { name: 'Transferir de organização' });
    // Um diálogo só: o de editar saiu de cena antes de este abrir.
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Editar Usuário' })).not.toBeInTheDocument(),
    );
    // A consequência é dita ANTES de confirmar.
    expect(transfer).toHaveTextContent(/carteira em clientes abertos/i);

    await ui.click(within(transfer).getByRole('combobox', { name: 'Organização de destino' }));
    // Bruna é da Hologram: a Hologram NÃO é opção (o backend responderia 409).
    expect(screen.queryByRole('option', { name: 'Hologram' })).not.toBeInTheDocument();
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));
    await ui.click(within(transfer).getByRole('button', { name: 'Transferir' }));

    await waitFor(() =>
      expect(transferMock).toHaveBeenCalledWith({ organization_id: PROSPECTA.id }),
    );
    expect(toastSuccess).toHaveBeenCalledWith(
      expect.stringContaining('agora é da organização Prospecta'),
    );
  });

  it('o 409 do responsável de cliente aberto fica INLINE no campo, dizendo o que fazer', async () => {
    authState.user = PLATFORM;
    transferMock.mockReset().mockRejectedValue(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'x',
        userMessage:
          'Este usuário é o gerente responsável de 2 clientes abertos. Defina outro responsável antes de transferir.',
      }),
    );
    const ui = userEvent.setup();
    render(<UsersPage />);

    const edit = await abrirEdicaoDe(ui, 'Bruna R.');
    await ui.click(within(edit).getByRole('button', { name: 'Transferir de organização' }));
    const transfer = await screen.findByRole('dialog', { name: 'Transferir de organização' });
    await ui.click(within(transfer).getByRole('combobox', { name: 'Organização de destino' }));
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));
    await ui.click(within(transfer).getByRole('button', { name: 'Transferir' }));

    expect(await within(transfer).findByText(/Defina outro responsável/)).toBeVisible();
    // Continua aberto: a pessoa lê o que fazer e vai fazer.
    expect(screen.getByRole('dialog', { name: 'Transferir de organização' })).toBeVisible();
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe('Administradores da plataforma — aba própria, só para a plataforma (86e3chrxw)', () => {
  // O nome da região rolável é DIFERENTE do rótulo da aba de propósito: a
  // busca por papel e nome casa por substring, e "Administradores da
  // plataforma" acertaria a aba e a região ao mesmo tempo.
  function regiao() {
    return screen.getByRole('region', { name: 'Lista de administradores da plataforma' });
  }

  it('a plataforma vê as duas abas, começa no staff e o clique grava a aba na URL', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<UsersPage />);

    expect(screen.getAllByRole('tab')).toHaveLength(2);
    expect(screen.getByRole('tab', { name: 'Staff das organizações' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // A lista da plataforma NÃO está montada na aba de staff: nem o request
    // dela é pago, nem o nome aparece fora da aba.
    expect(screen.getByText('Bruna R.')).toBeVisible();
    expect(screen.queryByText('Pedro H.')).not.toBeInTheDocument();

    await ui.click(screen.getByRole('tab', { name: 'Administradores da plataforma' }));
    expect(replaceMock).toHaveBeenCalledWith('/configuracoes/usuarios?tab=plataforma', {
      scroll: false,
    });
  });

  it('?tab=plataforma: a tabela lista nome, e-mail, status e data — e nenhuma ação', () => {
    authState.user = PLATFORM;
    currentSearch = 'tab=plataforma';
    render(<UsersPage />);

    expect(screen.getByRole('tab', { name: 'Administradores da plataforma' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    const lista = regiao();
    expect(within(lista).getByText('Pedro H.')).toBeVisible();
    expect(within(lista).getByText('pedro@hologramgestao.com')).toBeVisible();
    expect(within(lista).getByText('Laio S.')).toBeVisible();
    // Desativar não tira o escopo: a conta continua alcançando tudo se for
    // reativada, então esconder a linha seria pior do que marcá-la.
    expect(within(lista).getByText('Inativo')).toBeVisible();
    expect(within(lista).getByText('Ativo')).toBeVisible();
    // Os dois foram promovidos no mesmo dia: a data aparece duas vezes.
    expect(within(lista).getAllByText('18 de set de 2026')).toHaveLength(2);
    // SÓ-LEITURA por construção: `PATCH /users/{id}` responde 404 para linha
    // de plataforma, e promover ou despromover é só pelo script. Nem coluna
    // de ações, nem "Novo Usuário" nesta aba — botão aqui seria ação que o
    // servidor nega (§4.9).
    expect(within(lista).queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Novo Usuário' })).not.toBeInTheDocument();
    expect(screen.queryByText('Bruna R.')).not.toBeInTheDocument();
    // A lista de staff não é consultada nesta aba.
    expect(lastListEnabled).toBe(false);
  });

  it('diz que a entrada e a saída são pelo script, não por esta tela', () => {
    authState.user = PLATFORM;
    currentSearch = 'tab=plataforma';
    render(<UsersPage />);

    expect(screen.getByText(/script de promoção/i)).toBeVisible();
  });

  it('voltar para o staff LIMPA o parâmetro: ?tab=staff seria ruído na URL', async () => {
    authState.user = PLATFORM;
    currentSearch = 'tab=plataforma';
    const ui = userEvent.setup();
    render(<UsersPage />);

    await ui.click(screen.getByRole('tab', { name: 'Staff das organizações' }));
    expect(replaceMock).toHaveBeenCalledWith('/configuracoes/usuarios', { scroll: false });
  });

  it('o admin da organização não vê abas, e ?tab=plataforma é ignorado em silêncio', () => {
    // Só a plataforma pode saber quem é plataforma (a rota é
    // `ManagePlatformDep`). O deep link não vira AccessDenied porque a página
    // em si ele pode ver: cai na vista única, a de sempre.
    authState.user = ORG_ADMIN;
    currentSearch = 'tab=plataforma';
    render(<UsersPage />);

    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    expect(screen.queryByText('Pedro H.')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: 'Lista de administradores da plataforma' }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Novo Usuário' })).toBeVisible();
    expect(screen.getByText('Bruna R.')).toBeVisible();
  });

  it('carregando: não finge lista vazia', () => {
    authState.user = PLATFORM;
    currentSearch = 'tab=plataforma';
    platformAdminsState.data = undefined;
    platformAdminsState.isLoading = true;
    render(<UsersPage />);

    expect(within(regiao()).getByText('Carregando...')).toBeVisible();
    expect(within(regiao()).queryByText('Pedro H.')).not.toBeInTheDocument();
  });

  it('erro: diz que falhou, em vez de parecer que não há ninguém', () => {
    authState.user = PLATFORM;
    currentSearch = 'tab=plataforma';
    platformAdminsState.data = undefined;
    platformAdminsState.isError = true;
    render(<UsersPage />);

    expect(
      within(regiao()).getByText('Não foi possível carregar os administradores da plataforma.'),
    ).toBeVisible();
  });

  it('vazio: estado próprio, distinto do erro', () => {
    authState.user = PLATFORM;
    currentSearch = 'tab=plataforma';
    platformAdminsState.data = [];
    render(<UsersPage />);

    expect(
      within(regiao()).getByText('Nenhum administrador da plataforma cadastrado.'),
    ).toBeVisible();
  });
});
