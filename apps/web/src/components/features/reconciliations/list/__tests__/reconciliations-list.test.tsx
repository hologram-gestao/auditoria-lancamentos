/**
 * Testes da Lista de Conciliações (FRONT 04.5 / R1).
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - filtros de conta + mês + status combinados (E), lidos DA URL e escritos
 *     NA URL, sempre voltando para a página 1;
 *   - rodapé de paginação com `x–y de N` + página atual/total;
 *   - cada item mostra conta, mês, badge de status, nº de arquivos e, quando
 *     processada, os contadores;
 *   - linha clicável leva ao detalhe e ação interna não dispara a navegação;
 *   - estados vazio / erro;
 *   - axe-core sem violações `critical`/`serious`.
 *
 * Os componentes Radix REAIS são usados (nada de stub de `ui/select`): é o
 * markup real que precisa passar no axe. jsdom não implementa as APIs de
 * ponteiro que o Radix consulta, então o `beforeAll` abaixo as preenche.
 */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
const pushMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: pushMock }),
  usePathname: () => '/clientes/c1',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

const listState = {
  data: undefined as
    | { data: ReconciliationSessionSummary[]; pagination: Record<string, number> }
    | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
/** Último `params` que a lista mandou para o hook — prova o "URL → request". */
let lastQueryParams: ReconciliationsListParams | undefined;

vi.mock('@/hooks/use-clients', () => ({
  useReconciliationsList: (_id: string, params: ReconciliationsListParams) => {
    lastQueryParams = params;
    return listState;
  },
}));

vi.mock('@/hooks/use-reconciliations', () => ({
  useCancelReconciliation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDiscardReconciliation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReprocessReconciliation: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

// Imports do SUT DEPOIS dos `vi.mock`: as factories acima fecham sobre
// variáveis deste módulo, e importar o SUT no topo as avaliaria antes da
// inicialização (TDZ).
import { ReconciliationsList } from '@/components/features/reconciliations/list/reconciliations-list';
import type { ReconciliationSessionSummary, ReconciliationsListParams } from '@/lib/api/clients';
import { assertNoA11yViolations } from '@/test/a11y';

const ACCOUNTS = [
  {
    id: 'a1',
    omie_conta_id: 10,
    name: 'Cartão Itaú',
    bank_name: 'Itaú',
    account_type: 'CR',
    synced_at: '2026-06-01T00:00:00Z',
  },
];

function session(over: Partial<ReconciliationSessionSummary> = {}): ReconciliationSessionSummary {
  return {
    id: 's1',
    omie_conta_id: 10,
    account_type: 'credit_card',
    reference_month: '2026-06-01',
    status: 'reviewing',
    created_at: '2026-06-12T14:32:00Z',
    total_file_entries: 30,
    conciliated_count: 25,
    sem_omie_count: 3,
    omie_sem_arquivo_count: 2,
    anomaly_count: 1,
    error_message: null,
    error_code: null,
    total_files: 3,
    ...over,
  };
}

function renderList() {
  return render(<ReconciliationsList clientId="c1" accounts={ACCOUNTS} onCreateClick={vi.fn()} />);
}

beforeAll(() => {
  // Radix consulta estas APIs; jsdom não as implementa.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  replaceMock.mockClear();
  pushMock.mockClear();
  currentSearch = '';
  lastQueryParams = undefined;
  listState.data = {
    data: [session()],
    pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
  };
  listState.isLoading = false;
  listState.isFetching = false;
  listState.isError = false;
  listState.error = null;
});

describe('ReconciliationsList — item da lista', () => {
  it('mostra conta, mês, badge de status, nº de arquivos e contadores', () => {
    renderList();
    const item = screen.getByRole('link', { name: /Abrir conciliação de Cartão Itaú/ });
    expect(within(item).getByText('Cartão Itaú')).toBeVisible();
    expect(within(item).getByText('Junho de 2026')).toBeVisible();
    // Vocabulário do produto: `reviewing` no banco é "Processada" na tela.
    expect(within(item).getByText('Processada')).toBeVisible();
    expect(within(item).getByText('3 arquivos')).toBeVisible();
    expect(within(item).getByText('25 conciliados')).toBeVisible();
    expect(within(item).getByText('3 sem Omie')).toBeVisible();
    expect(within(item).getByText('2 Omie sem arquivo')).toBeVisible();
  });

  it('mostra QUEM conciliou, com o e-mail na dica (86e2n39f1)', () => {
    listState.data = {
      data: [session({ created_by: { name: 'Ana da Hologram', email: 'ana@hologram.com.br' } })],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    renderList();
    expect(screen.getByText(/Conciliado por/)).toBeVisible();
    expect(
      screen.getByRole('img', { name: 'Ana da Hologram — ana@hologram.com.br' }),
    ).toBeVisible();
  });

  it('autor mascarado ("Equipe Hologram") aparece SEM dica — não há nada a revelar', () => {
    listState.data = {
      data: [session({ created_by: { name: 'Equipe Hologram', email: null } })],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    renderList();
    expect(screen.getByText(/Conciliado por/)).toBeVisible();
    expect(screen.getByText('Equipe Hologram')).toBeVisible();
    expect(screen.queryByRole('img', { name: /Equipe Hologram/ })).not.toBeInTheDocument();
  });

  it('payload antigo sem autor mantém o "Criada em" de antes', () => {
    renderList();
    expect(screen.getByText(/Criada em/)).toBeVisible();
    expect(screen.queryByText(/Conciliado por/)).not.toBeInTheDocument();
  });

  it('a linha inteira é clicável e leva ao detalhe', async () => {
    const user = userEvent.setup();
    renderList();
    await user.click(screen.getByRole('link', { name: /Abrir conciliação de Cartão Itaú/ }));
    expect(pushMock).toHaveBeenCalledWith('/clientes/c1/conciliacao/s1');
  });

  it('ação interna NÃO dispara a navegação da linha (stopPropagation)', async () => {
    const user = userEvent.setup();
    renderList();
    await user.click(screen.getByRole('button', { name: 'Excluir' }));
    expect(pushMock).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeVisible();
  });

  it('em erro mostra o CÓDIGO, nunca a linguagem interna', () => {
    listState.data = {
      data: [
        session({
          status: 'error',
          error_code: 'ADL-PARSE-LIMIT',
          error_message: 'anthropic token limit exceeded',
        }),
      ],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    renderList();
    expect(screen.getByText(/cód\. ADL-PARSE-LIMIT/)).toBeVisible();
    expect(screen.queryByText(/token/i)).toBeNull();
    // Contadores não aparecem em sessão que falhou (não há o que contar).
    expect(screen.queryByText(/conciliados/)).toBeNull();
  });
});

describe('ReconciliationsList — filtros na URL', () => {
  it('lê os três filtros da URL e os combina no request (E)', () => {
    currentSearch = 'conta=10&mes=2026-06&status=error&page=2&pageSize=50';
    renderList();
    expect(lastQueryParams).toEqual({
      page: 2,
      pageSize: 50,
      omie_conta_id: 10,
      month: '2026-06',
      status: 'error',
    });
  });

  it('escreve o mês na querystring e volta para a página 1', async () => {
    const user = userEvent.setup();
    currentSearch = 'page=4';
    renderList();
    const monthInput = screen.getByLabelText('Filtrar por mês de referência');
    await user.type(monthInput, '2026-06');
    // `page` sai da URL (= página 1) no MESMO replace do filtro.
    expect(replaceMock).toHaveBeenLastCalledWith('/clientes/c1?mes=2026-06', { scroll: false });
  });

  it('escreve o status escolhido no select', async () => {
    const user = userEvent.setup();
    renderList();
    await user.click(screen.getByRole('combobox', { name: 'Filtrar por status' }));
    await user.click(screen.getByRole('option', { name: 'Em processamento' }));
    expect(replaceMock).toHaveBeenLastCalledWith('/clientes/c1?status=processing', {
      scroll: false,
    });
  });

  it('valor inválido na URL é ignorado em vez de virar 4xx no backend', () => {
    // `mes` fora do padrão e `status` que só existe no BANCO (não no filtro).
    currentSearch = 'mes=2026-13&status=reviewing&page=abc';
    renderList();
    expect(lastQueryParams).toEqual({ page: 1, pageSize: 20 });
    expect(screen.getByLabelText('Filtrar por mês de referência')).toHaveValue('');
  });

  it('"Limpar filtros" só aparece com filtro ativo e remove todos', async () => {
    const user = userEvent.setup();
    renderList();
    expect(screen.queryByRole('button', { name: 'Limpar filtros' })).toBeNull();

    currentSearch = 'conta=10&mes=2026-06&status=error&page=2';
    renderList();
    await user.click(screen.getAllByRole('button', { name: 'Limpar filtros' })[0]!);
    expect(replaceMock).toHaveBeenLastCalledWith('/clientes/c1', { scroll: false });
  });
});

describe('ReconciliationsList — paginação e estados', () => {
  it('rodapé mostra x–y de N e a página atual/total', () => {
    currentSearch = 'page=2&pageSize=20';
    listState.data = {
      data: [session()],
      pagination: { page: 2, pageSize: 20, total: 45, totalPages: 3 },
    };
    renderList();
    const footer = screen.getByRole('navigation', { name: 'Paginação de conciliações' });
    expect(within(footer).getByText('21–40 de 45')).toBeVisible();
    expect(within(footer).getByText('Página 2 de 3')).toBeVisible();
  });

  it('estado vazio sem filtro convida a criar a primeira conciliação', () => {
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    renderList();
    expect(
      screen.getByText('Nenhuma conciliação. Clique em "Criar conciliação" para começar.'),
    ).toBeVisible();
    // O rodapé continua visível, dizendo 0–0 de 0 (e não 1–0).
    expect(screen.getByText('0–0 de 0')).toBeVisible();
  });

  it('estado vazio COM filtro diz que o recorte é que não achou nada', () => {
    currentSearch = 'status=error';
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    renderList();
    expect(screen.getByText('Nenhuma conciliação encontrada com esses filtros.')).toBeVisible();
  });

  it('estado de erro oferece "Tentar novamente"', () => {
    listState.isError = true;
    listState.data = undefined;
    renderList();
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });

  it('estado de carregamento mostra o skeleton', () => {
    listState.isLoading = true;
    listState.data = undefined;
    renderList();
    expect(screen.getByLabelText('Carregando conciliações')).toBeInTheDocument();
  });
});

describe('ReconciliationsList — área rolável (86e2u4nxg)', () => {
  /**
   * jsdom não tem layout, então aqui NÃO se mede sobreposição — quem mede é o
   * `e2e/a11y-mocked.spec.ts` no browser real (job `web_a11y`). O que esta
   * trava impede é a causa-raiz voltar num refactor: o container dos cards é
   * `min-h-0 flex-1`, ou seja, autorizado a encolher abaixo da altura do
   * conteúdo. Sem `overflow` os cards são pintados FORA da caixa e a barra de
   * paginação, opaca, cobre o que vazou.
   */
  it('os cards vivem numa região rolável, alcançável pelo teclado', () => {
    renderList();

    const region = screen.getByRole('region', { name: 'Lista de conciliações' });
    expect(region).toHaveClass('overflow-auto');
    expect(region).toHaveAttribute('tabindex', '0');
    expect(region).toContainElement(
      screen.getByRole('link', { name: /Abrir conciliação de Cartão Itaú/ }),
    );
  });

  it('a barra de paginação fica FORA da região rolável (não rola junto)', () => {
    renderList();

    const region = screen.getByRole('region', { name: 'Lista de conciliações' });
    const footer = screen.getByRole('navigation', { name: 'Paginação de conciliações' });
    expect(region).not.toContainElement(footer);
  });

  it('o estado de carregamento também mora dentro da região', () => {
    listState.isLoading = true;
    listState.data = undefined;
    renderList();

    expect(screen.getByRole('region', { name: 'Lista de conciliações' })).toContainElement(
      screen.getByLabelText('Carregando conciliações'),
    );
  });
});

describe('ReconciliationsList — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = renderList();
    await assertNoA11yViolations(container);
  });

  it('não tem violações critical/serious no estado vazio', async () => {
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    const { container } = renderList();
    await assertNoA11yViolations(container);
  });
});
