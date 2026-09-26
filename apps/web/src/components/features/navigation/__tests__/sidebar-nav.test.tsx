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
  // O ClientShell agora monta o diálogo de exclusão (86e34jd1d).
  // O ClientShell/lista agora renderiza o coração de favorito (86e34jd5a).
  useSetFavorite: () => ({ mutate: vi.fn(), isPending: false }),
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

/** A plataforma (86e36ecwa): sem organização e sem tenant, vê tudo. */
const PLATFORM: AuthenticatedUser = {
  id: 'u-plat',
  email: 'plataforma@hologram.com.br',
  name: 'Plataforma',
  role: 'platform_admin',
  scope: 'platform',
  client_id: null,
  organization_id: null,
  organization_name: null,
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
    expect(within(nav).getByRole('link', { name: 'Categorias de Cliente' })).toHaveAttribute(
      'href',
      '/configuracoes/categorias',
    );
    // Organizações é da PLATAFORMA: o admin da organização não vê o item
    // (`manage_platform` tem ✅ numa coluna só da matriz).
    expect(within(nav).queryByRole('link', { name: 'Organizações' })).not.toBeInTheDocument();
    // Tipos de Anomalia saiu do menu do admin na D3 final (86e36ed1d): a
    // taxonomia é global do produto e só a plataforma a edita. O item e a rota
    // somem JUNTOS — os dois consultam `manage_anomaly_types`.
    expect(within(nav).queryByRole('link', { name: 'Tipos de Anomalia' })).not.toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('plataforma vê Organizações junto com as demais configurações', async () => {
    const { container } = render(<SidebarNav user={PLATFORM} />);

    const nav = screen.getByRole('navigation', { name: 'Navegação principal' });
    expect(within(nav).getByRole('link', { name: 'Organizações' })).toHaveAttribute(
      'href',
      '/configuracoes/organizacoes',
    );
    // E continua vendo o resto: a plataforma está em toda linha da matriz.
    expect(within(nav).getByRole('link', { name: 'Usuários' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Categorias de Cliente' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Tipos de Anomalia' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Clientes' })).toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('gerente da organização não vê Configurações (nenhum item da seção)', () => {
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

  it('gerente da organização opera a carteira E gere os usuários do tenant (D2)', () => {
    // Mudou na 86e36ecjp (já na main): a célula `manage_client_users` ganhou o
    // gerente. O "da carteira" não é decidido aqui — é o backend que nega o
    // cliente fora dela; o menu só não esconde o que o servidor libera.
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={SYSTEM_MANAGER} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Voltar para clientes' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Usuários' })).toHaveAttribute(
      'href',
      '/clientes/c1/usuarios',
    );
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

  it('operador do cliente: sem Voltar e sem Usuários — 7 seções', () => {
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    const links = within(nav).getAllByRole('link');
    // "Plano de Contas" entrou na S10, "Carteira" na S11 e "De-para" na S12:
    // LER é de todo papel que alcança o cliente nos três casos (o operador
    // inclusive). Quem some para ele é a ESCRITA — sincronizar, editar o
    // de-para —, dentro de cada tela, não o item de menu.
    expect(links.map((l) => l.textContent)).toEqual([
      'Conciliações',
      'Contas Bancárias',
      'Painel',
      'Glossário',
      'Plano de Contas',
      'Carteira',
      'De-para',
    ]);
  });

  it('a rota do de-para não deixa "Conciliações" ativo junto', () => {
    // "Conciliações" é o FALLBACK da camada do cliente: rota nova que não entre
    // na negação de `isReconciliations` marca dois itens ao mesmo tempo.
    currentPathname = '/clientes/c1/de-para';
    render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'De-para' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).not.toHaveAttribute(
      'aria-current',
    );
  });

  it('a rota da carteira não deixa "Conciliações" ativo junto', () => {
    // Mesma armadilha do plano de contas: "Conciliações" é o FALLBACK da camada
    // do cliente, então rota nova que não entre na negação de
    // `isReconciliations` marca dois itens ao mesmo tempo.
    currentPathname = '/clientes/c1/carteira';
    render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Carteira' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).not.toHaveAttribute(
      'aria-current',
    );
  });

  it('a rota do plano de contas não deixa "Conciliações" ativo junto', () => {
    // "Conciliações" é o FALLBACK da camada do cliente: rota nova que não entre
    // na negação de `isReconciliations` marca dois itens ao mesmo tempo.
    currentPathname = '/clientes/c1/plano-de-contas';
    render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Plano de Contas' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).not.toHaveAttribute(
      'aria-current',
    );
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
