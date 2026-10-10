/**
 * Testes da tela "Carteira" do cliente (FRONT 11.7 / R3 · R4 · R5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Não é o `web_a11y` — aquele roda só
 * `e2e/a11y-mocked.spec.ts`, onde ficam os cenários que precisam de CSS
 * computado e de browser real.
 *
 * Cobre os critérios de aceite verificáveis em jsdom:
 *   - o bloco de agregados exibe os quatro baldes POR TIPO, vindos do servidor,
 *     em BRL pelo helper existente (e sem somar nada no navegador);
 *   - cada valor dos cards é um botão que aplica o recorte pela URL conforme a
 *     tabela da 86e3eq9uy (`summaryFilterParams`), com `aria-pressed` e desfazer
 *     no segundo clique; os filtros ativos viram etiquetas removíveis;
 *   - filtros (tipo, situação, balde) e ordenação chegam ao SERVIDOR pela query
 *     — incluindo `pageSize`, sempre presente mesmo no default;
 *   - a página rola e a tabela não: sem `fill`, com o cabeçalho grudado na
 *     rolagem da página (`stickyHeader="page"`), sete colunas, clique na linha
 *     abre a gaveta de contexto;
 *   - `client_operator` LÊ mas **não** vê a ação de sincronizar (oculta, não
 *     desabilitada); `client_manager` vê;
 *   - nome não resolvido mostra o CÓDIGO com a marcação explícita, nunca vazio
 *     e nunca o código posando de nome;
 *   - carteira nunca sincronizada → ação de sincronizar, **sem zeros**; última
 *     tentativa falhou → agregados anteriores + data + aviso;
 *   - cliente encerrado: ação indisponível **com o motivo**;
 *   - loading, erro com "Tentar novamente", botão desabilitado durante o
 *     sync, e axe-core sem `critical`/`serious`.
 *
 * Os componentes Radix REAIS são usados (nada de stub de `ui/select`): é o
 * markup real que precisa passar no axe. jsdom não implementa as APIs de
 * ponteiro que o Radix consulta, então o `beforeAll` abaixo as preenche.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const replaceMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => '/clientes/c1/carteira',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const listState = {
  data: undefined as { data: ClientTitle[]; pagination: Record<string, number> } | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};

const summaryState = {
  data: undefined as TitlesSummary | undefined,
  isLoading: false,
  isError: false,
};

const syncState = { mutateAsync: vi.fn(), isPending: false };

const historyState = {
  data: undefined as TitleContext[] | undefined,
  isLoading: false,
  isError: false,
};

const registerContextState = { mutateAsync: vi.fn(), isPending: false };

/** Último `params` que a tela mandou para o hook — prova o "URL → request". */
let lastQueryParams: ListClientTitlesParams | undefined;

vi.mock('@/hooks/use-client-titles', () => ({
  useClientTitlesList: (_clientId: string, params: ListClientTitlesParams) => {
    lastQueryParams = params;
    return listState;
  },
  useClientTitlesSummary: () => summaryState,
  useSyncClientTitles: () => syncState,
  useTitleContextHistory: () => historyState,
  useRegisterTitleContext: () => registerContextState,
}));

const clientDetailState = {
  data: undefined as { closed_at: string | null; origin_status: string } | undefined,
};

vi.mock('@/hooks/use-clients', () => ({
  useClientDetail: () => clientDetailState,
}));

const authState = { user: null as AuthenticatedUser | null };

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (state: { user: AuthenticatedUser | null }) => unknown) =>
    selector(authState),
}));

