/**
 * Testes da tela de Organizações — a área da PLATAFORMA (86e36ecwa).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Não é o `web_a11y` — aquele roda só
 * `e2e/a11y-mocked.spec.ts`.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - só a plataforma vê a tela; admin da organização cai no `AccessDenied`
 *     com caminho de volta, e sem o botão de criar;
 *   - lista com as duas contagens, estados loading/vazio/erro;
 *   - criar: 409 vira erro INLINE no campo (não toast redundante);
 *   - suspender: confirmação que mostra a consequência com os números da
 *     própria linha, e manda `active: false`; reativar manda `true`;
 *   - axe-core sem violações `critical`/`serious`.
 *
 * Os componentes Radix REAIS são usados (nada de stub de `ui/dialog`): é o
 * markup real que precisa passar no axe.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const listState = {
  data: undefined as { data: OrganizationItem[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
};

/** Último `params` que a tela mandou ao hook — prova busca e paginação. */
let lastQueryParams: Record<string, unknown> | undefined;
const createMock = vi.fn();
const updateMock = vi.fn();

vi.mock('@/hooks/use-organizations', () => ({
  useOrganizationsList: (params: Record<string, unknown>) => {
    lastQueryParams = params;
    return listState;
  },
  useCreateOrganization: () => ({ mutateAsync: createMock, isPending: false, reset: vi.fn() }),
  useUpdateOrganization: () => ({ mutateAsync: updateMock, isPending: false, reset: vi.fn() }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

// `vi.hoisted`: a fábrica do mock desreferencia os spies na hora em que o SUT
// importa `sonner` — antes de qualquer `const` do corpo deste arquivo. Sem o
// hoisted, é TDZ ("Cannot access before initialization").
const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: toastSuccess, error: toastError } }));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import OrganizationsPage from '@/app/(app)/configuracoes/organizacoes/organizations-page';
import { ApiError } from '@/lib/api/client';
import type { OrganizationItem } from '@/lib/api/organizations';
import type { AuthenticatedUser } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const PLATFORM: AuthenticatedUser = {
  id: 'plat',
  email: 'plataforma@hologram.com.br',
  name: 'Plataforma',
  role: 'platform_admin',
  scope: 'platform',
  client_id: null,
  organization_id: null,
  organization_name: null,
};

const ORG_ADMIN: AuthenticatedUser = {
  id: 'adm',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: '0706eeb5-9718-4d03-bcda-ef615789e6ac',
  organization_name: 'Hologram',
};

function org(over: Partial<OrganizationItem> = {}): OrganizationItem {
  return {
    id: '0706eeb5-9718-4d03-bcda-ef615789e6ac',
    name: 'Hologram',
    active: true,
    clients_count: 12,
    users_count: 5,
    created_at: '2026-09-01T12:00:00Z',
    updated_at: '2026-09-10T12:00:00Z',
    ...over,
  };
}

function setList(rows: OrganizationItem[], total = rows.length) {
  listState.data = {
    data: rows,
    pagination: { page: 1, pageSize: 20, total, totalPages: Math.max(1, Math.ceil(total / 20)) },
  };
  listState.isLoading = false;
  listState.isError = false;
  listState.error = null;
}

beforeAll(() => {
  // jsdom não implementa as APIs de ponteiro que o Radix consulta.
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
    Element.prototype.setPointerCapture = () => undefined;
    Element.prototype.releasePointerCapture = () => undefined;
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => undefined;
  }
});

beforeEach(() => {
  vi.clearAllMocks();
  authState.user = PLATFORM;
  lastQueryParams = undefined;
  setList([org(), org({ id: 'org-2', name: 'Prospecta', clients_count: 0, users_count: 1 })]);
});

describe('OrganizationsPage — gating (a tela de coluna única da matriz)', () => {
  it('admin da organização não vê a tela nem a ação de criar', () => {
    authState.user = ORG_ADMIN;
    render(<OrganizationsPage />);

    expect(screen.queryByRole('button', { name: 'Nova organização' })).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(
      'A administração de organizações é restrita à plataforma.',
    );
    // Caminho de volta é a casa do papel, nunca um segundo beco sem saída.
    expect(screen.getByRole('link', { name: 'Voltar para o início' })).toHaveAttribute(
      'href',
      '/clientes',
    );
    // E nenhum nome de organização vaza para quem não pode ver a lista.
    expect(screen.queryByText('Prospecta')).not.toBeInTheDocument();
  });

  it('plataforma vê a lista com as duas contagens', async () => {
    const { container } = render(<OrganizationsPage />);

    expect(screen.getByRole('heading', { name: 'Organizações', level: 1 })).toBeVisible();
    const linhaHologram = screen.getByRole('row', { name: /Hologram/ });
    expect(within(linhaHologram).getByText('12')).toBeVisible();
    expect(within(linhaHologram).getByText('5')).toBeVisible();
    expect(within(linhaHologram).getByText('Ativa')).toBeVisible();
    await assertNoA11yViolations(container);
  });
});

