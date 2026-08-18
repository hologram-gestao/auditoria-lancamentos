/**
 * Gaveta de lançamento: classificação, confirmação e resumo (Sprint 7 / FRONT 07.7).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que estes testes travam:
 *
 *   - **Sem categoria não lança.** A linha fica com aviso visível e NÃO entra no
 *     corpo do request. É a regra que impede o ADL de mandar à Omie um
 *     `cCodCateg` vazio — que voltaria como erro do fornecedor por causa nossa.
 *   - **Aplicar em lote e sobrescrever depois.** O botão de lote preenche todas;
 *     a escolha individual feita em seguida é a que vale (a da linha vence).
 *   - **O resumo por linha aparece com o motivo do backend**, inclusive no lote
 *     parcial — e o toast do parcial é `warning`, nunca `success`.
 *   - **A gaveta NÃO fecha no parcial.** Fechar levaria embora exatamente o que
 *     o operador precisa ler, e é dali que ele reexecuta o que falhou.
 *   - **Reexecutar não reenvia o que já entrou:** a linha lançada sai do corpo
 *     do request seguinte.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const postMock = vi.fn();
const postState = { mutateAsync: postMock, isPending: false };
const categoriasState = {
  data: undefined as { data: { codigo: string; descricao: string }[]; total: number } | undefined,
  isLoading: false,
  isError: false,
  isFetching: false,
  refetch: vi.fn(),
};

vi.mock('@/hooks/use-omie-postings', () => ({
  usePostOmieLancamentos: () => postState,
  useOmieCategorias: () => categoriasState,
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
import { LancarNoOmieDrawer } from '@/components/features/reconciliations/review/lancar-no-omie-drawer';
import type { OmiePostingBatchPayload } from '@/lib/api/omie-postings';
import type { FileEntryItem } from '@/lib/api/reconciliations';
import { assertNoA11yViolations } from '@/test/a11y';

const SESSION_ID = 's1';

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

function renderDrawer(entries: FileEntryItem[] = DUAS, onPosted = vi.fn()) {
  return {
    onPosted,
    ...render(
      <LancarNoOmieDrawer
        sessionId={SESSION_ID}
        entries={entries}
        open
        onOpenChange={vi.fn()}
        onPosted={onPosted}
      />,
    ),
  };
}

/** Abre o combobox indicado pelo nome acessível e escolhe a opção pelo texto. */
async function escolherCategoria(ui: ReturnType<typeof userEvent.setup>, gatilho: RegExp, opcao: RegExp) {
  await ui.click(screen.getByRole('button', { name: gatilho }));
  const listbox = await screen.findByRole('listbox');
  await ui.click(within(listbox).getByRole('option', { name: opcao }));
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
  categoriasState.isLoading = false;
  categoriasState.isError = false;
  categoriasState.isFetching = false;
  categoriasState.data = {
    data: [
      { codigo: '1.01.01', descricao: 'Combustível' },
      { codigo: '2.02.02', descricao: 'Serviços de software' },
    ],
    total: 2,
  };
});

