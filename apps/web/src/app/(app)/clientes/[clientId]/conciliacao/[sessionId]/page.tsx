/**
 * Detalhe da conciliação — `/clientes/{clientId}/conciliacao/{sessionId}`
 * (Sprint 4 / R3; antes esta rota caía direto na tela de revisão).
 *
 * Server component magrinho: extrai os params e delega. O `<Suspense>` é
 * obrigatório — a tela usa `useSearchParams` para manter a aba ativa na URL.
 */

import { Suspense } from 'react';

import { SessionDetailScreen } from '@/components/features/reconciliations/detail/session-detail-screen';

interface PageProps {
  params: { clientId: string; sessionId: string };
}

export default function ReconciliationDetailPage({ params }: PageProps) {
  return (
    <Suspense fallback={<DetailFallback />}>
      <SessionDetailScreen clientId={params.clientId} sessionId={params.sessionId} />
    </Suspense>
  );
}

function DetailFallback() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Carregando conciliação">
      <div className="bg-muted h-7 w-72 animate-pulse rounded" />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="bg-card h-16 animate-pulse rounded-lg border" />
        ))}
      </div>
    </div>
  );
}