describe('OrganizationsPage — estados da lista', () => {
  it('carregando, vazio e erro têm cada um a sua mensagem', () => {
    listState.data = undefined;
    listState.isLoading = true;
    const { rerender } = render(<OrganizationsPage />);
    expect(screen.getByText('Carregando organizações...')).toBeVisible();

    setList([]);
    rerender(<OrganizationsPage />);
    expect(screen.getByText(/Nenhuma organização encontrada/)).toBeVisible();

    listState.data = undefined;
    listState.isLoading = false;
    listState.isError = true;
    listState.error = new ApiError(500, {
      code: 'INTERNAL_ERROR',
      message: 'falhou',
      userMessage: 'Não foi possível carregar.',
    });
    rerender(<OrganizationsPage />);
    expect(screen.getByText('Não foi possível carregar.')).toBeVisible();
  });

  it('paginar e depois buscar: a busca volta para a primeira página', async () => {
    const ui = userEvent.setup();
    // Duas páginas, senão "Próxima" nasce desabilitada e o teste passaria sem
    // nunca sair da página 1.
    setList([org()], 40);
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Próxima página' }));
    await waitFor(() => expect(lastQueryParams?.page).toBe(2));

    await ui.type(screen.getByRole('textbox', { name: 'Buscar organizações' }), 'prospe');
    await waitFor(() => expect(lastQueryParams?.search).toBe('prospe'));
    expect(lastQueryParams?.page).toBe(1);
  });

  it('trocar itens por página reseta a página no MESMO passo (sem request descartado)', async () => {
    const ui = userEvent.setup();
    setList([org()], 200);
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Próxima página' }));
    await waitFor(() => expect(lastQueryParams?.page).toBe(2));

    await ui.click(screen.getByRole('combobox', { name: 'Itens por página' }));
    await ui.click(await screen.findByRole('option', { name: '50' }));

    await waitFor(() => expect(lastQueryParams?.pageSize).toBe(50));
    // Se o reset viesse por efeito, este seria 2 no primeiro render.
    expect(lastQueryParams?.page).toBe(1);
  });
});

describe('OrganizationsPage — criar e renomear', () => {
  it('cria pelo nome e fecha o diálogo', async () => {
    const ui = userEvent.setup();
    createMock.mockResolvedValue(org({ id: 'nova', name: 'Prospecta' }));
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Nova organização' }));
    const dialog = await screen.findByRole('dialog');
    await ui.type(within(dialog).getByLabelText('Nome'), 'Prospecta');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar organização' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledWith({ name: 'Prospecta' }));
    expect(toastSuccess).toHaveBeenCalledWith('Organização criada.');
  });

  it('nome repetido (409) vira erro INLINE no campo, sem toast redundante', async () => {
    const ui = userEvent.setup();
    createMock.mockRejectedValue(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'ja existe',
        userMessage: 'Já existe uma organização com este nome.',
      }),
    );
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Nova organização' }));
    const dialog = await screen.findByRole('dialog');
    await ui.type(within(dialog).getByLabelText('Nome'), 'Hologram');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar organização' }));

    expect(
      await within(dialog).findByText('Já existe uma organização com este nome.'),
    ).toBeVisible();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('nome vazio nem chega ao servidor', async () => {
    const ui = userEvent.setup();
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Nova organização' }));
    const dialog = await screen.findByRole('dialog');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar organização' }));

    expect(await within(dialog).findByText('Informe o nome da organização.')).toBeVisible();
    expect(createMock).not.toHaveBeenCalled();
  });

  it('editar abre com o nome atual e salva a renomeação', async () => {
    const ui = userEvent.setup();
    updateMock.mockResolvedValue(org({ name: 'Hologram Gestão' }));
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Editar Hologram' }));
    const dialog = await screen.findByRole('dialog');
    const campo = within(dialog).getByLabelText('Nome');
    expect(campo).toHaveValue('Hologram');
    await ui.clear(campo);
    await ui.type(campo, 'Hologram Gestão');
    await ui.click(within(dialog).getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledWith({ name: 'Hologram Gestão' }));
  });
});

describe('OrganizationsPage — suspender e reativar', () => {
  it('a confirmação mostra a consequência com os números da linha', async () => {
    const ui = userEvent.setup();
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Suspender Hologram' }));
    const dialog = await screen.findByRole('dialog');

    // O número precisa estar ANTES de confirmar: é ele que informa a decisão.
    expect(dialog).toHaveTextContent('5 usuários perdem');
    expect(dialog).toHaveTextContent('12 clientes');
    await assertNoA11yViolations(dialog);
  });

  it('confirmar suspensão manda active=false', async () => {
    const ui = userEvent.setup();
    updateMock.mockResolvedValue(org({ active: false }));
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Suspender Hologram' }));
    const dialog = await screen.findByRole('dialog');
    await ui.click(within(dialog).getByRole('button', { name: 'Suspender' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledWith({ active: false }));
    expect(toastSuccess).toHaveBeenCalledWith('Organização suspensa.');
  });

  it('organização suspensa mostra o selo e oferece reativar (active=true)', async () => {
    const ui = userEvent.setup();
    setList([org({ name: 'Prospecta', active: false, users_count: 1, clients_count: 0 })]);
    updateMock.mockResolvedValue(org({ active: true }));
    render(<OrganizationsPage />);

    expect(screen.getByText('Suspensa')).toBeVisible();
    await ui.click(screen.getByRole('button', { name: 'Reativar Prospecta' }));
    const dialog = await screen.findByRole('dialog');
    await ui.click(within(dialog).getByRole('button', { name: 'Reativar' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledWith({ active: true }));
    expect(toastSuccess).toHaveBeenCalledWith('Organização reativada.');
  });

  it('o singular da consequência não vira "1 usuários"', async () => {
    const ui = userEvent.setup();
    setList([org({ name: 'Prospecta', users_count: 1, clients_count: 1 })]);
    render(<OrganizationsPage />);

    await ui.click(screen.getByRole('button', { name: 'Suspender Prospecta' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('1 usuário perde');
    expect(dialog).toHaveTextContent('1 cliente');
  });
});
