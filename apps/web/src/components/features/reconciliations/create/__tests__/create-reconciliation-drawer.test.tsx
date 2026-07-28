/**
 * Testes da gaveta "Criar conciliação" (FRONT 04.6 / R2 + R5).
 *
 * Critérios cobertos:
 *   - é uma GAVETA (dialog), em 2 steps navegáveis, com o footer no padrão
 *     (Cancelar à esquerda ↔ primária à direita) e foco inicial no primário;
 *   - V1–V3 preservadas POR ARQUIVO (extensão/tamanho, duplicata, extração);
 *   - falha de extração de um arquivo informa QUAL e permite removê-lo sem
 *     perder os outros — e a parte que falhou vai no payload com `error_code`;
 *   - botão de confirmar é async: `disabled` + spinner, reabilita em erro;
 *   - 409 de conta+mês oferece anexar à conciliação existente (cenário S-3);
 *   - axe-core sem violações `critical`/`serious`.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const checkDuplicateMock = vi.fn();
const parseStatementMock = vi.fn();
const createReconciliationMock = vi.fn();
const attachSessionFilesMock = vi.fn();
const listReconciliationsMock = vi.fn();
const sha256Mock = vi.fn();
const toastErrorMock = vi.fn();

vi.mock('@/lib/api/reconciliations', () => ({
  checkDuplicate: (...args: unknown[]) => checkDuplicateMock(...args),
  parseStatement: (...args: unknown[]) => parseStatementMock(...args),
  createReconciliation: (...args: unknown[]) => createReconciliationMock(...args),
  attachSessionFiles: (...args: unknown[]) => attachSessionFilesMock(...args),
}));

// Só `listReconciliations` é mockado — `isCreditCardAccount` continua o real.
vi.mock('@/lib/api/clients', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    listReconciliations: (...args: unknown[]) => listReconciliationsMock(...args),
  };
});

vi.mock('@/lib/crypto/hash', () => ({ sha256Hex: (...args: unknown[]) => sha256Mock(...args) }));

// Referências lazy: `vi.mock` é hoisted acima das `const`, então a factory não
// pode CAPTURAR o valor — só chamá-lo depois.
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: (...args: unknown[]) => toastErrorMock(...args) },
}));

import { CreateReconciliationDrawer } from '@/components/features/reconciliations/create/create-reconciliation-drawer';
import { ApiError } from '@/lib/api/client';
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

function pdf(name: string, size = 1024): File {
  const file = new File(['conteudo'], name, { type: 'application/pdf' });
  Object.defineProperty(file, 'size', { value: size });
  return file;
}

function statement() {
  return {
    bank_name: 'Itaú',
    account_type: 'credit_card' as const,
    period_start: '2026-06-01',
    period_end: '2026-06-30',
    opening_balance: '0.00',
    closing_balance: '100.00',
    transactions: [
      { date: '2026-06-10', description: 'Compra', amount: '-100.00', balance: null, is_payment: false },
    ],
  };
}

function checksum(ok = true) {
  return {
    ok,
    applicable: true,
    account_type: 'credit_card' as const,
    expected: '100.00',
    computed: ok ? '100.00' : '90.00',
    difference: ok ? '0.00' : '10.00',
    tolerance: '0.01',
    reason: null,
  };
}

const onCreated = vi.fn();
const onOpenChange = vi.fn();

function renderDrawer() {
  return render(
    <CreateReconciliationDrawer
      open
      onOpenChange={onOpenChange}
      clientId="c1"
      accounts={ACCOUNTS}
      onCreated={onCreated}
    />,
  );
}

/** Vai do Step 1 ao Step 2 escolhendo conta + mês. */
async function advanceToStep2(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('combobox', { name: 'Conta bancária' }));
  await user.click(screen.getByRole('option', { name: /Cartão Itaú/ }));
  await user.type(screen.getByLabelText('Mês de referência'), '2026-06');
  await user.click(screen.getByRole('button', { name: /Avançar/ }));
  await screen.findByLabelText('Adicionar arquivos');
}

async function addFile(user: ReturnType<typeof userEvent.setup>, file: File) {
  const input = document.getElementById('reconciliation-files') as HTMLInputElement;
  await user.upload(input, file);
}

beforeAll(() => {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  sha256Mock.mockImplementation(async (file: File) => `hash-${file.name}`);
  checkDuplicateMock.mockResolvedValue({ duplicate: false });
  parseStatementMock.mockImplementation(async (params: { file: File }) => ({
    statement: statement(),
    checksum: checksum(),
    fileHash: `server-${params.file.name}`,
  }));
  createReconciliationMock.mockResolvedValue({
    session_id: 's-new',
    status: 'processing',
    total_files: 1,
  });
});

