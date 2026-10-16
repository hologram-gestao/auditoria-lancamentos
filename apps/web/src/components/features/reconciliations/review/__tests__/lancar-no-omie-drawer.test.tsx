/**
 * Gaveta de lançamento: forma, classificação, confirmação e resumo (Sprint 7 /
 * FRONT 07.7; provisão no Contas a Pagar desde a 86e3n70q0, bloco C).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que estes testes travam:
 *
 *   - **O padrão é provisionar**, como o servidor; o direto é escolha explícita e
 *     só existe no processo "na data da compra". No processo do vencimento a
 *     gaveta oferece só a provisão e diz por quê, com a frase do servidor.
 *   - **O pedido deriva da forma da linha:** a linha direta sai SÓ com
 *     `file_entry_id`, `cod_categoria` e `modo` (campo da provisão num direto é
 *     400 para o lote inteiro).
 *   - **Fornecedor:** encontrado, sem cadastro (razão social e e-mail), erro,
 *     CNPJ inválido, fornecedor padrão — e sem nenhum a linha fica de fora.
 *   - **Compra dividida em categorias (subtask 86e3nb818):** R$ 103,80 = R$ 100,00
 *     na categoria da compra + R$ 3,80 em IOF; a soma que não fecha segura a linha.
 *   - **Rateio:** percentuais somando 100, obrigatório quando o cliente exige.
 *   - **Linha com envio anterior abre TRAVADA na forma gravada.**
 *   - **O resumo** diz o número do título e da baixa, onde entrou, onde parou.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const postMock = vi.fn();
const postState = { mutateAsync: postMock, isPending: false };
const findMock = vi.fn();
const findState = { mutateAsync: findMock, isPending: false };
const categoriasState = {
  data: undefined as { data: { codigo: string; descricao: string }[]; total: number } | undefined,
  isLoading: false,
  isError: false,
  isFetching: false,
  refetch: vi.fn(),
};
type SearchData = {
  data: {
    codigo: number;
    razao_social: string;
    nome_fantasia: string | null;
    cnpj_cpf: string | null;
  }[];
  total: number;
  truncated: boolean;
};
/** 86e3qhhrd — o que a busca pelo nome devolve para cada texto pedido. */
const searchByText = vi.fn<(nome: string) => SearchData | undefined>(() => undefined);
const searchCalls: string[] = [];
const suggestionsState = {
  data: undefined as { file_entry_id: string; sugestoes: SearchData['data'] }[] | undefined,
};
const departamentosState = {
  data: {
    data: [
      { codigo: '800000001', descricao: 'Comercial', estrutura: '001' },
      { codigo: '800000004', descricao: 'Administrativo', estrutura: '002' },
    ],
    total: 2,
  },
  isLoading: false,
  isError: false,
  isFetching: false,
  refetch: vi.fn(),
};

vi.mock('@/hooks/use-omie-postings', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  usePostOmieLancamentos: () => postState,
  useOmieCategorias: () => categoriasState,
  useOmieDepartamentos: () => departamentosState,
  useFindOmieFornecedor: () => findState,
  useSearchOmieFornecedores: (_clientId: string, nome: string) => {
    if (nome.trim() !== '') searchCalls.push(nome.trim());
    return {
      data: nome.trim() === '' ? undefined : searchByText(nome.trim()),
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    };
  },
  useRefreshOmieFornecedores: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
  useOmieSupplierSuggestions: () => suggestionsState,
  useOmieFornecedorByCode: () => ({
    data: { codigo: 700000001, razao_social: 'PAPELARIA DEMO LTDA', nome_fantasia: null },
    isLoading: false,
  }),
  usePostedInSession: () => ({}),
  useOpenLancarDrawer: (_sessionId: string, onReady: (targets: unknown[]) => void) => ({
    open: async (targets: unknown[]) => onReady(targets),
    openingTargets: null,
  }),
}));

const toastSuccess = vi.fn();
const toastWarning = vi.fn();
const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...a: unknown[]) => toastSuccess(...a),
    warning: (...a: unknown[]) => toastWarning(...a),
    error: (...a: unknown[]) => toastError(...a),
  },
}));

