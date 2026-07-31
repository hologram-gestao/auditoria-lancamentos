/**
 * Usuários do cliente — `/clientes/{clientId}/usuarios` (Sprint 5 / R5).
 *
 * Server component fino: extrai o `clientId` e delega. O `<Suspense>` é
 * obrigatório — a tela usa `useSearchParams` (busca/paginação na URL) e sem o
 * boundary o Next força a rota inteira a client-side rendering.
 */

import { Suspense } from 'react';

import { ClientUsersScreen } from '@/components/features/client-users/client-users-screen';

import ClientUsersLoading from './loading';

export default function ClientUsersPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<ClientUsersLoading />}>
      <ClientUsersScreen clientId={params.clientId} />
    </Suspense>
  );
}
