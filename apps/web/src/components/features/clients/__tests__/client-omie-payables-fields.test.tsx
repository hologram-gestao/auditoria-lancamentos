/**
 * Configuração do Contas a Pagar no cliente (86e3n70q0, bloco C): "Exigir rateio
 * por departamento nos títulos" e "Fornecedor padrão no Omie", com o MESMO gate do
 * campo do cartão (conexão Omie no editar; "Conectar com o Omie" no criar).
 *
 * O fornecedor padrão é escolhido pela busca por CNPJ e guardado como CÓDIGO;
 * "Remover" manda `null`; sem mexer, o campo nem vai no PATCH (tri-estado). Na
 * criação só o rateio: a busca pergunta ao Omie de um cliente que ainda não existe.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
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

const findMock = vi.fn();
const byCodeMock = vi.fn();
vi.mock('@/hooks/use-omie-postings', () => ({
  useFindOmieFornecedor: () => ({ mutateAsync: findMock, isPending: false }),
  useOmieFornecedorByCode: (_clientId: string, codigo: number | null) => byCodeMock(codigo),
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
vi.mock('@/components/features/clients/client-managers-section', () => ({
  ClientManagersSection: () => null,
}));

import { CreateClientModal } from '@/components/features/clients/create-client-modal';
import { EditClientModal } from '@/components/features/clients/edit-client-modal';
import { ApiError } from '@/lib/api/client';
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

function client(over: Partial<Client> = {}): Client {
  return {
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
    card_posting_date_mode: 'purchase_date',
    omie_department_required: false,
    omie_default_supplier_code: null,
    ...over,
  };
}

const OMIE: ConnectionStub = {
  provider_type: 'omie',
  status: 'ativa',
  capabilities: ['verificar_credencial', 'listar_contas', 'listar_lancamentos', 'escrever'],
};
const ARQUIVO: ConnectionStub = {
  provider_type: 'arquivo',
  status: 'ativa',
  capabilities: ['listar_lancamentos'],
};
const RATEIO = 'Exigir rateio por departamento nos títulos';

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  createMock.mockReset().mockResolvedValue({ id: 'novo' });
  updateMock.mockReset().mockResolvedValue({ id: 'c1' });
  findMock.mockReset();
  byCodeMock.mockReset().mockReturnValue({
    data: { codigo: 700000001, razao_social: 'PAPELARIA DEMO LTDA', nome_fantasia: null },
    isLoading: false,
  });
  authState.user = ADMIN;
  connectionsState.data = [OMIE];
});

async function salvarEdicao(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Salvar' }));
  await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
  return updateMock.mock.calls[0]![0] as Record<string, unknown>;
}

describe('Novo cliente', () => {
  it('o rateio aparece com o Omie ligado e vai no POST; o fornecedor fica para o editar', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);
    expect(screen.queryByRole('switch', { name: RATEIO })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText('Nome do cliente'), 'Prospecta');
    await user.click(screen.getByRole('switch', { name: 'Conectar com o Omie' }));
    expect(screen.getByText(/fornecedor padrão no Omie é escolhido depois/)).toBeInTheDocument();
    await user.click(screen.getByRole('switch', { name: RATEIO }));
    await user.click(screen.getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock.mock.calls[0]![0]).toMatchObject({ omie_department_required: true });
  });

  it('desligar o Omie desliga o rateio e nada sai no POST', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);
    await user.type(screen.getByLabelText('Nome do cliente'), 'Prospecta');
    const omie = screen.getByRole('switch', { name: 'Conectar com o Omie' });
    await user.click(omie);
    await user.click(screen.getByRole('switch', { name: RATEIO }));
    await user.click(omie);
    await user.click(screen.getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock.mock.calls[0]![0]).not.toHaveProperty('omie_department_required');
  });
});

describe('Editar cliente', () => {
  it('o rateio mostra o valor salvo e a troca vai no PATCH; o fornecedor intocado não vai', async () => {
    const user = userEvent.setup();
    render(
      <EditClientModal
        open
        onOpenChange={vi.fn()}
        client={client({ omie_department_required: true })}
      />,
    );
    const rateio = screen.getByRole('switch', { name: RATEIO });
    expect(rateio).toBeChecked();
    await user.click(rateio);

    const payload = await salvarEdicao(user);
    expect(payload.omie_department_required).toBe(false);
    expect(payload).not.toHaveProperty('omie_default_supplier_code');
  });

  it('fornecedor padrão gravado aparece pelo NOME, e "Remover" manda null', async () => {
    const user = userEvent.setup();
    render(
      <EditClientModal
        open
        onOpenChange={vi.fn()}
        client={client({ omie_default_supplier_code: 700000001 })}
      />,
    );
    expect(byCodeMock).toHaveBeenCalledWith(700000001);
    expect(screen.getByText('PAPELARIA DEMO LTDA')).toBeInTheDocument();
    expect(screen.queryByText(/700000001/)).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Remover' }));
    expect(screen.getByText('Nenhum fornecedor padrão.')).toBeInTheDocument();
    const payload = await salvarEdicao(user);
    expect(payload).toHaveProperty('omie_default_supplier_code', null);
  });

  it('escolher pela busca por CNPJ guarda o código do cadastro achado', async () => {
    const user = userEvent.setup();
    findMock.mockResolvedValue({
      codigo: 700000002,
      razao_social: 'POSTO DEMO COMBUSTIVEIS LTDA',
      nome_fantasia: 'Posto Demo',
    });
    render(<EditClientModal open onOpenChange={vi.fn()} client={client()} />);

    await user.click(screen.getByRole('button', { name: 'Escolher fornecedor padrão' }));
    await user.type(screen.getByLabelText('CNPJ do fornecedor padrão'), '98765432000198');
    await user.click(screen.getByRole('button', { name: 'Buscar no Omie' }));

    expect(findMock).toHaveBeenCalledWith({ cnpj: '98765432000198' });
    expect(await screen.findByText('POSTO DEMO COMBUSTIVEIS LTDA')).toBeInTheDocument();
    const payload = await salvarEdicao(user);
    expect(payload.omie_default_supplier_code).toBe(700000002);
  });

  it('CNPJ sem cadastro: o padrão tem de existir no Omie, e nada muda', async () => {
    const user = userEvent.setup();
    findMock.mockRejectedValue(
      new ApiError(404, { code: 'FORNECEDOR_NAO_ENCONTRADO', message: 'x', userMessage: 'x' }),
    );
    render(<EditClientModal open onOpenChange={vi.fn()} client={client()} />);

    await user.click(screen.getByRole('button', { name: 'Escolher fornecedor padrão' }));
    await user.type(screen.getByLabelText('CNPJ do fornecedor padrão'), '11222333000181');
    await user.click(screen.getByRole('button', { name: 'Buscar no Omie' }));

    expect(
      await screen.findByText(/Cadastre o fornecedor no Omie e busque de novo/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Razão social')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Cancelar a troca' }));
    const payload = await salvarEdicao(user);
    expect(payload).not.toHaveProperty('omie_default_supplier_code');
  });

  it('cliente só-arquivo não vê os campos e o PATCH não os leva', async () => {
    connectionsState.data = [ARQUIVO];
    const user = userEvent.setup();
    render(<EditClientModal open onOpenChange={vi.fn()} client={client()} />);
    expect(screen.queryByRole('switch', { name: RATEIO })).toBeNull();
    expect(screen.queryByText('Fornecedor padrão no Omie')).toBeNull();
    const payload = await salvarEdicao(user);
    expect(payload).not.toHaveProperty('omie_department_required');
  });

  it('não tem violações critical/serious, com a busca aberta', async () => {
    const user = userEvent.setup();
    render(
      <EditClientModal
        open
        onOpenChange={vi.fn()}
        client={client({ omie_default_supplier_code: 700000001 })}
      />,
    );
    const dialog = screen.getByRole('dialog');
    await assertNoA11yViolations(dialog);
    await user.click(within(dialog).getByRole('button', { name: 'Trocar' }));
    expect(screen.getByLabelText('CNPJ do fornecedor padrão')).toBeInTheDocument();
    await assertNoA11yViolations(dialog);
  });
});