// Imports do SUT DEPOIS dos `vi.mock`.
import {
  LancarNoOmieDrawer,
  type PostingClientContext,
} from '@/components/features/reconciliations/review/lancar-no-omie-drawer';
import { DIRECT_IN_DUE_DATE_PROCESS_MESSAGE } from '@/components/features/reconciliations/review/omie-posting-eligibility';
import { ApiError } from '@/lib/api/client';
import type { OmiePostingBatchPayload, OmiePostingState } from '@/lib/api/omie-postings';
import type { FileEntryItem } from '@/lib/api/reconciliations';
import { assertNoA11yViolations } from '@/test/a11y';
import { TEST_POSTING_CONTEXT } from '@/test/posting-context';

const SESSION_ID = 's1';
const CNPJ_FOUND = '12.345.678/0001-95';
const CNPJ_NEW = '11.222.333/0001-81';
const FOUND = {
  codigo: 700000001,
  razao_social: 'PAPELARIA DEMO LTDA',
  nome_fantasia: 'Papelaria',
  cnpj_cpf: CNPJ_FOUND,
};
/** Fornecedor do exterior: só a busca pelo nome o acha (sem CNPJ). */
const EXTERIOR = {
  codigo: 700000003,
  razao_social: 'SOFTWARE DEMO GMBH',
  nome_fantasia: 'Software Demo',
  cnpj_cpf: null,
};

function entry(over: Partial<FileEntryItem> = {}): FileEntryItem {
  return {
    id: 'e1',
    transaction_date: '2026-06-10',
    description: 'Posto Shell 1234',
    amount: '-150.50',
    balance: null,
    situation: 'sem_omie',
    user_action: null,
    user_note: null,
    omie_lancamento_id: null,
    omie_posting: null,
    ...over,
  };
}

const DUAS = [
  entry(),
  entry({
    id: 'e2',
    transaction_date: '2026-06-14',
    description: 'Assinatura de software',
    amount: '-89.90',
  }),
];

function posting(over: Partial<OmiePostingState> = {}): OmiePostingState {
  return {
    kind: 'provisao',
    status: 'failed',
    step: 'baixa',
    attempts: 0,
    supplier_code: 700000001,
    payable_id: 8800001,
    payment_id: null,
    omie_lancamento_id: null,
    error_code: 'OMIE_FAULT',
    error_message: 'Ocorreu um erro: 1234 - conta bloqueada',
    ...over,
  };
}

interface RenderOptions {
  onPosted?: ReturnType<typeof vi.fn>;
  context?: Partial<PostingClientContext>;
}

function renderDrawer(entries: FileEntryItem[] = DUAS, options: RenderOptions = {}) {
  const onPosted = options.onPosted ?? vi.fn();
  return {
    onPosted,
    ...render(
      <LancarNoOmieDrawer
        sessionId={SESSION_ID}
        entries={entries}
        open
        onOpenChange={vi.fn()}
        onPosted={onPosted}
        context={{ ...TEST_POSTING_CONTEXT, ...options.context }}
      />,
    ),
  };
}

type UI = ReturnType<typeof userEvent.setup>;

/** Abre o combobox indicado pelo nome acessível e escolhe a opção pelo texto. */
async function escolher(ui: UI, gatilho: RegExp, opcao: RegExp) {
  await ui.click(screen.getByRole('button', { name: gatilho }));
  const listbox = await screen.findByRole('listbox');
  await ui.click(within(listbox).getByRole('option', { name: opcao }));
}

/** Digita o CNPJ no campo indicado e clica no "Buscar no Omie" ao lado dele. */
async function buscarFornecedor(ui: UI, campo: RegExp, cnpj: string) {
  const input = screen.getByLabelText(campo);
  await ui.clear(input);
  await ui.type(input, cnpj);
  const wrapper = input.parentElement as HTMLElement;
  await ui.click(within(wrapper).getByRole('button', { name: 'Buscar no Omie' }));
}

async function digitarValor(ui: UI, rotulo: RegExp, valor: string) {
  const input = screen.getByLabelText(rotulo);
  await ui.clear(input);
  await ui.type(input, valor);
}

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  postState.isPending = false;
  findState.isPending = false;
  searchByText.mockImplementation(() => undefined);
  searchCalls.length = 0;
  suggestionsState.data = undefined;
  categoriasState.isLoading = false;
  categoriasState.isError = false;
  categoriasState.isFetching = false;
  categoriasState.data = {
    data: [
      { codigo: '1.01.01', descricao: 'Combustível' },
      { codigo: '2.02.02', descricao: 'Serviços de software' },
      { codigo: '2.09.09', descricao: 'IOF' },
    ],
    total: 3,
  };
  departamentosState.isError = false;
});

