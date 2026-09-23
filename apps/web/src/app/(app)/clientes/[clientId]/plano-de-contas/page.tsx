/**
 * Plano de contas do cliente — `/clientes/{clientId}/plano-de-contas`
 * (Sprint 10 / R3).
 *
 * Server component fino: extrai o `clientId` e delega, como `glossario/` e
 * `usuarios/`. O `<Suspense>` é obrigatório — a tela usa `useSearchParams`
 * (filtros e paginação na URL) e sem o boundary o Next força a rota inteira a
 * client-side rendering.
 */

import { Suspense } from 'react';

import { ChartOfAccountsScreen } from '@/components/features/chart-of-accounts/chart-of-accounts-screen';

import ChartOfAccountsLoading from './loading';

export default function ChartOfAccountsPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<ChartOfAccountsLoading />}>
      <ChartOfAccountsScreen clientId={params.clientId} />
    </Suspense>
  );
}
