/**
 * Testes da aba "Origem por arquivo" (Sprint 14 — FRONT 14.5 · 14.6 / R1 · R2 · R5).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest). Layout, contraste e os prints são do `web_a11y`,
 * em browser real (`e2e/a11y-mocked.spec.ts`).
 *
 * Cobre os critérios verificáveis em jsdom:
 *   - sem mapeamento → estado vazio com ação só para `manage_input_mapping`;
 *     o operador vê a explicação e nenhum botão de configurar;
 *   - com mapeamento → o resumo (campo ← coluna) e "Alterar mapeamento" só
 *     para quem configura; o operador ENVIA (célula ✅) sem botão de mapeamento;
 *   - com mapeamento, o envio vai direto (competência + arquivo + total em
 *     centavos → decimal), sem abrir o editor; o sucesso mostra as contagens e
 *     o link para a prévia do de-para da competência;
 *   - sem mapeamento, o envio CONDUZ ao editor já inspecionando o arquivo e,
 *     salvo o mapeamento, o envio PROSSEGUE com o mesmo arquivo;
 *   - mapeamento que não se sabe se existe (GET falhou, detalhe carregando)
 *     nunca vira "sem mapeamento": erro/carregando, sem envio nem editor;
 *   - a gaveta devolve o foco a quem a abriu (Cancelar e salvar);
 *   - cada código de recusa da BACK 14.3 tem renderização própria com o
 *     motivo específico; código desconhecido → toast genérico;
 *   - editor: alterar mapeamento existente exige `AlertDialog`; criar não; a
 *     convenção de sinal é obrigatória e os campos dependentes seguem a escolha;
 *   - lista de processados: vazio (`TableEmpty`) e com linhas (autor mascarado);
 *   - deep link sem conexão `arquivo` explica e leva a Contas Bancárias, onde as
 *     origens se conectam;
 *   - axe-core sem `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const pushMock = vi.fn();
const replaceMock = vi.fn();
let currentSearch = '';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
  usePathname: () => '/clientes/c1/origem-arquivo',
  useSearchParams: () => new URLSearchParams(currentSearch),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

function mutationState() {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
  };
}

const mappingState = {
  data: undefined as InputMappingPayload | undefined,
  isLoading: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const importsState = {
  data: undefined as FileImportItem[] | undefined,
  isLoading: false,
  isFetching: false,
  isError: false,
  error: null as unknown,
  refetch: vi.fn(),
};
const saveState = mutationState();
const inspectState = mutationState();
const processState = mutationState();

vi.mock('@/hooks/use-client-file-origin', () => ({
  useInputMapping: () => mappingState,
  useSaveInputMapping: () => saveState,
  useInspectFile: () => inspectState,
  useProcessFile: () => processState,
  useFileImports: () => importsState,
}));

const clientDetailState = {
  data: undefined as
    | { closed_at: string | null; origin_status: string; connections?: ClientConnection[] }
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

// Imports do SUT DEPOIS dos `vi.mock`.
import { FileOriginScreen } from '@/components/features/file-origin/file-origin-screen';
import { ApiError } from '@/lib/api/client';
import type {
  AuthenticatedUser,
  ClientConnection,
  FileImportItem,
  InputMapping,
  InputMappingPayload,
} from '@/lib/contracts';
import { assertNoA11yViolations } from '@/test/a11y';

const CLIENT_ID = 'c1';
const ORG = 'org-1';

const manager: AuthenticatedUser = {
  id: 'm',
  email: 'contador@parceiro.com.br',
  name: 'Contador Parceiro',
  role: 'manager',
  scope: 'system',
  client_id: null,
  organization_id: ORG,
  organization_name: 'Hologram',
};
const clientOperator: AuthenticatedUser = {
  id: 'co',
  email: 'operador@cliente.com.br',
  name: 'Operador',
  role: 'client_operator',
  scope: 'client',
  client_id: CLIENT_ID,
  organization_id: ORG,
  organization_name: 'Hologram',
};

const FILE_CONNECTION: ClientConnection = {
  id: 'arq-1',
  provider_type: 'arquivo',
  label: 'Arquivo',
  status: 'ativa',
  last_checked_at: null,
  accounts_synced_at: null,
  capabilities: ['listar_lancamentos'],
};

const OMIE_CONNECTION: ClientConnection = {
  id: 'omie-1',
  provider_type: 'omie',
  label: 'Omie',
  status: 'ativa',
  last_checked_at: '2026-09-22T12:00:00Z',
  accounts_synced_at: '2026-09-22T12:00:00Z',
  capabilities: [
    'verificar_credencial',
    'listar_contas',
    'listar_lancamentos',
    'escrever',
    'listar_titulos_em_aberto',
  ],
};

const MAPPING: InputMapping = {
  id: 'map-1',
  fileFormat: 'xlsx',
  csvDelimiter: null,
  encoding: null,
  dateColumn: 'Data',
  descriptionColumn: 'Histórico',
  amountColumn: 'Valor',
  categoryColumn: 'Categoria',
  categoryMode: 'coluna_categoria',
  accountColumn: null,
  documentColumn: null,
  dateFormat: 'dd/mm/yyyy',
  decimalSeparator: ',',
  signConvention: 'valor_com_sinal',
  natureColumn: null,
  debitValue: null,
  creditValue: null,
  debitColumn: null,
  creditColumn: null,
  createdAt: '2026-09-01T12:00:00Z',
  updatedAt: '2026-09-20T12:00:00Z',
};

const IMPORT: FileImportItem = {
  id: 'imp-1',
  competence: '2026-08',
  rows: 240,
  fileHash: 'a'.repeat(64),
  mappingId: 'map-1',
  processedAt: '2026-09-05T13:20:00Z',
  author: { name: 'Equipe Hologram', email: null },
};

function xlsxFile(name = 'agosto.xlsx'): File {
  return new File(['PK\u0003\u0004'], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
}

function refusal(status: number, code: string, details?: Record<string, unknown>): ApiError {
  return new ApiError(status, {
    code,
    message: 'x',
    userMessage: `Mensagem tipada de ${code}.`,
    details,
  });
}

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  currentSearch = '';
  pushMock.mockClear();
  replaceMock.mockClear();
  // O mock do sonner é do MÓDULO (compartilhado): sem limpar, o `toast.error`
  // do teste do 400 genérico vaza para "SEM_CONEXAO não é toast".
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.info).mockClear();
  authState.user = manager;
  clientDetailState.data = {
    closed_at: null,
    origin_status: 'ativa',
    connections: [FILE_CONNECTION],
  };
  mappingState.data = { mapping: MAPPING };
  mappingState.isLoading = false;
  mappingState.isError = false;
  mappingState.error = null;
  importsState.data = [];
  importsState.isLoading = false;
  importsState.isError = false;
  for (const state of [saveState, inspectState, processState]) {
    state.mutate = vi.fn();
    state.mutateAsync = vi.fn().mockResolvedValue({});
    state.reset = vi.fn();
    state.isPending = false;
  }
  inspectState.mutateAsync = vi.fn().mockResolvedValue({
    format: 'xlsx',
    columns: ['Data', 'Histórico', 'Valor', 'Categoria'],
    sample: [['01/08/2026', 'Aluguel', '-1500,00', 'Ocupação']],
    hasMapping: false,
  });
  processState.mutateAsync = vi.fn().mockResolvedValue({
    importId: 'imp-9',
    competence: '2026-09',
    rows: 240,
    columnsRecognized: 4,
    categoriesCreated: 3,
    absent: 0,
    mappingId: 'map-1',
    processedAt: '2026-09-27T12:00:00Z',
  });
});

async function uploadFile(user: ReturnType<typeof userEvent.setup>, file = xlsxFile()) {
  const input = screen.getByLabelText('Arquivo (.csv ou .xlsx)', { selector: 'input' });
  await user.upload(input, file);
  return file;
}

describe('Mapeamento — estados e gating (R1 · R5)', () => {
  it('sem mapeamento: estado vazio com "Configurar mapeamento" para o manager', () => {
    mappingState.data = { mapping: null };
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    const empty = screen.getByTestId('input-mapping-empty');
    expect(empty).toHaveTextContent('ainda não tem mapeamento de colunas');
    expect(within(empty).getByRole('button', { name: 'Configurar mapeamento' })).toBeVisible();
    // Sem mapeamento o envio conduz ao editor, e o botão diz isso.
    expect(screen.getByRole('button', { name: 'Configurar mapeamento e enviar' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Enviar arquivo' })).not.toBeInTheDocument();
  });

  it('sem mapeamento: o operador lê a explicação e não tem botão nenhum de configurar', () => {
    mappingState.data = { mapping: null };
    authState.user = clientOperator;
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    expect(screen.getByTestId('input-mapping-empty')).toHaveTextContent(/Peça a alguém da equipe/);
    expect(screen.queryByRole('button', { name: /Configurar mapeamento/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Alterar mapeamento/ })).not.toBeInTheDocument();
    expect(screen.getByTestId('upload-needs-mapping')).toHaveTextContent(/Peça a alguém/);
    expect(screen.queryByRole('button', { name: /enviar/i })).not.toBeInTheDocument();
  });

  it('com mapeamento: o resumo lista campo ← coluna e o manager pode alterar', () => {
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    const section = screen.getByTestId('input-mapping-section');
    const summary = within(section).getByTestId('mapping-summary');
    expect(summary).toHaveTextContent('Data');
    expect(summary).toHaveTextContent('Histórico');
    expect(summary).toHaveTextContent('Valor com sinal');
    expect(summary).toHaveTextContent('dd/mm/aaaa');
    expect(within(section).getByRole('button', { name: 'Alterar mapeamento' })).toBeVisible();
    expect(screen.queryByTestId('input-mapping-empty')).not.toBeInTheDocument();
  });

  it('com mapeamento: o operador vê o resumo, envia, e não tem botão de mapeamento (célula ❌)', () => {
    authState.user = clientOperator;
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    expect(screen.getByTestId('mapping-summary')).toBeVisible();
    expect(screen.queryByRole('button', { name: /Alterar mapeamento/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Configurar mapeamento/ })).not.toBeInTheDocument();
    // `upload_client_file` é dos 5 papéis: o operador ENVIA.
    expect(screen.getByRole('button', { name: 'Enviar arquivo' })).toBeVisible();
    expect(screen.getByTestId('mapping-will-apply')).toHaveTextContent('Será aplicado');
  });

  it('cliente encerrado: nenhuma escrita, com o motivo dito', () => {
    clientDetailState.data = { ...clientDetailState.data!, closed_at: '2026-09-01T00:00:00Z' };
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    expect(screen.queryByRole('button', { name: /Alterar mapeamento/ })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Cliente encerrado: o envio de arquivos está indisponível/),
    ).toBeVisible();
  });

  // 86e3fqnc9: a aba é listada para TODO cliente, então estes três estados são o
  // que a maioria vê. Cada um explica o recurso, o porquê daqui e a ação certa.
  it('cliente com Omie: explica que a origem é outra e NÃO oferece conectar (409)', () => {
    clientDetailState.data = {
      closed_at: null,
      origin_status: 'ativa',
      connections: [OMIE_CONNECTION],
    };
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    const empty = screen.getByTestId('no-file-origin');
    expect(empty).toHaveAttribute('data-state', 'outra-origem');
    expect(empty).toHaveTextContent('Os lançamentos deste cliente vêm da origem Omie');
    expect(empty).toHaveTextContent('um tipo de origem de lançamentos só');
    // Conectar arquivo aqui é 409 `ORIGEM_JA_CONECTADA`: nada de botão (§4.9).
    expect(screen.queryByRole('link', { name: /Conectar origem/ })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ver origem do cliente' })).toHaveAttribute(
      'href',
      `/clientes/${CLIENT_ID}/contas`,
    );
    expect(screen.queryByTestId('file-upload-section')).not.toBeInTheDocument();
  });

  it('cliente sem origem nenhuma: explica o recurso e abre a gaveta já no tipo Arquivo', () => {
    clientDetailState.data = { closed_at: null, origin_status: 'sem_origem', connections: [] };
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    const empty = screen.getByTestId('no-file-origin');
    expect(empty).toHaveAttribute('data-state', 'sem-origem');
    expect(empty).toHaveTextContent('não tem sistema contábil');
    expect(screen.getByRole('link', { name: 'Conectar origem por arquivo' })).toHaveAttribute(
      'href',
      `/clientes/${CLIENT_ID}/contas?conectar=arquivo`,
    );
  });

  it('sem origem e sem `manage_client_connections`: diz a quem pedir, sem botão', () => {
    authState.user = clientOperator;
    clientDetailState.data = { closed_at: null, origin_status: 'sem_origem', connections: [] };
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    const empty = screen.getByTestId('no-file-origin');
    expect(empty).toHaveAttribute('data-state', 'sem-origem');
    expect(empty).toHaveTextContent('Peça ao administrador ou ao gerente responsável');
    expect(screen.queryByRole('link', { name: /Conectar origem/ })).not.toBeInTheDocument();
  });

  it('cliente encerrado e sem origem por arquivo: explica o encerramento, sem ação', () => {
    clientDetailState.data = {
      closed_at: '2026-09-01T00:00:00Z',
      origin_status: 'sem_origem',
      connections: [],
    };
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    const empty = screen.getByTestId('no-file-origin');
    expect(empty).toHaveAttribute('data-state', 'encerrado');
    expect(empty).toHaveTextContent('Este cliente foi encerrado');
    expect(screen.queryByRole('link', { name: /Conectar origem/ })).not.toBeInTheDocument();
  });

  it('conexão arquivo fora do ar: o envio mostra o estado da origem, não o botão', () => {
    clientDetailState.data = {
      closed_at: null,
      origin_status: 'ativa',
      connections: [{ ...FILE_CONNECTION, status: 'erro' }],
    };
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    expect(screen.getByRole('status', { name: '' })).toBeDefined();
    expect(document.querySelector('[data-origin-state="ORIGEM_COM_ERRO"]')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Enviar arquivo' })).not.toBeInTheDocument();
  });
});

describe('Envio — com mapeamento é um passo só (R1 · R5)', () => {
  it('envia competência + arquivo sem abrir o editor e mostra as contagens e o link', async () => {
    const user = userEvent.setup();
    const { toast } = await import('sonner');
    currentSearch = 'competence=2026-09';
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    const file = await uploadFile(user);
    await user.click(screen.getByRole('button', { name: 'Enviar arquivo' }));

    await waitFor(() => expect(processState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(processState.mutateAsync).toHaveBeenCalledWith({
      file,
      competence: '2026-09',
      declaredTotal: null,
    });
    // O editor NÃO abriu.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    const success = await screen.findByTestId('upload-success');
    expect(success).toHaveTextContent(/240 linhas/);
    expect(success).toHaveTextContent(/3 categorias novas/);
    expect(
      within(success).getByRole('link', { name: /Ver de-para de Setembro de 2026/ }),
    ).toHaveAttribute('href', `/clientes/${CLIENT_ID}/de-para?view=previa&competence=2026-09`);
    expect(toast.success).toHaveBeenCalled();
  });

  it('o total do arquivo guarda centavos e vai como decimal; a exibição é pt-BR', async () => {
    const user = userEvent.setup();
    currentSearch = 'competence=2026-09';
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    const total = screen.getByLabelText(/Total do arquivo/);
    await user.type(total, '123456');
    expect(total).toHaveValue('1.234,56');

    await uploadFile(user);
    await user.click(screen.getByRole('button', { name: 'Enviar arquivo' }));

    await waitFor(() =>
      expect(processState.mutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ declaredTotal: '1234.56' }),
      ),
    );
  });

  it('sem arquivo escolhido, avisa e não chama o servidor', async () => {
    const user = userEvent.setup();
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    await user.click(screen.getByRole('button', { name: 'Enviar arquivo' }));
    expect(await screen.findByText('Escolha o arquivo do mês (.csv ou .xlsx).')).toBeVisible();
    expect(processState.mutateAsync).not.toHaveBeenCalled();
  });
});

/** Escolhe a coluna de um campo no `Select` do editor (Radix, lista em portal). */
async function pickColumn(
  user: ReturnType<typeof userEvent.setup>,
  drawer: HTMLElement,
  field: string,
  column: string,
) {
  await user.click(within(drawer).getByRole('combobox', { name: `Coluna: ${field}` }));
  await user.click(await screen.findByRole('option', { name: column }));
}