describe('Forma do lançamento', () => {
  it('o padrão é provisionar, e sem fornecedor a compra fica de fora', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry()]);

    const forma = screen.getByRole('group', { name: /Forma do lançamento — 10\/06\/2026/ });
    expect(
      within(forma).getByRole('radio', { name: /Provisionar no Contas a Pagar/ }),
    ).toBeChecked();

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);

    expect(screen.getByText(/Sem fornecedor: busque pelo nome ou pelo CNPJ/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 1/ })).toBeDisabled();
    expect(screen.getByText(/1 compra fica de fora/)).toHaveTextContent('(sem fornecedor)');
  });

  it('a linha direta sai SÓ com file_entry_id, cod_categoria e modo', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry()]);

    // Liga um rateio na provisão e DEPOIS troca para direto: nada da provisão
    // pode ir no pedido.
    await ui.click(screen.getByRole('switch', { name: /Ratear por departamento/ }));
    await ui.click(screen.getByRole('radio', { name: /Lançar direto na conta do cartão/ }));
    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);

    postMock.mockResolvedValue(
      payload([line('e1', 'lancada', { kind: 'direto', omie_lancamento_id: 5001 })]),
    );
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([
      { file_entry_id: 'e1', cod_categoria: '1.01.01', modo: 'direto' },
    ]);
  });

  it('processo do vencimento: só a provisão, com a frase do servidor', () => {
    renderDrawer([entry()], {
      context: { cardPostingDateMode: 'invoice_due_date', invoiceDueDate: '2026-10-13' },
    });

    expect(screen.queryByRole('radio')).toBeNull();
    expect(
      screen.getByText(
        (_, el) => el?.textContent?.endsWith(DIRECT_IN_DUE_DATE_PROCESS_MESSAGE) ?? false,
        {
          selector: 'p',
        },
      ),
    ).toBeInTheDocument();
  });

  it('linha com provisão parada abre TRAVADA, sem pedir o fornecedor de novo', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry({ omie_posting: posting() })]);

    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.getByText(/parou no passo baixa/)).toBeInTheDocument();
    expect(screen.getByText(/não pode ser trocada/)).toBeInTheDocument();
    expect(screen.getByText(/O título nº 8800001 já existe no Omie/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Buscar fornecedor no Omie/)).toBeNull();

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([
      { file_entry_id: 'e1', modo: 'provisionar', cod_categoria: '1.01.01' },
    ]);
  });

  it('provisão ainda no passo do fornecedor pode trocar de forma', () => {
    renderDrawer([
      entry({
        omie_posting: posting({
          status: 'pending',
          step: 'fornecedor',
          supplier_code: null,
          payable_id: null,
          error_code: null,
          error_message: null,
        }),
      }),
    ]);
    expect(screen.getByRole('radio', { name: /Lançar direto/ })).toBeEnabled();
  });

  it('direto enviado sem confirmação abre travado em direto', () => {
    renderDrawer([
      entry({
        omie_posting: posting({
          kind: 'direto',
          status: 'pending',
          step: null,
          attempts: 1,
          supplier_code: null,
          payable_id: null,
          error_code: null,
          error_message: null,
        }),
      }),
    ]);
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.getByText('Lançar direto na conta do cartão')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Buscar fornecedor no Omie/)).toBeNull();
  });
});

