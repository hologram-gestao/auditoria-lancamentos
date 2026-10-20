/**
 * Testes da aba "Clientes e fornecedores" do plano contábil (86e3qhhrn; sub-abas, vínculo
 * manual e remover vínculo desde 86e3tttaf).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). No worktree do agent: `--cache=false`.
 *
 * Cobre o que dá para provar em jsdom:
 *   - duas sub-abas, "Clientes (N)" e "Fornecedores (N)", com a contagem do resumo do
 *     servidor e o tipo na URL (`vinculo`, `cliente` por padrão); a lista mostra código,
 *     nome e o documento MASCARADO como o servidor mandou (a tela nunca tem o inteiro);
 *     documento que não decifrou sai como texto neutro;
 *   - "Vincular conta": a gaveta pré-marca o tipo da sub-aba, o zod recusa o documento com
 *     dígito errado antes de enviar, e o 409 do documento já vinculado cai no CAMPO com o
 *     código da outra conta; "Remover vínculo" confirma num `AlertDialog`;
 *   - importar: sucesso mostra as contagens DA RESPOSTA e as linhas que ficaram sem
 *     vínculo (linha, conta, motivo em português); 422 `LINHAS_INVALIDAS` vira a tabela
 *     com o código da conta, nunca toast; `RELATORIO_DE_OUTRA_EMPRESA` é a mensagem do
 *     servidor; só código desconhecido cai no toast;
 *   - cliente encerrado e cliente sem plano não oferecem importar, vincular nem remover;
 *   - axe sem `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

let currentSearch = '';
const setManySpy = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/clientes/c1/plano-contabil',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));
vi.mock('@/hooks/use-url-state', async () => {
  const actual = await vi.importActual<typeof UrlState>('@/hooks/use-url-state');
  return {
    ...actual,
    useUrlState: () => ({
      get: (key: string) => new URLSearchParams(currentSearch).get(key),
      setMany: setManySpy,
    }),
  };
});

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

const listState = {
  data: undefined as AccountingPartyLinksListResponse | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const importState = { mutateAsync: vi.fn(), isPending: false };
const linkState = { mutateAsync: vi.fn(), isPending: false };
const unlinkState = { mutateAsync: vi.fn(), isPending: false };
vi.mock('@/hooks/use-client-accounting-chart', () => ({
  useAccountingPartiesList: () => listState,
  useImportAccountingParties: () => importState,
  useSetAccountingPartyLink: () => linkState,
  useRemoveAccountingPartyLink: () => unlinkState,
}));
// O seletor de conta real busca no servidor (Combobox com Popover): aqui ele vira um
// `select` nativo com o MESMO nome acessível, para o teste escolher a conta.
vi.mock('@/components/features/accounting-chart/accounting-account-combobox', () => ({
  AccountingAccountCombobox: (props: {
    label: string;
    value: string | null;
    onValueChange: (id: string, account: null) => void;
  }) => (
    <select
      aria-label={props.label}
      value={props.value ?? ''}
      onChange={(event) => props.onValueChange(event.target.value, null)}
    >
      <option value="">Escolher</option>
      <option value="acc-686">686 — Conta nova</option>
    </select>
  ),
}));

import { AccountingPartiesTab } from '@/components/features/accounting-chart/accounting-parties-tab';
import type * as UrlState from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import type {
  AccountingPartiesImportResult,
  AccountingPartyLink,
  AccountingPartyLinksListResponse,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

function link(overrides: Partial<AccountingPartyLink> = {}): AccountingPartyLink {
  return {
    accountId: 'acc-685',
    code: '685',
    name: 'Cliente Alfa Ltda',
    nameResolved: true,
    active: true,
    partyType: 'cliente',
    documentKind: 'cnpj',
    documentMasked: '**.***.***/0001-81',
    documentResolved: true,
    updatedAt: '2026-10-17T12:00:00Z',
    ...overrides,
  };
}

