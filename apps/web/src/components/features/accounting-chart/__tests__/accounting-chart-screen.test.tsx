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
/** A sonda ainda não respondeu (a lista pode ter chegado antes). */
let probeLoading = false;
const importState = { mutateAsync: vi.fn(), isPending: false };
const sourceState = {
  data: [] as SourceAccountEntry[],
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const bindingState = { mutateAsync: vi.fn(), isPending: false };
const createAccountState = { mutateAsync: vi.fn(), isPending: false };
const updateAccountState = { mutateAsync: vi.fn(), isPending: false };

vi.mock('@/hooks/use-client-accounting-chart', () => ({
  useAccountingChartList: (_clientId: string, params: ListAccountingChartParams) => {
    if (params.pageSize === 1) {
      if (probeLoading) return { data: undefined, isLoading: true };
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
  useCreateAccountingAccount: () => createAccountState,
  useUpdateAccountingAccount: () => updateAccountState,
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
  probeLoading = false;
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
  createAccountState.mutateAsync = vi.fn();
  updateAccountState.mutateAsync = vi.fn();
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
    // "Ações" (o Editar da linha, 86e3nb816) só para quem gere o plano — o padrão é o gerente.
    expect(headers).toEqual(['Código', 'Classificação', 'Nome', 'Tipo', 'Situação', 'Ações']);
    expect(screen.getByText('Nome indisponível (indecifrável)')).toBeInTheDocument();
    expect(screen.queryByText('[indecifrável]')).not.toBeInTheDocument();
    // Badge com prefixo sr-only: o leitor de tela ouve "Situação: Inativa".
    expect(screen.getAllByText('Situação:', { exact: false }).length).toBeGreaterThan(0);
    await assertNoA11yViolations(container);
  });

  it('a hierarquia aparece: recuo do nome pelo grau e sintética em peso maior (86e3n70p9)', () => {
    // A ORDEM vem do servidor (sort_key); a tela só mostra o grau e o peso. A lista
    // chega como o servidor a devolve e é renderizada NESSA ordem, sem reordenar.
    withPlan([
      account({ id: 'g0', code: '1', classification: '1', name: 'Ativo', type: 'sintetica' }),
      account({
        id: 'g1',
        code: '3',
        classification: '1.1',
        name: 'Circulante',
        type: 'sintetica',
      }),
      account({ id: 'g4', code: '649', classification: '1.1.1.02.001', name: 'Banco' }),
      account({ id: 'sem', code: '100', classification: null, name: 'Sem classificação' }),
      account({ id: 'fundo', code: '7', classification: '1.2.3.4.5.6.7.8', name: 'Muito fundo' }),
    ]);
    render(<AccountingChartScreen clientId="c1" />);

    const table = screen.getAllByRole('table')[0]!;
    const names = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[2]!.querySelector('[data-depth]')!);
    expect(names.map((n) => n.textContent)).toEqual([
      'Ativo',
      'Circulante',
      'Banco',
      'Sem classificação',
      'Muito fundo',
    ]);
    expect(names.map((n) => n.getAttribute('data-depth'))).toEqual(['0', '1', '4', '0', '7']);
    expect(names[0]).toHaveClass('pl-0', 'font-semibold');
    expect(names[1]).toHaveClass('pl-4', 'font-semibold');
    expect(names[2]).toHaveClass('pl-16');
    expect(names[2]).not.toHaveClass('font-semibold');
    expect(names[3]).toHaveClass('pl-0');
    expect(names[3]).not.toHaveClass('font-semibold');
    // Acima do teto, recua como a mais funda (nunca some da tela).
    expect(names[4]).toHaveClass('pl-20');
    // As demais colunas não mudam: código, classificação, tipo e situação seguem iguais.
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((th) => th.textContent);
    // "Ações" (o Editar da linha, 86e3nb816) só para quem gere o plano — o padrão é o gerente.
    expect(headers).toEqual(['Código', 'Classificação', 'Nome', 'Tipo', 'Situação', 'Ações']);
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
    // 86e3gkd7y e 86e3n70p6: o export nativo do Domínio também entra, e o modelo diz isso.
    expect(
      within(empty).getByText(/plano de contas exportado do Domínio, em \.xls ou \.xlsx, também/),
    ).toBeInTheDocument();
    // Um botão só na tela: sem plano ele mora no estado vazio.
    expect(screen.getAllByRole('button', { name: 'Importar planilha' })).toHaveLength(1);
  });

  it('lista vazia chega ANTES da sonda: continua um botão só', () => {
    // Corrida real do e2e (hologram, 390px): com a sonda pendente, `noPlan`
    // ainda é falso e o topo repetia o botão do estado vazio.
    withoutPlan();
    probeLoading = true;
    render(<AccountingChartScreen clientId="c1" />);

    expect(screen.getByTestId('accounting-chart-empty')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Importar planilha' })).toHaveLength(1);
  });

  /**
   * 86e3gkd80: a tela é do padrão em que a PÁGINA rola, com e sem plano. A raiz
   * declara `data-page-scroll` (é o que solta a altura fixa do `ClientShell`),
   * a tabela do plano usa o par `pageScroll`/`stickyHeader="page"` e a seção da
   * conta do banco vem depois, no fluxo. O jsdom não faz layout: a borda final
   * alcançável por rolagem é medida no e2e.
   */
  it.each([
    ['sem plano', () => withoutPlan()],
    ['com plano', () => withPlan([account()])],
  ])('%s: a página rola, e a raiz declara o padrão', (_label, arrange) => {
    arrange();
    render(<AccountingChartScreen clientId="c1" />);

    const root = screen
      .getByRole('heading', { level: 1, name: 'Plano contábil' })
      .closest('section');
    expect(root).toHaveAttribute('data-page-scroll');
    expect(root).not.toHaveClass('h-full', 'min-h-0', 'flex-1');
    const region = screen.getByRole('region', { name: 'Contas do plano contábil (rolável)' });
    expect(region).toHaveClass('xl:overflow-clip');
    expect(region.parentElement).toHaveClass('overflow-clip');
    const bank = screen.getByTestId('bank-accounts-section');
    expect(root).toContainElement(bank);
    expect(region.compareDocumentPosition(bank) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
    await user.upload(within(dialog).getByLabelText('Planilha (.csv, .xlsx ou .xls)'), file);
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

  it('a gaveta diz que o plano exportado do Domínio, em .xls ou .xlsx, também é aceito', async () => {
    withoutPlan();
    render(<AccountingChartScreen clientId="c1" />);
    const { dialog } = await openAndPick(/Importar planilha/);

    const model = within(dialog).getByRole('region', { name: 'Modelo da planilha' });
    expect(
      within(model).getByText(
        'O plano de contas exportado do Domínio, em .xls ou .xlsx, também é aceito do jeito que sai do sistema: a plataforma reconhece o arquivo e o converte para este modelo.',
      ),
    ).toBeInTheDocument();
    // O rótulo do campo nomeia os três formatos que o servidor lê.
    expect(within(dialog).getByText(/Planilha \(\.csv, \.xlsx ou \.xls\)/)).toBeInTheDocument();
  });

  it('LINHAS_INVALIDAS do plano do Domínio: os sete motivos novos saem em português', async () => {
    withoutPlan();
    const reasons = [
      'codigo_ausente',
      'classificacao_ausente',
      'classificacao_repetida',
      'grau_ausente',
      'grau_divergente',
      'linha_irreconhecivel',
      'conta_fora_do_bloco',
    ];
    importState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('LINHAS_INVALIDAS', {
        lines: reasons.map((reason, index) => ({ line: 100 + index, reason })),
        total: reasons.length,
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Importar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    const notice = await within(dialog).findByRole('alert');
    for (const reason of reasons) {
      // Nenhum motivo sai cru: cada um tem o rótulo do vocabulário.
      expect(within(notice).queryByText(reason)).not.toBeInTheDocument();
    }
    expect(
      within(notice).getByText('Grau diferente da profundidade da classificação'),
    ).toBeInTheDocument();
    expect(within(notice).getByText(/Conta depois do fim do plano/)).toBeInTheDocument();
    // O cabeçalho do Domínio não está na linha 1: o rodapé não pode afirmar isso.
    expect(within(notice).getByTestId('accounting-invalid-lines-count')).toHaveTextContent(
      '7 linhas inválidas. Os números são os das linhas da planilha, como aparecem no Excel.',
    );
  });

  it('CABECALHO_DIVERGENTE nomeia o que falta e CONTA o que veio da planilha', async () => {
    withoutPlan();
    importState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('CABECALHO_DIVERGENTE', {
        missingColumns: ['tipo'],
        repeatedColumns: [],
        unexpectedColumnCount: 1,
        foundColumnCount: 3,
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Importar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    const notice = await within(dialog).findByRole('alert');
    expect(
      within(notice).getByRole('list', { name: 'Colunas obrigatórias que faltam' }),
    ).toHaveTextContent('tipo');
    // O nome da coluna que veio da PLANILHA não é exibido: numa planilha sem
    // cabeçalho a linha 1 é conta do cliente (86e3fvffy). Só a contagem sai.
    expect(within(notice).getByTestId('accounting-header-counts')).toHaveTextContent(
      'A planilha tem 3 colunas, 1 fora do modelo.',
    );
    expect(
      within(notice).queryByRole('list', { name: 'Colunas fora do modelo' }),
    ).not.toBeInTheDocument();
    expect(
      within(notice).queryByRole('list', { name: 'Colunas encontradas na planilha' }),
    ).not.toBeInTheDocument();
    expect(
      within(notice).queryByRole('list', { name: 'Colunas repetidas' }),
    ).not.toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('CABECALHO_DIVERGENTE por coluna REPETIDA nomeia a repetida (é do modelo)', async () => {
    withoutPlan();
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    importState.mutateAsync = vi.fn().mockRejectedValue(
      refusal('CABECALHO_DIVERGENTE', {
        missingColumns: [],
        repeatedColumns: ['nome'],
        unexpectedColumnCount: 0,
        foundColumnCount: 4,
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    const { user, dialog } = await openAndPick(/Importar planilha/);
    await user.click(within(dialog).getByRole('button', { name: 'Importar' }));

    const notice = await within(dialog).findByRole('alert');
    // `nome` é nome do MODELO, não texto da planilha: pode ser exibido.
    expect(within(notice).getByRole('list', { name: 'Colunas repetidas' })).toHaveTextContent(
      'nome',
    );
    expect(within(notice).getByTestId('accounting-header-counts')).toHaveTextContent(
      'A planilha tem 4 colunas.',
    );
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

describe('conta manual (86e3nb816)', () => {
  it.each([
    ['plataforma', platform],
    ['admin', admin],
    ['gerente da organização', manager],
  ])('%s vê "Nova conta" e "Editar" na linha', (_label, user) => {
    authState.user = user;
    render(<AccountingChartScreen clientId="c1" />);
    expect(screen.getByRole('button', { name: 'Nova conta' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Editar conta 649' })).toBeInTheDocument();
  });

  it.each([
    ['gerente do cliente', clientManager],
    ['operador do cliente', clientOperator],
  ])('%s não vê "Nova conta" nem "Editar"', (_label, user) => {
    authState.user = user;
    render(<AccountingChartScreen clientId="c1" />);
    expect(screen.queryByRole('button', { name: 'Nova conta' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Editar conta/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Ações' })).not.toBeInTheDocument();
  });

  it('cliente encerrado: sem "Nova conta" nem "Editar", com o motivo', () => {
    authState.user = admin;
    clientDetailState.data = { closed_at: '2026-09-01T00:00:00Z', accounts: [] };
    render(<AccountingChartScreen clientId="c1" />);
    expect(screen.queryByRole('button', { name: 'Nova conta' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Editar conta/ })).not.toBeInTheDocument();
    expect(screen.getByText(/assim como incluir e editar contas/)).toBeInTheDocument();
  });

  it('sem plano, "Nova conta" continua no topo (dá para montar o plano à mão)', () => {
    withoutPlan();
    render(<AccountingChartScreen clientId="c1" />);
    expect(screen.getByRole('button', { name: 'Nova conta' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Importar planilha/ })).toHaveLength(1);
  });

  it('inclui a conta com o código e o nome aparados e a classificação vazia como nula', async () => {
    const user = userEvent.setup();
    createAccountState.mutateAsync = vi.fn().mockResolvedValue(account({ id: 'n', code: '663' }));
    render(<AccountingChartScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: 'Nova conta' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('switch')).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Código reduzido'), ' 663 ');
    await user.type(within(dialog).getByLabelText('Nome'), '  Aluguéis a receber  ');
    await user.click(within(dialog).getByRole('button', { name: 'Incluir conta' }));

    await waitFor(() =>
      expect(createAccountState.mutateAsync).toHaveBeenCalledWith({
        code: '663',
        name: 'Aluguéis a receber',
        type: 'analitica',
        classification: null,
      }),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Conta 663 incluída no plano.');
  });

  it('código com separador é recusado no formulário, sem request', async () => {
    const user = userEvent.setup();
    render(<AccountingChartScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Nova conta' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Código reduzido'), '66;3');
    await user.type(within(dialog).getByLabelText('Nome'), 'Conta');
    await user.click(within(dialog).getByRole('button', { name: 'Incluir conta' }));
    expect(
      await within(dialog).findByText(/Use letras, números, ponto e hífen/),
    ).toBeInTheDocument();
    expect(createAccountState.mutateAsync).not.toHaveBeenCalled();
  });

  it('código repetido (409) aparece NO CAMPO do código, sem toast', async () => {
    const user = userEvent.setup();
    createAccountState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(409, {
        code: 'CONTA_CONTABIL_CODIGO_EXISTENTE',
        message: 'x',
        userMessage: 'Já existe uma conta com este código no plano contábil do cliente.',
        details: { code: '649', accountId: 'acc-649' },
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Nova conta' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Código reduzido'), '649');
    await user.type(within(dialog).getByLabelText('Nome'), 'Outra');
    await user.click(within(dialog).getByRole('button', { name: 'Incluir conta' }));

    const code = within(dialog).getByLabelText('Código reduzido');
    await waitFor(() => expect(code).toHaveAttribute('aria-invalid', 'true'));
    expect(within(dialog).getByText(/Já existe uma conta com este código/)).toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });

  it('a edição bloqueia o código e manda só o que mudou', async () => {
    const user = userEvent.setup();
    updateAccountState.mutateAsync = vi.fn().mockResolvedValue(account({ name: 'Banco novo' }));
    render(<AccountingChartScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: 'Editar conta 649' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Editar conta 649' })).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Código reduzido')).toBeDisabled();
    const name = within(dialog).getByLabelText('Nome');
    await user.clear(name);
    await user.type(name, 'Banco novo');
    await user.click(within(dialog).getByRole('button', { name: 'Salvar alterações' }));

    await waitFor(() =>
      expect(updateAccountState.mutateAsync).toHaveBeenCalledWith({
        accountId: 'acc-649',
        payload: { name: 'Banco novo' },
      }),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Conta 649 atualizada.');
  });

  it('inativar conta em uso (422) aparece NO CAMPO da situação, com as contagens', async () => {
    const user = userEvent.setup();
    updateAccountState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(422, {
        code: 'CONTA_CONTABIL_EM_USO',
        message: 'x',
        userMessage:
          'Esta conta não pode ficar inativa: ela está em uso por 2 decisões do de-para.',
        details: { reason: 'inativa', decisionCount: 2, bindingCount: 0 },
      }),
    );
    render(<AccountingChartScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Editar conta 649' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('switch', { name: 'Conta ativa' }));
    await user.click(within(dialog).getByRole('button', { name: 'Salvar alterações' }));

    expect(await within(dialog).findByText(/em uso por 2 decisões do de-para/)).toBeInTheDocument();
    expect(updateAccountState.mutateAsync).toHaveBeenCalledWith({
      accountId: 'acc-649',
      payload: { active: false },
    });
    expect(toastError).not.toHaveBeenCalled();
  });

  it('gaveta aberta sem violações de a11y', async () => {
    const user = userEvent.setup();
    const { container } = render(<AccountingChartScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Editar conta 649' }));
    await screen.findByRole('dialog');
    await assertNoA11yViolations(container.ownerDocument.body);
  });
});