describe('Fornecedor', () => {
  it('encontrado pelo CNPJ: mostra o cadastro e o pedido leva o CÓDIGO dele', async () => {
    const ui = userEvent.setup();
    findMock.mockResolvedValue(FOUND);
    renderDrawer([entry()]);

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    await buscarFornecedor(ui, /Buscar fornecedor no Omie — 10\/06\/2026/, CNPJ_FOUND);

    expect(findMock).toHaveBeenCalledWith({ cnpj: '12345678000195' });
    expect(await screen.findByText('PAPELARIA DEMO LTDA')).toBeInTheDocument();
    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([
      {
        file_entry_id: 'e1',
        modo: 'provisionar',
        cod_categoria: '1.01.01',
        codigo_fornecedor: 700000001,
      },
    ]);
  });

  it('pelo nome: a lista aparece ao parar de digitar e o pedido leva o código (86e3qhhrd)', async () => {
    const ui = userEvent.setup();
    searchByText.mockImplementation((nome) =>
      nome.toLowerCase().startsWith('soft')
        ? { data: [EXTERIOR], total: 1, truncated: false }
        : { data: [], total: 0, truncated: false },
    );
    renderDrawer([entry()]);

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    const input = screen.getByLabelText(/Buscar fornecedor no Omie — 10\/06\/2026/);
    await ui.type(input, 'softw');
    // Com letra é nome: o botão da busca por CNPJ some.
    expect(screen.queryByRole('button', { name: 'Buscar no Omie' })).toBeNull();
    const option = await screen.findByRole('button', { name: /SOFTWARE DEMO GMBH/ });
    expect(option).toHaveTextContent('sem CNPJ');
    expect(screen.getByText('1 fornecedor encontrado.')).toBeInTheDocument();
    // Só o texto já parado vai à busca, e nunca o que a pessoa digitou no meio.
    expect(searchCalls.at(-1)).toBe('softw');
    expect(findMock).not.toHaveBeenCalled();

    await ui.click(option);
    expect(screen.getByRole('button', { name: /Trocar fornecedor/ })).toBeInTheDocument();
    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([
      {
        file_entry_id: 'e1',
        modo: 'provisionar',
        cod_categoria: '1.01.01',
        codigo_fornecedor: 700000003,
      },
    ]);
  });

  it('nome sem resultado: diz o que fazer, sem lista', async () => {
    const ui = userEvent.setup();
    searchByText.mockImplementation(() => ({ data: [], total: 0, truncated: false }));
    renderDrawer([entry()]);

    await ui.type(screen.getByLabelText(/Buscar fornecedor no Omie — 10\/06\/2026/), 'zzzz');

    expect(
      await screen.findByText(/Nenhum fornecedor ativo com esse nome no Omie do cliente/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Atualizar lista/ })).toBeInTheDocument();
  });

  it('sugestão pela compra: aparece antes de digitar e só vale com o clique', async () => {
    const ui = userEvent.setup();
    suggestionsState.data = [{ file_entry_id: 'e1', sugestoes: [EXTERIOR] }];
    renderDrawer([entry({ description: 'SOFTWARE DEMO*ASSINATURA' })]);

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    expect(screen.getByText('Sugestão pelo nome da compra:')).toBeInTheDocument();
    // Nada aplicado sozinho: sem o clique, a linha segue sem fornecedor.
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 1/ })).toBeDisabled();

    await ui.click(screen.getByRole('button', { name: /SOFTWARE DEMO GMBH/ }));
    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));
    expect(postMock).toHaveBeenCalledWith([
      expect.objectContaining({ file_entry_id: 'e1', codigo_fornecedor: 700000003 }),
    ]);
  });

  it('"Trocar fornecedor" volta ao campo vazio e a linha fica sem fornecedor', async () => {
    const ui = userEvent.setup();
    suggestionsState.data = [{ file_entry_id: 'e1', sugestoes: [EXTERIOR] }];
    renderDrawer([entry()]);

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    await ui.click(screen.getByRole('button', { name: /SOFTWARE DEMO GMBH/ }));
    await ui.click(screen.getByRole('button', { name: /Trocar fornecedor/ }));

    expect(screen.getByLabelText(/Buscar fornecedor no Omie — 10\/06\/2026/)).toHaveValue('');
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 1/ })).toBeDisabled();
  });

  it('sem cadastro: pede razão social e e-mail, e só então lança', async () => {
    const ui = userEvent.setup();
    findMock.mockRejectedValue(
      new ApiError(404, {
        code: 'FORNECEDOR_NAO_ENCONTRADO',
        message: 'x',
        userMessage: 'Nenhum fornecedor com este CNPJ no Omie do cliente.',
      }),
    );
    renderDrawer([entry()]);

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    await buscarFornecedor(ui, /Buscar fornecedor no Omie — 10\/06\/2026/, CNPJ_NEW);

    expect(await screen.findByText(/não tem cadastro no Omie do cliente/)).toBeInTheDocument();
    expect(screen.getByText(/não se desfaz pela API/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 1/ })).toBeDisabled();

    await ui.type(screen.getByLabelText('Razão social'), 'Loja Nova Ltda');
    await ui.type(screen.getByLabelText('E-mail'), 'financeiro@lojanova.com.br');
    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([
      {
        file_entry_id: 'e1',
        modo: 'provisionar',
        cod_categoria: '1.01.01',
        cnpj_fornecedor: '11222333000181',
        razao_social: 'Loja Nova Ltda',
        email: 'financeiro@lojanova.com.br',
      },
    ]);
  });

  it('erro da busca: a mensagem do servidor e "Tentar de novo"', async () => {
    const ui = userEvent.setup();
    findMock.mockRejectedValue(
      new ApiError(504, {
        code: 'OMIE_TIMEOUT',
        message: 'x',
        userMessage: 'O Omie não respondeu no tempo esperado ao buscar o fornecedor.',
      }),
    );
    renderDrawer([entry()]);

    await buscarFornecedor(ui, /Buscar fornecedor no Omie — 10\/06\/2026/, CNPJ_FOUND);

    expect(await screen.findByText(/não respondeu no tempo esperado/)).toBeInTheDocument();
    findMock.mockResolvedValue(FOUND);
    await ui.click(screen.getByRole('button', { name: 'Tentar de novo' }));
    expect(await screen.findByText('PAPELARIA DEMO LTDA')).toBeInTheDocument();
  });

  it('CNPJ inválido nem sai para o servidor', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry()]);

    await buscarFornecedor(ui, /Buscar fornecedor no Omie — 10\/06\/2026/, '12.345.678/0001-96');

    expect(screen.getByText('CNPJ inválido. Confira os 14 dígitos.')).toBeInTheDocument();
    expect(findMock).not.toHaveBeenCalled();
  });

  it('fornecedor padrão do cliente: mostra o nome e o pedido não leva CNPJ', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry()], { context: { defaultSupplierCode: 700000001 } });

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    const padrao = screen.getByRole('checkbox', { name: /Usar o fornecedor padrão do cliente/ });
    expect(padrao).toHaveAccessibleName(/PAPELARIA DEMO LTDA/);
    await ui.click(padrao);
    expect(screen.queryByLabelText(/Buscar fornecedor no Omie/)).toBeNull();

    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));
    expect(postMock).toHaveBeenCalledWith([
      { file_entry_id: 'e1', modo: 'provisionar', cod_categoria: '1.01.01' },
    ]);
  });
});

