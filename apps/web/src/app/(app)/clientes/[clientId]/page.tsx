/**
 * Tela de detalhe do cliente — `/clientes/{clientId}`. Doc §10.1.
 *
 * Server component que apenas extrai o `clientId` de `params` e delega
 * para `<ClientDetailClient>`. Mantém a regra do CLAUDE.md §6: "server
 * components por padrão" — o trabalho client-side fica isolado no
 * orquestrador, com TanStack Query controlando a busca dos dados.
 */

import { ClientDetailClient } from '@/components/features/clients/client-detail-client';

export default function ClientDetailPage({ params }: { params: { clientId: string } }) {
  return <ClientDetailClient clientId={params.clientId} />;
}