// Imports do SUT DEPOIS dos `vi.mock` (as factories fecham sobre variáveis
// deste módulo; importar antes as avaliaria na TDZ).
import {
  ClientTitlesScreen,
  DEFAULT_PAGE_SIZE,
} from '@/components/features/client-titles/client-titles-screen';
import {
  isSummaryFilterActive,
  summaryFilterParams,
} from '@/components/features/client-titles/client-titles-summary';
import { buildClientTitlesQuery, type ListClientTitlesParams } from '@/lib/api/client-titles';
import type {
  AgingTotals,
  AuthenticatedUser,
  ClientTitle,
  TitleContext,
  TitlesSummary,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const TENANT = '11111111-1111-4111-8111-111111111111';

const clientManager: AuthenticatedUser = {
  id: 'cm',
  email: 'gerente@cliente.com.br',
  name: 'Gerente do Cliente',
  role: 'client_manager',
  scope: 'client',
  client_id: TENANT,
  organization_id: '0706eeb5-9718-4d03-bcda-ef615789e6ac',
  organization_name: 'Hologram',
};
const clientOperator: AuthenticatedUser = { ...clientManager, id: 'co', role: 'client_operator' };

function title(overrides: Partial<ClientTitle> = {}): ClientTitle {
  return {
    id: '99999999-9999-4999-8999-999999999999',
    externalId: '4010',
    titleType: 'a_receber',
    dueDate: '2026-06-10',
    amount: '1500.00',
    status: 'em_aberto',
    overdueDays: 106,
    bucket: '90_mais',
    categoryCode: '1.01.01',
    supplierCode: 2624256082,
    supplierName: 'Padaria Aurora Ltda',
    supplierNameResolved: true,
    omieContaId: 777,
    documentNumber: 'NF 1234',
    lastSyncedAt: '2026-09-24T09:00:00Z',
    contextCount: 0,
    ...overrides,
  };
}

/**
 * Os agregados da base consolidada de 17/06/2026, que a sprint torna
 * deriváveis: 107.413,10 em aberto e 82.865,50 no balde de 90+.
 */
function totals(overrides: Partial<AgingTotals> = {}): AgingTotals {
  return {
    totalEmAberto: '107413.10',
    totalAVencer: '10000.00',
    totalVencido: '97413.10',
    bucket1a30: '8000.00',
    bucket31a60: '3547.60',
    bucket61a90: '3000.00',
    bucket90Mais: '82865.50',
    qtdEmAberto: 148,
    qtdAVencer: 12,
    qtdVencido: 136,
    ...overrides,
  };
}

function zeroTotals(): AgingTotals {
  return {
    totalEmAberto: '0.00',
    totalAVencer: '0.00',
    totalVencido: '0.00',
    bucket1a30: '0.00',
    bucket31a60: '0.00',
    bucket61a90: '0.00',
    bucket90Mais: '0.00',
    qtdEmAberto: 0,
    qtdAVencer: 0,
    qtdVencido: 0,
  };
}

function summary(overrides: Partial<TitlesSummary> = {}): TitlesSummary {
  return {
    aReceber: totals(),
    aPagar: totals({ totalEmAberto: '20000.00', qtdEmAberto: 30 }),
    neverSynced: false,
    syncedAt: '2026-09-24T09:00:00Z',
    syncFailedAt: null,
    referenceDate: '2026-09-24',
    ...overrides,
  };
}

/**
 * `formatBRL` usa espaço NÃO-quebrável — locator com espaço normal nunca casa,
 * e é um defeito que já custou uma rodada de gate (S7). Recebe o valor como se
 * lê na tela ("82.865,50") e escapa o ponto de milhar.
 */
const brl = (valor: string) => new RegExp(`R\\$\\s*${valor.replace(/\./g, '\\.')}`);

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  currentSearch = '';
  replaceMock.mockClear();
  lastQueryParams = undefined;
  authState.user = clientManager;
  clientDetailState.data = { closed_at: null, origin_status: 'ativa' };
  syncState.mutateAsync = vi.fn().mockResolvedValue({});
  syncState.isPending = false;
  listState.data = {
    data: [
      title(),
      title({
        externalId: '4011',
        titleType: 'a_pagar',
        dueDate: '2026-10-05',
        amount: '250.90',
        overdueDays: 0,
        bucket: 'a_vencer',
        supplierName: null,
        supplierNameResolved: false,
        documentNumber: null,
      }),
    ],
    pagination: { page: 1, pageSize: 20, total: 2, totalPages: 1 },
  };
  listState.isLoading = false;
  listState.isFetching = false;
  listState.isError = false;
  summaryState.data = summary();
  summaryState.isLoading = false;
  summaryState.isError = false;
  historyState.data = [];
  historyState.isLoading = false;
  historyState.isError = false;
  registerContextState.mutateAsync = vi.fn().mockResolvedValue({});
  registerContextState.isPending = false;
});

describe('ClientTitlesScreen — agregados e aging (R3)', () => {
  it('mostra os quatro baldes POR TIPO, com os valores do servidor em BRL', () => {
    render(<ClientTitlesScreen clientId="c1" />);

    const aReceber = within(screen.getByRole('region', { name: 'A receber' }));
    for (const rotulo of ['1 a 30 dias', '31 a 60 dias', '61 a 90 dias', '90+ dias']) {
      expect(aReceber.getByText(rotulo, { selector: 'dt' })).toBeVisible();
    }
    // O balde que a reunião procura primeiro, no PAR rótulo/valor.
    const noventaMais = aReceber.getByText('90+ dias', { selector: 'dt' });
    expect(noventaMais.parentElement).toHaveTextContent(brl('82.865,50'));

    // Os dois tipos existem e são blocos separados: somá-los num total único
    // daria o número que não serve para decisão nenhuma.
    expect(screen.getByRole('region', { name: 'A pagar' })).toBeVisible();
  });

  it('não soma nada no navegador: os totais não vêm da página', () => {
    // A página traz 2 linhas de R$ 1.500,00 e R$ 250,90; o bloco continua
    // dizendo 107.413,10. Se alguém trocar o servidor por um `reduce`, cai.
    render(<ClientTitlesScreen clientId="c1" />);
    const aReceber = within(screen.getByRole('region', { name: 'A receber' }));
    expect(aReceber.getByText('Em aberto', { selector: 'dt' }).parentElement).toHaveTextContent(
      brl('107.413,10'),
    );
  });

  it('a referência do atraso é a data do SERVIDOR, e aparece na tela, sem "aging"', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    expect(
      screen.getByText(/Atraso calculado sobre a carteira inteira, com referência em 24\/09\/2026/),
    ).toBeVisible();
    expect(screen.queryByText(/aging/i)).toBeNull();
  });

  it('"Atualizado em" mostra a última sincronização íntegra para todo leitor', () => {
    authState.user = clientOperator;
    render(<ClientTitlesScreen clientId="c1" />);
    // O horário depende do fuso do runner — o dia não.
    expect(screen.getByTestId('titles-synced-at')).toHaveTextContent(/Atualizado em 24\/09\/2026/);
  });

  it('"Atualizado em" some enquanto o resumo carrega e quando nunca sincronizou', () => {
    summaryState.isLoading = true;
    summaryState.data = undefined;
    const { unmount } = render(<ClientTitlesScreen clientId="c1" />);
    expect(screen.queryByTestId('titles-synced-at')).toBeNull();
    unmount();

    summaryState.isLoading = false;
    summaryState.data = summary({
      aReceber: zeroTotals(),
      aPagar: zeroTotals(),
      neverSynced: true,
      syncedAt: null,
    });
    render(<ClientTitlesScreen clientId="c1" />);
    expect(screen.queryByTestId('titles-synced-at')).toBeNull();
  });

  it('carregando os agregados mostra o skeleton proporcional', () => {
    summaryState.isLoading = true;
    summaryState.data = undefined;
    render(<ClientTitlesScreen clientId="c1" />);
    expect(screen.getByLabelText('Carregando os agregados da carteira')).toBeInTheDocument();
  });

  /**
   * As três parcelas em `grid-cols-3` fixo imprimiam os três valores UM POR
   * CIMA DO OUTRO a 390px (colunas de ~95px para um `text-xl whitespace-nowrap`
   * de ~150px). O `whitespace-nowrap` do valor NÃO sai — valor monetário que
   * quebra depois do hífen vira outro número (S7) —, então quem cede é a
   * contagem de colunas. Sobreposição de verdade só o browser mede: o e2e
   * compara `boundingBox()` dos três valores.
   */
  it('as três parcelas empilham até sm (não dividem 390px em três colunas)', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    const aReceber = within(screen.getByRole('region', { name: 'A receber' }));
    const parcelas = aReceber.getByText('Em aberto', { selector: 'dt' }).closest('dl');
    expect(parcelas).toHaveClass('grid-cols-1', 'sm:grid-cols-3');
    expect(parcelas).not.toHaveClass('grid-cols-3');
  });
});

