/**
 * A lista de Clientes ciente de ORGANIZAÇÃO (86e36ed1d).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que se prova aqui, sem browser:
 *   - a plataforma vê a coluna "Organização" e o filtro, e o filtro vira
 *     `?organizationId=` na consulta (server-side, não recorte no navegador);
 *   - o admin da organização não vê nem a coluna nem o filtro;
 *   - "Novo Cliente" volta a ser decidido pela MATRIZ (`create_client`): o
 *     gerente vê — é um critério de aceite da task — e a plataforma também,
 *     porque o formulário dela agora tem o seletor de organização. Era essa a
 *     dívida que o helper datado `canCreateWithoutOrganizationPicker` cobria
 *     até aqui;
 *   - o usuário DE tenant não fica nesta tela (o layout o manda para a casa
 *     dele), então também não vê o botão.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const listState = {
  data: undefined as { data: Client[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
};
/** Último `params` que a tela mandou ao hook — prova o filtro server-side. */
let lastQueryParams: Record<string, unknown> | undefined;

// `vi.hoisted`: a fábrica do `vi.mock` roda antes de qualquer `const` do corpo
// deste arquivo — referenciar um daqui dentro dá TDZ ("Cannot access before
// initialization"), a mesma armadilha já paga com o mock do `sonner`.
const { noopMutation } = vi.hoisted(() => ({
  noopMutation: () => ({ mutateAsync: () => Promise.resolve(), isPending: false, reset: () => {} }),
}));
vi.mock('@/hooks/use-clients', () => ({
  useClientsList: (params: Record<string, unknown>) => {
    lastQueryParams = params;
    return listState;
  },
  useCreateClient: noopMutation,
  useTestConnection: noopMutation,
  useUpdateClient: noopMutation,
  useAssignClient: noopMutation,
  useClientManagers: () => ({ data: [], isLoading: false, isError: false, error: null }),
  useAddClientManager: noopMutation,
  useRemoveClientManager: noopMutation,
  useSetFavorite: noopMutation,
  useCloseClient: noopMutation,
}));

vi.mock('@/hooks/use-client-categories', () => ({
  useClientCategories: () => ({ data: [], isLoading: false, isError: false, error: null }),
}));

vi.mock('@/hooks/use-users', () => ({
  useUsersList: () => ({ data: { data: [] }, isLoading: false }),
}));

const organizationsState = {
  data: undefined as { data: OrganizationItem[] } | undefined,
  isLoading: false,
};
vi.mock('@/hooks/use-organizations', () => ({
  useOrganizationsList: () => organizationsState,
}));

const replaceMock = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => '/clientes',
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: toastSuccess, error: toastError } }));

// Imports do SUT DEPOIS dos `vi.mock`.
import ClientesPage from '@/app/(app)/clientes/page';
import type { Client } from '@/lib/api/clients';
import type { OrganizationItem } from '@/lib/api/organizations';
import type { AuthenticatedUser } from '@/lib/contracts';

const HOLOGRAM = { id: '0706eeb5-9718-4d03-bcda-ef615789e6ac', name: 'Hologram' };
const PROSPECTA = { id: '11111111-2222-3333-4444-555555555555', name: 'Prospecta' };

function member(over: Partial<AuthenticatedUser>): AuthenticatedUser {
  return {
    id: 'u',
    name: 'Pessoa',
    email: 'pessoa@hologram.com.br',
    role: 'admin',
    scope: 'system',
    client_id: null,
    organization_id: HOLOGRAM.id,
    organization_name: HOLOGRAM.name,
    ...over,
  };
}

const PLATFORM = member({
  id: 'plat',
  role: 'platform_admin',
  scope: 'platform',
  organization_id: null,
  organization_name: null,
});
const ORG_ADMIN = member({ id: 'adm' });
const ORG_MANAGER = member({ id: 'mgr', role: 'manager' });

function client(over: Partial<Client> = {}): Client {
  return {
    id: 'c1',
    name: 'Cliente Exemplo Ltda',
    active: true,
    organization: HOLOGRAM,
    created_at: '2026-05-01T12:00:00Z',
    updated_at: '2026-07-20T12:00:00Z',
    responsible_manager: null,
    reconciliation_count: 3,
    is_favorite: false,
    category: null,
    manager_count: 0,
    closed_at: null,
    // S9: `origin_status` é obrigatório no contrato — o default aqui é
    // "tem origem", para os casos desta suíte não medirem o selo novo.
    origin_status: 'ativa',
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
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  listState.data = {
    data: [client(), client({ id: 'c2', name: 'Outro Cliente SA', organization: PROSPECTA })],
    pagination: { page: 1, pageSize: 20, total: 2, totalPages: 1 },
  };
  listState.isLoading = false;
  listState.isError = false;
  organizationsState.data = {
    data: [organization(), organization({ id: PROSPECTA.id, name: PROSPECTA.name })],
  };
  organizationsState.isLoading = false;
  lastQueryParams = undefined;
  replaceMock.mockReset();
});

describe('Clientes — dimensão de organização (plataforma)', () => {
  it('mostra a coluna Organização com o dono de cada cliente', () => {
    authState.user = PLATFORM;
    render(<ClientesPage />);

    const table = screen.getByRole('table');
    expect(within(table).getByRole('columnheader', { name: 'Organização' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'Prospecta' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'Hologram' })).toBeInTheDocument();
  });

  it('o filtro vira ?organizationId= na consulta e volta para a página 1', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<ClientesPage />);

    expect(lastQueryParams).toMatchObject({ organizationId: undefined });

    await ui.click(screen.getByRole('combobox', { name: 'Filtrar por organização' }));
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));

    await waitFor(() => expect(lastQueryParams).toMatchObject({ organizationId: PROSPECTA.id }));
    expect(lastQueryParams).toMatchObject({ page: 1 });
  });

  it('o formulário de criação pede a organização de destino', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<ClientesPage />);

    await ui.click(screen.getByRole('button', { name: 'Novo Cliente' }));

    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByRole('combobox', { name: 'Organização do cliente' }),
    ).toBeInTheDocument();
  });
});

describe('Clientes — o staff não ganha a dimensão', () => {
  it('admin: sem coluna, sem filtro, e o formulário sem o seletor', async () => {
    authState.user = ORG_ADMIN;
    const ui = userEvent.setup();
    render(<ClientesPage />);

    const table = screen.getByRole('table');
    expect(
      within(table).queryByRole('columnheader', { name: 'Organização' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('combobox', { name: 'Filtrar por organização' }),
    ).not.toBeInTheDocument();

    await ui.click(screen.getByRole('button', { name: 'Novo Cliente' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).queryByRole('combobox', { name: 'Organização do cliente' }),
    ).not.toBeInTheDocument();
  });
});

describe('Clientes — "Novo Cliente" é decidido pela matriz', () => {
  it.each([
    ['plataforma', () => PLATFORM],
    ['admin da organização', () => ORG_ADMIN],
    ['gerente da organização', () => ORG_MANAGER],
  ])('%s vê o botão (todos têm create_client)', (_label, who) => {
    authState.user = who();
    render(<ClientesPage />);

    expect(screen.getByRole('button', { name: 'Novo Cliente' })).toBeInTheDocument();
  });

  it('usuário DE tenant não fica nesta tela: vai para a casa dele', async () => {
    authState.user = member({
      id: 'op',
      role: 'client_operator',
      scope: 'client',
      client_id: 'c1',
    });
    const { container } = render(<ClientesPage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith('/clientes/c1'));
    // E não pinta a lista global nem por um frame — nem o botão.
    expect(container).toBeEmptyDOMElement();
  });
});
