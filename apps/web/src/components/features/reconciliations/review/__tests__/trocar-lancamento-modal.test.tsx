/**
 * Testes do modal "Trocar lançamento" (follow-up de a11y 86e2gy1n0).
 *
 * O defeito: a seleção do candidato Omie era um `onClick` no `<tr>` + um
 * `aria-selected`. Quem navega por teclado abria o modal e **não conseguia
 * selecionar nada** (WCAG 2.1.1), e o `aria-selected` era ARIA inválido em
 * `role="row"` fora de `grid`/`treegrid`.
 *
 * **O axe não pega nenhum dos dois** — medido, não suposto (rodando esta suíte
 * contra o código antigo, os 2 testes de axe que sobrevivem passam):
 *   - defeito (1): não existe regra para "handler de clique em elemento
 *     não-focável"; a violação que disparava ali (`scrollable-region-focusable`)
 *     parou por efeito colateral da correção do `ui/table.tsx`
 *     (ADR-004-FE-A11Y-TABLE) — a regra silenciou, o defeito não;
 *   - defeito (2): `aria-allowed-attr` **não** reprova `aria-selected` em `<tr>`,
 *     porque a ARIA 1.2 lista `aria-selected` como *supported* em `role="row"`.
 *     Ele só carrega significado dentro de `grid`/`treegrid`; fora disso é ruído
 *     que o leitor de tela anuncia sem que exista modelo de seleção por trás.
 *
 * Por isso a trava é comportamental: um teste que **opera o modal só com
 * teclado** e uma asserção direta sobre a ausência do atributo. Os testes de axe
 * ficam como rede para regressões de outra natureza (rótulo, foco, contraste
 * fora de jsdom).
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

const mutateAsyncMock: Mock = vi.fn();
const toastSuccess: Mock = vi.fn();
const toastError: Mock = vi.fn();

const candidatesState = {
  data: undefined as unknown[] | undefined,
  isLoading: false,
};

vi.mock('@/hooks/use-reconciliations', () => ({
  useAvailableOmieEntries: () => candidatesState,
  usePatchFileEntry: () => ({ mutateAsync: mutateAsyncMock, isPending: false }),
}));

vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => toastSuccess(...args),
    error: (...args: unknown[]) => toastError(...args),
  },
}));

import { TrocarLancamentoModal } from '@/components/features/reconciliations/review/trocar-lancamento-modal';
import type { AvailableOmieEntry, FileEntryItem } from '@/lib/api/reconciliations';
import { assertNoA11yViolations } from '@/test/a11y';

const entry = {
  id: 'entry-1',
  transaction_date: '2026-06-10',
  description: 'Pagamento fornecedor X',
  amount: '-150.50',
} as FileEntryItem;

function candidate(over: Partial<AvailableOmieEntry> = {}): AvailableOmieEntry {
  return {
    omie_id: 9001,
    transaction_date: '2026-06-11',
    description: 'NF 123 - Fornecedor X',
    supplier: 'Fornecedor X LTDA',
    category: 'Serviços',
    amount: '-150.50',
    status: 'Conciliado',
    ...over,
  };
}

function renderModal(onOpenChange = vi.fn()) {
  return render(
    <TrocarLancamentoModal sessionId="s1" entry={entry} open onOpenChange={onOpenChange} />,
  );
}

beforeEach(() => {
  mutateAsyncMock.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  candidatesState.data = [candidate(), candidate({ omie_id: 9002, description: 'NF 124' })];
  candidatesState.isLoading = false;
});

describe('TrocarLancamentoModal — operabilidade por teclado', () => {
  it('cada candidato tem um radio focável com nome acessível próprio', () => {
    renderModal();
    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(2);
    expect(
      screen.getByRole('radio', { name: /Selecionar lançamento .*NF 123 - Fornecedor X/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /NF 124/ })).toBeInTheDocument();
  });

  it('seleciona um candidato e confirma SEM mouse (Tab + Enter)', async () => {
    mutateAsyncMock.mockResolvedValue({});
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    renderModal(onOpenChange);

    const radio = screen.getByRole('radio', { name: /NF 123 - Fornecedor X/ });
    expect(screen.getByRole('button', { name: 'Confirmar' })).toBeDisabled();

    // Tab até o radio: prova que ele está na ordem de foco natural (o defeito
    // era exatamente não haver nada focável na tabela de candidatos).
    for (let i = 0; i < 30 && document.activeElement !== radio; i++) {
      await user.tab();
    }
    expect(radio).toHaveFocus();

    await user.keyboard('{Enter}');
    expect(radio).toBeChecked();

    const confirmar = screen.getByRole('button', { name: 'Confirmar' });
    await waitFor(() => expect(confirmar).toBeEnabled());
    confirmar.focus();
    await user.keyboard('{Enter}');

    await waitFor(() => expect(mutateAsyncMock).toHaveBeenCalledTimes(1));
    expect(mutateAsyncMock).toHaveBeenCalledWith({
      entryId: 'entry-1',
      payload: { omie_lancamento_id: 9001 },
    });
    expect(toastSuccess).toHaveBeenCalledWith('Lançamento Omie atualizado.');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('Espaço (comportamento nativo do radio) também seleciona', async () => {
    const user = userEvent.setup();
    renderModal();
    const radio = screen.getByRole('radio', { name: /NF 124/ });
    radio.focus();
    await user.keyboard(' ');
    expect(radio).toBeChecked();
    expect(screen.getByRole('button', { name: 'Confirmar' })).toBeEnabled();
  });

  it('clicar na linha continua selecionando (conveniência de mouse preservada)', async () => {
    const user = userEvent.setup();
    renderModal();
    // Célula de descrição da 1ª linha — clique fora do radio, para provar que a
    // linha inteira continua sendo alvo de mouse.
    await user.click(screen.getByText('NF 123 - Fornecedor X'));
    expect(screen.getByRole('radio', { name: /NF 123 - Fornecedor X/ })).toBeChecked();
  });
});

describe('TrocarLancamentoModal — ARIA', () => {
  it('nenhuma linha usa aria-selected (proibido em role="row" fora de grid)', () => {
    renderModal();
    expect(document.querySelectorAll('tr[aria-selected]')).toHaveLength(0);
  });

  it('não tem violações axe critical/serious com candidatos listados', async () => {
    renderModal();
    // Portalado pelo Radix — o axe precisa olhar o `document.body`, não o
    // container do render (pitfall já registrado no teste do sino).
    await assertNoA11yViolations(document.body);
  });

  it('não tem violações axe critical/serious com um candidato selecionado', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(screen.getByRole('radio', { name: /NF 123 - Fornecedor X/ }));
    await assertNoA11yViolations(document.body);
  });

  it('não tem violações axe critical/serious no estado vazio', async () => {
    candidatesState.data = [];
    renderModal();
    expect(screen.getByText('Nenhum lançamento disponível.')).toBeInTheDocument();
    await assertNoA11yViolations(document.body);
  });
});
