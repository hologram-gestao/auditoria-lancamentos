/**
 * Testes do breadcrumb com o nível da conciliação (86e2u513w).
 *
 * Cobre: trilha de 2 níveis nas seções (cliente é a página atual), 3 níveis
 * dentro de uma conciliação (cliente vira LINK — a volta explícita para a
 * lista — e o `aria-current` desce para a sessão), gating do elo "Clientes"
 * para tenant, skeleton durante a carga, rótulo genérico em erro, e a rota
 * `processando`. O rótulo "Conta · Mês" vem do helper único `session-label`.
 */
import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

let currentPathname = '/clientes/c1';

vi.mock('next/navigation', () => ({
  usePathname: () => currentPathname,
}));

const clientState = {
  data: undefined as
    | { name: string; active: boolean; accounts: { omie_conta_id: number; name: string }[] }
    | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

const sessionState = {
  data: undefined as { omie_conta_id: number; reference_month: string } | undefined,
  isError: false,
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => clientState,
}));

vi.mock('@/hooks/use-reconciliations', () => ({
  useSessionDetail: () => sessionState,
}));

let currentUser: unknown;

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { user: unknown }) => unknown) => selector({ user: currentUser }),
}));

vi.mock('@/components/features/clients/edit-client-modal', () => ({
  EditClientModal: () => null,
}));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo — importar no topo as avaliaria antes da inicialização).
import { ClientShell } from '@/components/features/clients/client-shell';
import { sessionIdFromPathname } from '@/components/features/navigation/nav-items';
import type { AuthenticatedUser } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ADMIN: AuthenticatedUser = {
  id: 'u-admin',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
};

const CLIENT_OPERATOR: AuthenticatedUser = {
  id: 'u-co',
  email: 'operador@cliente.com.br',
  name: 'Operador do Cliente',
  role: 'client_operator',
  scope: 'client',
  client_id: 'c1',
};

const SESSION_LABEL = 'Cartão Itaú · Junho de 2026';

beforeEach(() => {
  currentPathname = '/clientes/c1';
  currentUser = ADMIN;
  clientState.data = {
    name: 'Padaria Pão Quente Ltda',
    active: true,
    accounts: [{ omie_conta_id: 10, name: 'Cartão Itaú' }],
  };
  clientState.isLoading = false;
  clientState.isError = false;
  sessionState.data = { omie_conta_id: 10, reference_month: '2026-06-01' };
  sessionState.isError = false;
});

function renderShell() {
  return render(
    <ClientShell clientId="c1">
      <div>conteúdo</div>
    </ClientShell>,
  );
}

function trilha() {
  return within(screen.getByRole('navigation', { name: 'Breadcrumb' }));
}

describe('sessionIdFromPathname', () => {
  it('só casa as rotas de conciliação, com e sem processando', () => {
    expect(sessionIdFromPathname('/clientes/c1')).toBeNull();
    expect(sessionIdFromPathname('/clientes/c1/contas')).toBeNull();
    expect(sessionIdFromPathname('/clientes/c1/conciliacao/s9')).toBe('s9');
    expect(sessionIdFromPathname('/clientes/c1/conciliacao/processando/s9')).toBe('s9');
    expect(sessionIdFromPathname('/configuracoes/usuarios')).toBeNull();
  });
});

describe('ClientShell — breadcrumb', () => {
  it('nas seções do cliente a trilha tem 2 níveis e o cliente é a página atual', () => {
    currentPathname = '/clientes/c1/contas';
    renderShell();

    expect(trilha().getByRole('link', { name: 'Clientes' })).toHaveAttribute('href', '/clientes');
    const clientCrumb = trilha().getByText('Padaria Pão Quente Ltda');
    expect(clientCrumb).toHaveAttribute('aria-current', 'page');
    expect(
      trilha().queryByRole('link', { name: 'Padaria Pão Quente Ltda' }),
    ).not.toBeInTheDocument();
    expect(trilha().queryByText(SESSION_LABEL)).not.toBeInTheDocument();
  });

  it('no detalhe da conciliação o cliente vira link e o aria-current desce', async () => {
    currentPathname = '/clientes/c1/conciliacao/s9';
    const { container } = renderShell();

    expect(trilha().getByRole('link', { name: 'Padaria Pão Quente Ltda' })).toHaveAttribute(
      'href',
      '/clientes/c1',
    );
    const sessionCrumb = trilha().getByText(SESSION_LABEL);
    expect(sessionCrumb).toHaveAttribute('aria-current', 'page');
    // O nível do cliente deixou de ser a página atual.
    expect(trilha().getByRole('link', { name: 'Padaria Pão Quente Ltda' })).not.toHaveAttribute(
      'aria-current',
    );
    await assertNoA11yViolations(container);
  });

  it('usuário de tenant nunca vê o elo para a lista global', () => {
    currentUser = CLIENT_OPERATOR;
    currentPathname = '/clientes/c1/conciliacao/s9';
    renderShell();

    expect(trilha().queryByRole('link', { name: 'Clientes' })).not.toBeInTheDocument();
    expect(trilha().getByRole('link', { name: 'Padaria Pão Quente Ltda' })).toBeInTheDocument();
    expect(trilha().getByText(SESSION_LABEL)).toHaveAttribute('aria-current', 'page');
  });

  it('sessão carregando mostra skeleton sem perder o link do cliente', () => {
    currentPathname = '/clientes/c1/conciliacao/s9';
    sessionState.data = undefined;
    renderShell();

    expect(trilha().getByText('Carregando conciliação…')).toBeInTheDocument();
    expect(trilha().getByRole('link', { name: 'Padaria Pão Quente Ltda' })).toBeInTheDocument();
  });

  it('erro na sessão mantém o nível com rótulo genérico — a volta continua visível', () => {
    currentPathname = '/clientes/c1/conciliacao/s9';
    sessionState.data = undefined;
    sessionState.isError = true;
    renderShell();

    expect(trilha().getByText('Conciliação')).toHaveAttribute('aria-current', 'page');
    expect(trilha().getByRole('link', { name: 'Padaria Pão Quente Ltda' })).toBeInTheDocument();
  });

  it('a rota processando também ganha o nível da sessão', () => {
    currentPathname = '/clientes/c1/conciliacao/processando/s9';
    renderShell();

    expect(trilha().getByText(SESSION_LABEL)).toHaveAttribute('aria-current', 'page');
  });

  it('conta fora da lista sincronizada cai no rótulo por id', () => {
    currentPathname = '/clientes/c1/conciliacao/s9';
    sessionState.data = { omie_conta_id: 99, reference_month: '2026-06-01' };
    renderShell();

    expect(trilha().getByText('Conta #99 · Junho de 2026')).toHaveAttribute('aria-current', 'page');
  });
});
