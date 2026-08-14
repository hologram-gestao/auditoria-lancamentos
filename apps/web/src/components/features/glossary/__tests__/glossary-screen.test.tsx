/**
 * Testes da tela "Glossário" do cliente (FRONT 06.6 / R2).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Não é o `web_a11y` — aquele roda só
 * `e2e/a11y-mocked.spec.ts`, onde ficam os cenários que precisam de CSS
 * computado e de browser real.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - lista paginada com tipo/nome/descrição, e paginação lida da URL;
 *   - estados loading, vazio (com CTA real) e erro (com "Tentar novamente");
 *   - gerente do cliente cria/edita/remove; **operador vê a lista e não vê
 *     NENHUMA ação de escrita** (oculta, não desabilitada);
 *   - gaveta com validação espelhando os limites do servidor e payload **sem**
 *     `client_id`, com `code`/`description` vazios virando `null`;
 *   - remoção passa por `role="alertdialog"`;
 *   - entrada indecifrável se comunica por badge, não só pelo texto;
 *   - axe-core sem violações `critical`/`serious`.
 *
 * Os componentes Radix REAIS são usados (nada de stub de `ui/select`/`ui/sheet`):
 * é o markup real que precisa passar no axe. jsdom não implementa as APIs de
 * ponteiro que o Radix consulta, então o `beforeAll` abaixo as preenche.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/clientes/c1/glossario',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const listState = {
  data: undefined as
    | { data: { entries: GlossaryEntry[]; version: number }; pagination: Record<string, number> }
    | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

/** Último `params` que a tela mandou para o hook — prova o "URL → request". */
let lastQueryParams: ListGlossaryParams | undefined;
const createMock = vi.fn();
const updateMock = vi.fn();
const deleteMock = vi.fn();

