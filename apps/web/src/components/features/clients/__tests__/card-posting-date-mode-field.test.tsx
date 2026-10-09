/**
 * "Compras do cartão no Omie" no novo cliente e no editar cliente (86e3n70p0).
 *
 * O processo é DECLARADO: o default é o de sempre (na data da compra), a troca
 * vai no payload, e o rótulo é o mesmo nas duas telas.
 *
 * E só existe para quem tem Omie (86e3n70p6): no novo cliente, com o switch
 * "Conectar com o Omie" ligado (desligar volta ao padrão); no editar, para cliente
 * com conexão Omie em qualquer estado. Só-arquivo e sem origem não veem o campo.
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

type ConnectionStub = Pick<ClientConnection, 'provider_type' | 'status' | 'capabilities'>;
const connectionsState = { data: undefined as ConnectionStub[] | undefined };
vi.mock('@/hooks/use-client-connections', () => ({
  useClientConnections: () => ({ data: connectionsState.data, isLoading: false }),
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
import type { AuthenticatedUser, ClientConnection } from '@/lib/contracts';
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
const OMIE_SWITCH = 'Conectar com o Omie';

const OMIE_COM_ERRO: ConnectionStub = {
  provider_type: 'omie',
  status: 'erro',
  capabilities: ['verificar_credencial', 'listar_contas', 'listar_lancamentos', 'escrever'],
};
const ARQUIVO: ConnectionStub = {
  provider_type: 'arquivo',
  status: 'ativa',
  capabilities: ['listar_lancamentos'],
};

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
  connectionsState.data = [OMIE_COM_ERRO];
});

describe('Novo cliente', () => {
  it('sem o Omie ligado o campo não existe', () => {
    render(<CreateClientModal open onOpenChange={vi.fn()} />);
    expect(screen.getByRole('switch', { name: OMIE_SWITCH })).not.toBeChecked();
    expect(screen.queryByRole('combobox', { name: FIELD })).not.toBeInTheDocument();
  });

  it('ligar o Omie mostra o campo, nascendo na data da compra', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);
    await user.click(screen.getByRole('switch', { name: OMIE_SWITCH }));
    expect(screen.getByRole('combobox', { name: FIELD })).toHaveTextContent('Na data da compra');
  });

  it('declarar "no vencimento da fatura" vai no payload', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Nome do cliente'), 'Prospecta');
    await user.click(screen.getByRole('switch', { name: OMIE_SWITCH }));
    await user.click(screen.getByRole('combobox', { name: FIELD }));
    await user.click(screen.getByRole('option', { name: 'No vencimento da fatura' }));
    await user.click(screen.getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.card_posting_date_mode).toBe('invoice_due_date');
  });

  it('desligar o Omie volta o campo ao padrão e nada escolhido sai no POST', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Nome do cliente'), 'Prospecta');
    const omie = screen.getByRole('switch', { name: OMIE_SWITCH });
    await user.click(omie);
    await user.click(screen.getByRole('combobox', { name: FIELD }));
    await user.click(screen.getByRole('option', { name: 'No vencimento da fatura' }));
    await user.click(omie);
    expect(screen.queryByRole('combobox', { name: FIELD })).not.toBeInTheDocument();

    // Religado, o campo volta no padrão: a escolha anterior não ficou guardada.
    await user.click(omie);
    expect(screen.getByRole('combobox', { name: FIELD })).toHaveTextContent('Na data da compra');
    await user.click(omie);

    await user.click(screen.getByRole('button', { name: 'Salvar' }));
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0]![0] as Record<string, unknown>;
    // Omitido É `purchase_date` no servidor (o payload só leva o campo quando difere).
    expect(payload).not.toHaveProperty('card_posting_date_mode');
  });
});

describe('Editar cliente', () => {
  it('cliente com Omie (mesmo com erro) mostra o processo atual e salva a troca', async () => {
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

  it.each([
    ['só arquivo', [ARQUIVO]],
    ['sem origem', []],
    ['conexões ainda carregando', undefined],
  ])('cliente %s não vê o campo, e o valor salvo segue intacto', async (_, connections) => {
    connectionsState.data = connections;
    const user = userEvent.setup();
    render(<EditClientModal open onOpenChange={vi.fn()} client={CLIENT} />);

    expect(screen.queryByRole('combobox', { name: FIELD })).not.toBeInTheDocument();
    // A descrição do modal não promete um campo que não está lá.
    expect(screen.getByRole('dialog')).not.toHaveTextContent('compras do cartão');
    await user.click(screen.getByRole('button', { name: 'Salvar' }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    const payload = updateMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload.card_posting_date_mode).toBe('invoice_due_date');
  });

  it('não tem violações critical/serious', async () => {
    render(<EditClientModal open onOpenChange={vi.fn()} client={CLIENT} />);
    await assertNoA11yViolations(screen.getByRole('dialog'));
  });
});
