/**
 * Painel do cliente — `/clientes/{clientId}/painel` (86e3k1q54). A entrada do
 * cliente é a lista de conciliações (`/clientes/{clientId}`, decisão do Pedro
 * em 08/10/2026); o painel tem rota própria.
 *
 * Server component fino: extrai o `clientId` e delega. O painel lê
 * `?conectar=<tipo>` quando mostra a seção de origens (cliente sem origem
 * ativa), então vai dentro de um `<Suspense>` pelo mesmo motivo da lista.
 */

import { Suspense } from 'react';

import { ClientDashboard } from '@/components/features/clients/client-dashboard';

export default function ClientDashboardPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense>
      <ClientDashboard clientId={params.clientId} />
    </Suspense>
  );
}
