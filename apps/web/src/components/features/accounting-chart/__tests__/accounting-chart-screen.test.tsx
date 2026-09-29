/**
 * Testes da tela "Plano contábil" do cliente (FRONT 16.5 / R1 · R3 · R5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). No worktree do agent: `--cache=false`.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - importar e editar a associação ficam OCULTOS para `client_manager` e
 *     `client_operator` e VISÍVEIS para plataforma, admin e gerente (a matriz
 *     célula a célula está em `lib/__tests__/authz.test.ts`); a leitura é de todos;
 *   - sem plano: o estado vazio documenta o modelo (colunas e exemplo) e oferece
 *     Importar só a quem pode;
 *   - reimportação sobre plano existente exige confirmação ANTES de enviar,
 *     dizendo que as contas ausentes ficam inativas; sucesso mostra as
 *     contagens DA RESPOSTA;
 *   - recusa 422 vira ESTADO ramificado por `code` (linha × motivo em
 *     português, colunas nomeadas), nunca toast; só código desconhecido é toast;
 *   - seção da conta do banco: pendente em destaque, slot padrão rotulado, nome
 *     da conta Omie pelo cache de contas, e o 422 de conta não lançável como
 *     erro do CAMPO;
 *   - nome `[indecifrável]` como texto neutro; axe sem `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/clientes/c1/plano-contabil',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

interface ListState {
  data: AccountingChartListResponse | undefined;
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

const listState: ListState = {
  data: undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
};
/** Total do plano SEM filtro — o que a sonda de 1 linha responde. */
let planTotal = 0;
const importState = { mutateAsync: vi.fn(), isPending: false };
const sourceState = {
  data: [] as SourceAccountEntry[],
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const bindingState = { mutateAsync: vi.fn(), isPending: false };

vi.mock('@/hooks/use-client-accounting-chart', () => ({
  useAccountingChartList: (_clientId: string, params: ListAccountingChartParams) => {
    if (params.pageSize === 1) {
      return {
        data: {
          data: [],
          pagination: { page: 1, pageSize: 1, total: planTotal, totalPages: planTotal },
        },
        isLoading: false,
      };
    }
    return listState;
  },
  useImportAccountingChart: () => importState,
  useSourceAccounts: () => sourceState,
  useSetSourceAccountBinding: () => bindingState,
}));

const clientDetailState = {
  data: undefined as
    | { closed_at: string | null; accounts: { omie_conta_id: number; name: string }[] }
    | undefined,
};
vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => clientDetailState,
}));

const authState = { user: null as AuthenticatedUser | null };
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

import { AccountingChartScreen } from '@/components/features/accounting-chart/accounting-chart-screen';
import { ApiError } from '@/lib/api/client';
import type { ListAccountingChartParams } from '@/lib/api/client-accounting-chart';
import type {
  AccountingAccount,
  AccountingChartListResponse,
  AuthenticatedUser,
  SourceAccountEntry,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const ORG = '0706eeb5-9718-4d03-bcda-ef615789e6ac';
const TENANT = '11111111-1111-4111-8111-111111111111';

const platform: AuthenticatedUser = {
  id: 'p',
  email: 'suporte@hologram.com.br',
  name: 'Suporte',
  role: 'platform_admin',
  scope: 'platform',
  client_id: null,
  organization_id: null,
  organization_name: null,
};
const admin: AuthenticatedUser = {
  id: 'a',
  email: 'admin@hologram.com.br',
  name: 'Admin',
  role: 'admin',
  scope: 'system',
  client_id: null,
  organization_id: ORG,
  organization_name: 'Hologram',
};
const manager: AuthenticatedUser = { ...admin, id: 'm', role: 'manager' };
const clientManager: AuthenticatedUser = {
  id: 'cm',
  email: 'gerente@cliente.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: TENANT,
  organization_id: ORG,
  organization_name: 'Hologram',
};
const clientOperator: AuthenticatedUser = { ...clientManager, id: 'co', role: 'client_operator' };

function account(overrides: Partial<AccountingAccount> = {}): AccountingAccount {
  return {
    id: 'acc-649',
    code: '649',
    classification: '1.1.1.02.001',
    name: 'Banco conta movimento',
    nameResolved: true,
    type: 'analitica',
    active: true,
    postable: true,
    updatedAt: '2026-09-28T10:00:00Z',
    ...overrides,
  };
}

function withPlan(accounts: AccountingAccount[]) {
  planTotal = accounts.length;
  listState.data = {
    data: accounts,
    pagination: { page: 1, pageSize: 50, total: accounts.length, totalPages: 1 },
  };
}

function withoutPlan() {
  planTotal = 0;
  listState.data = { data: [], pagination: { page: 1, pageSize: 50, total: 0, totalPages: 0 } };
}

function refusal(code: string, details: Record<string, unknown> = {}): ApiError {
  return new ApiError(422, {
    code,
    message: 'x',
    userMessage: `Mensagem tipada de ${code}.`,
    details,
  });
}

function csvFile(name = 'plano.csv'): File {
  return new File(['codigo_reduzido;nome;tipo\n649;Banco;analitica\n'], name, {
    type: 'text/csv',
  });
}

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentSearch = '';
  authState.user = manager;
  clientDetailState.data = {
    closed_at: null,
    accounts: [{ omie_conta_id: 4455, name: 'Itaú — Conta movimento' }],
  };
  withPlan([
    account(),
    account({
      id: 'acc-10',
      code: '10',
      classification: '1.1',
      name: 'Ativo circulante',
      type: 'sintetica',
      postable: false,
    }),
  ]);
  listState.isLoading = false;
  listState.isFetching = false;
  listState.isError = false;
  importState.mutateAsync = vi.fn();
  importState.isPending = false;
  sourceState.data = [
    {
      sourceType: 'omie',
      sourceAccountId: '4455',
      isDefault: false,
      pending: false,
      bankAccount: {
        id: 'acc-649',
        code: '649',
        name: 'Banco conta movimento',
        nameResolved: true,
        postable: true,
      },
    },
    {
      sourceType: 'arquivo',
      sourceAccountId: null,
      isDefault: true,
      pending: true,
      bankAccount: null,
    },
  ];
  sourceState.isLoading = false;
  sourceState.isError = false;
  bindingState.mutateAsync = vi.fn();
  bindingState.isPending = false;
  toastSuccess.mockClear();
  toastError.mockClear();
});

