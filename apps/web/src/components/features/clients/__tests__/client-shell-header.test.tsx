/**
 * O `ClientShell` sem cabeçalho (86e3fr9q3, feedback do Lucas em 28/09/2026).
 *
 * Substitui os testes do breadcrumb (86e2u513w) e do menu "Ações do cliente"
 * (86e3eq9uy): os dois descreviam o cabeçalho que SAIU de propósito. O que se
 * prova aqui é a ausência dele — nada de trilha, nome do cliente como título,
 * selos, favorito ou menu de ações — e o aviso que passou a explicar o cliente
 * encerrado. O título de cada página é o h1 da própria tela (testado nas telas);
 * Encerrar mora na lista de clientes (`clients-screen-org.test.tsx`).
 */
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/clientes/c1/carteira',
}));

const clientState = {
  data: undefined as { name: string; active: boolean; closed_at: string | null } | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => clientState,
}));

let currentUser: unknown;

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { user: unknown }) => unknown) => selector({ user: currentUser }),
}));

// Imports do SUT DEPOIS dos `vi.mock`.
import { ClientShell } from '@/components/features/clients/client-shell';
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

const CLIENT_NAME = 'Padaria Pão Quente Ltda';

beforeEach(() => {
  currentUser = ADMIN;
  clientState.data = { name: CLIENT_NAME, active: true, closed_at: null };
  clientState.isLoading = false;
  clientState.isError = false;
});

function renderShell() {
  return render(
    <ClientShell clientId="c1">
      <h1>Carteira</h1>
    </ClientShell>,
  );
}

describe('ClientShell — sem cabeçalho (86e3fr9q3)', () => {
  it('renderiza só a página: sem breadcrumb, sem nome do cliente, sem selos nem ações', async () => {
    const { container } = renderShell();

    // O único título é o da tela filha.
    expect(screen.getAllByRole('heading')).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1, name: 'Carteira' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument();
    expect(screen.queryByText(CLIENT_NAME)).not.toBeInTheDocument();
    expect(screen.queryByText('Ativo')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /favorit/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ações do cliente/ })).not.toBeInTheDocument();
    expect(screen.queryByText('Cliente encerrado: somente leitura')).not.toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('cliente encerrado ganha o aviso de somente leitura no topo', async () => {
    clientState.data = {
      name: 'Cliente encerrado #abc12345',
      active: false,
      closed_at: '2026-09-10T12:00:00Z',
    };
    const { container } = renderShell();

    expect(screen.getByText('Cliente encerrado: somente leitura')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Encerrar|Editar/ })).not.toBeInTheDocument();
    await assertNoA11yViolations(container);
  });
});
