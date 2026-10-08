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
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

let currentPathname = '/clientes';

vi.mock('next/navigation', () => ({
  usePathname: () => currentPathname,
}));

const detailState = {
  data: undefined as
    | {
        name: string;
        organization?: { id: string };
        connections?: Array<{
          id: string;
          provider_type: string;
          label: string;
          status: 'ativa' | 'inativa' | 'erro';
          capabilities: string[];
        }>;
      }
    | undefined,
  isLoading: false,
  isError: false,
};

// Resumo do cliente (86e3k1q3x): só o `data` importa para o menu. `enabled`
// é registrado para provar que deep link negado não dispara request.
const summaryState = {
  data: undefined as ClientSummary | undefined,
  isError: false,
};
const summaryCalls: Array<{ clientId: string; enabled: boolean | undefined }> = [];

vi.mock('@/hooks/use-client-summary', () => ({
  useClientSummary: (clientId: string, _month?: string, options: { enabled?: boolean } = {}) => {
    summaryCalls.push({ clientId, enabled: options.enabled });
    return summaryState;
  },
}));

// Catálogo de destinos (ajuste de 08/10/2026): o nome de cada destino no detalhe
// do contador do De-para. `organizationId` é registrado para provar a regra da
// plataforma (só ela diz qual organização).
const destinationCalls: Array<{ organizationId: string | null; enabled: boolean | undefined }> = [];
vi.mock('@/hooks/use-client-mapping', () => ({
  useMappingDestinations: (organizationId: string | null, options: { enabled?: boolean } = {}) => {
    destinationCalls.push({ organizationId, enabled: options.enabled });
    return {
      data: [
        { type: 'conta_contabil', name: 'Conta contábil' },
        { type: 'fluxo_de_caixa', name: 'Fluxo de caixa' },
      ],
    };
  },
}));

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
import type { AuthenticatedUser, ClientSummary } from '@/lib/contracts';
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

/** Resumo com as três pendências que viram contador, e as que não viram. */
const SUMMARY: ClientSummary = {
  referenceMonth: '2026-10',
  reconciliations: {
    accountsTotal: 4,
    accountsWithSession: 3,
    byStatus: { processing: 1, reviewing: 2, done: 1, error: 0 },
    habitualAccountIds: [1, 2, 3],
  },
  anomalies: { openTotal: 7, byType: [{ code: 'wrong_date', count: 7 }], resolvedInMonth: 2 },
  cardPurchasesToPost: { count: 3, totalAmount: '-420.00' },
  mapping: [
    {
      destinationCode: 'conta_contabil',
      withoutDecision: 4,
      coveragePct: '81.2',
      materialized: false,
    },
    {
      destinationCode: 'fluxo_de_caixa',
      withoutDecision: 1,
      coveragePct: null,
      materialized: false,
    },
  ],
  titles: {
    overdueCount: 12,
    overdueTotal: '82865.50',
    aPagar: { overdueCount: 3, overdueTotal: '4380.00' },
    aReceber: { overdueCount: 9, overdueTotal: '78485.50' },
    syncedAt: '2026-10-07T09:10:00Z',
    neverSynced: false,
  },
  latestSession: null,
};

