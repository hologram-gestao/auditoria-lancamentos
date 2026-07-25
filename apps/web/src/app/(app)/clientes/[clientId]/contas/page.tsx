/**
 * Contas Bancárias do cliente — `/clientes/{clientId}/contas` (Sprint 4 / R6).
 *
 * `<Suspense>` obrigatório: a tela usa `useSearchParams` (paginação na URL).
 */

import { Suspense } from 'react';

import { BankAccountsScreen } from '@/components/features/clients/bank-accounts-screen';

export default function ClientAccountsPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<AccountsFallback />}>
      <BankAccountsScreen clientId={params.clientId} />
    </Suspense>
  );
}

function AccountsFallback() {
  return (
    <div
      className="space-y-2 rounded-lg border p-4"
      aria-busy="true"
      aria-label="Carregando contas bancárias"
    >
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="bg-muted h-4 w-full animate-pulse rounded" />
      ))}
    </div>
  );
}
