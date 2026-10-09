/**
 * Vocabulário de TELA do processo de lançamento das compras do cartão no Omie
 * (86e3n70p0). Um lugar só: o editar cliente, o novo cliente, a gaveta de
 * conciliação e o cabeçalho da revisão leem daqui, para a mesma coisa não ter
 * dois nomes em duas telas.
 *
 * O valor vem do contrato (`CardPostingDateMode`); um valor novo no backend
 * quebra o `Record` abaixo até alguém decidir o rótulo dele.
 */
import type { CardPostingDateMode } from '@/lib/contracts';

export const CARD_POSTING_DATE_MODE_FIELD_LABEL = 'Compras do cartão no Omie';

export const CARD_POSTING_DATE_MODE_LABEL: Record<CardPostingDateMode, string> = {
  purchase_date: 'Na data da compra',
  invoice_due_date: 'No vencimento da fatura',
};

/** Ordem das opções nos seletores: o processo de sempre primeiro. */
export const CARD_POSTING_DATE_MODES: readonly CardPostingDateMode[] = [
  'purchase_date',
  'invoice_due_date',
];

export const CARD_POSTING_DATE_MODE_HINT =
  'Como as compras do cartão de crédito deste cliente entram no Omie. Vale para as conciliações de cartão criadas a partir de agora.';

export function isCardPostingDateMode(value: unknown): value is CardPostingDateMode {
  return CARD_POSTING_DATE_MODES.includes(value as CardPostingDateMode);
}

interface SessionCardProcess {
  account_type: string;
  card_posting_date_mode?: CardPostingDateMode | null;
  invoice_due_date?: string | null;
}

/**
 * A linha do cabeçalho da revisão que diz COMO esta fatura foi cruzada.
 * `null` fora do cartão e em sessão antiga (sem o snapshot do processo).
 * Recebe a data já formatada para não importar o formatador aqui.
 */
export function sessionCardProcessLine(
  session: SessionCardProcess,
  formatDate: (iso: string) => string,
): string | null {
  if (session.account_type !== 'credit_card' || session.card_posting_date_mode == null) {
    return null;
  }
  if (session.card_posting_date_mode === 'invoice_due_date') {
    const lot = session.invoice_due_date ? formatDate(session.invoice_due_date) : '—';
    return `Compras lançadas no vencimento da fatura · Lote da fatura: ${lot}`;
  }
  return session.invoice_due_date
    ? `Compras lançadas na data da compra · Vencimento da fatura: ${formatDate(session.invoice_due_date)}`
    : 'Compras lançadas na data da compra';
}
