/**
 * O diálogo de categoria ciente de ORGANIZAÇÃO (86e36ed1d).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * Este é o TERCEIRO formulário de criação da task, e o único que não tinha
 * teste nenhum. A fábrica única de schema (`organizationTargetField`) existe
 * justamente para os três não divergirem — o que só vale se os três forem
 * medidos. O que se prova aqui:
 *
 *   - a plataforma escolhe a organização de destino, e a escolha vai NO
 *     PAYLOAD (sem isso o POST dela é 400, e o campo na tela seria enfeite);
 *   - na EDIÇÃO o campo não aparece: categoria não troca de organização, e o
 *     `ClientCategoryUpdate` do backend nem aceita o campo — mandá-lo seria
 *     pedir algo que o contrato não oferece;
 *   - o admin da organização não vê o seletor, e o payload dele sai SEM
 *     organização: é a ausência que faz o backend usar a organização da LINHA.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const createMock = vi.fn();
const updateMock = vi.fn();
vi.mock('@/hooks/use-client-categories', () => ({
  useCreateClientCategory: () => ({ mutateAsync: createMock, isPending: false, reset: vi.fn() }),
  useUpdateClientCategory: () => ({ mutateAsync: updateMock, isPending: false, reset: vi.fn() }),
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

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: toastSuccess, error: toastError } }));

// Imports do SUT DEPOIS dos `vi.mock`.
import { ClientCategoryDialog } from '@/components/features/client-categories/client-category-dialog';
import type { ClientCategoryItem } from '@/lib/api/client-categories';
import type { OrganizationItem } from '@/lib/api/organizations';
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

const CATEGORY: ClientCategoryItem = {
  id: 'cat-1',
  name: 'Fintech',
  tone: 'info',
  clients_count: 3,
  organization_id: HOLOGRAM.id,
  organization_name: HOLOGRAM.name,
};

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
  organizationsState.data = {
    data: [organization(), organization({ id: PROSPECTA.id, name: PROSPECTA.name })],
  };
  organizationsState.isLoading = false;
  createMock.mockReset().mockResolvedValue(CATEGORY);
  updateMock.mockReset().mockResolvedValue(CATEGORY);
  toastSuccess.mockReset();
  toastError.mockReset();
});

const noop = () => undefined;

describe('ClientCategoryDialog — organização de destino (plataforma)', () => {
  it('criar: a organização escolhida vai no payload', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<ClientCategoryDialog open onOpenChange={noop} category={null} />);

    const dialog = screen.getByRole('dialog');
    await ui.type(within(dialog).getByLabelText('Nome'), 'Varejo');
    await ui.click(within(dialog).getByRole('combobox', { name: 'Organização da categoria' }));
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));
    await ui.click(within(dialog).getByRole('button', { name: 'Criar categoria' }));

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Varejo', organization_id: PROSPECTA.id }),
      ),
    );
  });

  it('editar: o campo não existe, e o PATCH não leva organização', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<ClientCategoryDialog open onOpenChange={noop} category={CATEGORY} />);

    const dialog = screen.getByRole('dialog');
    expect(
      within(dialog).queryByRole('combobox', { name: 'Organização da categoria' }),
    ).not.toBeInTheDocument();

    await ui.clear(within(dialog).getByLabelText('Nome'));
    await ui.type(within(dialog).getByLabelText('Nome'), 'Fintech BR');
    await ui.click(within(dialog).getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    // `ClientCategoryUpdate` não tem o campo: mandá-lo seria pedir por algo que
    // o contrato não oferece, e a categoria não troca de dono.
    expect(updateMock.mock.calls[0]?.[0]).not.toHaveProperty('organization_id');
  });
});

describe('ClientCategoryDialog — o admin da organização cria na própria', () => {
  it('sem seletor, e o payload sai SEM organização', async () => {
    authState.user = ORG_ADMIN;
    const ui = userEvent.setup();
    render(<ClientCategoryDialog open onOpenChange={noop} category={null} />);

    const dialog = screen.getByRole('dialog');
    expect(
      within(dialog).queryByRole('combobox', { name: 'Organização da categoria' }),
    ).not.toBeInTheDocument();

    await ui.type(within(dialog).getByLabelText('Nome'), 'Saúde');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar categoria' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    // A ausência é o que faz o backend usar a organização da LINHA do ator.
    expect(createMock.mock.calls[0]?.[0]).not.toHaveProperty('organization_id');
  });
});
