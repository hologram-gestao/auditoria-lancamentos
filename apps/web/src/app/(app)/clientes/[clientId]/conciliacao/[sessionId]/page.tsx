/**
 * Tela de Revisão — `/clientes/{clientId}/conciliacao/{sessionId}` (S11 FRONT 9.11).
 *
 * Server component magrinho: só extrai os params e delega para o orquestrador
 * client-side. O contrato funcional vive em `Docs/documentation/14. Tela de
 * Revisão`. Mantém o mesmo padrão de `/processando/[sessionId]/page.tsx`.
 */

import { ReviewScreen } from '@/components/features/reconciliations/review/review-screen';

interface PageProps {
  params: { clientId: string; sessionId: string };
}

export default function ReviewPage({ params }: PageProps) {
  return <ReviewScreen clientId={params.clientId} sessionId={params.sessionId} />;
}
