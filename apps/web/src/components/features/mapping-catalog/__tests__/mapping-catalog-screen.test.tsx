/**
 * Tela "Destinos do de-para" (86e3n70pn) — Configurações, catálogo da organização.
 *
 * Cobre em jsdom: a permissão da tela (`manage_mapping_catalog`: admin vê, gerente
 * recebe o acesso negado), a lista de destinos com a contagem de alvos, o destino
 * padrão (o demonstrativo) e o da URL, a explicação do `conta_contabil` sem lista de
 * alvos, criar em lote colando linhas (o que chega ao servidor e o 409 no CAMPO),
 * editar o nome e inativar. Os cenários de browser real (contraste, 390px) ficam em
 * `e2e/a11y-mocked.spec.ts`.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let currentSearch = '';
const replaceMock = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/configuracoes/destinos-de-para',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

function mutationState() {
  return { mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), isPending: false };
}

const destinationsState = {
  data: [] as MappingDestination[],
  isLoading: false,
  isError: false,
  error: null as unknown,
};
const targetsPageState = {
  data: undefined as MappingTargetListResponse | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
};
let lastTargetsDestination: string | undefined;
const createState = mutationState();
const updateState = mutationState();

vi.mock('@/hooks/use-client-mapping', () => ({
  useMappingDestinations: () => destinationsState,
  useMappingTargetsPage: (destinationId: string) => {
    lastTargetsDestination = destinationId;
    return targetsPageState;
  },
  useCreateMappingTargets: () => createState,
  useUpdateMappingTarget: () => updateState,
}));

vi.mock('@/components/features/organizations/organization-select', () => ({
  ALL_ORGANIZATIONS: 'all',
  OrganizationFilterSelect: () => null,
  useOrganizationOptions: () => ({ organizations: [], isLoading: false, isError: false }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

import { MappingCatalogScreen } from '@/components/features/mapping-catalog/mapping-catalog-screen';
import { ApiError } from '@/lib/api/client';
import type {
  AuthenticatedUser,
  MappingDestination,
  MappingTargetListResponse,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ORG = '0706eeb5-9718-4d03-bcda-ef615789e6ac';

const admin: AuthenticatedUser = {
  id: 'a',
  email: 'admin@prospecta.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: ORG,
  organization_name: 'Prospecta',
};
const manager: AuthenticatedUser = { ...admin, id: 'm', role: 'manager' };

function destination(overrides: Partial<MappingDestination> = {}): MappingDestination {
  return {
    id: 'd-demo',
    type: 'demonstrativo_contabil',
    name: 'Demonstrativo contábil',
    active: true,
    organizationId: ORG,
    targetsCount: 2,
    ...overrides,
  };
}

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentSearch = '';
  replaceMock.mockClear();
  authState.user = admin;
  lastTargetsDestination = undefined;
  destinationsState.data = [
    destination({ id: 'd-conta', type: 'conta_contabil', name: 'Conta contábil', targetsCount: 0 }),
    destination(),
    destination({ id: 'd-caixa', type: 'fluxo_de_caixa', name: 'Fluxo de caixa', targetsCount: 0 }),
  ];
  targetsPageState.data = {
    data: [
      { id: 't1', code: '1.01', name: 'Receita bruta', active: true },
      { id: 't2', code: '2.01', name: 'Despesas antigas', active: false },
    ],
    pagination: { page: 1, pageSize: 50, total: 2, totalPages: 1 },
  };
  targetsPageState.isError = false;
  createState.mutateAsync.mockReset();
  updateState.mutateAsync.mockReset();
});

describe('MappingCatalogScreen — acesso', () => {
  it('o gerente (sem manage_mapping_catalog) recebe o acesso negado', () => {
    authState.user = manager;
    render(<MappingCatalogScreen />);
    expect(screen.getByText(/configurados pelo administrador da organização/)).toBeVisible();
    expect(screen.queryByRole('heading', { name: 'Destinos do de-para' })).not.toBeInTheDocument();
  });
});

describe('MappingCatalogScreen — destinos e alvos', () => {
  it('lista os destinos com a contagem e abre no demonstrativo', async () => {
    const { container } = render(<MappingCatalogScreen />);
    expect(screen.getByRole('heading', { name: 'Destinos do de-para', level: 1 })).toBeVisible();
    const destinos = screen.getByRole('region', { name: 'Destinos da organização (rolável)' });
    // Duas vezes cada: a coluna (de `sm` para cima) e a linha sob o nome (celular).
    expect(within(destinos).getAllByText('Nenhum alvo')).toHaveLength(2);
    expect(within(destinos).getAllByText('Plano contábil de cada cliente')).toHaveLength(2);
    expect(within(destinos).getAllByText('2 alvos')).toHaveLength(2);
    expect(
      within(destinos).getByRole('button', { name: 'Ver alvos de Demonstrativo contábil' }),
    ).toHaveAttribute('aria-pressed', 'true');
    expect(lastTargetsDestination).toBe('d-demo');
    expect(screen.getByRole('heading', { name: 'Alvos de Demonstrativo contábil' })).toBeVisible();
    expect(screen.getByText('Receita bruta')).toBeVisible();
    await assertNoA11yViolations(container);
  });

  it('o destino da URL vence o padrão; escolher outro grava na URL', async () => {
    const user = userEvent.setup();
    currentSearch = 'destino=d-caixa';
    render(<MappingCatalogScreen />);
    expect(screen.getByRole('heading', { name: 'Alvos de Fluxo de caixa' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Ver alvos de Demonstrativo contábil' }));
    expect(replaceMock).toHaveBeenCalledWith(expect.stringContaining('destino=d-demo'), {
      scroll: false,
    });
  });

  it('conta contábil: explica o plano contábil do cliente, sem lista nem "Adicionar alvos"', () => {
    currentSearch = 'destino=d-conta';
    render(<MappingCatalogScreen />);
    expect(
      screen.getByRole('heading', { name: 'Conta contábil: sem alvos de catálogo' }),
    ).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Adicionar alvos' })).not.toBeInTheDocument();
  });

  it('inativar e reativar mandam só a situação', async () => {
    const user = userEvent.setup();
    updateState.mutateAsync.mockResolvedValue({});
    render(<MappingCatalogScreen />);
    await user.click(screen.getByRole('button', { name: 'Inativar o alvo 1.01' }));
    expect(updateState.mutateAsync).toHaveBeenCalledWith({
      targetId: 't1',
      patch: { active: false },
    });
    await user.click(screen.getByRole('button', { name: 'Reativar o alvo 2.01' }));
    expect(updateState.mutateAsync).toHaveBeenLastCalledWith({
      targetId: 't2',
      patch: { active: true },
    });
  });

  it('editar troca só o nome', async () => {
    const user = userEvent.setup();
    updateState.mutateAsync.mockResolvedValue({});
    render(<MappingCatalogScreen />);
    await user.click(screen.getByRole('button', { name: 'Editar o alvo 1.01' }));
    const dialog = await screen.findByRole('dialog', { name: 'Editar alvo 1.01' });
    const input = within(dialog).getByLabelText('Nome');
    await user.clear(input);
    await user.type(input, 'Receita operacional bruta');
    await user.click(within(dialog).getByRole('button', { name: 'Salvar' }));
    expect(updateState.mutateAsync).toHaveBeenCalledWith({
      targetId: 't1',
      patch: { name: 'Receita operacional bruta' },
    });
  });
});

describe('MappingCatalogScreen — adicionar alvos colando linhas', () => {
  it('cria o lote com as linhas coladas (ponto e vírgula e TAB)', async () => {
    const user = userEvent.setup();
    createState.mutateAsync.mockResolvedValue([{}, {}]);
    render(<MappingCatalogScreen />);
    await user.click(screen.getByRole('button', { name: 'Adicionar alvos' }));
    const dialog = await screen.findByRole('dialog', { name: 'Adicionar alvos' });
    await user.click(within(dialog).getByLabelText('Alvos'));
    await user.paste('3.01;Receita bruta\n\n3.02\tDeduções; abatimentos');
    expect(within(dialog).getByText(/2 alvos prontos para criar/)).toBeVisible();
    await user.click(within(dialog).getByRole('button', { name: 'Criar 2 alvos' }));
    expect(createState.mutateAsync).toHaveBeenCalledWith({
      targets: [
        { code: '3.01', name: 'Receita bruta' },
        { code: '3.02', name: 'Deduções; abatimentos' },
      ],
    });
  });

  it('linha inválida é apontada no campo e nada vai ao servidor', async () => {
    const user = userEvent.setup();
    render(<MappingCatalogScreen />);
    await user.click(screen.getByRole('button', { name: 'Adicionar alvos' }));
    const dialog = await screen.findByRole('dialog', { name: 'Adicionar alvos' });
    await user.click(within(dialog).getByLabelText('Alvos'));
    await user.paste('3.01;Receita\nsem separador');
    await user.click(within(dialog).getByRole('button', { name: 'Criar alvo' }));
    expect(await within(dialog).findByText(/linha 2: use "código;nome"/)).toBeVisible();
    expect(createState.mutateAsync).not.toHaveBeenCalled();
  });

  it('código que já existe (409) aparece no campo, sem fechar o diálogo', async () => {
    const user = userEvent.setup();
    createState.mutateAsync.mockRejectedValue(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'x',
        userMessage: 'Estes códigos já existem neste destino ou se repetem no lote: 1.01.',
      }),
    );
    render(<MappingCatalogScreen />);
    await user.click(screen.getByRole('button', { name: 'Adicionar alvos' }));
    const dialog = await screen.findByRole('dialog', { name: 'Adicionar alvos' });
    await user.click(within(dialog).getByLabelText('Alvos'));
    await user.paste('1.01;Receita');
    await user.click(within(dialog).getByRole('button', { name: 'Criar alvo' }));
    expect(await within(dialog).findByText(/Estes códigos já existem neste destino/)).toBeVisible();
    expect(screen.getByRole('dialog', { name: 'Adicionar alvos' })).toBeVisible();
  });
});