/**
 * Parte B da 86e3eq9uy: a tabela botão → parâmetros é a fonte única do recorte
 * dos cards, e decide também quando um botão está ativo.
 */
describe('summaryFilterParams — o mapeamento dos cards para a URL', () => {
  it('mapeia os sete valores de um card para os parâmetros do servidor', () => {
    expect(summaryFilterParams('a_receber', 'em_aberto')).toEqual({
      type: 'a_receber',
      situation: 'em_aberto',
      bucket: null,
    });
    expect(summaryFilterParams('a_receber', 'a_vencer')).toEqual({
      type: 'a_receber',
      situation: null,
      bucket: 'a_vencer',
    });
    expect(summaryFilterParams('a_pagar', 'vencido')).toEqual({
      type: 'a_pagar',
      situation: 'vencido',
      bucket: null,
    });
    for (const bucket of ['1_30', '31_60', '61_90', '90_mais'] as const) {
      expect(summaryFilterParams('a_pagar', bucket)).toEqual({
        type: 'a_pagar',
        situation: null,
        bucket,
      });
    }
  });

  it('ativo só quando os TRÊS parâmetros batem exatamente', () => {
    const atual = { type: 'a_receber', situation: null, bucket: '90_mais' } as const;
    expect(isSummaryFilterActive(atual, 'a_receber', '90_mais')).toBe(true);
    // Mesmo balde, outro tipo: não é o mesmo recorte.
    expect(isSummaryFilterActive(atual, 'a_pagar', '90_mais')).toBe(false);
    // Balde com situação junto (link antigo): não é a linha da tabela.
    expect(
      isSummaryFilterActive(
        { type: 'a_receber', situation: 'vencido', bucket: '90_mais' },
        'a_receber',
        '90_mais',
      ),
    ).toBe(false);
    // Só o tipo, sem situação nem balde, não acende nenhum dos sete.
    const soTipo = { type: 'a_receber', situation: null, bucket: null } as const;
    for (const key of ['em_aberto', 'a_vencer', 'vencido', '1_30', '90_mais'] as const) {
      expect(isSummaryFilterActive(soTipo, 'a_receber', key)).toBe(false);
    }
  });
});