function withLinks(links: AccountingPartyLink[]) {
  listState.data = {
    data: links,
    pagination: { page: 1, pageSize: 50, total: links.length, totalPages: 1 },
    summary: {
      contasVinculadas: links.length,
      clientes: links.filter((l) => l.partyType === 'cliente').length,
      fornecedores: links.filter((l) => l.partyType === 'fornecedor').length,
    },
  };
}

function importResult(
  overrides: Partial<AccountingPartiesImportResult> = {},
): AccountingPartiesImportResult {
  return {
    relatorio: 'fornecedor',
    linhas: 93,
    contasVinculadas: 88,
    vinculosNovos: 88,
    vinculosAtualizados: 0,
    vinculosSemMudanca: 0,
    naoVinculadas: [
      { line: 46, reason: 'sem_conta', accountCode: '0' },
      { line: 54, reason: 'conta_compartilhada', accountCode: '746' },
    ],
    naoVinculadasTotal: 5,
    listLimit: 50,
    ...overrides,
  };
}

function apiError(status: number, code: string, details: Record<string, unknown> = {}) {
  return new ApiError(status, {
    code,
    message: code,
    userMessage: `Mensagem do servidor para ${code}`,
    details,
  });
}

async function chooseFile(name = 'RELAÇÃO DE FORNECEDORES.xls') {
  const input = screen.getByLabelText('Relação exportada do Domínio (.xls ou .xlsx)');
  const file = new File(['xls'], name, { type: 'application/vnd.ms-excel' });
  await userEvent.upload(input, file);
  return file;
}

beforeEach(() => {
  currentSearch = '';
  setManySpy.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  importState.mutateAsync.mockReset();
  importState.isPending = false;
  linkState.mutateAsync.mockReset();
  unlinkState.mutateAsync.mockReset();
  listState.isLoading = false;
  listState.isError = false;
  listState.error = null;
  withLinks([
    link(),
    link({
      accountId: 'acc-918',
      code: '918',
      name: 'Pessoa Física Beta',
      partyType: 'cliente',
      documentKind: 'cpf',
      documentMasked: '***.***.*47-25',
    }),
    link({
      accountId: 'acc-700',
      code: '700',
      name: 'Fornecedor Gama',
      partyType: 'fornecedor',
      documentKind: null,
      documentMasked: null,
      documentResolved: false,
      nameResolved: false,
    }),
  ]);
});

