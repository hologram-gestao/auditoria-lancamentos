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
    | {
        accounts: ReturnType<typeof account>[];
        accounts_synced_at: string | null;
        // S9: sem origem ATIVA o sync responderia 409 — a tela deixa de
        // oferecer "Extrair contas" e explica o estado (R7).
        origin_status?: 'sem_origem' | 'ativa' | 'erro';
      }
    | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const syncState = { mutateAsync: vi.fn(), isPending: false };

vi.mock('@/hooks/use-clients', () => ({
  // O ClientShell agora monta o diálogo de exclusão (86e34jd1d).
  // O ClientShell/lista agora renderiza o coração de favorito (86e34jd5a).
  useSetFavorite: () => ({ mutate: vi.fn(), isPending: false }),
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
    origin_status: 'ativa',
  };
  detailState.isLoading = false;
  detailState.isFetching = false;
  detailState.isError = false;
});

describe('BankAccountsScreen — área rolável (86e2uca1d)', () => {
  it('a tabela é o scroller vertical da área, e a barra fica fora dela', () => {
    render(<BankAccountsScreen clientId="c1" />);

    // `fill` é o que impede a tabela de vazar por baixo da barra de paginação
    // (opaca). A medição da sobreposição é do browser (`e2e/a11y-mocked`).
    const region = screen.getByRole('region', { name: 'Contas bancárias (rolável)' });
    expect(region).toHaveClass('overflow-auto', 'min-h-0');
    expect(region).not.toContainElement(
      screen.getByRole('navigation', { name: 'Paginação de contas bancárias' }),
    );
  });
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
      origin_status: 'ativa',
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
    detailState.data = { accounts: [], accounts_synced_at: null, origin_status: 'ativa' };
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

/**
 * S9 / R7 — ausência e falha de origem são ESTADO, não erro nem tabela vazia
 * ambígua. E a ação que o servidor negaria com 409 não é oferecida (§4.9).
 */
describe('BankAccountsScreen — estado de origem (S9)', () => {
  it('sem origem: explica o estado e NÃO oferece "Extrair contas"', () => {
    detailState.data = { accounts: [], accounts_synced_at: null, origin_status: 'sem_origem' };
    render(<BankAccountsScreen clientId="c1" />);

    const notice = screen.getByText('Este cliente não tem origem conectada');
    expect(notice).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Extrair contas do Omie' })).toBeNull();
    expect(screen.getByText(/ainda não tem uma origem de onde buscá-las/)).toBeVisible();
  });

  it('origem com erro: diz "com erro" e NUNCA "sem origem"', () => {
    detailState.data = { accounts: [], accounts_synced_at: null, origin_status: 'erro' };
    render(<BankAccountsScreen clientId="c1" />);

    expect(screen.getByText('A origem deste cliente está com erro')).toBeVisible();
    expect(screen.queryByText('Este cliente não tem origem conectada')).toBeNull();
  });

  it('cliente encerrado não ganha estado de origem (já é só-leitura)', () => {
    detailState.data = {
      accounts: [],
      accounts_synced_at: null,
      origin_status: 'sem_origem',
      closed_at: '2026-09-01T12:00:00Z',
    } as never;
    render(<BankAccountsScreen clientId="c1" />);
    expect(screen.queryByText('Este cliente não tem origem conectada')).toBeNull();
  });
});

describe('BankAccountsScreen — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<BankAccountsScreen clientId="c1" />);
    await assertNoA11yViolations(container);
  });
});
