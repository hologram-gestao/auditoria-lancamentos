/**
 * Tela principal do cliente — `/clientes/{clientId}`: a **Lista de
 * Conciliações** (Sprint 4 / R1). O antigo "Histórico de Conciliações", que era
 * uma seção dentro do detalhe, virou esta visão.
 *
 * Server component fino: extrai o `clientId` e delega. O `<Suspense>` é
 * obrigatório — a lista usa `useSearchParams` (filtros/paginação na URL) e o
 * Next exige um boundary para não forçar a rota inteira a client-side rendering.
 */

import { Suspense } from 'react';

import { ReconciliationsScreen } from '@/components/features/reconciliations/list/reconciliations-screen';

export default function ClientReconciliationsPage({
  params,
}: {
  params: { clientId: string };
}) {
  return (
    <Suspense fallback={<ListFallback />}>
      <ReconciliationsScreen clientId={params.clientId} />
    </Suspense>
  );
}

function ListFallback() {
  return (
    <div role="status" className="space-y-3" aria-busy="true" aria-label="Carregando conciliações">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
          <div className="bg-muted h-4 w-1/3 animate-pulse rounded" />
          <div className="bg-muted h-3 w-2/3 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}