describe('Classificação', () => {
  it('sem categoria a compra fica bloqueada e o envio nem é oferecido', () => {
    renderDrawer();

    expect(screen.getAllByText(/Sem categoria — esta compra não será lançada/)).toHaveLength(2);
    expect(screen.getByRole('button', { name: /Confirmar e lançar 0 de 2/ })).toBeDisabled();
    expect(screen.getByText('2 compras ficam de fora (sem categoria)')).toBeInTheDocument();
  });

  it('classifica UMA e o lote leva só ela', async () => {
    const ui = userEvent.setup();
    renderDrawer();

    await escolherCategoria(ui, /Categoria da compra de 10\/06\/2026/, /Combustível/);

    const confirmar = screen.getByRole('button', { name: /Confirmar e lançar 1 de 2/ });
    expect(confirmar).toBeEnabled();
    postMock.mockResolvedValue(payload([line('e1', 'lancada', { omie_lancamento_id: 5001 })]));
    await ui.click(confirmar);

    expect(postMock).toHaveBeenCalledWith([{ file_entry_id: 'e1', cod_categoria: '1.01.01' }]);
  });

  it('aplicar em lote classifica todas, e a escolha da linha prevalece depois', async () => {
    const ui = userEvent.setup();
    renderDrawer();

    await escolherCategoria(ui, /Categoria para todas as compras/, /Combustível/);
    await ui.click(screen.getByRole('button', { name: 'Aplicar a 2 compras' }));

    expect(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ })).toBeEnabled();
    expect(screen.queryByText(/Sem categoria — esta compra não será lançada/)).toBeNull();

    // Override individual DEPOIS do lote: a segunda linha muda, a primeira não.
    await escolherCategoria(
      ui,
      /Categoria da compra de 14\/06\/2026 — Assinatura de software/,
      /Serviços de software/,
    );

    postMock.mockResolvedValue(payload([line('e1', 'lancada'), line('e2', 'lancada')]));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }));

    expect(postMock).toHaveBeenCalledWith([
      { file_entry_id: 'e1', cod_categoria: '1.01.01' },
      { file_entry_id: 'e2', cod_categoria: '2.02.02' },
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
  async function lancarComResultado(linhas: ReturnType<typeof line>[]) {
    const ui = userEvent.setup();
    const view = renderDrawer();
    await escolherCategoria(ui, /Categoria para todas as compras/, /Combustível/);
    await ui.click(screen.getByRole('button', { name: 'Aplicar a 2 compras' }));
    postMock.mockResolvedValue(payload(linhas));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }));
    return { ui, ...view };
  }

  it('parcial: mostra o motivo de cada linha, avisa por toast de aviso e NÃO fecha', async () => {
    await lancarComResultado([
      line('e1', 'lancada', { omie_lancamento_id: 5001 }),
      line('e2', 'erro', {
        reason: 'erro_omie',
        message: 'Categoria 1.01.01 nao encontrada para o cliente.',
      }),
    ]);

    await waitFor(() => expect(screen.getByText(/Lançada no Omie/)).toBeInTheDocument());
    expect(screen.getByText(/lançamento nº 5001/)).toBeInTheDocument();
    expect(
      screen.getByText(/Categoria 1\.01\.01 nao encontrada para o cliente\./),
    ).toBeInTheDocument();
    // Parcial é AVISO — nunca sucesso verde, que diria "deu tudo certo".
    expect(toastWarning).toHaveBeenCalledWith('1 de 2 compras lançadas. Veja o motivo das demais.');
    expect(toastSuccess).not.toHaveBeenCalled();
    // A gaveta segue aberta: é dela que o operador lê o motivo e reexecuta.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('reexecução não reenvia a linha que já entrou', async () => {
    const { ui } = await lancarComResultado([
      line('e1', 'lancada', { omie_lancamento_id: 5001 }),
      line('e2', 'erro', { reason: 'omie_indisponivel', message: 'O Omie não respondeu.' }),
    ]);

    postMock.mockClear();
    postMock.mockResolvedValue(payload([line('e2', 'lancada', { omie_lancamento_id: 5002 })]));
    await ui.click(screen.getByRole('button', { name: /Tentar novamente 1 de 1/ }));

    expect(postMock).toHaveBeenCalledWith([{ file_entry_id: 'e2', cod_categoria: '1.01.01' }]);
  });

  it('tudo lançado: toast de sucesso e a linha volta para a tabela como lançada', async () => {
    const onPosted = vi.fn();
    const ui = userEvent.setup();
    renderDrawer(DUAS, onPosted);
    await escolherCategoria(ui, /Categoria para todas as compras/, /Combustível/);
    await ui.click(screen.getByRole('button', { name: 'Aplicar a 2 compras' }));
    postMock.mockResolvedValue(
      payload([
        line('e1', 'lancada', { omie_lancamento_id: 5001 }),
        line('e2', 'lancada', { omie_lancamento_id: 5002 }),
      ]),
    );
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }));

    await waitFor(() => expect(toastSuccess).toHaveBeenCalledWith('2 compras lançadas no Omie.'));
    expect(onPosted).toHaveBeenCalledWith({ e1: 5001, e2: 5002 });
    // Sem nada pendente, some a ação de envio e sobra "Fechar".
    expect(screen.queryByRole('button', { name: /Confirmar e lançar/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Concluir' })).toBeInTheDocument();
  });

  it('erro do LOTE inteiro (ex.: recurso desligado) vira toast de erro e mantém a classificação', async () => {
    const ui = userEvent.setup();
    renderDrawer();
    await escolherCategoria(ui, /Categoria para todas as compras/, /Combustível/);
    await ui.click(screen.getByRole('button', { name: 'Aplicar a 2 compras' }));

    postMock.mockRejectedValue(new Error('boom'));
    await ui.click(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    // A classificação continua na tela — o operador não refaz o trabalho.
    expect(screen.getByRole('button', { name: /Confirmar e lançar 2 de 2/ })).toBeEnabled();
  });
});

describe('Acessibilidade', () => {
  it('sem violações critical/serious com o combobox ABERTO', async () => {
    const ui = userEvent.setup();
    const view = renderDrawer();

    await ui.click(screen.getByRole('button', { name: /Categoria para todas as compras/ }));
    await screen.findByRole('listbox');

    // O conteúdo do popover é portalizado: medir `container` deixaria a lista
    // de fora, que é justamente o que se quer medir.
    await assertNoA11yViolations(document.body);
    expect(view.container).toBeTruthy();
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
      screen.getByRole('button', { name: /Categoria da compra de.*2\.02\.02 · Serviços de software/ }),
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
