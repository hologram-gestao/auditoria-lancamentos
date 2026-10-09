/**
 * "Compras do cartão no Omie" no novo cliente e no editar cliente (86e3n70p0).
 *
 * O processo é DECLARADO: o default é o de sempre (na data da compra), a troca
 * vai no payload, e o rótulo é o mesmo nas duas telas.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/clientes',
  useSearchParams: () => new URLSearchParams(''),
}));

const createMock = vi.fn();
const updateMock = vi.fn();

vi.mock('@/hooks/use-clients', () => ({
  useCreateClient: () => ({ mutateAsync: createMock, isPending: false, reset: vi.fn() }),
  useTestConnection: () => ({ mutateAsync: vi.fn(), isPending: false, reset: vi.fn() }),
  useUpdateClient: () => ({ mutateAsync: updateMock, isPending: false, reset: vi.fn() }),
}));

vi.mock('@/hooks/use-client-categories', () => ({
  useClientCategories: () => ({ data: [], isLoading: false }),
}));

vi.mock('@/components/features/organizations/organization-select', () => ({
  useOrganizationOptions: () => ({ organizations: [], isLoading: false, isError: false }),
  organizationOptionLabel: (o: { name: string }) => o.name,
  OrganizationLoadError: () => null,
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// A carteira do cliente (só para admin) tem os próprios testes e hooks.
vi.mock('@/components/features/clients/client-managers-section', () => ({
  ClientManagersSection: () => null,
}));

import { CreateClientModal } from '@/components/features/clients/create-client-modal';
import { EditClientModal } from '@/components/features/clients/edit-client-modal';
import type { Client } from '@/lib/api/clients';
import type { AuthenticatedUser } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ADMIN: AuthenticatedUser = {
  id: 'me',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: 'org-1',
  organization_name: 'Hologram',
};

const CLIENT: Client = {
  id: 'c1',
  name: 'Prospecta Exemplo',
  active: true,
  organization: { id: 'org-1', name: 'Hologram' },
  created_at: '2026-05-01T12:00:00Z',
  updated_at: '2026-09-20T12:00:00Z',
  responsible_manager: null,
  reconciliation_count: 0,
  is_favorite: false,
  manager_count: 0,
  category: null,
  origin_status: 'ativa',
  card_posting_date_mode: 'invoice_due_date',
};

const FIELD = 'Compras do cartão no Omie';

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  createMock.mockReset().mockResolvedValue({ id: 'novo' });
  updateMock.mockReset().mockResolvedValue({ id: 'c1' });
  authState.user = ADMIN;
});

describe('Novo cliente', () => {
  it('nasce na data da compra, o processo de sempre', () => {
    render(<CreateClientModal open onOpenChange={vi.fn()} />);
    expect(screen.getByRole('combobox', { name: FIELD })).toHaveTextContent('Na data da compra');
  });

  it('declarar "no vencimento da fatura" vai no payload', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Nome do cliente'), 'Prospecta');
    await user.click(screen.getByRole('combobox', { name: FIELD }));
    await user.click(screen.getByRole('option', { name: 'No vencimento da fatura' }));
    await user.click(screen.getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.card_posting_date_mode).toBe('invoice_due_date');
  });
});

describe('Editar cliente', () => {
  it('mostra o processo atual do cliente e salva a troca', async () => {
    const user = userEvent.setup();
    render(<EditClientModal open onOpenChange={vi.fn()} client={CLIENT} />);

    const field = screen.getByRole('combobox', { name: FIELD });
    expect(field).toHaveTextContent('No vencimento da fatura');

    await user.click(field);
    await user.click(screen.getByRole('option', { name: 'Na data da compra' }));
    await user.click(screen.getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    const payload = updateMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.card_posting_date_mode).toBe('purchase_date');
  });

  it('não tem violações critical/serious', async () => {
    render(<EditClientModal open onOpenChange={vi.fn()} client={CLIENT} />);
    await assertNoA11yViolations(screen.getByRole('dialog'));
  });
});
