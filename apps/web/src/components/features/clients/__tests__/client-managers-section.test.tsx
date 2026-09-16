/**
 * Seção "Gerentes com acesso" (86e390m4c): a carteira compartilhada na tela.
 *
 * O que se prova aqui, sem browser:
 *   - o responsável tem selo e NÃO tem "Remover" (o servidor negaria — §4.9);
 *     o colaborador tem "Tornar responsável" e "Remover";
 *   - remover pede confirmação NOMEANDO quem deixa de ver o cliente e só então
 *     chama a remoção (o caso da Bruna: nada some em silêncio);
 *   - tornar responsável avisa que ninguém perde o acesso;
 *   - "Adicionar" só oferece gerente ativo que ainda não tem acesso;
 *   - erro do servidor (409 com `userMessage`) vira toast e a confirmação
 *     continua aberta — a mensagem é mostrada, não engolida.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const managersQuery = {
  data: undefined as ClientManager[] | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
};
const addMutation = { mutateAsync: vi.fn(), isPending: false };
const removeMutation = { mutateAsync: vi.fn(), isPending: false };
const assignMutation = { mutateAsync: vi.fn(), isPending: false };
vi.mock('@/hooks/use-clients', () => ({
  useClientManagers: () => managersQuery,
  useAddClientManager: () => addMutation,
  useRemoveClientManager: () => removeMutation,
  useAssignClient: () => assignMutation,
}));

const usersQuery = { data: undefined as { data: User[] } | undefined, isLoading: false };
vi.mock('@/hooks/use-users', () => ({ useUsersList: () => usersQuery }));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import { ClientManagersSection } from '@/components/features/clients/client-managers-section';
import { ApiError } from '@/lib/api/client';
import type { Client, ClientManager } from '@/lib/api/clients';
import type { User } from '@/lib/api/users';
import type { AuthenticatedUser } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ADMIN: AuthenticatedUser = {
  id: 'admin-1',
  name: 'Admin',
  email: 'admin@hologram.com.br',
  role: 'admin',
  scope: 'system',
  client_id: null,
};

const client: Client = {
  id: 'c1',
  name: 'Cliente Exemplo Ltda',
  active: true,
  created_at: '2026-05-01T12:00:00Z',
  updated_at: '2026-07-20T12:00:00Z',
  responsible_manager: { id: 'u1', name: 'Bruna R.', email: 'bruna@hologram.com.br' },
  reconciliation_count: 3,
  is_favorite: false,
  category: null,
  manager_count: 2,
};

function user(id: string, name: string, over: Partial<User> = {}): User {
  return {
    id,
    name,
    email: `${id}@hologram.com.br`,
    role: 'manager',
    active: true,
    created_at: '2026-06-01T12:00:00Z',
    updated_at: '2026-06-01T12:00:00Z',
    ...over,
  };
}

const RESPONSIBLE: ClientManager = {
  id: 'u1',
  name: 'Bruna R.',
  email: 'bruna@hologram.com.br',
  active: true,
  is_responsible: true,
  assigned_at: '2026-07-01T12:00:00Z',
};
const COLLABORATOR: ClientManager = {
  id: 'u2',
  name: 'Murilo C.',
  email: 'murilo@hologram.com.br',
  active: true,
  is_responsible: false,
  assigned_at: '2026-07-02T12:00:00Z',
};

beforeAll(() => {
  // Radix (Select) consulta APIs de ponteiro que o jsdom não implementa —
  // mesmo contorno do teste de usuários do cliente.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  managersQuery.data = [RESPONSIBLE, COLLABORATOR];
  managersQuery.isLoading = false;
  managersQuery.isError = false;
  usersQuery.data = {
    data: [
      user('u1', 'Bruna R.'),
      user('u2', 'Murilo C.'),
      user('u3', 'Gerente Disponível'),
      user('u4', 'Gerente Inativo', { active: false }),
      user('u5', 'Outro Admin', { role: 'admin' }),
    ],
  };
  usersQuery.isLoading = false;
  authState.user = ADMIN;
  addMutation.mutateAsync.mockReset().mockResolvedValue([]);
  removeMutation.mutateAsync.mockReset().mockResolvedValue([]);
  assignMutation.mutateAsync.mockReset().mockResolvedValue(client);
  toastSuccess.mockReset();
  toastError.mockReset();
});

describe('ClientManagersSection', () => {
  it('lista o responsável com selo e SEM "Remover"; o colaborador tem as duas ações', async () => {
    const { baseElement } = render(<ClientManagersSection client={client} />);

    const region = screen.getByRole('region', { name: 'Gerentes com acesso ao cliente' });
    const items = within(region).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent('Bruna R.');
    expect(within(items[0]!).getByText('Responsável')).toBeInTheDocument();
    expect(
      within(items[0]!).queryByRole('button', { name: 'Remover acesso de Bruna R.' }),
    ).toBeNull();
    expect(within(items[0]!).queryByRole('button', { name: 'Tornar responsável' })).toBeNull();

    expect(items[1]).toHaveTextContent('Murilo C.');
    expect(
      within(items[1]!).getByRole('button', { name: 'Remover acesso de Murilo C.' }),
    ).toBeInTheDocument();
    expect(within(items[1]!).getByRole('button', { name: 'Tornar responsável' })).toBeEnabled();
    expect(screen.getByText('2 pessoas')).toBeInTheDocument();

    await assertNoA11yViolations(baseElement);
  });

  it('remover: a confirmação nomeia quem deixa de ver o cliente e só então remove', async () => {
    const { baseElement } = render(<ClientManagersSection client={client} />);

    fireEvent.click(screen.getByRole('button', { name: 'Remover acesso de Murilo C.' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Remover acesso' });
    expect(dialog).toHaveTextContent('Murilo C. deixa de ver o cliente Cliente Exemplo Ltda');
    // Nada foi chamado só por abrir a confirmação.
    expect(removeMutation.mutateAsync).not.toHaveBeenCalled();
    await assertNoA11yViolations(baseElement);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remover acesso' }));
    await waitFor(() => expect(removeMutation.mutateAsync).toHaveBeenCalledWith('u2'));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith('Murilo C. deixou de ver Cliente Exemplo Ltda.'),
    );
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  });

  it('tornar responsável: avisa que ninguém perde o acesso e chama o assign', async () => {
    const { baseElement } = render(<ClientManagersSection client={client} />);

    fireEvent.click(screen.getByRole('button', { name: 'Tornar responsável' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Tornar responsável' });
    expect(dialog).toHaveTextContent(
      'Murilo C. passa a responder pelo cliente Cliente Exemplo Ltda',
    );
    expect(dialog).toHaveTextContent('Ninguém perde o acesso');
    expect(dialog).toHaveTextContent('Bruna R. continua na carteira como colaborador');
    await assertNoA11yViolations(baseElement);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmar' }));
    await waitFor(() => expect(assignMutation.mutateAsync).toHaveBeenCalledWith({ user_id: 'u2' }));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        'Murilo C. agora é o responsável por Cliente Exemplo Ltda.',
      ),
    );
    expect(removeMutation.mutateAsync).not.toHaveBeenCalled();
  });

  it('adicionar: só oferece gerente ATIVO que ainda não tem acesso', async () => {
    const ui = userEvent.setup();
    render(<ClientManagersSection client={client} />);

    const adicionar = screen.getByRole('button', { name: 'Adicionar' });
    expect(adicionar).toBeDisabled();

    await ui.click(screen.getByRole('combobox', { name: 'Adicionar gerente' }));
    const options = await screen.findAllByRole('option');
    // Fora: os dois que já têm acesso, o inativo e o admin (o backend daria 400).
    expect(options.map((o) => o.textContent)).toEqual(['Gerente Disponível']);
    await ui.click(options[0]!);

    expect(adicionar).toBeEnabled();
    await ui.click(adicionar);
    await waitFor(() => expect(addMutation.mutateAsync).toHaveBeenCalledWith({ user_id: 'u3' }));
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(
        'Gerente Disponível passou a ter acesso a Cliente Exemplo Ltda.',
      ),
    );
  });

  it('erro do servidor vira toast com a mensagem dele e a confirmação continua aberta', async () => {
    removeMutation.mutateAsync.mockRejectedValueOnce(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'responsible',
        userMessage: 'Este gerente é o responsável pelo cliente. Defina outro responsável antes.',
      }),
    );
    render(<ClientManagersSection client={client} />);

    fireEvent.click(screen.getByRole('button', { name: 'Remover acesso de Murilo C.' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Remover acesso' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remover acesso' }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'Este gerente é o responsável pelo cliente. Defina outro responsável antes.',
      ),
    );
    expect(screen.getByRole('alertdialog', { name: 'Remover acesso' })).toBeInTheDocument();
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('marca "(você)" quando o admin da sessão está na carteira e degrada sem lista', () => {
    authState.user = { ...ADMIN, id: 'u2' };
    render(<ClientManagersSection client={client} />);
    expect(screen.getByText('Murilo C. (você)')).toBeInTheDocument();
  });

  it('responsável desativado ganha o selo "Inativo" e continua sem "Remover"', () => {
    managersQuery.data = [{ ...RESPONSIBLE, active: false }, COLLABORATOR];
    render(<ClientManagersSection client={client} />);
    const region = screen.getByRole('region', { name: 'Gerentes com acesso ao cliente' });
    const [first] = within(region).getAllByRole('listitem');
    expect(within(first!).getByText('Inativo')).toBeInTheDocument();
    expect(within(first!).getByText('Responsável')).toBeInTheDocument();
    expect(within(first!).queryByRole('button', { name: /Remover acesso/ })).toBeNull();
  });

  it('sem ninguém na carteira: mensagem de vazio, e o Select segue oferecendo gerentes', () => {
    managersQuery.data = [];
    render(<ClientManagersSection client={client} />);
    expect(screen.getByText('Nenhum gerente tem acesso a este cliente.')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Adicionar gerente' })).toBeEnabled();
  });
});