vi.mock('@/hooks/use-glossary', () => ({
  useGlossaryList: (_clientId: string, params: ListGlossaryParams) => {
    lastQueryParams = params;
    return listState;
  },
  useCreateGlossaryEntry: () => ({ mutateAsync: createMock, isPending: false }),
  useUpdateGlossaryEntry: () => ({ mutateAsync: updateMock, isPending: false }),
  useDeleteGlossaryEntry: () => ({ mutateAsync: deleteMock, isPending: false }),
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import { GlossaryScreen } from '@/components/features/glossary/glossary-screen';
import type { ListGlossaryParams } from '@/lib/api/glossary';
import type { AuthenticatedUser, GlossaryEntry } from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const CLIENT_ID = 'c1';

function entry(over: Partial<GlossaryEntry> = {}): GlossaryEntry {
  return {
    id: 'g1',
    kind: 'categoria',
    code: '3.1.02',
    name: 'Taxas bancárias',
    description: 'Tarifas cobradas pelo banco, nunca classificadas como juros.',
    decryptFailed: false,
    ...over,
  };
}

function actor(over: Partial<AuthenticatedUser> = {}): AuthenticatedUser {
  return {
    id: 'me',
    email: 'gerente@cliente-exemplo.com.br',
    name: 'Gerente do Cliente',
    role: 'client_manager',
    scope: 'client',
    client_id: CLIENT_ID,
    ...over,
  };
}

function setList(rows: GlossaryEntry[], total = rows.length, version = 3) {
  listState.data = {
    data: { entries: rows, version },
    pagination: { page: 1, pageSize: 20, total, totalPages: Math.max(1, Math.ceil(total / 20)) },
  };
}

beforeAll(() => {
  // Radix consulta APIs de ponteiro que o jsdom não implementa.
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  currentSearch = '';
  authState.user = actor();
  listState.isLoading = false;
  listState.isFetching = false;
  listState.isError = false;
  listState.error = null;
  setList([entry()]);
});

describe('GlossaryScreen — área rolável (86e2uca1d)', () => {
  it('a tabela é o scroller vertical da área, e a barra fica fora dela', () => {
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    const region = screen.getByRole('region', { name: 'Glossário do cliente (rolável)' });
    expect(region).toHaveClass('overflow-auto', 'min-h-0');
    expect(region).not.toContainElement(
      screen.getByRole('navigation', { name: 'Paginação de entradas' }),
    );
  });
});

describe('GlossaryScreen — lista', () => {
  it('mostra tipo, nome, código e descrição de cada entrada', () => {
    setList([
      entry({ id: 'g1', kind: 'categoria', name: 'Taxas bancárias' }),
      entry({
        id: 'g2',
        kind: 'regra',
        code: null,
        name: 'IOF nunca é juros',
        description: 'Classificar IOF em despesa financeira própria.',
      }),
      entry({ id: 'g3', kind: 'fornecedor', code: null, name: 'Moinho Prado', description: null }),
    ]);
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    const taxas = screen.getByRole('row', { name: /Taxas bancárias/ });
    expect(within(taxas).getByText('Categoria')).toBeInTheDocument();
    expect(within(taxas).getByText(/3\.1\.02/)).toBeInTheDocument();

    const regra = screen.getByRole('row', { name: /IOF nunca é juros/ });
    expect(within(regra).getByText('Regra de auditoria')).toBeInTheDocument();

    const fornecedor = screen.getByRole('row', { name: /Moinho Prado/ });
    expect(within(fornecedor).getByText('Fornecedor típico')).toBeInTheDocument();
    // Sem descrição, a célula mostra um travessão — nunca fica em branco.
    expect(within(fornecedor).getByText('—')).toBeInTheDocument();
  });

  it('entrada indecifrável se comunica por BADGE, não só pelo texto do backend', () => {
    setList([entry({ id: 'g9', name: '[indecifrável]', decryptFailed: true })]);
    render(<GlossaryScreen clientId={CLIENT_ID} />);
    expect(screen.getByText('Indecifrável')).toBeInTheDocument();
  });

  it('lê a paginação da URL e a repassa ao request', () => {
    currentSearch = 'page=3&pageSize=50';
    render(<GlossaryScreen clientId={CLIENT_ID} />);
    expect(lastQueryParams).toMatchObject({ page: 3, pageSize: 50 });
  });

  it('empty-state traz um botão real para o primeiro cadastro', async () => {
    const ui = userEvent.setup();
    setList([], 0);
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    expect(screen.getByText('Nenhuma entrada no glossário deste cliente')).toBeInTheDocument();
    const cta = screen.getAllByRole('button', { name: 'Nova entrada' });
    await ui.click(cta[cta.length - 1] as HTMLElement);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('erro mostra mensagem amigável e "Tentar novamente"', async () => {
    const ui = userEvent.setup();
    listState.isError = true;
    listState.error = new Error('boom interno do framework');
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Não foi possível carregar o glossário');
    // Nunca vaza `error.message` cru.
    expect(alert).not.toHaveTextContent('boom interno do framework');
    await ui.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(listState.refetch).toHaveBeenCalled();
  });

  it('mostra skeleton enquanto carrega, sem empty-state prematuro', () => {
    listState.isLoading = true;
    listState.data = undefined;
    render(<GlossaryScreen clientId={CLIENT_ID} />);
    expect(
      screen.queryByText('Nenhuma entrada no glossário deste cliente'),
    ).not.toBeInTheDocument();
  });
});

describe('GlossaryScreen — gating por papel (matriz de lib/authz)', () => {
  it('operador do cliente LÊ a lista e não vê nenhuma ação de escrita', () => {
    authState.user = actor({ id: 'op', role: 'client_operator' });
    setList([entry({ name: 'Taxas bancárias' })]);
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    // A rota continua acessível em leitura — nada de AccessDenied aqui.
    expect(screen.getByRole('heading', { name: 'Glossário' })).toBeInTheDocument();
    expect(screen.getByText('Taxas bancárias')).toBeInTheDocument();

    // Ações OCULTAS (não desabilitadas) e coluna "Ações" ausente.
    expect(screen.queryByRole('button', { name: 'Nova entrada' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Editar Taxas bancárias' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Remover Taxas bancárias' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Ações' })).not.toBeInTheDocument();
  });

  it('operador vê um empty-state sem CTA de cadastro', () => {
    authState.user = actor({ id: 'op', role: 'client_operator' });
    setList([], 0);
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    expect(screen.getByText('Este cliente ainda não tem glossário')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Nova entrada' })).not.toBeInTheDocument();
  });

  it('gerente do cliente mantém o glossário do próprio tenant', () => {
    render(<GlossaryScreen clientId={CLIENT_ID} />);
    expect(screen.getByRole('button', { name: 'Nova entrada' })).toBeInTheDocument();
  });

  it('admin e gerente do SISTEMA também mantêm (linha da matriz do backend)', () => {
    for (const role of ['admin', 'manager'] as const) {
      authState.user = actor({ id: role, role, scope: 'system', client_id: null });
      const view = render(<GlossaryScreen clientId={CLIENT_ID} />);
      expect(screen.getByRole('button', { name: 'Nova entrada' })).toBeInTheDocument();
      view.unmount();
    }
  });
});

describe('GlossaryScreen — gaveta de criação/edição', () => {
  async function openDrawer() {
    const ui = userEvent.setup();
    render(<GlossaryScreen clientId={CLIENT_ID} />);
    await ui.click(screen.getByRole('button', { name: 'Nova entrada' }));
    await screen.findByRole('dialog');
    return ui;
  }

  it('oferece exatamente os três tipos do contrato', async () => {
    const ui = await openDrawer();
    await ui.click(screen.getByRole('combobox', { name: /Tipo/ }));

    const options = await screen.findAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([
      'Categoria',
      'Fornecedor típico',
      'Regra de auditoria',
    ]);
  });

  it('exige o nome (só-espaços é vazio, como no servidor)', async () => {
    const ui = await openDrawer();
    const dialog = screen.getByRole('dialog');

    await ui.type(within(dialog).getByLabelText('Nome'), '   ');
    await ui.click(within(dialog).getByRole('button', { name: 'Adicionar entrada' }));

    expect(await within(dialog).findByText('Informe o nome.')).toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it('recusa nome acima do limite do servidor (120)', async () => {
    const ui = await openDrawer();
    const dialog = screen.getByRole('dialog');

    await ui.type(within(dialog).getByLabelText('Nome'), 'x'.repeat(121));
    await ui.click(within(dialog).getByRole('button', { name: 'Adicionar entrada' }));

    expect(await within(dialog).findByText('Nome muito longo (máx. 120).')).toBeInTheDocument();
    expect(createMock).not.toHaveBeenCalled();
  });

  it('cria mandando null nos opcionais vazios e SEM client_id no body', async () => {
    const ui = await openDrawer();
    const dialog = screen.getByRole('dialog');

    await ui.type(within(dialog).getByLabelText('Nome'), 'Taxas bancárias');
    await ui.click(within(dialog).getByRole('button', { name: 'Adicionar entrada' }));

    await waitFor(() => expect(createMock).toHaveBeenCalledTimes(1));
    const payload = createMock.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(payload).toEqual({
      kind: 'categoria',
      name: 'Taxas bancárias',
      code: null,
      description: null,
    });
    expect(payload).not.toHaveProperty('client_id');
    expect(payload).not.toHaveProperty('id');
  });

  it('editar pré-preenche e envia o registro COMPLETO (o PATCH substitui)', async () => {
    const ui = userEvent.setup();
    setList([entry({ id: 'g1', name: 'Taxas bancárias', code: '3.1.02' })]);
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    await ui.click(screen.getByRole('button', { name: 'Editar Taxas bancárias' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Nome')).toHaveValue('Taxas bancárias');
    expect(within(dialog).getByLabelText('Código (opcional)')).toHaveValue('3.1.02');

    await ui.click(within(dialog).getByRole('button', { name: 'Salvar alterações' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    expect(updateMock.mock.calls[0]?.[0]).toEqual({
      kind: 'categoria',
      name: 'Taxas bancárias',
      code: '3.1.02',
      description: 'Tarifas cobradas pelo banco, nunca classificadas como juros.',
    });
  });

  it('Cancelar fica à ESQUERDA da ação primária no rodapé da gaveta', async () => {
    await openDrawer();
    const dialog = screen.getByRole('dialog');
    const cancel = within(dialog).getByRole('button', { name: 'Cancelar' });
    const submit = within(dialog).getByRole('button', { name: 'Adicionar entrada' });
    expect(cancel.compareDocumentPosition(submit) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('GlossaryScreen — remoção', () => {
  it('pede confirmação em alertdialog antes de remover', async () => {
    const ui = userEvent.setup();
    setList([entry({ id: 'g1', name: 'Taxas bancárias' })]);
    render(<GlossaryScreen clientId={CLIENT_ID} />);

    await ui.click(screen.getByRole('button', { name: 'Remover Taxas bancárias' }));

    const confirm = await screen.findByRole('alertdialog');
    expect(deleteMock).not.toHaveBeenCalled();
    await ui.click(within(confirm).getByRole('button', { name: 'Remover' }));
    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith({ entryId: 'g1' }));
  });
});

describe('GlossaryScreen — acessibilidade', () => {
  it('não tem violações critical/serious na lista', async () => {
    setList([
      entry(),
      entry({ id: 'g2', kind: 'regra', name: 'IOF nunca é juros', code: null }),
      entry({ id: 'g3', kind: 'fornecedor', name: 'Moinho Prado', decryptFailed: true }),
    ]);
    const { container } = render(<GlossaryScreen clientId={CLIENT_ID} />);
    await assertNoA11yViolations(container);
  });

  it('não tem violações critical/serious no estado vazio', async () => {
    setList([], 0);
    const { container } = render(<GlossaryScreen clientId={CLIENT_ID} />);
    await assertNoA11yViolations(container);
  });

  it('não tem violações critical/serious na leitura do operador', async () => {
    authState.user = actor({ id: 'op', role: 'client_operator' });
    setList([entry(), entry({ id: 'g2', kind: 'regra', name: 'IOF nunca é juros' })]);
    const { container } = render(<GlossaryScreen clientId={CLIENT_ID} />);
    await assertNoA11yViolations(container);
  });

  it('não tem violações critical/serious com a gaveta aberta', async () => {
    const ui = userEvent.setup();
    render(<GlossaryScreen clientId={CLIENT_ID} />);
    await ui.click(screen.getByRole('button', { name: 'Nova entrada' }));
    await screen.findByRole('dialog');
    // A gaveta vai para um portal fora do container do render — o axe precisa
    // olhar o `document.body` inteiro, senão mede o lado errado da página.
    await assertNoA11yViolations(document.body);
  });

  it('não tem violações critical/serious na confirmação de remoção', async () => {
    const ui = userEvent.setup();
    setList([entry({ id: 'g1', name: 'Taxas bancárias' })]);
    render(<GlossaryScreen clientId={CLIENT_ID} />);
    await ui.click(screen.getByRole('button', { name: 'Remover Taxas bancárias' }));
    await screen.findByRole('alertdialog');
    await assertNoA11yViolations(document.body);
  });
});
