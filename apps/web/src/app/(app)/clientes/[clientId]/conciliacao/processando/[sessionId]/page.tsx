/**
 * Tela "Processamento em andamento" — `/clientes/{clientId}/conciliacao/processando/{sessionId}`.
 * `[FRONT 8.7]`, Doc §13.1.
 *
 * Server component magrinho (mesmo padrão de `nova/page.tsx` e `clientes/{id}/page.tsx`):
 * só extrai os dois params da URL e delega o trabalho — polling, redirects,
 * timeout, alertas — pro client component `<ProcessingScreen>`.
 *
 * Ambos os params são UUIDs gerados pelo servidor; nenhuma sanitização
 * adicional é feita aqui. Se o `sessionId` for inválido, o GET /status
 * retorna 404 e o `<ProcessingScreen>` mostra o estado de erro.
 */

import { ProcessingScreen } from '@/components/features/reconciliations/processing-screen';

interface PageProps {
  params: { clientId: string; sessionId: string };
}

export default function ProcessingPage({ params }: PageProps) {
  return <ProcessingScreen clientId={params.clientId} sessionId={params.sessionId} />;
}