describe('gating por capacidade (manage_client_accounting_chart)', () => {
  it.each([
    ['plataforma', platform],
    ['admin', admin],
    ['gerente da organização', manager],
  ])('%s vê Reimportar e as ações da conta do banco', (_label, user) => {
    authState.user = user;
    render(<AccountingChartScreen clientId="c1" />);

    expect(screen.getByRole('button', { name: /Reimportar planilha/ })).toBeInTheDocument();
    const section = screen.getByTestId('bank-accounts-section');
    expect(
      within(section).getByRole('button', { name: /Associar conta do banco/ }),
    ).toBeInTheDocument();
    expect(
      within(section).getByRole('button', { name: /Trocar conta do banco/ }),
    ).toBeInTheDocument();
  });

  it.each([
    ['gerente do cliente', clientManager],
    ['operador do cliente', clientOperator],
  ])('%s LÊ a lista e a conta do banco, sem importar nem editar', (_label, user) => {
    authState.user = user;
    render(<AccountingChartScreen clientId="c1" />);

    expect(screen.getByRole('cell', { name: 'Banco conta movimento' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /importar/i })).not.toBeInTheDocument();
    const section = screen.getByTestId('bank-accounts-section');
    expect(
      within(section).queryByRole('button', { name: /Associar|Trocar/ }),
    ).not.toBeInTheDocument();
    expect(
      within(section).getByText(
        /Peça a quem administra o plano contábil no escritório para associar/,
      ),
    ).toBeInTheDocument();
  });

  it.each([
    ['admin', admin],
    ['gerente da organização', manager],
  ])(
    'cliente encerrado (%s): importar some COM o motivo, a conta do banco diz que é o encerramento, sem Associar/Trocar',
    (_label, user) => {
      authState.user = user;
      clientDetailState.data = { closed_at: '2026-09-01T00:00:00Z', accounts: [] };
      render(<AccountingChartScreen clientId="c1" />);

      expect(screen.queryByRole('button', { name: /importar/i })).not.toBeInTheDocument();
      expect(
        screen.getByText(/Cliente encerrado: a importação está indisponível/),
      ).toBeInTheDocument();
      expect(screen.getByRole('cell', { name: 'Banco conta movimento' })).toBeInTheDocument();

      const section = screen.getByTestId('bank-accounts-section');
      expect(
        within(section).queryByRole('button', { name: /Associar|Trocar/ }),
      ).not.toBeInTheDocument();
      expect(within(section).getByTestId('bank-accounts-pending')).toHaveTextContent(
        'Cliente encerrado: a associação não pode mais ser alterada.',
      );
      expect(within(section).queryByText(/Peça a/)).not.toBeInTheDocument();
    },
  );
});