describe('Envio sem mapeamento conduz ao editor (R1)', () => {
  it('abre o editor já inspecionando o arquivo, Salvar grava sem diálogo e o envio PROSSEGUE', async () => {
    const user = userEvent.setup();
    mappingState.data = { mapping: null };
    currentSearch = 'competence=2026-09';
    saveState.mutateAsync = vi.fn().mockResolvedValue({ mapping: MAPPING, created: true });
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    const file = await uploadFile(user);
    await user.click(screen.getByRole('button', { name: 'Configurar mapeamento e enviar' }));

    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByRole('heading', { name: 'Configurar mapeamento' })).toBeVisible();
    await waitFor(() => expect(inspectState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(inspectState.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ file, csvDelimiter: null, encoding: null }),
    );
    // A amostra apareceu (colunas do inspect).
    const sample = await within(drawer).findByTestId('inspection-sample');
    expect(sample).toHaveTextContent('4 colunas encontradas');
    expect(within(sample).getByRole('columnheader', { name: 'Histórico' })).toBeVisible();
    expect(processState.mutateAsync).not.toHaveBeenCalled();

    // Declara o mapeamento com as colunas do inspect e salva.
    await pickColumn(user, drawer, 'Data', 'Data');
    await pickColumn(user, drawer, 'Descrição', 'Histórico');
    // A coluna do valor é dependente da convenção: aparece depois de escolhê-la.
    await user.click(within(drawer).getByRole('radio', { name: /Valor com sinal/ }));
    await pickColumn(user, drawer, 'Valor', 'Valor');
    await user.click(within(drawer).getByRole('button', { name: 'Salvar mapeamento' }));

    // Criar não pede confirmação: o PUT sai direto...
    await waitFor(() => expect(saveState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(saveState.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        dateColumn: 'Data',
        descriptionColumn: 'Histórico',
        amountColumn: 'Valor',
        signConvention: 'valor_com_sinal',
      }),
    );
    // ...e, salvo o mapeamento, o envio segue com o MESMO arquivo e competência,
    // sem a pessoa clicar em Enviar de novo.
    await waitFor(() => expect(processState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(processState.mutateAsync).toHaveBeenCalledWith({
      file,
      competence: '2026-09',
      declaredTotal: null,
    });
    expect(await screen.findByTestId('upload-success')).toHaveTextContent(/240 linhas/);
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringMatching(/processado: 240 linhas/),
      expect.anything(),
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('abrir o editor pelo estado vazio e salvar NÃO dispara envio nenhum', async () => {
    const user = userEvent.setup();
    mappingState.data = { mapping: null };
    saveState.mutateAsync = vi.fn().mockResolvedValue({ mapping: MAPPING, created: true });
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    await user.click(screen.getByRole('button', { name: 'Configurar mapeamento' }));
    const drawer = await screen.findByRole('dialog');
    await user.upload(within(drawer).getByLabelText('Arquivo (.csv ou .xlsx)'), xlsxFile());
    await user.click(within(drawer).getByRole('button', { name: 'Inspecionar arquivo' }));
    await within(drawer).findByTestId('inspection-sample');
    await pickColumn(user, drawer, 'Data', 'Data');
    await pickColumn(user, drawer, 'Descrição', 'Histórico');
    // A coluna do valor é dependente da convenção: aparece depois de escolhê-la.
    await user.click(within(drawer).getByRole('radio', { name: /Valor com sinal/ }));
    await pickColumn(user, drawer, 'Valor', 'Valor');
    await user.click(within(drawer).getByRole('button', { name: 'Salvar mapeamento' }));

    await waitFor(() => expect(saveState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(processState.mutateAsync).not.toHaveBeenCalled();
  });
});

describe('Mapeamento que NÃO se sabe se existe — nunca vira "sem mapeamento" (R5)', () => {
  it('GET do mapeamento falhou: erro com "Tentar novamente", sem envio nem editor', async () => {
    const user = userEvent.setup();
    mappingState.data = undefined;
    mappingState.isError = true;
    mappingState.error = new ApiError(500, {
      code: 'INTERNAL_ERROR',
      message: 'x',
      userMessage: 'Erro inesperado.',
    });
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    // O PUT SUBSTITUI o mapeamento: tratar a falha como vazio abriria o editor
    // de CRIAÇÃO e gravaria por cima do existente sem o AlertDialog.
    expect(screen.queryByTestId('input-mapping-empty')).not.toBeInTheDocument();
    expect(screen.queryByTestId('upload-needs-mapping')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Configurar mapeamento/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Enviar arquivo' })).not.toBeInTheDocument();
    expect(screen.getByTestId('upload-mapping-error')).toHaveTextContent(
      /Não foi possível carregar o mapeamento/,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    const retries = screen.getAllByRole('button', { name: 'Tentar novamente' });
    await user.click(retries[0]!);
    expect(mappingState.refetch).toHaveBeenCalled();
  });

  it('refetch em segundo plano que falha (dado antigo em cache) também é erro', () => {
    mappingState.data = { mapping: MAPPING };
    mappingState.isError = true;
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    expect(screen.queryByRole('button', { name: /Alterar mapeamento/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Enviar arquivo' })).not.toBeInTheDocument();
    expect(screen.getByTestId('upload-mapping-error')).toBeVisible();
  });

  it('detalhe do cliente ainda carregando (query do mapeamento desligada): carregando, sem ação', () => {
    clientDetailState.data = undefined;
    mappingState.data = undefined;
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    expect(screen.queryByTestId('input-mapping-empty')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Configurar mapeamento/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Enviar/ })).not.toBeInTheDocument();
  });
});

describe('Editor — foco devolvido a quem abriu (OpenerCapture)', () => {
  it('Cancelar devolve o foco ao "Configurar mapeamento"', async () => {
    const user = userEvent.setup();
    mappingState.data = { mapping: null };
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    const opener = screen.getByRole('button', { name: 'Configurar mapeamento' });
    await user.click(opener);
    const drawer = await screen.findByRole('dialog');
    await user.click(within(drawer).getByRole('button', { name: 'Cancelar' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });

  it('salvar uma alteração devolve o foco ao "Alterar mapeamento"', async () => {
    const user = userEvent.setup();
    saveState.mutateAsync = vi.fn().mockResolvedValue({ mapping: MAPPING, created: false });
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    const opener = screen.getByRole('button', { name: 'Alterar mapeamento' });
    await user.click(opener);
    const drawer = await screen.findByRole('dialog');
    await user.click(within(drawer).getByRole('button', { name: 'Salvar alterações' }));
    const confirm = await screen.findByRole('alertdialog');
    await user.click(within(confirm).getByRole('button', { name: 'Confirmar alteração' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
  });
});

describe('Editor — convenção obrigatória, dependentes e confirmação (R1 · R5)', () => {
  it('sem convenção de sinal (e sem colunas obrigatórias) não salva e diz o que falta', async () => {
    const user = userEvent.setup();
    mappingState.data = { mapping: null };
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    await user.click(screen.getByRole('button', { name: 'Configurar mapeamento' }));
    const drawer = await screen.findByRole('dialog');
    // Sem inspeção não há colunas: Salvar fica bloqueado e a instrução aparece.
    const salvar = within(drawer).getByRole('button', { name: 'Salvar mapeamento' });
    expect(salvar).toBeDisabled();
    expect(
      within(drawer).getByText('Inspecione um arquivo para escolher as colunas.'),
    ).toBeVisible();

    await user.upload(within(drawer).getByLabelText('Arquivo (.csv ou .xlsx)'), xlsxFile());
    await user.click(within(drawer).getByRole('button', { name: 'Inspecionar arquivo' }));
    await within(drawer).findByTestId('inspection-sample');
    expect(salvar).toBeEnabled();

    await user.click(salvar);
    expect(
      await within(drawer).findByText('Declare como o arquivo indica débito e crédito.'),
    ).toBeVisible();
    expect(within(drawer).getByText('Escolha a coluna da data.')).toBeVisible();
    expect(saveState.mutateAsync).not.toHaveBeenCalled();
  });

  it('os campos dependentes seguem a convenção escolhida', async () => {
    const user = userEvent.setup();
    mappingState.data = { mapping: null };
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    await user.click(screen.getByRole('button', { name: 'Configurar mapeamento' }));
    const drawer = await screen.findByRole('dialog');

    expect(within(drawer).queryByTestId('sign-dependent-fields')).not.toBeInTheDocument();

    await user.click(within(drawer).getByRole('radio', { name: /Coluna de natureza/ }));
    const dependent = within(drawer).getByTestId('sign-dependent-fields');
    expect(within(dependent).getByLabelText('Texto que significa débito')).toBeVisible();
    expect(within(dependent).getByLabelText('Texto que significa crédito')).toBeVisible();
    expect(within(dependent).getByRole('combobox', { name: 'Coluna: Valor' })).toBeVisible();
    expect(
      within(dependent).queryByRole('combobox', { name: 'Coluna: Débito' }),
    ).not.toBeInTheDocument();

    await user.click(within(drawer).getByRole('radio', { name: /Colunas separadas/ }));
    expect(within(drawer).getByRole('combobox', { name: 'Coluna: Débito' })).toBeVisible();
    expect(within(drawer).getByRole('combobox', { name: 'Coluna: Crédito' })).toBeVisible();
    expect(
      within(drawer).queryByRole('combobox', { name: 'Coluna: Valor' }),
    ).not.toBeInTheDocument();
    expect(within(drawer).queryByLabelText('Texto que significa débito')).not.toBeInTheDocument();
  });

  it('alterar um mapeamento EXISTENTE abre o AlertDialog e só grava após confirmar', async () => {
    const user = userEvent.setup();
    saveState.mutateAsync = vi.fn().mockResolvedValue({ mapping: MAPPING, created: false });
    render(<FileOriginScreen clientId={CLIENT_ID} />);

    await user.click(screen.getByRole('button', { name: 'Alterar mapeamento' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByRole('heading', { name: 'Alterar mapeamento' })).toBeVisible();
    // As colunas do mapeamento salvo já estão nos seletores — sem inspecionar.
    expect(within(drawer).getByRole('combobox', { name: 'Coluna: Data' })).toHaveTextContent(
      'Data',
    );

    await user.click(within(drawer).getByRole('button', { name: 'Salvar alterações' }));
    const confirm = await screen.findByRole('alertdialog');
    expect(confirm).toHaveTextContent('Alterar o mapeamento deste cliente?');
    expect(confirm).toHaveTextContent(/altera como os próximos arquivos serão lidos/);
    expect(saveState.mutateAsync).not.toHaveBeenCalled();

    await user.click(within(confirm).getByRole('button', { name: 'Confirmar alteração' }));
    await waitFor(() => expect(saveState.mutateAsync).toHaveBeenCalledTimes(1));
    expect(saveState.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        fileFormat: 'xlsx',
        dateColumn: 'Data',
        descriptionColumn: 'Histórico',
        amountColumn: 'Valor',
        signConvention: 'valor_com_sinal',
        natureColumn: null,
        csvDelimiter: null,
      }),
    );
  });

  it('400 genérico do servidor vira mensagem amigável, nunca o texto interno', async () => {
    const user = userEvent.setup();
    const { toast } = await import('sonner');
    saveState.mutateAsync = vi.fn().mockRejectedValue(
      new ApiError(400, {
        code: 'VALIDATION_ERROR',
        message: 'ValueError: coluna_natureza exige amountColumn',
        userMessage: 'Dados inválidos.',
      }),
    );
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    await user.click(screen.getByRole('button', { name: 'Alterar mapeamento' }));
    const drawer = await screen.findByRole('dialog');
    await user.click(within(drawer).getByRole('button', { name: 'Salvar alterações' }));
    const confirm = await screen.findByRole('alertdialog');
    await user.click(within(confirm).getByRole('button', { name: 'Confirmar alteração' }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    const message = String(vi.mocked(toast.error).mock.calls[0]?.[0]);
    expect(message).toMatch(/confira a convenção de sinal/);
    expect(message).not.toMatch(/ValueError/);
  });
});

describe('Recusas — cada código com o motivo específico (R2 · R5)', () => {
  async function submitWithRefusal(error: ApiError, user: ReturnType<typeof userEvent.setup>) {
    processState.mutateAsync = vi.fn().mockRejectedValue(error);
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    await uploadFile(user);
    await user.click(screen.getByRole('button', { name: 'Enviar arquivo' }));
    await waitFor(() => expect(processState.mutateAsync).toHaveBeenCalled());
  }

  it('CABECALHO_DIVERGENTE nomeia as colunas ausentes e oferece revisar o mapeamento', async () => {
    const user = userEvent.setup();
    await submitWithRefusal(
      refusal(422, 'CABECALHO_DIVERGENTE', {
        missingColumns: ['Histórico'],
        foundColumnCount: 3,
      }),
      user,
    );
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveAttribute('data-refusal-code', 'CABECALHO_DIVERGENTE');
    expect(within(alert).getByRole('list', { name: /ausentes/ })).toHaveTextContent('Histórico');
    // A contagem substitui a lista de colunas encontradas: o nome vinha CRU do
    // arquivo, e num arquivo sem cabeçalho a linha 1 é lançamento (86e3fvffy).
    expect(within(alert).getByTestId('file-header-counts')).toHaveTextContent('3 colunas');
    expect(within(alert).queryByRole('list', { name: /encontradas/ })).toBeNull();
    await user.click(within(alert).getByRole('button', { name: 'Revisar mapeamento' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('Alterar mapeamento');
  });

  it('LINHAS_INVALIDAS lista linha × motivo em PT-BR e diz "mostrando K de N"', async () => {
    const user = userEvent.setup();
    await submitWithRefusal(
      refusal(422, 'LINHAS_INVALIDAS', {
        lines: [
          { line: 7, reason: 'valor_nao_numerico' },
          { line: 12, reason: 'data_fora_da_competencia' },
        ],
        total: 5,
      }),
      user,
    );
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveAttribute('data-refusal-code', 'LINHAS_INVALIDAS');
    const rows = within(alert).getAllByRole('row');
    expect(rows).toHaveLength(3); // cabeçalho + 2
    expect(rows[1]).toHaveTextContent('7');
    expect(rows[1]).toHaveTextContent('Valor não numérico');
    expect(rows[2]).toHaveTextContent('Data fora da competência informada');
    expect(within(alert).getByTestId('invalid-lines-count')).toHaveTextContent(
      'Mostrando 2 de 5 linhas inválidas.',
    );
  });

  it('TOTAL_DIVERGENTE mostra os dois totais lado a lado, formatados', async () => {
    const user = userEvent.setup();
    await submitWithRefusal(
      refusal(422, 'TOTAL_DIVERGENTE', { declaredTotal: '1000.00', computedTotal: '-980.50' }),
      user,
    );
    const alert = await screen.findByRole('alert');
    const comparison = within(alert).getByTestId('total-comparison');
    expect(comparison).toHaveTextContent('Total informado');
    expect(comparison).toHaveTextContent(/R\$\s*1\.000,00/);
    expect(comparison).toHaveTextContent('Soma do arquivo');
    expect(comparison).toHaveTextContent(/-R\$\s*980,50/);
  });

  it.each(['FORMATO_NAO_SUPORTADO', 'ARQUIVO_INVALIDO'])(
    '%s mostra a mensagem TIPADA do servidor',
    async (code) => {
      const user = userEvent.setup();
      await submitWithRefusal(refusal(422, code), user);
      const alert = await screen.findByRole('alert');
      expect(alert).toHaveAttribute('data-refusal-code', code);
      expect(alert).toHaveTextContent(`Mensagem tipada de ${code}.`);
    },
  );

  it('ARQUIVO_JA_PROCESSADO aponta a lista de processados', async () => {
    const user = userEvent.setup();
    await submitWithRefusal(refusal(409, 'ARQUIVO_JA_PROCESSADO'), user);
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('link', { name: 'Ver arquivos processados' })).toHaveAttribute(
      'href',
      expect.stringContaining('#arquivos-processados'),
    );
  });

  it('SEM_MAPEAMENTO leva ao editor (manager) — e o operador só lê', async () => {
    const user = userEvent.setup();
    await submitWithRefusal(refusal(409, 'SEM_MAPEAMENTO', { foundColumns: ['Data'] }), user);
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('button', { name: 'Configurar mapeamento' })).toBeVisible();
  });

  it('SEM_CONEXAO (taxonomia S9) vira o estado de origem, não toast', async () => {
    const user = userEvent.setup();
    const { toast } = await import('sonner');
    await submitWithRefusal(refusal(409, 'SEM_CONEXAO'), user);
    await waitFor(() =>
      expect(document.querySelector('[data-origin-state="SEM_CONEXAO"]')).not.toBeNull(),
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('código DESCONHECIDO cai no toast genérico com o userMessage', async () => {
    const user = userEvent.setup();
    const { toast } = await import('sonner');
    await submitWithRefusal(refusal(500, 'INTERNAL_ERROR'), user);
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Mensagem tipada de INTERNAL_ERROR.'),
    );
    expect(document.querySelector('[data-refusal-code]')).toBeNull();
  });
});

describe('Arquivos processados (R3)', () => {
  it('vazio usa TableEmpty depois da tabela, nunca célula colSpan', () => {
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    const section = screen.getByTestId('file-imports');
    expect(within(section).getByText('Nenhum arquivo processado ainda')).toBeVisible();
    expect(within(section).queryByRole('cell')).not.toBeInTheDocument();
  });

  it('com linhas mostra competência, linhas, data e o autor mascarado', () => {
    importsState.data = [IMPORT];
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    const section = screen.getByTestId('file-imports');
    const rows = within(section).getAllByRole('row');
    expect(rows).toHaveLength(2);
    expect(rows[1]).toHaveTextContent('Agosto de 2026');
    expect(rows[1]).toHaveTextContent('240');
    expect(rows[1]).toHaveTextContent('Equipe Hologram');
    expect(within(section).queryByText('Nenhum arquivo processado ainda')).not.toBeInTheDocument();
  });

  it('erro na lista oferece "Tentar novamente"', () => {
    importsState.isError = true;
    importsState.data = undefined;
    render(<FileOriginScreen clientId={CLIENT_ID} />);
    const section = screen.getByTestId('file-imports');
    expect(within(section).getByRole('button', { name: 'Tentar novamente' })).toBeVisible();
  });
});

describe('acessibilidade', () => {
  it('não tem violações critical/serious do axe-core (com mapeamento e lista)', async () => {
    importsState.data = [IMPORT];
    const { container } = render(<FileOriginScreen clientId={CLIENT_ID} />);
    await assertNoA11yViolations(container);
  });

  it('não tem violações no estado vazio do operador', async () => {
    mappingState.data = { mapping: null };
    authState.user = clientOperator;
    const { container } = render(<FileOriginScreen clientId={CLIENT_ID} />);
    await assertNoA11yViolations(container);
  });
});