describe('lista de vínculos', () => {
  it('sub-abas com a contagem do servidor, documento mascarado e o resumo', async () => {
    withLinks([
      link(),
      link({
        accountId: 'acc-918',
        code: '918',
        name: 'Pessoa Física Beta',
        documentKind: 'cpf',
        documentMasked: '***.***.*47-25',
      }),
      link({
        accountId: 'acc-700',
        code: '700',
        name: 'Fornecedor Gama',
        documentKind: null,
        documentMasked: null,
        documentResolved: false,
        nameResolved: false,
      }),
    ]);
    listState.data!.summary = { contasVinculadas: 4, clientes: 3, fornecedores: 1 };
    const { container } = render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);

    const tabs = screen.getByRole('tablist', { name: 'Tipo do vínculo' });
    expect(within(tabs).getByRole('tab', { name: 'Clientes (3)' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(within(tabs).getByRole('tab', { name: 'Fornecedores (1)' })).toHaveAttribute(
      'aria-selected',
      'false',
    );
    // O filtro "Vínculo" saiu: as sub-abas são o recorte.
    expect(screen.queryByRole('group', { name: 'Vínculo' })).not.toBeInTheDocument();

    const region = screen.getByRole('region', { name: 'Contas vinculadas a clientes (rolável)' });
    expect(within(region).getAllByRole('row')).toHaveLength(4);
    expect(within(region).getByText('**.***.***/0001-81')).toBeInTheDocument();
    expect(within(region).getByText('***.***.*47-25')).toBeInTheDocument();
    // Nenhum documento inteiro em lugar nenhum: a tela só recebe o mascarado.
    expect(container.textContent).not.toMatch(/\d{14}|\d{11}/);
    // Documento e nome que não decifraram: texto NEUTRO, sem cara de dado.
    expect(within(region).getByText('Documento indisponível')).toBeInTheDocument();
    expect(within(region).getByText('Nome indisponível (indecifrável)')).toBeInTheDocument();
    const summary = screen.getByTestId('accounting-parties-summary');
    expect(within(summary).getByText('Contas vinculadas').nextElementSibling).toHaveTextContent(
      '4',
    );
    await assertNoA11yViolations(container);
  });

  it('a sub-aba vai para a URL (`vinculo`), volta à página 1 e o vazio é do tipo', async () => {
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    await userEvent.click(screen.getByRole('tab', { name: /Fornecedores/ }));
    expect(setManySpy).toHaveBeenCalledWith({ vinculo: 'fornecedor', vpage: null });

    currentSearch = 'vinculo=fornecedor';
    withLinks([]);
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    expect(
      screen.getByRole('region', { name: 'Contas vinculadas a fornecedores (rolável)' }),
    ).toBeInTheDocument();
    expect(screen.getByTestId('accounting-parties-empty')).toHaveTextContent(
      /Nenhuma conta vinculada a fornecedor ainda/,
    );
  });

  it('sem vínculo nenhum, o vazio orienta a importar a relação ou vincular à mão', () => {
    withLinks([]);
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    expect(screen.getByTestId('accounting-parties-empty')).toHaveTextContent(
      /Importe a Relação de clientes do Domínio ou vincule uma conta à mão/,
    );
  });

  it('erro de carga: mensagem e "Tentar novamente", nunca tela branca', async () => {
    listState.isError = true;
    listState.error = apiError(500, 'INTERNAL_ERROR');
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    expect(screen.getByRole('alert')).toHaveTextContent('Mensagem do servidor para INTERNAL_ERROR');
    await userEvent.click(screen.getByRole('button', { name: 'Tentar novamente' }));
    expect(listState.refetch).toHaveBeenCalled();
  });
});

describe('vincular conta à mão (86e3tttaf)', () => {
  async function openSheet() {
    await userEvent.click(screen.getByRole('button', { name: 'Vincular conta' }));
    return screen.findByRole('dialog', { name: 'Vincular conta' });
  }

  it('o tipo vem pré-marcado com a sub-aba ativa', async () => {
    currentSearch = 'vinculo=fornecedor';
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    const sheet = await openSheet();
    expect(within(sheet).getByRole('radio', { name: 'Fornecedor' })).toBeChecked();
    expect(within(sheet).getByRole('radio', { name: 'Cliente' })).not.toBeChecked();
  });

  it('documento com dígito errado é recusado no campo e nada é enviado', async () => {
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    const sheet = await openSheet();
    await userEvent.selectOptions(
      within(sheet).getByRole('combobox', { name: 'Conta do plano contábil' }),
      'acc-686',
    );
    await userEvent.type(within(sheet).getByLabelText('CNPJ ou CPF'), '45.723.174/0001-19');
    await userEvent.click(within(sheet).getByRole('button', { name: 'Vincular' }));
    expect(
      await within(sheet).findByText(/CNPJ ou CPF inválido: confira os dígitos/),
    ).toBeInTheDocument();
    expect(linkState.mutateAsync).not.toHaveBeenCalled();
  });

  it('sem conta escolhida, o zod pede a conta', async () => {
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    const sheet = await openSheet();
    await userEvent.type(within(sheet).getByLabelText('CNPJ ou CPF'), '52998224725');
    await userEvent.click(within(sheet).getByRole('button', { name: 'Vincular' }));
    expect(
      await within(sheet).findByText('Escolha a conta do plano contábil.'),
    ).toBeInTheDocument();
    expect(linkState.mutateAsync).not.toHaveBeenCalled();
  });

  it('envia conta, tipo e documento; o sucesso fecha a gaveta e leva à sub-aba gravada', async () => {
    linkState.mutateAsync.mockResolvedValue(
      link({ accountId: 'acc-686', code: '686', partyType: 'fornecedor' }),
    );
    const { container } = render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    const sheet = await openSheet();
    await assertNoA11yViolations(container.ownerDocument.body);
    await userEvent.selectOptions(
      within(sheet).getByRole('combobox', { name: 'Conta do plano contábil' }),
      'acc-686',
    );
    await userEvent.click(within(sheet).getByRole('radio', { name: 'Fornecedor' }));
    await userEvent.type(within(sheet).getByLabelText('CNPJ ou CPF'), '45.723.174/0001-10');
    await userEvent.click(within(sheet).getByRole('button', { name: 'Vincular' }));

    await waitFor(() =>
      expect(linkState.mutateAsync).toHaveBeenCalledWith({
        accountId: 'acc-686',
        payload: { partyType: 'fornecedor', document: '45.723.174/0001-10' },
      }),
    );
    expect(toastSuccess).toHaveBeenCalledWith('Conta 686 vinculada a um fornecedor.');
    expect(setManySpy).toHaveBeenCalledWith({ vinculo: 'fornecedor', vpage: null });
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Vincular conta' })).not.toBeInTheDocument(),
    );
  });

  it('409 DOCUMENTO_JA_VINCULADO cai no campo do documento com o código da outra conta', async () => {
    linkState.mutateAsync.mockRejectedValue(
      apiError(409, 'DOCUMENTO_JA_VINCULADO', { accountCode: '685', accountId: 'acc-685' }),
    );
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    const sheet = await openSheet();
    await userEvent.selectOptions(
      within(sheet).getByRole('combobox', { name: 'Conta do plano contábil' }),
      'acc-686',
    );
    const input = within(sheet).getByLabelText('CNPJ ou CPF');
    await userEvent.type(input, '52998224725');
    await userEvent.click(within(sheet).getByRole('button', { name: 'Vincular' }));

    expect(await within(sheet).findByText(/já está vinculado à conta 685/)).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(toastError).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Vincular conta' })).toBeInTheDocument();
  });

  it('422 da conta (corrida: virou sintética) cai no campo da conta', async () => {
    linkState.mutateAsync.mockRejectedValue(
      apiError(422, 'CONTA_CONTABIL_INVALIDA', { field: 'accountId', reason: 'conta_sintetica' }),
    );
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    const sheet = await openSheet();
    await userEvent.selectOptions(
      within(sheet).getByRole('combobox', { name: 'Conta do plano contábil' }),
      'acc-686',
    );
    await userEvent.type(within(sheet).getByLabelText('CNPJ ou CPF'), '52998224725');
    await userEvent.click(within(sheet).getByRole('button', { name: 'Vincular' }));
    expect(
      await within(sheet).findByText('Mensagem do servidor para CONTA_CONTABIL_INVALIDA'),
    ).toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe('remover vínculo (86e3tttaf)', () => {
  it('confirma num AlertDialog que diz a consequência e chama a rota com a conta', async () => {
    unlinkState.mutateAsync.mockResolvedValue(undefined);
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    await userEvent.click(screen.getByRole('button', { name: 'Remover vínculo da conta 685' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Remover vínculo' });
    expect(dialog).toHaveTextContent(/a planilha da Omie deixa de levar o CNPJ\/CPF dela/);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Remover vínculo' }));
    await waitFor(() => expect(unlinkState.mutateAsync).toHaveBeenCalledWith('acc-685'));
    expect(toastSuccess).toHaveBeenCalledWith('Vínculo da conta 685 removido.');
  });
});

describe('importar a relação', () => {
  it('sucesso mostra as contagens DA RESPOSTA e as linhas sem vínculo com conta e motivo', async () => {
    importState.mutateAsync.mockResolvedValue(importResult());
    const { container } = render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    const file = await chooseFile();
    await userEvent.click(screen.getByRole('button', { name: 'Importar relação' }));

    await waitFor(() => expect(importState.mutateAsync).toHaveBeenCalledWith(file));
    const panel = await screen.findByTestId('accounting-parties-import-success');
    expect(panel).toHaveTextContent(
      'Relação de fornecedores importada: 93 pessoas, 88 contas vinculadas (88 novas, 0 atualizadas, 0 sem mudança); 5 linhas ficaram sem vínculo.',
    );
    expect(within(panel).getByRole('cell', { name: '746' })).toBeInTheDocument();
    expect(
      within(panel).getByText(/Conta compartilhada por mais de uma pessoa/),
    ).toBeInTheDocument();
    expect(within(panel).getByText(/Sem conta no Domínio/)).toBeInTheDocument();
    expect(panel).toHaveTextContent('Mostrando 2 de 5 linhas.');
    expect(toastSuccess).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('accounting-parties-refusal')).not.toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('LINHAS_INVALIDAS vira a tabela linha x conta x motivo, sem toast', async () => {
    importState.mutateAsync.mockRejectedValue(
      apiError(422, 'LINHAS_INVALIDAS', {
        lines: [
          { line: 9, reason: 'conta_nao_encontrada', accountCode: '685' },
          { line: 12, reason: 'conta_sintetica', accountCode: '10' },
        ],
        total: 2,
      }),
    );
    const { container } = render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    await chooseFile();
    await userEvent.click(screen.getByRole('button', { name: 'Importar relação' }));

    const refusal = await screen.findByTestId('accounting-parties-refusal');
    expect(refusal).toHaveAttribute('data-refusal-code', 'LINHAS_INVALIDAS');
    expect(within(refusal).getByRole('cell', { name: '685' })).toBeInTheDocument();
    expect(
      within(refusal).getByText('Conta não encontrada no plano contábil do cliente'),
    ).toBeInTheDocument();
    expect(
      within(refusal).getByText('Conta sintética (só agrupa): não recebe vínculo'),
    ).toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
    expect(screen.queryByTestId('accounting-parties-import-success')).not.toBeInTheDocument();
    await assertNoA11yViolations(container);
  });

  it('RELATORIO_DE_OUTRA_EMPRESA mostra a mensagem do servidor; código desconhecido é toast', async () => {
    importState.mutateAsync.mockRejectedValueOnce(apiError(422, 'RELATORIO_DE_OUTRA_EMPRESA'));
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    await chooseFile();
    await userEvent.click(screen.getByRole('button', { name: 'Importar relação' }));
    const refusal = await screen.findByTestId('accounting-parties-refusal');
    expect(refusal).toHaveTextContent('Este relatório é de outra empresa');
    expect(refusal).toHaveTextContent('Mensagem do servidor para RELATORIO_DE_OUTRA_EMPRESA');

    importState.mutateAsync.mockRejectedValueOnce(apiError(500, 'INTERNAL_ERROR'));
    await userEvent.click(screen.getByRole('button', { name: 'Importar relação' }));
    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('Mensagem do servidor para INTERNAL_ERROR'),
    );
  });

  it('sem arquivo, o zod recusa no cliente e nada é enviado', async () => {
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan />);
    await userEvent.click(screen.getByRole('button', { name: 'Importar relação' }));
    expect(
      await screen.findByText('Escolha a relação exportada do Domínio (.xls ou .xlsx).'),
    ).toBeInTheDocument();
    expect(importState.mutateAsync).not.toHaveBeenCalled();
  });
});

describe('estado que decide a ação', () => {
  it('cliente encerrado: sem formulário, sem vincular nem remover, e a lista continua', () => {
    render(<AccountingPartiesTab clientId="c1" isClosed hasPlan />);
    expect(screen.queryByRole('button', { name: 'Importar relação' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Vincular conta' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Remover vínculo/ })).not.toBeInTheDocument();
    expect(screen.getByTestId('accounting-parties-blocked-note')).toHaveTextContent(
      /Cliente encerrado/,
    );
    expect(screen.getByText('**.***.***/0001-81')).toBeInTheDocument();
  });

  it('cliente sem plano: orienta a importar o plano antes', () => {
    withLinks([]);
    render(<AccountingPartiesTab clientId="c1" isClosed={false} hasPlan={false} />);
    expect(screen.queryByRole('button', { name: 'Importar relação' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Vincular conta' })).not.toBeInTheDocument();
    expect(screen.getByTestId('accounting-parties-blocked-note')).toHaveTextContent(
      /Importe o plano contábil do cliente antes/,
    );
  });
});
