/**
 * Testes do sidebar em CAMADAS (86e2n39h7).
 *
 * Cobre: troca de camada pelo pathname (global ⇄ cliente), gating por papel
 * nas duas camadas (matriz §4.9 — nunca oferecer rota que o servidor nega),
 * "Voltar para clientes" só para a equipe Hologram, nome do cliente no topo
 * (com skeleton e degradação em erro), `aria-current` no item ativo e axe.
 *
 * O tenant que abre deep link de OUTRO tenant cai na camada global — quem
 * explica a negação é o conteúdo (`AccessDenied`), não o menu.
 */
import { render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

let currentPathname = '/clientes';

vi.mock('next/navigation', () => ({
  usePathname: () => currentPathname,
}));

const detailState = {
  data: undefined as { name: string } | undefined,
  isLoading: false,
  isError: false,
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => detailState,
}));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo — importar no topo as avaliaria antes da inicialização).
import { clientIdFromPathname } from '@/components/features/navigation/nav-items';
import { SidebarNav } from '@/components/features/navigation/sidebar-nav';
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

const SYSTEM_MANAGER: AuthenticatedUser = {
  ...ADMIN,
  id: 'u-manager',
  email: 'manager@hologram.com.br',
  name: 'Gerente',
  role: 'manager',
};

const CLIENT_MANAGER: AuthenticatedUser = {
  id: 'u-cm',
  email: 'gerente@cliente.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: 'c1',
};

const CLIENT_OPERATOR: AuthenticatedUser = {
  ...CLIENT_MANAGER,
  id: 'u-co',
  email: 'operador@cliente.com.br',
  name: 'Operador do Cliente',
  role: 'client_operator',
};

beforeEach(() => {
  currentPathname = '/clientes';
  detailState.data = { name: 'Cliente Exemplo Ltda' };
  detailState.isLoading = false;
  detailState.isError = false;
});

describe('clientIdFromPathname', () => {
  it('só casa rotas com um segmento de id abaixo de /clientes', () => {
    expect(clientIdFromPathname('/clientes')).toBeNull();
    expect(clientIdFromPathname('/clientes/c1')).toBe('c1');
    expect(clientIdFromPathname('/clientes/c1/contas')).toBe('c1');
    expect(clientIdFromPathname('/clientes/c1/conciliacao/s9')).toBe('c1');
    expect(clientIdFromPathname('/configuracoes/usuarios')).toBeNull();
  });
});

describe('SidebarNav — camada global', () => {
  it('admin vê Clientes ativo e o bloco Configurações', async () => {
    const { container } = render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Navegação principal' });
    expect(within(nav).getByRole('link', { name: 'Clientes' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByText('Configurações')).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Usuários' })).toHaveAttribute(
      'href',
      '/configuracoes/usuarios',
    );
    expect(within(nav).getByRole('link', { name: 'Tipos de Anomalia' })).toHaveAttribute(
      'href',
      '/configuracoes/anomalias',
    );
    await assertNoA11yViolations(container);
  });

  it('gerente do sistema não vê Configurações (admin-only)', () => {
    render(<SidebarNav user={SYSTEM_MANAGER} />);

    const nav = screen.getByRole('navigation', { name: 'Navegação principal' });
    expect(within(nav).getByRole('link', { name: 'Clientes' })).toBeInTheDocument();
    expect(within(nav).queryByText('Configurações')).not.toBeInTheDocument();
    expect(within(nav).queryByRole('link', { name: 'Usuários' })).not.toBeInTheDocument();
  });

  it('tenant em deep link de OUTRO tenant degrada para a camada global dele', () => {
    currentPathname = '/clientes/c-alheio/contas';
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    // Nada do cliente alheio: nem menu contextual, nem "Clientes" global.
    expect(screen.queryByRole('navigation', { name: 'Seções do cliente' })).not.toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Navegação principal' });
    expect(within(nav).queryByRole('link', { name: 'Clientes' })).not.toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).toHaveAttribute(
      'href',
      '/clientes/c1',
    );
  });
});

describe('SidebarNav — camada do cliente', () => {
  it('equipe Hologram: Voltar + nome do cliente + seções, e o menu global some', async () => {
    currentPathname = '/clientes/c1/contas';
    const { container } = render(<SidebarNav user={ADMIN} />);

    expect(
      screen.queryByRole('navigation', { name: 'Navegação principal' }),
    ).not.toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Voltar para clientes' })).toHaveAttribute(
      'href',
      '/clientes',
    );
    expect(within(nav).getByText('Cliente Exemplo Ltda')).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Contas Bancárias' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    // Admin tem `manage_client_users` — "Usuários" do TENANT aparece.
    expect(within(nav).getByRole('link', { name: 'Usuários' })).toHaveAttribute(
      'href',
      '/clientes/c1/usuarios',
    );
    await assertNoA11yViolations(container);
  });

  it('gerente do sistema opera a carteira mas não vê Usuários do tenant', () => {
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={SYSTEM_MANAGER} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Voltar para clientes' })).toBeInTheDocument();
    expect(within(nav).queryByRole('link', { name: 'Usuários' })).not.toBeInTheDocument();
  });

  it('gerente do cliente: sem Voltar (não há camada acima), com Usuários', () => {
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={CLIENT_MANAGER} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(
      within(nav).queryByRole('link', { name: 'Voltar para clientes' }),
    ).not.toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Usuários' })).toBeInTheDocument();
    expect(within(nav).getByText('Cliente Exemplo Ltda')).toBeInTheDocument();
  });

  it('operador do cliente: sem Voltar e sem Usuários — 4 seções', () => {
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    const links = within(nav).getAllByRole('link');
    expect(links.map((l) => l.textContent)).toEqual([
      'Conciliações',
      'Contas Bancárias',
      'Painel',
      'Glossário',
    ]);
  });

  it('detalhe de conciliação mantém "Conciliações" ativo (mesma área, nível abaixo)', () => {
    currentPathname = '/clientes/c1/conciliacao/s9';
    render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Contas Bancárias' })).not.toHaveAttribute(
      'aria-current',
    );
  });

  it('nome em carga mostra skeleton; erro esconde o bloco sem derrubar o menu', () => {
    currentPathname = '/clientes/c1';
    detailState.data = undefined;

    const { rerender } = render(<SidebarNav user={ADMIN} />);
    let nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByText('Cliente')).toBeInTheDocument();
    expect(within(nav).queryByText('Cliente Exemplo Ltda')).not.toBeInTheDocument();

    detailState.isError = true;
    rerender(<SidebarNav user={ADMIN} />);
    nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).queryByText('Cliente')).not.toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).toBeInTheDocument();
  });
});
