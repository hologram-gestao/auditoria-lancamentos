/**
 * Porta de entrada do lançamento no Omie (Sprint 7 / FRONT 07.6).
 *
 * **Executor:** job `Web (lint · type · test)` do `.github/workflows/ci.yml`
 * (`pnpm test:web` → vitest).
 *
 * O que estes testes travam — e por que cada um existe:
 *
 *   - **Elegibilidade é a mesma do servidor.** A ação escreve na contabilidade
 *     do cliente; oferecê-la numa linha que o backend recusa é mostrar ao
 *     operador um caminho que termina em erro. A matriz cobre cartão × conta
 *     corrente e `sem_omie` × `conciliado` × `ignorado` × já vinculada.
 *   - **Linha inelegível não entra na seleção.** É o que impede o lote de
 *     carregar uma compra que o operador não podia mandar.
 *   - **Duplo-clique não vira dois envios.** O botão é `disabled` de verdade
 *     durante o envio (não só `aria-disabled`), então o segundo clique não
 *     chega ao handler.
 *   - **O motivo do bloqueio é ALCANÇÁVEL.** `aria-disabled` (e não `disabled`)
 *     mantém o botão na ordem de foco: quem usa leitor de tela lê a razão.
 *     Com `disabled` real, o botão sai da árvore de foco e a razão vira
 *     decoração — foi essa a escolha registrada no componente.
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const fileEntriesState = {
  data: undefined as { data: unknown[]; pagination: unknown } | undefined,
  isLoading: false,
};

vi.mock('@/hooks/use-reconciliations', () => ({
  useFileEntries: () => fileEntriesState,
  useOmieLancamentos: () => ({ data: [], isLoading: false }),
  useAllSessionAnomalies: () => ({ data: [], isLoading: false }),
  useAllSemOmieEntries: () => ({ data: [], isLoading: false }),
  usePatchFileEntry: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

const postMock = vi.fn();
vi.mock('@/hooks/use-omie-postings', () => ({
  usePostOmieLancamentos: () => ({ mutateAsync: postMock, isPending: false }),
  useOmieCategorias: () => ({ data: undefined, isLoading: false }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// Imports do SUT DEPOIS dos `vi.mock`.
import {
  LancarLoteBar,
  LancarNoOmieButton,
} from '@/components/features/reconciliations/review/lancar-no-omie-controls';
import { MovementsTab } from '@/components/features/reconciliations/review/movements-tab';
import {
  getPostingBlock,
  POSTING_BLOCK_MESSAGE,
} from '@/components/features/reconciliations/review/omie-posting-eligibility';
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

function setEntries(rows: FileEntryItem[]) {
  fileEntriesState.data = {
    data: rows,
    pagination: { page: 1, pageSize: 20, total: rows.length, totalPages: 1 },
  };
}

const launchButtons = () => screen.queryAllByRole('button', { name: /Lançar no Omie/ });

beforeAll(() => {
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => undefined;
  Element.prototype.releasePointerCapture ??= () => undefined;
  Element.prototype.scrollIntoView ??= () => undefined;
});

beforeEach(() => {
  vi.clearAllMocks();
  fileEntriesState.isLoading = false;
  setEntries([entry()]);
});

describe('Elegibilidade — espelho declarado do servidor', () => {
  it.each([
    ['sem_omie de cartão', { situation: 'sem_omie' }, true, null],
    ['conciliada', { situation: 'conciliado' }, true, 'nao_e_sem_omie'],
    ['ignorada', { situation: 'ignorado' }, true, 'linha_ignorada'],
    [
      'já vinculada a lançamento',
      { situation: 'sem_omie', omie_lancamento_id: 9001 },
      true,
      'ja_lancada',
    ],
    ['sem_omie de conta corrente', { situation: 'sem_omie' }, false, 'sessao_nao_e_cartao'],
  ])('%s', (_nome, over, isCard, esperado) => {
    expect(getPostingBlock(entry(over as Partial<FileEntryItem>), { isCard })).toBe(esperado);
  });

  it('ignorada VENCE já-lançada — a ordem de precedência é a do backend', () => {
    // As duas condições valem ao mesmo tempo; o motivo lido tem de ser o mesmo
    // que o servidor devolveria no resumo, senão a tela e o lote se contradizem.
    expect(
      getPostingBlock(entry({ situation: 'ignorado', omie_lancamento_id: 9001 }), { isCard: true }),
    ).toBe('linha_ignorada');
  });
});

describe('Aba de Movimentações — cartão', () => {
  it('linha sem_omie exibe a ação e pode ser selecionada', () => {
    render(<MovementsTab sessionId={SESSION_ID} isCard />);

    const acao = launchButtons()[0];
    expect(acao).toBeDefined();
    expect(acao).not.toHaveAttribute('aria-disabled');
    expect(screen.getByRole('checkbox', { name: /Selecionar a compra de 10\/06\/2026/ })).toBeEnabled();
  });

  it.each([
    ['conciliada', { id: 'e2', situation: 'conciliado', omie_lancamento_id: 9001 }, 'ja_lancada'],
    ['ignorada', { id: 'e3', situation: 'ignorado' }, 'linha_ignorada'],
  ] as const)(
    'linha %s tem a ação indisponível, com o motivo alcançável, e não entra na seleção',
    (_nome, over, motivo) => {
      setEntries([entry(over)]);
      render(<MovementsTab sessionId={SESSION_ID} isCard />);

      const acao = launchButtons()[0];
      expect(acao).toHaveAttribute('aria-disabled', 'true');
      // `aria-describedby` aponta para o texto do motivo — e o motivo é a
      // MESMA frase que o backend devolveria.
      const descId = acao?.getAttribute('aria-describedby');
      expect(descId).toBeTruthy();
      expect(document.getElementById(descId as string)?.textContent).toBe(
        POSTING_BLOCK_MESSAGE[motivo],
      );
      expect(screen.getByRole('checkbox', { name: /Selecionar a compra de/ })).toBeDisabled();
    },
  );

  it('a barra de lote aparece com a contagem ao marcar uma compra', async () => {
    const ui = userEvent.setup();
    render(<MovementsTab sessionId={SESSION_ID} isCard />);

    expect(screen.queryByRole('button', { name: /Lançar 1 compra no Omie/ })).toBeNull();
    await ui.click(screen.getByRole('checkbox', { name: /Selecionar a compra de/ }));
    expect(screen.getByRole('button', { name: 'Lançar 1 compra no Omie' })).toBeEnabled();
  });

  it('"selecionar todos da página" marca só as elegíveis', async () => {
    const ui = userEvent.setup();
    setEntries([
      entry({ id: 'e1' }),
      entry({ id: 'e2', situation: 'ignorado' }),
      entry({ id: 'e3', description: 'Uber 998' }),
    ]);
    render(<MovementsTab sessionId={SESSION_ID} isCard />);

    await ui.click(screen.getByRole('checkbox', { name: /Selecionar todas as compras desta página/ }));

    expect(screen.getByRole('button', { name: 'Lançar 2 compras no Omie' })).toBeEnabled();
  });

  it('sem violações critical/serious com linhas selecionadas', async () => {
    const ui = userEvent.setup();
    setEntries([
      entry({ id: 'e1' }),
      entry({ id: 'e2', description: 'Assinatura anual', situation: 'ignorado' }),
    ]);
    const view = render(<MovementsTab sessionId={SESSION_ID} isCard />);

    await ui.click(screen.getByRole('checkbox', { name: /Posto Shell 1234/ }));

    await assertNoA11yViolations(view.container);
  });
});

describe('Aba de Movimentações — conta corrente', () => {
  it('não expõe seleção nem ação de lançamento em lugar nenhum', () => {
    setEntries([entry(), entry({ id: 'e2', situation: 'conciliado', omie_lancamento_id: 9001 })]);
    render(<MovementsTab sessionId={SESSION_ID} isCard={false} />);

    expect(launchButtons()).toHaveLength(0);
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });
});

describe('Botões async', () => {
  /**
   * O `Button` desabilitado tem `pointer-events: none`, e o user-event recusa
   * clicar nele por padrão. Aqui o ponto do teste é exatamente o contrário:
   * MANDAR o clique e provar que ele não chega ao handler.
   */
  const setupIgnorandoPointerEvents = () => userEvent.setup({ pointerEventsCheck: 0 });

  it('durante o envio o botão fica desabilitado e o duplo-clique não repete a ação', async () => {
    const ui = setupIgnorandoPointerEvents();
    const onLaunch = vi.fn();
    render(<LancarLoteBar selectedCount={3} pending onLaunch={onLaunch} onClear={vi.fn()} />);

    const botao = screen.getByRole('button', { name: /Lançar 3 compras no Omie/ });
    expect(botao).toBeDisabled();
    await ui.click(botao);
    await ui.click(botao);
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('a ação da linha não dispara duas vezes num duplo-clique', async () => {
    const ui = setupIgnorandoPointerEvents();
    const onClick = vi.fn();
    const { rerender } = render(<LancarNoOmieButton block={null} onClick={onClick} />);

    const botao = screen.getByRole('button', { name: /Lançar no Omie/ });
    await ui.click(botao);
    // O envio começou: a tela reflete `pending` e o 2º clique não passa.
    rerender(<LancarNoOmieButton block={null} pending onClick={onClick} />);
    await ui.click(botao);

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('bloqueada por elegibilidade: continua focável (o motivo é lido) e inerte', async () => {
    const ui = userEvent.setup();
    const onClick = vi.fn();
    render(<LancarNoOmieButton block="linha_ignorada" onClick={onClick} />);

    const botao = screen.getByRole('button', { name: /Lançar no Omie/ });
    expect(botao).toBeEnabled(); // `aria-disabled`, não `disabled`
    await ui.click(botao);
    expect(onClick).not.toHaveBeenCalled();
  });
});