describe('Compra dividida em categorias (86e3nb818)', () => {
  it('R$ 103,80 = R$ 100,00 na categoria da compra + R$ 3,80 de IOF', async () => {
    const ui = userEvent.setup();
    findMock.mockResolvedValue(FOUND);
    renderDrawer([entry({ amount: '-103.80', description: 'Ferramenta SaaS' })]);

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Serviços de software/);
    await ui.click(screen.getByRole('switch', { name: /Dividir em categorias/ }));

    // A 1ª parte nasce com a categoria escolhida e o valor inteiro.
    expect(screen.getByLabelText(/Valor da parte 1/)).toHaveValue('103,80');
    await digitarValor(ui, /Valor da parte 1/, '10000');
    expect(screen.getByText(/^Falta/)).toHaveTextContent('3,80');
    await escolher(ui, /Categoria da parte 2/, /IOF/);
    await digitarValor(ui, /Valor da parte 2/, '380');
    expect(screen.getByText('Fecha com o valor da compra.')).toBeInTheDocument();

    await buscarFornecedor(ui, /Buscar fornecedor no Omie — 10\/06\/2026/, CNPJ_FOUND);
    await screen.findByText('PAPELARIA DEMO LTDA');
    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([
      {
        file_entry_id: 'e1',
        modo: 'provisionar',
        categorias: [
          { codigo: '2.02.02', valor: '100.00' },
          { codigo: '2.09.09', valor: '3.80' },
        ],
        codigo_fornecedor: 700000001,
      },
    ]);
  });

  it('a soma que não fecha segura a linha', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry({ amount: '-103.80' })], { context: { defaultSupplierCode: 700000001 } });

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    await ui.click(screen.getByRole('checkbox', { name: /Usar o fornecedor padrão/ }));
    await ui.click(screen.getByRole('switch', { name: /Dividir em categorias/ }));
    await digitarValor(ui, /Valor da parte 1/, '10000');
    await escolher(ui, /Categoria da parte 2/, /IOF/);
    await digitarValor(ui, /Valor da parte 2/, '300');

    expect(screen.getByText(/^Falta/)).toHaveTextContent('0,80');
    expect(screen.getByText(/A soma das partes não fecha/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 1/ })).toBeDisabled();
  });
});

