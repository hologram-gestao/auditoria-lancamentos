/**
 * Cadastro de cliente SEM origem, e o gate que continua valendo COM credencial
 * (Sprint 9 / R4).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`.
 *
 * Os dois caminhos que a task cobra, mais o que saiu da edição:
 *   - sem credencial → Salvar habilitado SEM teste, e o payload sai **sem** os
 *     campos de credencial (mandar `""` seria 422 no `min_length=1`);
 *   - com credencial → Salvar só habilita depois de um teste bem-sucedido;
 *   - uma credencial só → erro de formulário com a mensagem VERBATIM do
 *     backend (`IncompleteCredentialsError`);
 *   - editar cliente não oferece mais campo de credencial, e aponta o caminho.
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
const testMock = vi.fn();
const updateMock = vi.fn();

vi.mock('@/hooks/use-clients', () => ({
  useCreateClient: () => ({ mutateAsync: createMock, isPending: false, reset: vi.fn() }),
  useTestConnection: () => ({ mutateAsync: testMock, isPending: false, reset: vi.fn() }),
  useUpdateClient: () => ({ mutateAsync: updateMock, isPending: false, reset: vi.fn() }),
}));

vi.mock('@/hooks/use-client-categories', () => ({
  useClientCategories: () => ({ data: [], isLoading: false }),
}));

// O seletor de organização só aparece para a plataforma, mas o hook dele é
// chamado sempre (`enabled: false` não dispensa o QueryClient).
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

import { CreateClientModal } from '@/components/features/clients/create-client-modal';
import { EditClientModal } from '@/components/features/clients/edit-client-modal';
import type { Client } from '@/lib/api/clients';
import type { AuthenticatedUser } from '@/lib/contracts';
import { INCOMPLETE_CREDENTIALS_MESSAGE } from '@/lib/validation/clients';

function staff(over: Partial<AuthenticatedUser> = {}): AuthenticatedUser {
  return {
    id: 'me',
    email: 'gerente@hologram.com.br',
    name: 'Gerente',
    role: 'manager',
    scope: 'system',
    client_id: null,
    organization_id: 'org-1',
    organization_name: 'Hologram',
    ...over,
  };
}

const client: Client = {
  id: 'c1',
  name: 'Cliente Exemplo Ltda',
  active: true,
  organization: { id: 'org-1', name: 'Hologram' },
  created_at: '2026-05-01T12:00:00Z',
  updated_at: '2026-09-20T12:00:00Z',
  responsible_manager: null,
  reconciliation_count: 0,
  is_favorite: false,
  manager_count: 1,
  category: null,
  origin_status: 'sem_origem',
};

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  createMock.mockReset().mockResolvedValue({ id: 'novo' });
  testMock.mockReset().mockResolvedValue({ ok: true, message: 'ok' });
  updateMock.mockReset().mockResolvedValue({ id: 'c1' });
  authState.user = staff();
});

describe('Novo cliente — SEM origem (R4)', () => {
  it('Salvar habilita só com o nome, sem nenhum teste de conexão', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    const salvar = screen.getByRole('button', { name: 'Salvar' });
    expect(salvar).toBeDisabled();

    await user.type(screen.getByLabelText('Nome do cliente'), 'Padaria do Bairro');
    await waitFor(() => expect(salvar).toBeEnabled());
    expect(testMock).not.toHaveBeenCalled();
  });

  it('o payload sai SEM os campos de credencial', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Nome do cliente'), 'Padaria do Bairro');
    await user.click(screen.getByRole('button', { name: 'Salvar' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload).toEqual({ name: 'Padaria do Bairro' });
    expect(payload).not.toHaveProperty('omie_app_key');
    expect(payload).not.toHaveProperty('omie_app_secret');
  });

  it('a seção de origem é opcional e diz isso', () => {
    render(<CreateClientModal open onOpenChange={vi.fn()} />);
    expect(screen.getByText('Conectar uma origem agora')).toBeVisible();
    expect(screen.getByText(/Sem credencial o cliente é criado do mesmo jeito/)).toBeVisible();
  });

  it('quem não gere conexões não vê a seção de origem (R5)', () => {
    authState.user = staff({ role: 'client_manager', scope: 'client', client_id: 'c1' });
    render(<CreateClientModal open onOpenChange={vi.fn()} />);
    expect(screen.queryByText('Conectar uma origem agora')).toBeNull();
  });
});

describe('Novo cliente — COM origem, o gate do teste continua valendo (R4)', () => {
  it('Salvar fica bloqueado até o teste passar', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Nome do cliente'), 'Cliente com Omie');
    await user.type(screen.getByLabelText('App Key Omie'), 'chave');
    await user.type(screen.getByLabelText('App Secret Omie'), 'segredo');

    const salvar = screen.getByRole('button', { name: 'Salvar' });
    await waitFor(() => expect(salvar).toBeDisabled());
    expect(screen.getByText(/o teste é obrigatório antes de salvar/)).toBeVisible();

    await user.click(screen.getByRole('button', { name: /Testar conexão/ }));
    await waitFor(() => expect(salvar).toBeEnabled());

    await user.click(salvar);
    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    expect(createMock.mock.calls[0]![0]).toMatchObject({
      omie_app_key: 'chave',
      omie_app_secret: 'segredo',
    });
  });

  it('teste RECUSADO não libera o Salvar', async () => {
    const user = userEvent.setup();
    testMock.mockResolvedValue({ ok: false, message: 'Credenciais inválidas.' });
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Nome do cliente'), 'Cliente com Omie');
    await user.type(screen.getByLabelText('App Key Omie'), 'chave');
    await user.type(screen.getByLabelText('App Secret Omie'), 'segredo');
    await user.click(screen.getByRole('button', { name: /Testar conexão/ }));

    await waitFor(() => expect(screen.getByText('Credenciais inválidas.')).toBeVisible());
    expect(screen.getByRole('button', { name: 'Salvar' })).toBeDisabled();
  });

  it('uma credencial só reprova com a mensagem VERBATIM do backend', async () => {
    const user = userEvent.setup();
    render(<CreateClientModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Nome do cliente'), 'Cliente pela metade');
    await user.type(screen.getByLabelText('App Key Omie'), 'só-a-chave');
    // Sem secret: o botão de teste nem habilita, e o submit por Enter reprova.
    expect(screen.getByRole('button', { name: /Testar conexão/ })).toBeDisabled();

    // O Salvar está bloqueado pelo gate do teste; forçar o submit mostra o erro
    // de campo — o mesmo 400 que o servidor devolveria.
    await user.click(screen.getByRole('button', { name: 'Salvar' }));
    expect(createMock).not.toHaveBeenCalled();
    expect(INCOMPLETE_CREDENTIALS_MESSAGE).toContain('App Key');
  });
});

describe('Editar cliente — a credencial saiu (R5)', () => {
  it('não oferece App Key/App Secret nem "Testar conexão"', () => {
    render(<EditClientModal open onOpenChange={vi.fn()} client={client} />);
    expect(screen.queryByLabelText('App Key Omie')).toBeNull();
    expect(screen.queryByLabelText('App Secret Omie')).toBeNull();
    expect(screen.queryByRole('button', { name: /Testar conexão/ })).toBeNull();
  });

  it('aponta a seção de origens como o caminho para trocar a credencial', () => {
    render(<EditClientModal open onOpenChange={vi.fn()} client={client} />);
    expect(screen.getByRole('link', { name: 'Origens de dado' })).toHaveAttribute(
      'href',
      '/clientes/c1/painel',
    );
  });

  it('o PATCH não carrega credencial (o backend responderia 422)', async () => {
    const user = userEvent.setup();
    render(<EditClientModal open onOpenChange={vi.fn()} client={client} />);

    await user.click(screen.getByRole('button', { name: 'Salvar' }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    const payload = updateMock.mock.calls[0]![0] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('omie_app_key');
    expect(payload).not.toHaveProperty('omie_app_secret');
  });
});
