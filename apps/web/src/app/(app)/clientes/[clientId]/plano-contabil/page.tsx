/**
 * Plano contábil do cliente — `/clientes/{clientId}/plano-contabil`
 * (Sprint 16 / R1 · R3).
 *
 * O plano do sistema contábil de DESTINO, distinto do "Plano de Contas" da
 * origem (`plano-de-contas/`, S10). Server component fino, como a irmã: extrai
 * o `clientId` e delega. O `<Suspense>` é obrigatório — a tela usa
 * `useSearchParams` (filtros e paginação na URL).
 */

import { Suspense } from 'react';

import { AccountingChartScreen } from '@/components/features/accounting-chart/accounting-chart-screen';

import AccountingChartLoading from './loading';

export default function AccountingChartPage({ params }: { params: { clientId: string } }) {
  return (
    <Suspense fallback={<AccountingChartLoading />}>
      <AccountingChartScreen clientId={params.clientId} />
    </Suspense>
  );
}
