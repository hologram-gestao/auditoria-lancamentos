/**
 * Tela "Nova Conciliação" — `/clientes/{clientId}/conciliacao/nova`. Doc §11.1.
 *
 * Server component que apenas extrai o `clientId` de `params` e delega para
 * `<NewReconciliationForm>`. Mesmo padrão da tela de detalhe (S7): server
 * component magrinho + client component com TanStack Query lá dentro.
 *
 * Esta sessão entrega APENAS o `[FRONT 5.1]` — o submit não dispara nada
 * real ainda. Hash + check-duplicate (`[FRONT 6.1]`) e parsing (S9) entram
 * nas próximas sessões.
 */

import { NewReconciliationForm } from '@/components/features/reconciliations/new-reconciliation-form';

export default function NewReconciliationPage({ params }: { params: { clientId: string } }) {
  return <NewReconciliationForm clientId={params.clientId} />;
}