describe('lista', () => {
  it('colunas, badges por token e nome indecifrável como texto neutro', async () => {
    withPlan([
      account(),
      account({ id: 'x', code: '700', name: '[indecifrável]', nameResolved: false, active: false }),
    ]);
    const { container } = render(<AccountingChartScreen clientId="c1" />);

    const table = screen.getAllByRole('table')[0]!;
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((th) => th.textContent);
    expect(headers).toEqual(['Código', 'Classificação', 'Nome', 'Tipo', 'Situação']);
    expect(screen.getByText('Nome indisponível (indecifrável)')).toBeInTheDocument();
    expect(screen.queryByText('[indecifrável]')).not.toBeInTheDocument();
    // Badge com prefixo sr-only: o leitor de tela ouve "Situação: Inativa".
    expect(screen.getAllByText('Situação:', { exact: false }).length).toBeGreaterThan(0);
    await assertNoA11yViolations(container);
  });

  it('a busca é só por código (o nome é cifrado) e os filtros vêm da URL', () => {
    currentSearch = 'type=sintetica&status=inativa&code=1';
    render(<AccountingChartScreen clientId="c1" />);

    expect(screen.getByLabelText('Buscar por código')).toHaveValue('1');
    expect(screen.queryByLabelText(/nome/i)).not.toBeInTheDocument();
    const tipo = screen.getByRole('group', { name: 'Tipo' });
    expect(within(tipo).getByRole('button', { name: 'Sintéticas' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('erro de carga: mensagem e "Tentar novamente", nunca tela branca', async () => {
    listState.isError = true;
    listState.error = new ApiError(500, { code: 'X', message: 'x', userMessage: 'Falhou agora.' });
    render(<AccountingChartScreen clientId="c1" />);

    expect(screen.getByRole('alert')).toHaveTextContent('Falhou agora.');
    await userEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(listState.refetch).toHaveBeenCalled();
  });
});

describe('sem plano', () => {
  it('estado vazio documenta o modelo e oferece Importar a quem pode', () => {
    withoutPlan();
    render(<AccountingChartScreen clientId="c1" />);

    const empty = screen.getByTestId('accounting-chart-empty');
    expect(
      within(empty).getByText('Este cliente ainda não tem plano contábil'),
    ).toBeInTheDocument();
    for (const column of ['codigo_reduzido', 'nome', 'tipo', 'classificacao']) {
      expect(within(empty).getAllByText(column).length).toBeGreaterThan(0);
    }
    expect(within(empty).getByText(/649;Banco conta movimento;analitica/)).toBeInTheDocument();
    // Um botão só na tela: sem plano ele mora no estado vazio.
    expect(screen.getAllByRole('button', { name: 'Importar planilha' })).toHaveLength(1);
  });

  it('operador vê o modelo, sem botão', () => {
    withoutPlan();
    authState.user = clientOperator;
    render(<AccountingChartScreen clientId="c1" />);

    expect(screen.getByText(/Quando alguém do escritório importar o plano/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /importar/i })).not.toBeInTheDocument();
  });
});

describe('importação', () => {
  async function openAndPick(buttonName: RegExp, file = csvFile()) {
    // `applyAccept: false`: o `accept` do input é conveniência do navegador; o
    // teste da extensão precisa que o arquivo errado CHEGUE ao formulário.
    const user = userEvent.setup({ applyAccept: false });
    await user.click(screen.getByRole('button', { name: buttonName }));
    const dialog = await screen.findByRole('dialog');
    await user.upload(within(dialog).getByLabelText('Planilha (.csv ou .xlsx)'), file);
    return { user, dialog };
  }

  it('primeira importação envia direto e mostra as contagens DA RESPOSTA', async () => {
    withoutPlan();
    importState.mutateAsync = vi
      .fn()
      .mockResolvedValue({ contas: 32, contasNovas: 32, contasInativadas: 0 });
    render(<AccountingChartScreen clientId="c1" />);

    const { user, dialog } = await openAndPick(/Importar planilha/);
    expect(
      within(dialog).queryByTestId('accounting-chart-reimport-warning'),
    ).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    await waitFor(() => expect(importState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(toastSuccess).toHaveBeenCalledWith(
      'Plano contábil importado: 32 contas, 32 novas, 0 inativadas.',
    );
    const success = await screen.findByTestId('accounting-chart-import-success');
    expect(within(success).getByText('Contas na planilha').nextSibling).toHaveTextContent('32');
  });

  it('reimportação exige confirmação ANTES de enviar, dizendo que as ausentes ficam inativas', async () => {
    importState.mutateAsync = vi
      .fn()
      .mockResolvedValue({ contas: 30, contasNovas: 1, contasInativadas: 3 });
    render(<AccountingChartScreen clientId="c1" />);

    const { user, dialog } = await openAndPick(/Reimportar planilha/);
    expect(within(dialog).getByTestId('accounting-chart-reimport-warning')).toHaveTextContent(
      /que NÃO estiverem na planilha ficarão inativas/,
    );
    await user.click(within(dialog).getByRole('button', { name: 'Reimportar' }));

    // 1º clique: só troca para o passo de confirmação — nada foi enviado.
    expect(importState.mutateAsync).not.toHaveBeenCalled();
    expect(within(dialog).getByRole('alert')).toHaveTextContent(/ficarão inativas/);
    await user.click(within(dialog).getByRole('button', { name: 'Confirmar reimportação' }));

    await waitFor(() => expect(importState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(toastSuccess).toHaveBeenCalledWith(
      'Plano contábil importado: 30 contas, 1 nova, 3 inativadas.',
    );
  });

  it('"Voltar" no passo de confirmação não envia nada', async () => {
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Reimportar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Reimportar' }));
    await user.click(within(dialog).getByRole('button', { name: 'Voltar' }));

    expect(within(dialog).getByRole('button', { name: 'Reimportar' })).toBeInTheDocument();
    expect(importState.mutateAsync).not.toHaveBeenCalled();
  });

  it('arquivo com extensão fora do par é recusado no cliente, sem request', async () => {
    withoutPlan();
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(
      /Importar planilha/,
      new File(['x'], 'plano.pdf', { type: 'application/pdf' }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    expect(await within(dialog).findByText(/Envie a planilha em CSV/)).toBeInTheDocument();
    expect(importState.mutateAsync).not.toHaveBeenCalled();
  });

  it('LINHAS_INVALIDAS vira tabela linha × motivo em português, sem toast', async () => {
    withoutPlan();
    importState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('LINHAS_INVALIDAS', {
        lines: [
          { line: 3, reason: 'codigo_repetido' },
          { line: 7, reason: 'tipo_invalido' },
        ],
        total: 60,
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Importar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    const notice = await within(dialog).findByRole('alert');
    expect(notice).toHaveAttribute('data-refusal-code', 'LINHAS_INVALIDAS');
    expect(within(notice).getByText('Código reduzido repetido na planilha')).toBeInTheDocument();
    expect(
      within(notice).getByText('Tipo diferente de "analitica" ou "sintetica"'),
    ).toBeInTheDocument();
    expect(within(notice).getByTestId('accounting-invalid-lines-count')).toHaveTextContent(
      'Mostrando 2 de 60 linhas inválidas.',
    );
    expect(toastError).not.toHaveBeenCalled();
  });

  it('CABECALHO_DIVERGENTE nomeia o que falta, o que sobra e o que repete', async () => {
    withoutPlan();
    importState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('CABECALHO_DIVERGENTE', {
        missingColumns: ['tipo'],
        unexpectedColumns: ['saldo'],
        repeatedColumns: [],
        foundColumns: ['codigo_reduzido', 'nome', 'saldo'],
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Importar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    const notice = await within(dialog).findByRole('alert');
    expect(
      within(notice).getByRole('list', { name: 'Colunas obrigatórias que faltam' }),
    ).toHaveTextContent('tipo');
    expect(within(notice).getByRole('list', { name: 'Colunas fora do modelo' })).toHaveTextContent(
      'saldo',
    );
    expect(
      within(notice).queryByRole('list', { name: 'Colunas repetidas' }),
    ).not.toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('CABECALHO_DIVERGENTE por coluna REPETIDA lista cada coluna encontrada, inclusive a repetida', async () => {
    withoutPlan();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    importState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('CABECALHO_DIVERGENTE', {
        missingColumns: [],
        unexpectedColumns: [],
        repeatedColumns: ['nome'],
        // O backend devolve as colunas CRUAS: o nome repetido aparece duas vezes.
        foundColumns: ['codigo_reduzido', 'nome', 'nome', 'tipo'],
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Importar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    const notice = await within(dialog).findByRole('alert');
    expect(within(notice).getByRole('list', { name: 'Colunas repetidas' })).toHaveTextContent(
      'nome',
    );
    const found = within(notice).getByRole('list', { name: 'Colunas encontradas na planilha' });
    expect(
      within(found)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['codigo_reduzido', 'nome', 'nome', 'tipo']);
    // Chave duplicada o React só denuncia no console; nenhum aviso dele aqui.
    const duplicateKeyWarnings = consoleError.mock.calls.filter((call) =>
      call.some((arg) => String(arg).includes('same key')),
    );
    expect(duplicateKeyWarnings).toEqual([]);
    consoleError.mockRestore();
  });

  it('ARQUIVO_INVALIDO sem contas explica o caso; código desconhecido é o único toast', async () => {
    withoutPlan();
    importState.mutateAsync = vi
      .fn()
      .mockRejectedValueOnce(refusal('ARQUIVO_INVALIDO', { reason: 'sem_contas' }))
      .mockRejectedValueOnce(
        new ApiError(500, {
          code: 'INTERNAL_ERROR',
          message: 'x',
          userMessage: 'Erro inesperado.',
        }),
      );
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Importar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));
    expect(
      await within(dialog).findByText(/não tem nenhuma conta abaixo do cabeçalho/),
    ).toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Erro inesperado.'));
  });
});

describe('conta do banco', () => {
  it('lista a conta Omie pelo nome do cache, o slot padrão rotulado e a pendente em destaque', async () => {
    const { container } = render(<AccountingChartScreen clientId="c1" />);
    const section = screen.getByTestId('bank-accounts-section');

    expect(within(section).getByText('Itaú — Conta movimento')).toBeInTheDocument();
    expect(within(section).getByText('Omie · conta 4455')).toBeInTheDocument();
    expect(
      within(section).getByText('Conta padrão (arquivo sem coluna de conta)'),
    ).toBeInTheDocument();
    expect(within(section).getByText('Pendente')).toBeInTheDocument();
    expect(within(section).getByTestId('bank-accounts-pending')).toHaveTextContent(
      '1 conta de origem sem conta do banco.',
    );
    expect(within(section).getByText('649 — Banco conta movimento')).toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('sem nome no cache, a conta de origem aparece pelo identificador', () => {
    clientDetailState.data = { closed_at: null, accounts: [] };
    render(<AccountingChartScreen clientId="c1" />);
    expect(
      within(screen.getByTestId('bank-accounts-section')).getByText('4455'),
    ).toBeInTheDocument();
  });

  it('o 422 de conta não lançável aparece NO CAMPO, não em toast', async () => {
    sourceState.data = [
      {
        sourceType: 'omie',
        sourceAccountId: '4455',
        isDefault: false,
        pending: false,
        // Associada a uma conta que depois foi inativada: salvar de novo é permitido
        // e é o servidor quem recusa.
        bankAccount: {
          id: 'acc-9',
          code: '9',
          name: 'Antiga',
          nameResolved: true,
          postable: false,
        },
      },
    ];
    bindingState.mutateAsync = vi
      .fn()
      .mockRejectedValue(
        refusal('CONTA_CONTABIL_NAO_LANCAVEL', { reason: 'inativa', accountId: 'acc-9' }),
      );
    const user = userEvent.setup();
    render(<AccountingChartScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: /Trocar conta do banco/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/deixou de ser analítica e ativa/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Salvar' }));

    const message = await within(dialog).findByText(/Esta conta está inativa/);
    expect(message).toHaveAttribute('role', 'alert');
    const trigger = within(dialog).getByRole('button', { name: /Conta contábil do banco/ });
    expect(trigger).toHaveAttribute('aria-invalid', 'true');
    expect(trigger.getAttribute('aria-describedby')).toContain(message.id);
    expect(toastError).not.toHaveBeenCalled();
    expect(bindingState.mutateAsync).toHaveBeenCalledWith({
      sourceType: 'omie',
      sourceAccountId: '4455',
      accountingAccountId: 'acc-9',
    });
  });

  it('sem plano, não há ação de associar (não há conta para escolher)', () => {
    withoutPlan();
    render(<AccountingChartScreen clientId="c1" />);
    const section = screen.getByTestId('bank-accounts-section');
    expect(within(section).queryByRole('button', { name: /Associar/ })).not.toBeInTheDocument();
    expect(
      within(section).getByText(/Importe o plano contábil para poder associar/),
    ).toBeInTheDocument();
  });

  it.each([
    ['gerente do cliente', clientManager],
    ['operador do cliente', clientOperator],
  ])('sem plano, %s não recebe a instrução de importar (a matriz nega)', (_label, user) => {
    authState.user = user;
    withoutPlan();
    render(<AccountingChartScreen clientId="c1" />);
    const section = screen.getByTestId('bank-accounts-section');
    expect(within(section).queryByRole('button', { name: /Associar/ })).not.toBeInTheDocument();
    expect(within(section).queryByText(/Importe/)).not.toBeInTheDocument();
    expect(within(section).getByTestId('bank-accounts-pending')).toHaveTextContent(
      'O plano contábil do cliente ainda não foi importado pelo escritório.',
    );
  });
});
