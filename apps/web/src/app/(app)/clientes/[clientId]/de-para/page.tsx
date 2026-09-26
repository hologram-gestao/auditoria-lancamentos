/**
 * De-para do cliente — `/clientes/{clientId}/de-para` (Sprint 12 / R6).
 *
 * Server component fino: extrai o `clientId` e delega, como `carteira/` e
 * `plano-de-contas/`. O `<Suspense>` é obrigatório — a tela usa
 * `useSearchParams` (destino, aba, filtros, página e competência na URL) e sem
 * o boundary o Next força a rota inteira a client-side rendering.
 */

import { Suspense } from 'react';

import { ClientMappingScreen } from '@/components/features/client-mapping/client-mapping-screen';

import ClientMappingLoading from './loading';

export default function ClientMappingPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<ClientMappingLoading />}>
      <ClientMappingScreen clientId={params.clientId} />
    </Suspense>
  );
}
