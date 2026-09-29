/**
 * Tela de Layouts de exportação — Sprint 13 (FRONT 13.5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que se prova aqui, sem browser:
 *   - só quem tem `manage_export_layouts` vê a tela; o gerente cai no
 *     `AccessDenied` (e o hook nem busca);
 *   - a lista mostra nome, sistema, versão atual; o vazio orienta com a ação;
 *     o erro mostra o `userMessage` e "Tentar novamente";
 *   - a plataforma tem a coluna Organização e o filtro vira `organizationId`
 *     na consulta (server-side) pela URL;
 *   - "Criar a partir do modelo Domínio" manda o `templateKey` do modelo; a
 *     plataforma é OBRIGADA a escolher a organização, e ela vai no payload;
 *   - erro tipado aparece pelo `userMessage` dentro do diálogo (nunca toast
 *     genérico) e o nome repetido marca o campo;
 *   - as versões abrem na gaveta, só leitura, com os parâmetros legíveis.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/configuracoes/layouts-exportacao',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const listState = {
  data: undefined as ExportLayoutItem[] | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
let lastListOrganization: string | null | undefined;
let lastListEnabled: boolean | undefined;
const detailState = {
  data: undefined as ExportLayoutDetail | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const templatesState = {
  data: undefined as ExportLayoutTemplateItem[] | undefined,
  isLoading: false,
  isError: false,
  isSuccess: true,
};
const createMock = vi.fn();
// O POST do "criar a partir do modelo" em andamento (86e3fyjbm, item 3).
const createState = { isPending: false };

vi.mock('@/hooks/use-export-layouts', () => ({
  useExportLayouts: (organizationId: string | null, options?: { enabled?: boolean }) => {
    lastListOrganization = organizationId;
    lastListEnabled = options?.enabled;
    return listState;
  },
  useExportLayout: () => detailState,
  useExportLayoutTemplates: () => templatesState,
  useCreateExportLayoutFromTemplate: () => ({
    mutateAsync: createMock,
    isPending: createState.isPending,
    reset: vi.fn(),
  }),
}));

const organizationsState = {
  data: undefined as { data: OrganizationItem[] } | undefined,
  isLoading: false,
  isError: false,
};
vi.mock('@/hooks/use-organizations', () => ({
  useOrganizationsList: () => organizationsState,
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

const { toastSuccess, toastError } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: toastSuccess, error: toastError } }));

// Imports do SUT DEPOIS dos `vi.mock`.
import ExportLayoutsPage from '@/app/(app)/configuracoes/layouts-exportacao/export-layouts-page';
import { ApiError } from '@/lib/api/client';
import type { OrganizationItem } from '@/lib/api/organizations';
import type {
  AuthenticatedUser,
  ExportLayoutDetail,
  ExportLayoutItem,
  ExportLayoutTemplateItem,
} from '@/lib/contracts';

const HOLOGRAM = { id: '0706eeb5-9718-4d03-bcda-ef615789e6ac', name: 'Hologram' };
const PROSPECTA = { id: '11111111-2222-3333-4444-555555555555', name: 'Prospecta' };

const PLATFORM: AuthenticatedUser = {
  id: 'plat',
  name: 'Plataforma',
  email: 'plataforma@hologram.com.br',
  role: 'platform_admin',
  scope: 'platform',
  client_id: null,
  organization_id: null,
  organization_name: null,
};
const ORG_ADMIN: AuthenticatedUser = {
  id: 'adm',
  name: 'Admin',
  email: 'admin@hologram.com.br',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: HOLOGRAM.id,
  organization_name: HOLOGRAM.name,
};
const MANAGER: AuthenticatedUser = { ...ORG_ADMIN, id: 'mgr', role: 'manager' };

const DOMINIO_DEFINITION = {
  columns: [
    { field: 'data', header: null },
    { field: 'conta_debito', header: null },
    { field: 'conta_credito', header: null },
    { field: 'valor', header: null },
    { field: 'historico', header: null },
  ],
  separator: ';',
  hasHeader: false,
  encoding: 'latin-1',
  lineEnding: 'crlf',
  dateFormat: 'dd/mm/aaaa',
  amountFormat: { prefix: 'R$ ', thousandsSeparator: '.', decimalSeparator: ',', decimalPlaces: 2 },
};

const TEMPLATE: ExportLayoutTemplateItem = {
  key: 'dominio_lancamentos_csv',
  name: 'Domínio: lançamentos contábeis (CSV)',
  targetSystem: 'Domínio',
  description: 'Importador de lançamentos contábeis (partidas simples) do Domínio.',
  definition: DOMINIO_DEFINITION,
};

function layout(over: Partial<ExportLayoutItem> = {}): ExportLayoutItem {
  return {
    id: 'lay-1',
    name: 'Domínio: lançamentos contábeis (CSV)',
    targetSystem: 'Domínio',
    organizationId: HOLOGRAM.id,
    latestVersion: 2,
    createdAt: '2026-09-20T12:00:00Z',
    updatedAt: '2026-09-28T12:00:00Z',
    ...over,
  };
}

function organization(over: Partial<OrganizationItem> = {}): OrganizationItem {
  return {
    id: HOLOGRAM.id,
    name: HOLOGRAM.name,
    active: true,
    clients_count: 3,
    users_count: 5,
    created_at: '2026-09-01T12:00:00Z',
    updated_at: '2026-09-10T12:00:00Z',
    ...over,
  };
}

beforeAll(() => {
  // jsdom não implementa as APIs de ponteiro que o Radix (Select) consulta.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  listState.data = [layout()];
  listState.isLoading = false;
  listState.isError = false;
  listState.error = null;
  detailState.data = undefined;
  detailState.isLoading = false;
  detailState.isError = false;
  templatesState.data = [TEMPLATE];
  templatesState.isLoading = false;
  templatesState.isError = false;
  templatesState.isSuccess = true;
  organizationsState.data = {
    data: [
      organization(),
      organization({ id: PROSPECTA.id, name: PROSPECTA.name }),
      organization({ id: 'org-susp', name: 'Suspensa Ltda', active: false }),
    ],
  };
  currentSearch = '';
  replaceMock.mockReset();
  createMock.mockReset().mockResolvedValue({ ...layout(), versions: [] });
  createState.isPending = false;
  toastSuccess.mockReset();
  toastError.mockReset();
  lastListOrganization = undefined;
  lastListEnabled = undefined;
});

describe('Layouts de exportação — acesso', () => {
  it('o gerente da organização cai no AccessDenied e a lista nem é buscada', () => {
    authState.user = MANAGER;
    render(<ExportLayoutsPage />);

    expect(screen.getByText(/restritos ao administrador da organização/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(lastListEnabled).toBe(false);
  });

  it('o admin da organização vê a lista sem coluna nem filtro de organização', () => {
    authState.user = ORG_ADMIN;
    render(<ExportLayoutsPage />);

    const table = screen.getByRole('table');
    expect(within(table).getByRole('cell', { name: 'Domínio' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'v2' })).toBeInTheDocument();
    expect(
      within(table).queryByRole('columnheader', { name: 'Organização' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('combobox', { name: 'Filtrar por organização' }),
    ).not.toBeInTheDocument();
    // O admin nunca manda recorte: o servidor usa a organização da LINHA.
    expect(lastListOrganization).toBeNull();
  });
});

describe('Layouts de exportação — estados', () => {
  it('vazio: "Nenhum layout ainda" com a ação de criar', async () => {
    authState.user = ORG_ADMIN;
    listState.data = [];
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    expect(screen.getByText('Nenhum layout ainda')).toBeInTheDocument();
    const actions = screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' });
    // A do topo e a do estado vazio.
    expect(actions).toHaveLength(2);
    await ui.click(actions[1]!);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('carregando: sem estado vazio nem linhas', () => {
    authState.user = ORG_ADMIN;
    listState.data = undefined;
    listState.isLoading = true;
    render(<ExportLayoutsPage />);

    expect(screen.queryByText('Nenhum layout ainda')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ver versões/ })).not.toBeInTheDocument();
  });

  it('erro: mostra o userMessage do servidor e "Tentar novamente"', async () => {
    authState.user = ORG_ADMIN;
    listState.data = undefined;
    listState.isError = true;
    listState.error = new ApiError(500, {
      code: 'INTERNAL',
      message: 'boom',
      userMessage: 'Serviço indisponível no momento.',
    });
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    expect(screen.getByRole('alert')).toHaveTextContent('Serviço indisponível no momento.');
    await ui.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(listState.refetch).toHaveBeenCalled();
  });
});

describe('Layouts de exportação — plataforma', () => {
  it('mostra a coluna Organização com o nome da organização de cada layout', () => {
    authState.user = PLATFORM;
    listState.data = [layout(), layout({ id: 'lay-2', organizationId: PROSPECTA.id })];
    render(<ExportLayoutsPage />);

    const table = screen.getByRole('table');
    expect(within(table).getByRole('columnheader', { name: 'Organização' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'Hologram' })).toBeInTheDocument();
    expect(within(table).getByRole('cell', { name: 'Prospecta' })).toBeInTheDocument();
  });

  it('o filtro vai para a URL e dela para a consulta (server-side)', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    const { rerender } = render(<ExportLayoutsPage />);

    await ui.click(screen.getByRole('combobox', { name: 'Filtrar por organização' }));
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));
    expect(replaceMock).toHaveBeenCalledWith(
      expect.stringContaining(`organizacao=${PROSPECTA.id}`),
      expect.anything(),
    );

    currentSearch = `organizacao=${PROSPECTA.id}`;
    rerender(<ExportLayoutsPage />);
    expect(lastListOrganization).toBe(PROSPECTA.id);
  });

  it('criar exige a organização, esconde a suspensa e a manda NO PAYLOAD', async () => {
    authState.user = PLATFORM;
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' })[0]!);
    const dialog = await screen.findByRole('dialog');

    // Sem escolher: o zod barra antes do servidor.
    await ui.click(within(dialog).getByRole('button', { name: 'Criar layout' }));
    expect(await within(dialog).findByText('Escolha a organização de destino.')).toBeVisible();
    expect(createMock).not.toHaveBeenCalled();

    await ui.click(within(dialog).getByRole('combobox', { name: 'Organização do layout' }));
    expect(screen.queryByRole('option', { name: /Suspensa Ltda/ })).not.toBeInTheDocument();
    await ui.click(await screen.findByRole('option', { name: 'Prospecta' }));
    await ui.click(within(dialog).getByRole('button', { name: 'Criar layout' }));

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        templateKey: 'dominio_lancamentos_csv',
        name: null,
        organizationId: PROSPECTA.id,
      }),
    );
    expect(toastSuccess).toHaveBeenCalled();
  });
});

describe('Layouts de exportação — criar a partir do modelo (admin)', () => {
  it('uma ação só: sem seletor de organização, com o nome digitado', async () => {
    authState.user = ORG_ADMIN;
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' })[0]!);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(TEMPLATE.description)).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('combobox', { name: 'Organização do layout' }),
    ).not.toBeInTheDocument();

    await ui.type(within(dialog).getByLabelText('Nome do layout (opcional)'), 'Domínio mensal');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar layout' }));

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({
        templateKey: 'dominio_lancamentos_csv',
        name: 'Domínio mensal',
      }),
    );
  });

  it('nome repetido (409 LAYOUT_NOME_DUPLICADO) marca o campo, sem toast', async () => {
    authState.user = ORG_ADMIN;
    createMock.mockRejectedValue(
      new ApiError(409, {
        code: 'LAYOUT_NOME_DUPLICADO',
        message: 'dup',
        userMessage: 'Já existe um layout com este nome nesta organização.',
      }),
    );
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' })[0]!);
    const dialog = await screen.findByRole('dialog');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar layout' }));

    expect(
      await within(dialog).findByText('Já existe um layout com este nome nesta organização.'),
    ).toBeVisible();
    expect(toastError).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('outro erro tipado aparece pelo userMessage DENTRO do diálogo, nunca toast', async () => {
    authState.user = ORG_ADMIN;
    createMock.mockRejectedValue(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'org suspensa',
        userMessage: 'A organização está suspensa.',
      }),
    );
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' })[0]!);
    const dialog = await screen.findByRole('dialog');
    await ui.click(within(dialog).getByRole('button', { name: 'Criar layout' }));

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'A organização está suspensa.',
    );
    expect(toastError).not.toHaveBeenCalled();
  });

  it('sem o modelo carregado, o botão de criar fica desabilitado e a tela explica', async () => {
    authState.user = ORG_ADMIN;
    templatesState.data = undefined;
    templatesState.isError = true;
    templatesState.isSuccess = false;
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' })[0]!);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('alert')).toHaveTextContent(
      'Não foi possível carregar o modelo Domínio',
    );
    expect(within(dialog).getByRole('button', { name: 'Criar layout' })).toBeDisabled();
  });
});

describe('Layouts de exportação — diálogo do modelo com a criação em andamento', () => {
  // 86e3fyjbm (3): Esc ou clique fora fechavam o diálogo com o POST ainda sem
  // resposta, e o erro dele não tinha mais onde aparecer.
  it('sem criação em andamento, Esc fecha o diálogo', async () => {
    authState.user = ORG_ADMIN;
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' })[0]!);
    await screen.findByRole('dialog');
    await ui.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('com a criação em andamento, nem Esc nem clique fora fecham o diálogo', async () => {
    authState.user = ORG_ADMIN;
    createState.isPending = true;
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getAllByRole('button', { name: 'Criar a partir do modelo Domínio' })[0]!);
    const dialog = await screen.findByRole('dialog');
    await ui.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBe(dialog);

    // Clique fora: o Radix fecha no pointerdown fora do conteúdo.
    fireEvent.pointerDown(document.body);
    expect(screen.getByRole('dialog')).toBe(dialog);
  });
});

describe('Layouts de exportação — versões (só leitura)', () => {
  it('a gaveta lista as versões com os parâmetros legíveis e nenhum campo editável', async () => {
    authState.user = ORG_ADMIN;
    detailState.data = {
      ...layout(),
      versions: [
        {
          version: 2,
          definition: { ...DOMINIO_DEFINITION, hasHeader: true },
          author: { name: 'Admin', email: 'admin@hologram.com.br' },
          createdAt: '2026-09-28T12:00:00Z',
        },
        {
          version: 1,
          definition: DOMINIO_DEFINITION,
          author: { name: 'Admin', email: null },
          createdAt: '2026-09-20T12:00:00Z',
        },
      ],
    };
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(
      screen.getByRole('button', { name: 'Ver versões de Domínio: lançamentos contábeis (CSV)' }),
    );
    const sheet = await screen.findByRole('dialog');
    const v1 = within(sheet).getByRole('region', { name: 'Versão 1' });
    expect(within(v1).getByText('ponto e vírgula (;)')).toBeInTheDocument();
    expect(within(v1).getByText('Latin-1 (ISO-8859-1)')).toBeInTheDocument();
    expect(within(v1).getByText(/Windows \(CRLF\)/)).toBeInTheDocument();
    expect(within(v1).getByText('R$ 1.234,50')).toBeInTheDocument();
    expect(within(v1).getByText('Conta débito')).toBeInTheDocument();
    expect(within(v1).getByText('Não tem')).toBeInTheDocument();
    const v2 = within(sheet).getByRole('region', { name: /Versão 2/ });
    expect(within(v2).getByText('atual')).toBeInTheDocument();
    expect(within(v2).getByText('Sim, na primeira linha')).toBeInTheDocument();
    // Só leitura: nada editável na gaveta.
    expect(within(sheet).queryByRole('textbox')).not.toBeInTheDocument();
    expect(within(sheet).queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('definição com forma inesperada vira aviso, nunca "undefined" na tela', async () => {
    authState.user = ORG_ADMIN;
    detailState.data = {
      ...layout({ latestVersion: 1 }),
      versions: [
        {
          version: 1,
          definition: { colunas: [] },
          author: { name: 'Admin', email: null },
          createdAt: '2026-09-20T12:00:00Z',
        },
      ],
    };
    const ui = userEvent.setup();
    render(<ExportLayoutsPage />);

    await ui.click(screen.getByRole('button', { name: /Ver versões de/ }));
    const sheet = await screen.findByRole('dialog');
    expect(
      within(sheet).getByText('Não foi possível ler os parâmetros desta versão.'),
    ).toBeInTheDocument();
    expect(sheet).not.toHaveTextContent('undefined');
  });
});
