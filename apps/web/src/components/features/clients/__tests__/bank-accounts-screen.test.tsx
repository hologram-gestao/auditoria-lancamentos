/**
 * Testes da tela "Contas Bancárias" do cliente (FRONT 04.5 / R6).
 *
 * Cobre: colunas exigidas (nome · banco · tipo · sincronização), paginação no
 * rodapé com estado na URL, botão async "Extrair contas do Omie" (spinner e
 * reabilitação em erro), estados vazio/erro e axe-core.
 *
 * A garantia "credencial Omie nunca aparece na UI" é estrutural: o contrato
 * gerado (`BankAccountResponse`) não tem campo de credencial, então nem existe
 * o que renderizar — o teste abaixo trava a lista de colunas.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let currentSearch = '';
const replaceMock = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/clientes/c1/contas',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function account(id: number) {
  return {
    id: `a${id}`,
    omie_conta_id: id,
    name: `Conta ${String(id).padStart(2, '0')}`,
    bank_name: 'Itaú',
    account_type: id % 2 === 0 ? 'CR' : 'CC',
    synced_at: '2026-06-10T09:00:00Z',
  };
}

const detailState = {
  data: undefined as
    | { accounts: ReturnType<typeof account>[]; accounts_synced_at: string | null }
    | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const syncState = { mutateAsync: vi.fn(), isPending: false };

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => detailState,
  useSyncAccounts: () => syncState,
}));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo — importar no topo as avaliaria antes da inicialização).
import { BankAccountsScreen } from '@/components/features/clients/bank-accounts-screen';
import { assertNoA11yViolations } from '@/test/a11y';

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date('2026-06-10T12:00:00Z'));
});

beforeEach(() => {
  currentSearch = '';
  replaceMock.mockClear();
  syncState.mutateAsync = vi.fn().mockResolvedValue(undefined);
  syncState.isPending = false;
  detailState.data = {
    accounts: [account(1), account(2)],
    accounts_synced_at: '2026-06-10T09:00:00Z',
  };
  detailState.isLoading = false;
  detailState.isFetching = false;
  detailState.isError = false;
});

describe('BankAccountsScreen — lista', () => {
  it('mostra nome, banco, tipo e sincronização de cada conta', () => {
    render(<BankAccountsScreen clientId="c1" />);
    const rows = screen.getAllByRole('row');
    // 1 header + 2 contas.
    expect(rows).toHaveLength(3);
    const first = within(rows[1]!);
    expect(first.getByText('Conta 01')).toBeVisible();
    expect(first.getByText('Itaú')).toBeVisible();
    expect(first.getByText('Conta Corrente')).toBeVisible();
    expect(first.getByText('Sincronizado há 3 h')).toBeVisible();
    // `CR` é cartão (nunca `CA` — bug M-1).
    expect(within(rows[2]!).getByText('Cartão de Crédito')).toBeVisible();
  });

  it('pagina em memória respeitando o pageSize da URL', () => {
    detailState.data = {
      accounts: Array.from({ length: 25 }, (_, i) => account(i + 1)),
      accounts_synced_at: '2026-06-10T09:00:00Z',
    };
    currentSearch = 'page=2&pageSize=10';
    render(<BankAccountsScreen clientId="c1" />);
    const footer = screen.getByRole('navigation', { name: 'Paginação de contas bancárias' });
    expect(within(footer).getByText('11–20 de 25')).toBeVisible();
    expect(within(footer).getByText('Página 2 de 3')).toBeVisible();
    expect(screen.getAllByRole('row')).toHaveLength(11);
  });

  it('página fora do intervalo cai na última válida em vez de tabela vazia', () => {
    currentSearch = 'page=99';
    render(<BankAccountsScreen clientId="c1" />);
    expect(screen.getByText('Página 1 de 1')).toBeVisible();
    expect(screen.getAllByRole('row')).toHaveLength(3);
  });
});

describe('BankAccountsScreen — extrair contas do Omie', () => {
  it('dispara a sincronização forçada ao clicar', async () => {
    const user = userEvent.setup();
    render(<BankAccountsScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Extrair contas do Omie' }));
    expect(syncState.mutateAsync).toHaveBeenCalledTimes(1);
  });

  it('em andamento fica desabilitado com spinner (bloqueia duplo-clique)', () => {
    syncState.isPending = true;
    render(<BankAccountsScreen clientId="c1" />);
    const button = screen.getByRole('button', { name: 'Extraindo…' });
    expect(button).toBeDisabled();
  });

  it('reabilita e avisa em erro', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    syncState.mutateAsync = vi.fn().mockRejectedValue(new Error('boom'));
    render(<BankAccountsScreen clientId="c1" />);
    const button = screen.getByRole('button', { name: 'Extrair contas do Omie' });
    await user.click(button);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // `isPending` volta a false pelo próprio TanStack; aqui o botão nunca
    // ficou preso num estado terminal de erro.
    expect(button).toBeEnabled();
  });
});

describe('BankAccountsScreen — estados', () => {
  it('vazio convida a extrair as contas', () => {
    detailState.data = { accounts: [], accounts_synced_at: null };
    render(<BankAccountsScreen clientId="c1" />);
    expect(screen.getByText(/Nenhuma conta bancária sincronizada/)).toBeVisible();
    expect(screen.getByText('Nunca sincronizado')).toBeVisible();
  });

  it('erro oferece "Tentar novamente"', () => {
    detailState.isError = true;
    detailState.data = undefined;
    render(<BankAccountsScreen clientId="c1" />);
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });

  it('carregando mostra o skeleton', () => {
    detailState.isLoading = true;
    detailState.data = undefined;
    render(<BankAccountsScreen clientId="c1" />);
    expect(screen.getByLabelText('Carregando contas bancárias')).toBeInTheDocument();
  });
});

describe('BankAccountsScreen — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<BankAccountsScreen clientId="c1" />);
    await assertNoA11yViolations(container);
  });
});
