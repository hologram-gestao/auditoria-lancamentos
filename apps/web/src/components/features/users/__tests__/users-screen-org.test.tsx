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
 *   - o badge de perfil rotula pelo papel, não por "admin ou o resto".
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const listState = {
  data: undefined as { data: User[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
};
/** Último `params` que a tela mandou ao hook — prova o filtro server-side. */
let lastQueryParams: Record<string, unknown> | undefined;
const createMock = vi.fn();

vi.mock('@/hooks/use-users', () => ({
  useUsersList: (params: Record<string, unknown>) => {
    lastQueryParams = params;
    return listState;
  },
  useCreateUser: () => ({ mutateAsync: createMock, isPending: false, reset: vi.fn() }),
  useUpdateUser: () => ({ mutateAsync: vi.fn(), isPending: false, reset: vi.fn() }),
  useActivateUser: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeactivateUser: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const organizationsState = {
  data: undefined as { data: OrganizationItem[] } | undefined,
  isLoading: false,
};
vi.mock('@/hooks/use-organizations', () => ({
  useOrganizationsList: () => organizationsState,
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
import UsersPage from '@/app/(app)/configuracoes/usuarios/page';
import type { OrganizationItem } from '@/lib/api/organizations';
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
  lastQueryParams = undefined;
  createMock.mockReset().mockResolvedValue(staff());
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