describe('Gaveta — estrutura e navegação', () => {
  it('é uma gaveta com footer Cancelar↔primária e foco inicial no primário', async () => {
    renderDrawer();
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeVisible();

    const cancel = within(dialog).getByRole('button', { name: 'Cancelar' });
    const advance = within(dialog).getByRole('button', { name: /Avançar/ });
    // Cancelar vem ANTES da primária no DOM — é o que o `justify-between` do
    // shell traduz em "esquerda ↔ direita".
    expect(cancel.compareDocumentPosition(advance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await waitFor(() => expect(advance).toHaveFocus());
  });

  it('não avança sem conta e mês (V1)', async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(await screen.findByRole('button', { name: /Avançar/ }));
    expect(await screen.findByText('Selecione uma conta bancária.')).toBeVisible();
    expect(screen.queryByLabelText('Adicionar arquivos')).toBeNull();
  });

  it('avança para o Step 2 e volta preservando os dados', async () => {
    const user = userEvent.setup();
    renderDrawer();
    await advanceToStep2(user);
    expect(screen.getByText('Junho de 2026')).toBeVisible();

    await user.click(screen.getByRole('button', { name: /Voltar/ }));
    expect(screen.getByLabelText('Mês de referência')).toHaveValue('2026-06');
  });
});

describe('Gaveta — múltiplos arquivos', () => {
  it('processa cada arquivo e mostra o resultado individual', async () => {
    const user = userEvent.setup();
    renderDrawer();
    await advanceToStep2(user);

    await addFile(user, pdf('parte-1.pdf'));
    await addFile(user, pdf('parte-2.pdf'));

    await waitFor(() => expect(parseStatementMock).toHaveBeenCalledTimes(2));
    const list = screen.getByRole('list', { name: 'Arquivos desta conciliação' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    // O contador do primário reflete quantas partes extraíram OK.
    expect(screen.getByRole('button', { name: 'Confirmar (2)' })).toBeEnabled();
  });

  it('rejeita arquivo acima do teto sem chamar a IA (V1)', async () => {
    const user = userEvent.setup();
    renderDrawer();
    await advanceToStep2(user);
    // 21 MB — acima do teto de 20 MB. (A extensão fora do allowlist é barrada
    // antes disso, pelo `accept` nativo do input, e o mesmo refine do Zod cobre
    // os dois casos.)
    await addFile(user, pdf('enorme.pdf', 21 * 1024 * 1024));

    expect(await screen.findByText(/excede o limite de 20 MB/)).toBeVisible();
    expect(parseStatementMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: /Confirmar/ })).toBeDisabled();
  });

  it('marca duplicata sem gastar chamada de IA (V3)', async () => {
    const user = userEvent.setup();
    checkDuplicateMock.mockResolvedValue({ duplicate: true });
    renderDrawer();
    await advanceToStep2(user);
    await addFile(user, pdf('repetido.pdf'));

    expect(await screen.findByText(/já faz parte da conciliação/)).toBeVisible();
    expect(parseStatementMock).not.toHaveBeenCalled();
  });

  it('falha de extração diz QUAL arquivo e permite removê-lo', async () => {
    const user = userEvent.setup();
    parseStatementMock.mockImplementation(async (params: { file: File }) => {
      if (params.file.name === 'ruim.pdf') {
        throw new ApiError(422, {
          code: 'PARSE_ERROR',
          message: 'anthropic tool_use inválido',
          userMessage: 'Não foi possível ler este arquivo.',
        });
      }
      return { statement: statement(), checksum: checksum(), fileHash: 'server-ok' };
    });

    renderDrawer();
    await advanceToStep2(user);
    await addFile(user, pdf('ruim.pdf'));
    await addFile(user, pdf('bom.pdf'));

    expect(await screen.findByText(/Não foi possível ler este arquivo/)).toBeVisible();
    // Código canônico, nunca a mensagem interna.
    expect(screen.getByText(/cód\. PARSE_ERROR/)).toBeVisible();
    expect(screen.queryByText(/tool_use/)).toBeNull();
    // O arquivo bom sobrevive e ainda dá para confirmar.
    expect(screen.getByRole('button', { name: 'Confirmar (1)' })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: 'Remover ruim.pdf' }));
    expect(screen.queryByText(/Não foi possível ler este arquivo/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Confirmar (1)' })).toBeEnabled();
  });

  it('a parte que falhou vai no payload com error_code (sessão sabe qual falhou)', async () => {
    const user = userEvent.setup();
    parseStatementMock.mockImplementation(async (params: { file: File }) => {
      if (params.file.name === 'ruim.pdf') {
        throw new ApiError(422, {
          code: 'PARSE_ERROR',
          message: 'x',
          userMessage: 'Não foi possível ler este arquivo.',
        });
      }
      return { statement: statement(), checksum: checksum(), fileHash: 'server-bom' };
    });

    renderDrawer();
    await advanceToStep2(user);
    await addFile(user, pdf('ruim.pdf'));
    await addFile(user, pdf('bom.pdf'));
    await screen.findByRole('button', { name: 'Confirmar (1)' });
    await user.click(screen.getByRole('button', { name: 'Confirmar (1)' }));

    await waitFor(() => expect(createReconciliationMock).toHaveBeenCalled());
    const payload = createReconciliationMock.mock.calls[0]![0] as {
      reference_month: string;
      files: { file_hash: string; error_code?: string; statement?: unknown }[];
    };
    expect(payload.reference_month).toBe('2026-06-01');
    expect(payload.files).toHaveLength(2);
    expect(payload.files.find((f) => f.error_code === 'PARSE_ERROR')?.file_hash).toBe(
      'hash-ruim.pdf',
    );
    // A parte OK viaja com o hash RECALCULADO NO SERVIDOR, não com o do cliente.
    expect(payload.files.find((f) => f.statement != null)?.file_hash).toBe('server-bom');
  });
});

describe('Gaveta — confirmação', () => {
  it('confirma, avisa o pai e não deixa clicar duas vezes', async () => {
    const user = userEvent.setup();
    let resolveCreate: (v: unknown) => void = () => undefined;
    createReconciliationMock.mockImplementation(
      () => new Promise((resolve) => (resolveCreate = resolve)),
    );

    renderDrawer();
    await advanceToStep2(user);
    await addFile(user, pdf('parte-1.pdf'));
    const confirm = await screen.findByRole('button', { name: 'Confirmar (1)' });
    await user.click(confirm);

    // Enquanto está em voo: desabilitado (duplo-clique impossível) e com rótulo
    // de progresso.
    const busy = await screen.findByRole('button', { name: 'Criando…' });
    expect(busy).toBeDisabled();

    resolveCreate({ session_id: 's-new', status: 'processing', total_files: 1 });
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith({ sessionId: 's-new', totalFiles: 1 }),
    );
  });

  it('reabilita o botão em erro (não fica preso)', async () => {
    const user = userEvent.setup();
    createReconciliationMock.mockRejectedValue(
      new ApiError(500, { code: 'INTERNAL_ERROR', message: 'x', userMessage: 'Deu ruim.' }),
    );
    renderDrawer();
    await advanceToStep2(user);
    await addFile(user, pdf('parte-1.pdf'));
    await user.click(await screen.findByRole('button', { name: 'Confirmar (1)' }));

    await waitFor(() => expect(toastErrorMock).toHaveBeenCalledWith('Deu ruim.'));
    expect(await screen.findByRole('button', { name: 'Confirmar (1)' })).toBeEnabled();
    expect(onCreated).not.toHaveBeenCalled();
  });

  it('409 de conta+mês oferece anexar à conciliação existente (S-3)', async () => {
    const user = userEvent.setup();
    createReconciliationMock.mockRejectedValue(
      new ApiError(409, {
        code: 'CONFLICT',
        message: 'x',
        userMessage: 'Já existe uma conciliação para esta conta e mês.',
      }),
    );
    listReconciliationsMock.mockResolvedValue({
      data: [{ id: 's-existente' }],
      pagination: { page: 1, pageSize: 1, total: 1, totalPages: 1 },
    });
    attachSessionFilesMock.mockResolvedValue({
      session_id: 's-existente',
      total_files: 3,
      reprocessing: true,
    });

    renderDrawer();
    await advanceToStep2(user);
    await addFile(user, pdf('parte-2.pdf'));
    await user.click(await screen.findByRole('button', { name: 'Confirmar (1)' }));

    const attach = await screen.findByRole('button', {
      name: 'Adicionar à conciliação existente',
    });
    await user.click(attach);

    await waitFor(() => expect(attachSessionFilesMock).toHaveBeenCalledWith('s-existente', expect.any(Array)));
    expect(onCreated).toHaveBeenCalledWith({ sessionId: 's-existente', totalFiles: 3 });
  });
});

describe('Gaveta — acessibilidade', () => {
  it('não tem violações critical/serious no Step 1', async () => {
    renderDrawer();
    const dialog = await screen.findByRole('dialog');
    await assertNoA11yViolations(dialog);
  });

  it('não tem violações critical/serious no Step 2 com arquivos', async () => {
    const user = userEvent.setup();
    renderDrawer();
    await advanceToStep2(user);
    await addFile(user, pdf('parte-1.pdf'));
    await screen.findByRole('button', { name: 'Confirmar (1)' });
    await assertNoA11yViolations(screen.getByRole('dialog'));
  });
});