describe('Rateio por departamento', () => {
  it('obrigatório para o cliente: sem rateio fica de fora; com 100% vai no pedido', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry()], {
      context: { departmentRequired: true, defaultSupplierCode: 700000001 },
    });

    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    await ui.click(screen.getByRole('checkbox', { name: /Usar o fornecedor padrão/ }));
    expect(screen.getByText(/exige rateio por departamento/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 1/ })).toBeDisabled();

    await ui.click(screen.getByRole('button', { name: 'Adicionar departamento' }));
    await escolher(ui, /Departamento da parte 1 do rateio/, /Comercial/);
    await digitarValor(ui, /Percentual da parte 1 do rateio/, '6000');
    expect(screen.getByText(/^Faltam/)).toHaveTextContent('40,00%');
    // A parte nova nasce com o que falta (40%).
    await ui.click(screen.getByRole('button', { name: 'Adicionar departamento' }));
    expect(screen.getByLabelText(/Percentual da parte 2 do rateio/)).toHaveValue('40,00');
    await escolher(ui, /Departamento da parte 2 do rateio/, /Administrativo/);
    expect(screen.getByText('Soma 100%.')).toBeInTheDocument();

    postMock.mockResolvedValue(payload([line('e1', 'lancada', { kind: 'provisao' })]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));
    expect(postMock).toHaveBeenCalledWith([
      {
        file_entry_id: 'e1',
        modo: 'provisionar',
        cod_categoria: '1.01.01',
        departamentos: [
          { codigo: '800000001', percentual: '60.00' },
          { codigo: '800000004', percentual: '40.00' },
        ],
      },
    ]);
  });
});

describe('Aplicar a todas', () => {
  it('categoria e fornecedor padrão valem para todas; a linha prevalece depois', async () => {
    const ui = userEvent.setup();
    renderDrawer(DUAS, { context: { defaultSupplierCode: 700000001 } });

    await escolher(ui, /Categoria para todas as compras/, /Combustível/);
    const lote = screen.getByRole('region', { name: 'Classificar todas de uma vez' });
    await ui.click(within(lote).getByRole('checkbox', { name: /Usar o fornecedor padrão/ }));
    await ui.click(screen.getByRole('button', { name: 'Aplicar a 2 compras' }));

    expect(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ })).toBeEnabled();
    await escolher(
      ui,
      /Categoria da compra de 14\/06\/2026 — Assinatura de software/,
      /Serviços de software/,
    );

    postMock.mockResolvedValue(payload([line('e1', 'lancada'), line('e2', 'lancada')]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }));
    expect(postMock).toHaveBeenCalledWith([
      { file_entry_id: 'e1', modo: 'provisionar', cod_categoria: '1.01.01' },
      { file_entry_id: 'e2', modo: 'provisionar', cod_categoria: '2.02.02' },
    ]);
  });

  it('categorias indisponíveis: avisa, oferece tentar de novo e não deixa lançar', () => {
    categoriasState.isError = true;
    categoriasState.data = undefined;
    renderDrawer();

    expect(screen.getByRole('alert')).toHaveTextContent(/Não foi possível carregar as categorias/);
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 2/ })).toBeDisabled();
  });
});