describe('ClientTitlesScreen — totais que filtram (86e3eq9uy, Parte B)', () => {
  it('cada valor é um botão com nome acessível de rótulo, tipo e valor, dentro do <dd>', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    const botao = screen.getByRole('button', {
      name: 'A receber, 90+ dias: R$\u00a082.865,50. Filtrar a lista',
    });
    expect(botao).toHaveAttribute('aria-pressed', 'false');
    expect(botao.closest('dd')).not.toBeNull();
    // Os três de cima levam a contagem no nome.
    expect(
      screen.getByRole('button', {
        name: 'A receber, Em aberto: R$\u00a0107.413,10, 148 títulos. Filtrar a lista',
      }),
    ).toBeVisible();
    // 7 por card, 2 cards.
    expect(screen.getAllByRole('button', { name: /Filtrar a lista$/ })).toHaveLength(14);
  });

  it('clicar em "90+ dias" de A receber aplica type + bucket pela URL, com a página zerada', async () => {
    const user = userEvent.setup();
    currentSearch = 'page=3';
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: /^A receber, 90\+ dias/ }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('type=a_receber');
    expect(url).toContain('bucket=90_mais');
    expect(url).not.toContain('situation=');
    expect(url).not.toContain('page=');
  });

  it('"Em aberto" leva situation=em_aberto (o total da paginação bate com o card)', async () => {
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: /^A pagar, Em aberto/ }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('type=a_pagar');
    expect(url).toContain('situation=em_aberto');
    expect(url).not.toContain('bucket=');
  });

  it('o botão do recorte atual fica aria-pressed, e o segundo clique desfaz os três parâmetros', async () => {
    const user = userEvent.setup();
    currentSearch = 'type=a_receber&bucket=90_mais';
    render(<ClientTitlesScreen clientId="c1" />);
    const ativo = screen.getByRole('button', { name: /^A receber, 90\+ dias/ });
    expect(ativo).toHaveAttribute('aria-pressed', 'true');
    // Só ELE: o mesmo balde do outro card não acende.
    expect(screen.getByRole('button', { name: /^A pagar, 90\+ dias/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(screen.getAllByRole('button', { name: /Filtrar a lista$/, pressed: true })).toHaveLength(
      1,
    );

    await user.click(ativo);
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).not.toContain('type=');
    expect(url).not.toContain('bucket=');
    expect(url).not.toContain('situation=');
  });

  it('link antigo com situation e bucket juntos continua funcionando: filtra e mostra as etiquetas', () => {
    currentSearch = 'type=a_pagar&situation=vencido&bucket=61_90&hasNoContext=true';
    render(<ClientTitlesScreen clientId="c1" />);
    expect(lastQueryParams).toMatchObject({
      type: 'a_pagar',
      situation: 'vencido',
      bucket: '61_90',
      hasNoContext: true,
    });
    const etiquetas = within(screen.getByRole('list', { name: 'Filtros ativos' }));
    for (const rotulo of ['A pagar', 'Vencido', '61 a 90 dias', 'Sem contexto']) {
      expect(etiquetas.getByRole('button', { name: `Remover filtro ${rotulo}` })).toBeVisible();
    }
    // Nenhum botão dos cards acende: a combinação não é uma linha da tabela.
    expect(screen.queryAllByRole('button', { name: /Filtrar a lista$/, pressed: true })).toEqual(
      [],
    );
  });

  it('remover uma etiqueta limpa SÓ aquele parâmetro e zera a página', async () => {
    const user = userEvent.setup();
    currentSearch = 'type=a_receber&bucket=90_mais&page=2';
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Remover filtro 90+ dias' }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('type=a_receber');
    expect(url).not.toContain('bucket=');
    expect(url).not.toContain('page=');
  });
});

