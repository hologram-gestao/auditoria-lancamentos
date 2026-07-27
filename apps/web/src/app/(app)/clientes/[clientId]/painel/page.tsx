/**
 * Painel do cliente — `/clientes/{clientId}/painel` (Sprint 4 / R7, desejável).
 *
 * Rota PRÓPRIA: a Lista de Conciliações continua sendo a tela principal do
 * cliente (`/clientes/{id}`) — o painel não disputa essa rota.
 */

import { ClientDashboard } from '@/components/features/clients/client-dashboard';

export default function ClientDashboardPage({ params }: { params: { clientId: string } }) {
  return <ClientDashboard clientId={params.clientId} />;
}
