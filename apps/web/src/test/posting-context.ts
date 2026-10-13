/**
 * Contexto da gaveta "Lançar no Omie" para os testes das abas da revisão
 * (86e3n70q0, bloco C): cliente sem rateio obrigatório e sem fornecedor padrão,
 * processo "na data da compra" (o de sempre).
 */
import type { PostingClientContext } from '@/components/features/reconciliations/review/lancar-no-omie-drawer';

export const TEST_POSTING_CONTEXT: PostingClientContext = {
  clientId: 'client-test',
  cardPostingDateMode: 'purchase_date',
  invoiceDueDate: null,
  departmentRequired: false,
  defaultSupplierCode: null,
};