describe('ClientTitlesScreen — barra de filtros compacta (86e3eq9uy, Parte C)', () => {
  it('Tipo é um grupo de três botões com aria-pressed; Situação e Balde saíram da barra', async () => {
    const user = userEvent.setup();
    currentSearch = 'type=a_pagar&page=2';
    render(<ClientTitlesScreen clientId="c1" />);
    const grupo = within(screen.getByRole('group', { name: 'Tipo' }));
    expect(grupo.getByRole('button', { name: 'Todos' })).toHaveAttribute('aria-pressed', 'false');
    expect(grupo.getByRole('button', { name: 'A receber' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(grupo.getByRole('button', { name: 'A pagar' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByLabelText('Situação')).toBeNull();
    expect(screen.queryByLabelText('Balde de atraso')).toBeNull();

    await user.click(grupo.getByRole('button', { name: 'A receber' }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('type=a_receber');
    expect(url).not.toContain('page=');

    await user.click(grupo.getByRole('button', { name: 'Todos' }));
    expect(String(replaceMock.mock.calls.at(-1)?.[0])).not.toContain('type=');
  });

  it('"Ordenar por" mantém o nome acessível, com o rótulo visível dentro do gatilho', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    const gatilho = screen.getByLabelText('Ordenar por');
    expect(gatilho).toHaveTextContent('Ordenar:');
    expect(gatilho).toHaveTextContent('Vencimento (mais antigo)');
  });
});

describe('ClientTitlesScreen — lista', () => {
  it('mostra as sete colunas: vencimento, atraso com o selo, tipo, devedor, valor, situação e contexto', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    const rows = screen.getAllByRole('row');
    // 1 cabeçalho + 2 títulos.
    expect(rows).toHaveLength(3);
    const cabecalhos = within(rows[0]!)
      .getAllByRole('columnheader')
      .map((th) => th.textContent);
    expect(cabecalhos).toEqual([
      'Vencimento',
      'Atraso',
      'Tipo',
      'Devedor / credor',
      'Valor',
      'Situação',
      'Contexto',
    ]);
    const first = within(rows[1]!);
    expect(first.getByText('10/06/2026')).toBeVisible();
    // Atraso: "N dias" + o selo do balde, na mesma célula; sem o "de atraso".
    expect(first.getByText('106 dias')).toBeVisible();
    expect(first.getByText('90+ dias')).toBeVisible();
    expect(first.getByText('A receber')).toBeVisible();
    expect(first.getByText('Padaria Aurora Ltda')).toBeVisible();
    expect(first.getByText(brl('1.500,00'))).toBeVisible();
    expect(first.getByText('Em aberto')).toBeVisible();
    expect(first.getByText('Doc. NF 1234')).toBeVisible();
    // Sem atraso: travessão, e o selo "A vencer".
    const second = within(rows[2]!);
    expect(second.getByText('—')).toBeVisible();
    expect(second.getByText('A vencer')).toBeVisible();
  });

  it('clicar na linha abre a gaveta de contexto do título certo (sem tabIndex/role no <tr>)', async () => {
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    const linha = screen.getAllByRole('row')[2]!;
    expect(linha).not.toHaveAttribute('tabindex');
    expect(linha).not.toHaveAttribute('role', 'button');
    expect(linha).toHaveClass('cursor-pointer');

    await user.click(within(linha).getByText('Nome não resolvido'));
    expect(await screen.findByRole('heading', { name: 'Contexto do título' })).toBeVisible();
    // A gaveta é do título clicado (4011: vence em 05/10/2026, R$ 250,90), não
    // do primeiro da lista.
    const gaveta = screen.getByRole('dialog');
    expect(gaveta).toHaveTextContent('Vencimento 05/10/2026');
    expect(gaveta).toHaveTextContent(brl('250,90'));
  });

  it('o botão da coluna Contexto não abre a gaveta duas vezes', async () => {
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: /Contexto do título 4010/ }));
    expect(await screen.findAllByRole('dialog')).toHaveLength(1);
  });

  it('sem view_title_context a linha não é clicável e a coluna Contexto não existe', () => {
    // Não há papel sem `view_title_context` na matriz de hoje (todos leem), então
    // o caso é simulado pelo tipo de usuário mais restrito: se algum dia a
    // célula virar ❌, é aqui que a tela tem de continuar coerente.
    render(<ClientTitlesScreen clientId="c1" />);
    const cabecalhos = within(screen.getAllByRole('row')[0]!).getAllByRole('columnheader');
    expect(cabecalhos.at(-1)).toHaveTextContent('Contexto');
  });

  it('nome não resolvido mostra o CÓDIGO marcado, nunca célula vazia', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    const segunda = within(screen.getAllByRole('row')[2]!);
    expect(segunda.getByText('2624256082')).toBeVisible();
    expect(segunda.getByText('Nome não resolvido')).toBeVisible();
    // A dica é acessível (role="img" + aria-label), nunca `title` nativo.
    expect(
      segunda.getByRole('img', { name: /Código 2624256082 — nome não resolvido/ }),
    ).toBeVisible();
  });

  it('a linha diz se o título já tem contexto, no visual e no nome acessível (R2)', () => {
    listState.data = {
      data: [
        title({ id: '11111111-1111-4111-8111-111111111111', externalId: '4010', contextCount: 2 }),
        title({ id: '22222222-2222-4222-8222-222222222222', externalId: '4011', contextCount: 0 }),
      ],
      pagination: { page: 1, pageSize: 20, total: 2, totalPages: 1 },
    };
    render(<ClientTitlesScreen clientId="c1" />);
    const comContexto = screen.getByRole('button', {
      name: 'Contexto do título 4010 (2 registrados)',
    });
    expect(comContexto).toHaveTextContent('2');
    const semContexto = screen.getByRole('button', {
      name: 'Contexto do título 4011 (nenhum registrado)',
    });
    expect(semContexto).not.toHaveTextContent(/\d/);
  });

  it('título sem devedor não é "não resolvido": não há o que resolver', () => {
    listState.data = {
      data: [title({ supplierCode: null, supplierName: null, supplierNameResolved: true })],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    render(<ClientTitlesScreen clientId="c1" />);
    expect(screen.getByText('Sem devedor informado')).toBeVisible();
    expect(screen.queryByText('Nome não resolvido')).toBeNull();
  });

  /**
   * `bg-destructive/10` dava 4,22:1 no escuro e 4,42:1 no Hologram (axe
   * `serious`, AA pede 4,5:1 em 12px): o alfa mistura o vermelho com o fundo da
   * página e apaga o contraste. `destructive-muted` é o token OPACO de fundo de
   * badge — mesma convenção que `warning`/`success`/`info` no arquivo.
   */
  it('o balde vencido usa o token OPACO de fundo, nunca destructive com alfa', () => {
    listState.data = {
      data: [title({ bucket: '90_mais' })],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    render(<ClientTitlesScreen clientId="c1" />);
    const badge = within(screen.getAllByRole('row')[1]!).getByText('90+ dias');
    expect(badge).toHaveClass('bg-destructive-muted', 'text-destructive');
    expect(badge.className).not.toContain('bg-destructive/');
  });

  it('título liquidado não ganha balde (ele saiu do aberto)', () => {
    listState.data = {
      data: [title({ status: 'liquidado', bucket: null })],
      pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
    };
    render(<ClientTitlesScreen clientId="c1" />);
    // Recorte na LINHA: "90+ dias" é também o rótulo de um balde no bloco de
    // agregados, e sem o `within` o locator casaria os dois.
    const linha = within(screen.getAllByRole('row')[1]!);
    expect(linha.getByText('Liquidado')).toBeVisible();
    expect(linha.queryByText('90+ dias')).toBeNull();
  });

  /**
   * 86e3eq9uy (Parte A): a página rola e a tabela não. O jsdom não faz layout,
   * então o que se trava AQUI são as classes que decidem o desenho: sem `fill`
   * (nada de `min-h-0` no wrapper), cabeçalho grudado na rolagem da PÁGINA de
   * `xl` para cima (`xl:overflow-clip` + `th` sticky), card com `overflow-clip`
   * (nunca `hidden`, que cria scroller e prende o sticky), e nenhum piso de
   * altura na área. A geometria de verdade é do e2e (rolar o `<main>` e medir).
   */
  it('a página rola e a tabela não: sem fill, cabeçalho grudado na rolagem da página', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    const region = screen.getByRole('region', { name: 'Títulos da carteira (rolável)' });
    expect(region).toHaveClass('overflow-auto', 'xl:overflow-clip');
    expect(region).not.toHaveClass('min-h-0');
    expect(region.className).toContain('[&_thead_th]:xl:sticky');
    expect(region.className).not.toContain('[&_thead_th]:sticky ');
    const card = region.parentElement;
    expect(card).toHaveClass('overflow-clip');
    expect(card).not.toHaveClass('overflow-hidden');
    const area = region.closest('[aria-busy]');
    expect(area).not.toHaveClass('min-h-[24rem]', 'flex-1', 'lg:min-h-[8rem]');
    // 86e3gkd80: a raiz declara o padrão, e é isso que solta a altura fixa do shell.
    expect(screen.getByRole('heading', { level: 1 }).closest('section')).toHaveAttribute(
      'data-page-scroll',
    );
    // A paginação vem DEPOIS da tabela, no fluxo, fora da região.
    const barra = screen.getByRole('navigation', { name: 'Paginação de títulos' });
    expect(region).not.toContainElement(barra);
    expect(region.compareDocumentPosition(barra) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('50 títulos por página por padrão (com a página rolando, 20 era pouco)', () => {
    render(<ClientTitlesScreen clientId="c1" />);
    expect(DEFAULT_PAGE_SIZE).toBe(50);
    expect(lastQueryParams).toMatchObject({ pageSize: 50 });
  });
});

describe('ClientTitlesScreen — filtros e ordenação NO SERVIDOR (R4)', () => {
  it('lê page e pageSize da URL e os manda SEMPRE para o servidor', () => {
    currentSearch = 'page=2&pageSize=50';
    listState.data = {
      data: [title()],
      pagination: { page: 2, pageSize: 50, total: 60, totalPages: 2 },
    };
    render(<ClientTitlesScreen clientId="c1" />);
    expect(lastQueryParams).toMatchObject({ page: 2, pageSize: 50 });
    const footer = screen.getByRole('navigation', { name: 'Paginação de títulos' });
    expect(within(footer).getByText('51–60 de 60')).toBeVisible();
  });

  it('tipo, situação e balde da URL chegam ao servidor', () => {
    currentSearch = 'type=a_pagar&situation=vencido&bucket=61_90';
    render(<ClientTitlesScreen clientId="c1" />);
    expect(lastQueryParams).toMatchObject({
      type: 'a_pagar',
      situation: 'vencido',
      bucket: '61_90',
    });
  });

  it('a ordenação é do servidor e guarda os DOIS parâmetros do contrato', async () => {
    const user = userEvent.setup();
    currentSearch = 'page=3';
    render(<ClientTitlesScreen clientId="c1" />);

    await user.click(screen.getByLabelText('Ordenar por'));
    await user.click(await screen.findByRole('option', { name: 'Valor (maior)' }));

    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).toContain('sortBy=amount');
    expect(url).toContain('sortOrder=desc');
    // Reordenar volta para a página 1 — senão a pessoa cai numa página que o
    // novo recorte não tem.
    expect(url).not.toContain('page=3');
  });

  it('filtro fora do vocabulário do servidor degrada para "sem filtro"', () => {
    // URL editada à mão não pode virar 400 na carga inicial.
    currentSearch = 'situation=qualquer-coisa&bucket=1000_mais&sortBy=descricao';
    render(<ClientTitlesScreen clientId="c1" />);
    expect(lastQueryParams?.situation).toBeNull();
    expect(lastQueryParams?.bucket).toBeNull();
    expect(lastQueryParams?.sortBy).toBe('due_date');
  });

  it('"Limpar filtros" derruba os filtros de uma vez', async () => {
    const user = userEvent.setup();
    currentSearch = 'type=a_pagar&situation=vencido&bucket=1_30';
    render(<ClientTitlesScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: 'Limpar filtros' }));
    const url = String(replaceMock.mock.calls.at(-1)?.[0]);
    expect(url).not.toContain('type=');
    expect(url).not.toContain('situation=');
    expect(url).not.toContain('bucket=');
  });

  it('a QUERY STRING carrega paginação e ordenação mesmo nos defaults', () => {
    // O request é montado aqui, e param condicional é o que gera 400/422 na
    // carga inicial — por isso `page`, `pageSize`, `sortBy` e `sortOrder` vão
    // SEMPRE, e os filtros ausentes simplesmente não aparecem.
    // O default do `buildClientTitlesQuery` é o do helper da API (20); a TELA
    // manda 50 explicitamente (`DEFAULT_PAGE_SIZE`), e `pageSize` vai sempre.
    expect(buildClientTitlesQuery({})).toBe('page=1&pageSize=20&sortBy=due_date&sortOrder=asc');
    expect(
      buildClientTitlesQuery({
        page: 2,
        pageSize: 50,
        type: 'a_receber',
        situation: 'vencido',
        bucket: '90_mais',
        sortBy: 'amount',
        sortOrder: 'desc',
      }),
    ).toBe(
      'page=2&pageSize=50&sortBy=amount&sortOrder=desc&type=a_receber&situation=vencido&bucket=90_mais',
    );
    // `situation=` vazio não é "sem filtro": é valor fora do `Literal`, que o
    // servidor recusa com 400.
    expect(buildClientTitlesQuery({ situation: null })).not.toContain('situation=');
  });
});

describe('ClientTitlesScreen — sincronizar (R5, §4.9)', () => {
  it('gerente do cliente vê a ação e ela dispara a sincronização', async () => {
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Sincronizar agora' }));
    expect(syncState.mutateAsync).toHaveBeenCalledTimes(1);
  });

  it('operador do cliente LÊ a carteira e NÃO vê a ação (oculta, não desabilitada)', () => {
    authState.user = clientOperator;
    render(<ClientTitlesScreen clientId="c1" />);

    // Lê: a tabela e os agregados continuam lá.
    expect(screen.getByText('Padaria Aurora Ltda')).toBeVisible();
    expect(screen.getByRole('region', { name: 'A receber' })).toBeVisible();
    // E não há botão nenhum de sincronizar — nem desabilitado.
    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
  });

  it('em andamento fica desabilitado com spinner (bloqueia duplo-clique)', () => {
    syncState.isPending = true;
    render(<ClientTitlesScreen clientId="c1" />);
    expect(screen.getByRole('button', { name: 'Sincronizando…' })).toBeDisabled();
  });

  it('avisa por toast ao concluir', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: 'Sincronizar agora' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Carteira sincronizada.'));
  });

  it('reabilita e avisa em erro', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    syncState.mutateAsync = vi.fn().mockRejectedValue(new Error('boom'));
    render(<ClientTitlesScreen clientId="c1" />);
    const button = screen.getByRole('button', { name: 'Sincronizar agora' });
    await user.click(button);
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(button).toBeEnabled();
  });

  it('cliente encerrado: leitura normal e ação indisponível COM o motivo', () => {
    clientDetailState.data = { closed_at: '2026-09-01T12:00:00Z', origin_status: 'ativa' };
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.getByText('Padaria Aurora Ltda')).toBeVisible();
    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
    expect(screen.getByText(/Cliente encerrado: a sincronização está indisponível/)).toBeVisible();
  });

  it('sem origem conectada: explica o estado e não oferece a ação', () => {
    clientDetailState.data = { closed_at: null, origin_status: 'sem_origem' };
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.getByText('Este cliente não tem origem conectada')).toBeVisible();
    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
  });
});

