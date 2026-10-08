/**
 * Tela de entrada do cliente — `/clientes/{clientId}`: o **painel** (86e3k1q54 /
 * 86e3k1q5n, decisão do Pedro em 07/10/2026). A lista de conciliações mora em
 * `/clientes/{clientId}/conciliacoes`.
 *
 * Server component fino: extrai o `clientId` e delega.
 */

import { ClientDashboard } from '@/components/features/clients/client-dashboard';

export default function ClientDashboardPage({ params }: { params: { clientId: string } }) {
  return <ClientDashboard clientId={params.clientId} />;
}
