/**
 * Carteira de títulos do cliente — `/clientes/{clientId}/carteira`
 * (Sprint 11 / R4).
 *
 * Server component fino: extrai o `clientId` e delega, como `plano-de-contas/`
 * e `glossario/`. O `<Suspense>` é obrigatório — a tela usa `useSearchParams`
 * (filtros, ordenação e paginação na URL) e sem o boundary o Next força a rota
 * inteira a client-side rendering.
 */

import { Suspense } from 'react';

import { ClientTitlesScreen } from '@/components/features/client-titles/client-titles-screen';

import ClientTitlesLoading from './loading';

export default function ClientTitlesPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<ClientTitlesLoading />}>
      <ClientTitlesScreen clientId={params.clientId} />
    </Suspense>
  );
}