describe('ClientTitlesScreen — estados (R3)', () => {
  it('nunca sincronizada: ação de sincronizar e NENHUM zero passando por resultado', () => {
    summaryState.data = summary({
      aReceber: zeroTotals(),
      aPagar: zeroTotals(),
      neverSynced: true,
      syncedAt: null,
    });
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.getByText('Esta carteira ainda não foi sincronizada com o Omie')).toBeVisible();
    // Duas ocorrências: o botão do cabeçalho e o do estado vazio.
    expect(screen.getAllByRole('button', { name: 'Sincronizar agora' })).toHaveLength(2);
    // O bloco de agregados NÃO aparece: `R$ 0,00` ali seria lido como
    // "este cliente não deve nada" (R3).
    expect(screen.queryByRole('region', { name: 'A receber' })).toBeNull();
    expect(screen.queryByText(/R\$\s*0,00/)).toBeNull();
  });

  it('nunca sincronizada: o texto fala em "atraso por faixa", não em aging', () => {
    summaryState.data = summary({
      aReceber: zeroTotals(),
      aPagar: zeroTotals(),
      neverSynced: true,
      syncedAt: null,
    });
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ClientTitlesScreen clientId="c1" />);
    expect(screen.getByText(/com o total devido e o atraso por faixa/)).toBeVisible();
    expect(screen.queryByText(/aging/i)).toBeNull();
  });

  it('operador no estado vazio não ganha a ação, e o texto muda', () => {
    authState.user = clientOperator;
    summaryState.data = summary({
      aReceber: zeroTotals(),
      aPagar: zeroTotals(),
      neverSynced: true,
      syncedAt: null,
    });
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.getByText(/Peça a alguém com acesso de sincronização/)).toBeVisible();
    expect(screen.queryByRole('button', { name: /sincronizar/i })).toBeNull();
  });

  // 86e3n70qj — era o que a pessoa via antes de clicar em "Sincronizar": com o
  // resumo ainda carregando, a carteira vazia dizia "Nenhum título nesta carteira."
  it('com o resumo carregando, o vazio não diz "nenhum título" (ainda não se sabe)', () => {
    summaryState.isLoading = true;
    summaryState.data = undefined;
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.queryByText('Nenhum título nesta carteira.')).toBeNull();
    expect(screen.queryByText('Esta carteira ainda não foi sincronizada com o Omie')).toBeNull();
  });

  it('resumo indisponível: o vazio não afirma que a carteira está vazia', () => {
    summaryState.isError = true;
    summaryState.data = undefined;
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.queryByText('Nenhum título nesta carteira.')).toBeNull();
    expect(screen.getByText(/Não foi possível ler o estado da carteira/)).toBeVisible();
  });

  it('última tentativa falhou: agregados da ÍNTEGRA anterior + data + aviso', () => {
    summaryState.data = summary({
      syncedAt: '2026-09-20T12:30:00Z',
      syncFailedAt: '2026-09-24T09:00:00Z',
    });
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.getByText('A última tentativa de sincronização falhou')).toBeVisible();
    // A data da última boa é o que diz se o dado abaixo é de ontem ou de meses
    // atrás. O horário depende do fuso do runner — o dia não.
    expect(screen.getByText(/última sincronização bem-sucedida, de 20\/09\/2026/)).toBeVisible();
    // E os agregados da íntegra anterior continuam na tela.
    expect(screen.getByRole('region', { name: 'A receber' })).toBeVisible();
  });

  it('filtro sem resultado oferece limpar, não "nunca sincronizada"', () => {
    currentSearch = 'bucket=61_90';
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    render(<ClientTitlesScreen clientId="c1" />);

    expect(screen.getByText('Nenhum título encontrado para este recorte.')).toBeVisible();
    expect(screen.queryByText('Esta carteira ainda não foi sincronizada com o Omie')).toBeNull();
  });

  it('carregando mostra o skeleton da tabela', () => {
    listState.isLoading = true;
    listState.data = undefined;
    const { container } = render(<ClientTitlesScreen clientId="c1" />);
    // As linhas do skeleton são `aria-hidden` de propósito (não são conteúdo),
    // então `getAllByRole('row')` só enxerga o cabeçalho — a contagem tem de
    // ser sobre o DOM.
    expect(container.querySelectorAll('tr[aria-hidden="true"]')).toHaveLength(5);
    expect(screen.getAllByRole('row')).toHaveLength(1);
  });

  it('erro oferece "Tentar novamente"', () => {
    listState.isError = true;
    listState.data = undefined;
    render(<ClientTitlesScreen clientId="c1" />);
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });
});