beforeEach(() => {
  summaryState.data = undefined;
  summaryState.isError = false;
  summaryCalls.length = 0;
  destinationCalls.length = 0;
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
    // S13 (R3): layouts de exportação são configuração da organização — o admin vê.
    expect(within(nav).getByRole('link', { name: 'Layouts de exportação' })).toHaveAttribute(
      'href',
      '/configuracoes/layouts-exportacao',
    );
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
    expect(within(nav).getByRole('link', { name: 'Layouts de exportação' })).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: 'Clientes' })).toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('gerente da organização não vê Configurações (nenhum item da seção)', () => {
    render(<SidebarNav user={SYSTEM_MANAGER} />);

    const nav = screen.getByRole('navigation', { name: 'Navegação principal' });
    expect(within(nav).getByRole('link', { name: 'Clientes' })).toBeInTheDocument();
    expect(within(nav).queryByText('Configurações')).not.toBeInTheDocument();
    expect(within(nav).queryByRole('link', { name: 'Usuários' })).not.toBeInTheDocument();
    // S13: o gerente GERA o arquivo contábil, mas não administra os layouts.
    expect(
      within(nav).queryByRole('link', { name: 'Layouts de exportação' }),
    ).not.toBeInTheDocument();
  });

  it('usuários do cliente não veem "Layouts de exportação" (S13)', () => {
    for (const user of [CLIENT_MANAGER, CLIENT_OPERATOR]) {
      const { unmount } = render(<SidebarNav user={user} />);
      const nav = screen.getByRole('navigation', { name: 'Navegação principal' });
      expect(
        within(nav).queryByRole('link', { name: 'Layouts de exportação' }),
      ).not.toBeInTheDocument();
      unmount();
    }
  });

  it('tenant em deep link de OUTRO tenant degrada para a camada global dele', () => {
    currentPathname = '/clientes/c-alheio/contas';
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    // Nada do cliente alheio: nem menu contextual, nem "Clientes" global.
    expect(screen.queryByRole('navigation', { name: 'Seções do cliente' })).not.toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Navegação principal' });
    expect(within(nav).queryByRole('link', { name: 'Clientes' })).not.toBeInTheDocument();
    // A casa do tenant é a raiz do próprio cliente, que é a LISTA de
    // conciliações (de novo desde 08/10/2026): o rótulo acompanha o destino.
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).toHaveAttribute(
      'href',
      '/clientes/c1',
    );
    expect(within(nav).queryByRole('link', { name: 'Painel' })).not.toBeInTheDocument();
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

  it('operador do cliente: sem Voltar e sem Usuários, 9 itens em Operação e Cadastros', () => {
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    const links = within(nav).getAllByRole('link');
    // "Plano de Contas" entrou na S10, "Carteira" na S11, "De-para" na S12,
    // "Plano contábil" na S16 e "Origem por arquivo" (que era condicional)
    // virou fixa no follow-up 86e3fqnc9: LER é de todo papel que alcança o
    // cliente nos cinco casos (o operador inclusive, que ENVIA o arquivo).
    // Quem some para ele é a ESCRITA, dentro de cada tela. A ordem é a das
    // seções do 86e3k1q2j: Operação, depois Cadastros.
    expect(links.map((l) => l.textContent)).toEqual([
      'Painel',
      'Conciliações',
      'Carteira',
      'De-para',
      'Origem por arquivo',
      'Contas Bancárias',
      'Glossário',
      'Plano de Contas',
      'Plano contábil',
    ]);
    // Sem `manage_client_users`, a seção Acesso some INTEIRA: nada de cabeçalho órfão.
    expect(within(nav).getByText('Operação')).toBeInTheDocument();
    expect(within(nav).getByText('Cadastros')).toBeInTheDocument();
    expect(within(nav).queryByText('Acesso')).not.toBeInTheDocument();
  });

  it('agrupa em Operação, Cadastros e Acesso, cada item sob o próprio cabeçalho', () => {
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    // A ordem do DOM é a ordem lida: cabeçalho, itens dele, próximo cabeçalho.
    const sequence = Array.from(nav.querySelectorAll('a, div'))
      .filter(
        (el) =>
          el.tagName === 'A' || ['Operação', 'Cadastros', 'Acesso'].includes(el.textContent ?? ''),
      )
      .map((el) => el.textContent);
    expect(sequence).toEqual([
      'Voltar para clientes',
      'Operação',
      'Painel',
      'Conciliações',
      'Carteira',
      'De-para',
      'Origem por arquivo',
      'Cadastros',
      'Contas Bancárias',
      'Glossário',
      'Plano de Contas',
      'Plano contábil',
      'Acesso',
      'Usuários',
    ]);
    // A raiz do cliente é a LISTA de conciliações (08/10/2026): "Conciliações"
    // é o ativo, e só ele; o Painel continua o primeiro item, em `/painel`.
    const current = within(nav)
      .getAllByRole('link')
      .filter((link) => link.getAttribute('aria-current') === 'page')
      .map((link) => link.textContent);
    expect(current).toEqual(['Conciliações']);
    expect(within(nav).getByRole('link', { name: 'Painel' })).toHaveAttribute(
      'href',
      '/clientes/c1/painel',
    );
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).toHaveAttribute(
      'href',
      '/clientes/c1',
    );
  });

  function activeItems(): Array<string | null> {
    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    return within(nav)
      .getAllByRole('link')
      .filter((link) => link.getAttribute('aria-current') === 'page')
      .map((link) => link.textContent);
  }

  it('"Conciliações" casa pela própria rota: a lista, o detalhe e o processamento', () => {
    for (const path of [
      '/clientes/c1',
      '/clientes/c1/conciliacao/s9',
      '/clientes/c1/conciliacao/processando/s9',
    ]) {
      currentPathname = path;
      const { unmount } = render(<SidebarNav user={ADMIN} />);
      expect(activeItems()).toEqual(['Conciliações']);
      unmount();
    }
  });

  it('"Painel" casa pela própria rota, sem acender "Conciliações"', () => {
    currentPathname = '/clientes/c1/painel';
    render(<SidebarNav user={ADMIN} />);
    expect(activeItems()).toEqual(['Painel']);
  });

  it('rota que nenhum item reivindica não acende item nenhum (sem ativo por exclusão)', () => {
    currentPathname = '/clientes/c1/rota-que-nao-existe';
    render(<SidebarNav user={ADMIN} />);
    expect(activeItems()).toEqual([]);
  });

  it('sem contador vindo do item, nenhum link carrega pílula de número', () => {
    currentPathname = '/clientes/c1';
    render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).queryAllByRole('img')).toHaveLength(0);
  });

  it('"Plano contábil" (S16) fica ativo na própria rota, sem marcar "Plano de Contas" nem "Conciliações"', () => {
    currentPathname = '/clientes/c1/plano-contabil';
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    const current = within(nav)
      .getAllByRole('link')
      .filter((link) => link.getAttribute('aria-current') === 'page')
      .map((link) => link.textContent);
    expect(current).toEqual(['Plano contábil']);
  });

  it('"Origem por arquivo" (S14) aparece para o cliente com conexão `arquivo`', () => {
    currentPathname = '/clientes/c1/origem-arquivo';
    detailState.data = {
      name: 'Cliente Exemplo Ltda',
      connections: [
        {
          id: 'arq-1',
          provider_type: 'arquivo',
          label: 'Arquivo',
          status: 'ativa',
          capabilities: ['listar_lancamentos'],
        },
      ],
    };
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    // O operador vê a aba: LER o mapeamento e ENVIAR o arquivo são dele.
    const item = within(nav).getByRole('link', { name: 'Origem por arquivo' });
    expect(item).toHaveAttribute('href', '/clientes/c1/origem-arquivo');
    // Cada item casa pela própria rota: "Conciliações" não fica ativo junto.
    expect(item).toHaveAttribute('aria-current', 'page');
    expect(within(nav).getByRole('link', { name: 'Conciliações' })).not.toHaveAttribute(
      'aria-current',
    );
  });

  it('a aba aparece TAMBÉM sem conexão `arquivo` — quem explica o estado é a tela', () => {
    // 86e3fqnc9: antes a aba sumia, e o recurso ficava invisível para quem
    // precisava descobri-lo. LER a aba não pede permissão (`AccessibleClientDep`),
    // então esconder aqui não era regra de §4.9 — o que o servidor negaria é
    // CONECTAR, e disso a tela cuida.
    currentPathname = '/clientes/c1';
    detailState.data = {
      name: 'Cliente Exemplo Ltda',
      connections: [
        {
          id: 'omie-1',
          provider_type: 'omie',
          label: 'Omie',
          status: 'ativa',
          capabilities: ['verificar_credencial', 'listar_contas', 'listar_lancamentos'],
        },
      ],
    };
    render(<SidebarNav user={ADMIN} />);
    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Origem por arquivo' })).toHaveAttribute(
      'href',
      '/clientes/c1/origem-arquivo',
    );
  });

  it('a aba aparece para o cliente SEM origem nenhuma, e para o operador', () => {
    currentPathname = '/clientes/c1';
    detailState.data = { name: 'Cliente Exemplo Ltda', connections: [] };
    render(<SidebarNav user={CLIENT_OPERATOR} />);
    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).getByRole('link', { name: 'Origem por arquivo' })).toBeInTheDocument();
  });

  it('a rota do de-para não deixa "Conciliações" ativo junto', () => {
    // Desde a 86e3k1q5n cada item casa pela própria rota; o teste fica como
    // trava contra a volta do "ativo por exclusão".
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
    // Desde a 86e3k1q5n cada item casa pela própria rota; o teste fica como
    // trava contra a volta do "ativo por exclusão".
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
    // Desde a 86e3k1q5n cada item casa pela própria rota; o teste fica como
    // trava contra a volta do "ativo por exclusão".
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

describe('SidebarNav — contadores de pendência (86e3k1q3x)', () => {
  function countOf(nav: HTMLElement, linkName: string): HTMLElement | null {
    const link = within(nav).getByRole('link', { name: new RegExp(`^${linkName}`) });
    return within(link).queryByRole('img');
  }

  it('Conciliações, Carteira e De-para ganham contador com nome acessível; o resto não', async () => {
    currentPathname = '/clientes/c1/contas';
    summaryState.data = SUMMARY;
    const { container } = render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    // processando + em revisão; vencidos; o MAIOR sem decisão entre os destinos
    // (ajuste de 08/10/2026: 4, e não 4 + 1). O nome acessível é o detalhe inteiro.
    expect(countOf(nav, 'Conciliações')).toHaveAccessibleName(
      '3 em andamento: 1 em processamento · 2 em revisão',
    );
    expect(countOf(nav, 'Conciliações')).toHaveTextContent('3');
    expect(countOf(nav, 'Carteira')).toHaveAccessibleName(
      '12 títulos vencidos: 9 a receber · 3 a pagar',
    );
    expect(countOf(nav, 'De-para')).toHaveTextContent('4');
    expect(countOf(nav, 'De-para')).toHaveAccessibleName(
      '4 categorias sem decisão · Conta contábil: 4, Fluxo de caixa: 1',
    );
    // Tom de cada um: a cor do badge que a tela de destino já usa.
    expect(countOf(nav, 'Conciliações')).toHaveClass('text-info');
    expect(countOf(nav, 'Carteira')).toHaveClass('text-destructive');
    expect(countOf(nav, 'De-para')).toHaveClass('text-warning');
    // Painel, Origem por arquivo, Cadastros e Acesso nunca têm contador.
    expect(within(nav).getAllByRole('img')).toHaveLength(3);
    expect(summaryCalls.at(-1)).toEqual({ clientId: 'c1', enabled: true });
    await assertNoA11yViolations(container);
  });

  it('o detalhe abre num tooltip no hover e no foco do link; item sem contador não tem', async () => {
    const user = userEvent.setup();
    currentPathname = '/clientes/c1/contas';
    summaryState.data = SUMMARY;
    render(<SidebarNav user={ADMIN} />);
    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });

    await user.hover(within(nav).getByRole('link', { name: /^Carteira/ }));
    expect(await screen.findByRole('tooltip')).toHaveTextContent(
      '12 títulos vencidos: 9 a receber · 3 a pagar',
    );
    await user.unhover(within(nav).getByRole('link', { name: /^Carteira/ }));

    // Foco do teclado no De-para: o mesmo tooltip, sem pílula focável.
    within(nav)
      .getByRole('link', { name: /^De-para/ })
      .focus();
    await waitFor(() =>
      expect(screen.getByRole('tooltip')).toHaveTextContent(
        '4 categorias sem decisão · Conta contábil: 4, Fluxo de caixa: 1',
      ),
    );
    expect(
      within(nav)
        .getAllByRole('img')
        .every((pill) => !pill.hasAttribute('tabindex')),
    ).toBe(true);

    // Contas Bancárias não tem contador: nem gatilho de tooltip.
    expect(within(nav).getByRole('link', { name: 'Contas Bancárias' })).not.toHaveAttribute(
      'data-state',
    );
  });

  it('a plataforma pede o catálogo da organização DO CLIENTE; o staff, o da própria', () => {
    currentPathname = '/clientes/c1';
    detailState.data = { name: 'Cliente Exemplo Ltda', organization: { id: 'org-1' } };
    render(<SidebarNav user={PLATFORM} />);
    expect(destinationCalls.at(-1)).toEqual({ organizationId: 'org-1', enabled: true });

    destinationCalls.length = 0;
    render(<SidebarNav user={ADMIN} />);
    expect(destinationCalls.at(-1)).toEqual({ organizationId: null, enabled: true });
  });

  it('papel sem a carteira (`titles` nulo) fica sem o contador da Carteira', () => {
    currentPathname = '/clientes/c1';
    summaryState.data = { ...SUMMARY, titles: null };
    render(<SidebarNav user={CLIENT_OPERATOR} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(countOf(nav, 'Carteira')).toBeNull();
    expect(countOf(nav, 'Conciliações')).not.toBeNull();
  });

  it('resumo em erro ou carregando: nenhuma pílula, e o menu inteiro aparece', () => {
    currentPathname = '/clientes/c1';
    summaryState.isError = true;
    render(<SidebarNav user={ADMIN} />);

    const nav = screen.getByRole('navigation', { name: 'Seções do cliente' });
    expect(within(nav).queryAllByRole('img')).toHaveLength(0);
    expect(within(nav).getByRole('link', { name: 'Carteira' })).toBeInTheDocument();
  });

  it('deep link de outro tenant não pede o resumo do cliente alheio', () => {
    currentPathname = '/clientes/c-alheio';
    render(<SidebarNav user={CLIENT_OPERATOR} />);
    expect(summaryCalls.every((call) => call.enabled === false)).toBe(true);
  });
});