describe('Resumo do lote', () => {
  /** Direto nas duas: o resumo é o mesmo caminho para as duas formas. */
  async function lancarDireto(linhas: ReturnType<typeof line>[]) {
    const ui = userEvent.setup();
    const view = renderDrawer(DUAS);
    const lote = screen.getByRole('region', { name: 'Classificar todas de uma vez' });
    await ui.click(within(lote).getByRole('radio', { name: /Lançar direto/ }));
    await escolher(ui, /Categoria para todas as compras/, /Combustível/);
    await ui.click(screen.getByRole('button', { name: 'Aplicar a 2 compras' }));
    postMock.mockResolvedValue(payload(linhas));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }));
    return { ui, ...view };
  }

  async function provisionarUma(
    linha: ReturnType<typeof line>,
    context: Partial<PostingClientContext> = {},
  ) {
    const ui = userEvent.setup();
    const onPosted = vi.fn();
    renderDrawer([entry()], { onPosted, context: { defaultSupplierCode: 700000001, ...context } });
    await escolher(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);
    await ui.click(screen.getByRole('checkbox', { name: /Usar o fornecedor padrão/ }));
    postMock.mockResolvedValue(payload([linha]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 1 de 1/ }));
    return { ui, onPosted };
  }

  it('provisão concluída no processo da compra: título, baixa e onde entrou', async () => {
    const { onPosted } = await provisionarUma(
      line('e1', 'lancada', {
        kind: 'provisao',
        step: 'concluido',
        supplier_code: 700000001,
        payable_id: 8800001,
        payment_id: 9900001,
      }),
    );

    expect(
      await screen.findByText('Provisionada no Omie · título nº 8800001 · baixa nº 9900001'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/baixado na conta do cartão na data da compra \(10\/06\/2026\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('1 compra provisionada no Omie.')).toBeInTheDocument();
    expect(toastSuccess).toHaveBeenCalledWith('1 compra provisionada no Omie.');
    expect(onPosted).toHaveBeenCalledWith({
      e1: expect.objectContaining({ kind: 'provisao', status: 'confirmed', payable_id: 8800001 }),
    });
  });

  it('provisão no processo do vencimento: título a vencer, sem baixa', async () => {
    await provisionarUma(
      line('e1', 'lancada', {
        kind: 'provisao',
        step: 'concluido',
        supplier_code: 700000001,
        payable_id: 8800001,
      }),
      { cardPostingDateMode: 'invoice_due_date', invoiceDueDate: '2026-10-13' },
    );

    expect(await screen.findByText('Provisionada no Omie · título nº 8800001')).toBeInTheDocument();
    expect(
      screen.getByText(/Título a vencer em 13\/10\/2026 \(vencimento da fatura\), sem baixa/),
    ).toBeInTheDocument();
  });

  it('parcial: diz o passo onde parou, os ids gravados e o botão vira "Tentar novamente"', async () => {
    const { ui } = await provisionarUma(
      line('e1', 'erro', {
        reason: 'erro_omie',
        message: 'Ocorreu um erro: 1234 - conta bloqueada',
        kind: 'provisao',
        step: 'baixa',
        supplier_code: 700000001,
        payable_id: 8800001,
      }),
    );

    expect(await screen.findByText('Parou no passo: baixa')).toBeInTheDocument();
    expect(screen.getByText(/conta bloqueada/)).toBeInTheDocument();
    expect(screen.getByText(/Já gravado no Omie: título nº 8800001/)).toBeInTheDocument();
    // Título criado não é "nada lançado": aviso, nunca erro.
    expect(toastWarning).toHaveBeenCalledWith(
      'O título foi criado, mas o lançamento parou no meio. Veja onde em cada linha.',
    );
    expect(toastError).not.toHaveBeenCalled();
    expect(screen.getByText('Nenhuma compra terminou no Omie.')).toBeInTheDocument();
    // A linha passa a estar TRAVADA na provisão, sem pedir o fornecedor de novo.
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.getByText(/parou no passo baixa/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /fornecedor padrão/ })).toBeNull();

    postMock.mockClear();
    postMock.mockResolvedValue(
      payload([line('e1', 'lancada', { kind: 'provisao', payable_id: 8800001 })]),
    );
    await ui.click(screen.getByRole('button', { name: /Tentar novamente 1 de 1/ }));
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it('"já lançada" de uma provisão aparece como concluída', async () => {
    await provisionarUma(
      line('e1', 'bloqueada', {
        reason: 'ja_lancada',
        message: 'Esta compra já foi provisionada no Omie (título nº 8800001).',
        kind: 'provisao',
        step: 'concluido',
        payable_id: 8800001,
      }),
    );

    expect(await screen.findByText('Provisionada no Omie · título nº 8800001')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Concluir' })).toBeInTheDocument();
    expect(toastSuccess).toHaveBeenCalledWith('1 compra provisionada no Omie.');
  });

  it('direto parcial: motivo de cada linha, toast de aviso e a gaveta NÃO fecha', async () => {
    await lancarDireto([
      line('e1', 'lancada', { kind: 'direto', omie_lancamento_id: 5001 }),
      line('e2', 'erro', {
        reason: 'erro_omie',
        message: 'Categoria 1.01.01 nao encontrada para o cliente.',
      }),
    ]);

    await waitFor(() => expect(screen.getByText(/lançamento nº 5001/)).toBeInTheDocument());
    expect(
      screen.getByText(/Categoria 1\.01\.01 nao encontrada para o cliente\./),
    ).toBeInTheDocument();
    expect(toastWarning).toHaveBeenCalledWith('1 de 2 compras lançadas. Veja o motivo das demais.');
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('reexecução não reenvia a linha que já entrou', async () => {
    const { ui } = await lancarDireto([
      line('e1', 'lancada', { kind: 'direto', omie_lancamento_id: 5001 }),
      line('e2', 'erro', { reason: 'omie_indisponivel', message: 'O Omie não respondeu.' }),
    ]);

    postMock.mockClear();
    postMock.mockResolvedValue(
      payload([line('e2', 'lancada', { kind: 'direto', omie_lancamento_id: 5002 })]),
    );
    await ui.click(screen.getByRole('button', { name: /Tentar novamente 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([
      { file_entry_id: 'e2', cod_categoria: '1.01.01', modo: 'direto' },
    ]);
  });

  it('erro do LOTE inteiro vira toast de erro e mantém a classificação', async () => {
    const ui = userEvent.setup();
    renderDrawer(DUAS, { context: { defaultSupplierCode: 700000001 } });
    await escolher(ui, /Categoria para todas as compras/, /Combustível/);
    const lote = screen.getByRole('region', { name: 'Classificar todas de uma vez' });
    await ui.click(within(lote).getByRole('checkbox', { name: /Usar o fornecedor padrão/ }));
    await ui.click(screen.getByRole('button', { name: 'Aplicar a 2 compras' }));

    postMock.mockRejectedValue(new Error('boom'));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ })).toBeEnabled();
  });
});

describe('Acessibilidade', () => {
  it('sem violações critical/serious com o combobox ABERTO', async () => {
    const ui = userEvent.setup();
    renderDrawer();

    await ui.click(screen.getByRole('button', { name: /Categoria para todas as compras/ }));
    await screen.findByRole('listbox');

    // O conteúdo do popover é portalizado: medir `container` deixaria a lista de fora.
    await assertNoA11yViolations(document.body);
  });

  it('sem violações com a linha aberta por inteiro (divisão, rateio e cadastro novo)', async () => {
    const ui = userEvent.setup();
    findMock.mockRejectedValue(
      new ApiError(404, { code: 'FORNECEDOR_NAO_ENCONTRADO', message: 'x', userMessage: 'x' }),
    );
    renderDrawer([entry()]);

    await ui.click(screen.getByRole('switch', { name: /Dividir em categorias/ }));
    await ui.click(screen.getByRole('switch', { name: /Ratear por departamento/ }));
    await buscarFornecedor(ui, /Buscar fornecedor no Omie — 10\/06\/2026/, CNPJ_NEW);
    await screen.findByLabelText('Razão social');

    await assertNoA11yViolations(document.body);
  });

  it('o combobox navega e escolhe só por teclado (WCAG 2.1.1)', async () => {
    const ui = userEvent.setup();
    renderDrawer([entry()]);

    await ui.click(screen.getByRole('button', { name: /Categoria da compra de/ }));
    const busca = await screen.findByRole('combobox');
    // Busca por digitação: "servicos" (sem acento) acha "Serviços de software".
    await ui.type(busca, 'servicos');
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getAllByRole('option')).toHaveLength(1);
    expect(busca).toHaveAttribute('aria-activedescendant');

    await ui.keyboard('{Enter}');

    expect(
      screen.getByRole('button', {
        name: /Categoria da compra de.*2\.02\.02 · Serviços de software/,
      }),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Fábricas do contrato (shapes do `OmiePostingBatchPayload` gerado)
// ---------------------------------------------------------------------------

function line(
  fileEntryId: string,
  status: 'lancada' | 'bloqueada' | 'erro',
  over: Partial<OmiePostingBatchPayload['lines'][number]> = {},
): OmiePostingBatchPayload['lines'][number] {
  return { file_entry_id: fileEntryId, status, ...over };
}

function payload(lines: OmiePostingBatchPayload['lines']): OmiePostingBatchPayload {
  return {
    lines,
    lancadas: lines.filter((l) => l.status === 'lancada').length,
    bloqueadas: lines.filter((l) => l.status === 'bloqueada').length,
    com_erro: lines.filter((l) => l.status === 'erro').length,
  };
}