describe('ClientTitlesScreen — contexto do título (Sprint 15 / R1 · R2)', () => {
  it('abre a gaveta de contexto a partir da linha, com o histórico e o formulário para quem PODE registrar', async () => {
    const user = userEvent.setup();
    historyState.data = [
      {
        id: 'ctx-1',
        titleId: title().id,
        type: 'acordo_de_pagamento',
        text: 'Fechamento quadrimestral acordado.',
        decryptFailed: false,
        author: { name: 'Ana Analista', email: 'ana@hologram.com.br' },
        createdAt: '2026-09-20T10:00:00Z',
      },
    ];
    render(<ClientTitlesScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: /Contexto do título 4010/ }));

    expect(await screen.findByRole('heading', { name: 'Contexto do título' })).toBeVisible();
    const entrada = screen.getByText('Fechamento quadrimestral acordado.');
    expect(entrada).toBeVisible();
    // Recorte na ENTRADA do histórico: "Acordo de pagamento" também é o valor
    // default do select do formulário logo abaixo — sem `within` o locator
    // casaria os dois.
    expect(within(entrada.closest('li')!).getByText('Acordo de pagamento')).toBeVisible();
    // Formulário presente: gerente do cliente tem `manage_title_context`.
    expect(screen.getByRole('button', { name: 'Registrar contexto' })).toBeVisible();
  });

  it('operador do cliente vê o histórico em LEITURA, sem a ação de registrar', async () => {
    authState.user = clientOperator;
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: /Contexto do título 4010/ }));

    expect(await screen.findByRole('heading', { name: 'Contexto do título' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Registrar contexto' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Fechar gaveta de contexto' })).toBeVisible();
  });

  it('nenhum contexto registrado: mensagem clara, não uma lista vazia muda', async () => {
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: /Contexto do título 4010/ }));
    expect(await screen.findByText('Nenhum contexto registrado para este título.')).toBeVisible();
  });

  it('registra um novo contexto e mostra o toast de sucesso', async () => {
    const { toast } = await import('sonner');
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);

    await user.click(screen.getByRole('button', { name: /Contexto do título 4010/ }));
    await user.type(await screen.findByLabelText('Descrição'), 'Cliente antecipou o pagamento.');
    await user.click(screen.getByRole('button', { name: 'Registrar contexto' }));

    await waitFor(() =>
      expect(registerContextState.mutateAsync).toHaveBeenCalledWith({
        type: 'acordo_de_pagamento',
        text: 'Cliente antecipou o pagamento.',
      }),
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Contexto registrado.'));
  });

  it('texto vazio é bloqueado pela validação do formulário (zod)', async () => {
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: /Contexto do título 4010/ }));
    await user.click(await screen.findByRole('button', { name: 'Registrar contexto' }));
    expect(await screen.findByText('Descreva o contexto.')).toBeVisible();
    expect(registerContextState.mutateAsync).not.toHaveBeenCalled();
  });

  it('Cancelar fecha a gaveta sem registrar', async () => {
    const user = userEvent.setup();
    render(<ClientTitlesScreen clientId="c1" />);
    await user.click(screen.getByRole('button', { name: /Contexto do título 4010/ }));
    await user.click(await screen.findByRole('button', { name: 'Cancelar' }));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Contexto do título' })).toBeNull(),
    );
  });
});

describe('ClientTitlesScreen — acessibilidade', () => {
  it('não tem violações critical/serious do axe-core', async () => {
    const { container } = render(<ClientTitlesScreen clientId="c1" />);
    await assertNoA11yViolations(container);
  });

  it('o estado vazio também passa no axe', async () => {
    summaryState.data = summary({
      aReceber: zeroTotals(),
      aPagar: zeroTotals(),
      neverSynced: true,
      syncedAt: null,
    });
    listState.data = { data: [], pagination: { page: 1, pageSize: 20, total: 0, totalPages: 0 } };
    const { container } = render(<ClientTitlesScreen clientId="c1" />);
    await assertNoA11yViolations(container);
  });
});
