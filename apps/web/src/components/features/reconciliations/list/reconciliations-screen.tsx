'use client';

/**
 * Orquestrador da lista de conciliações do cliente (`/clientes/{id}/conciliacoes`):
 * Lista + gaveta de criação (Sprint 4 / R1 + R2).
 *
 * O fluxo desacoplado inteiro (gaveta → toast → lista invalidada → polling de
 * 3 s → `autor_navegou_fora`) mora em `useCreateReconciliationDrawer`, o MESMO
 * que o painel do cliente usa para o "Nova conciliação" (86e3k1q54).
 */

import { useClientDetail } from '@/hooks/use-clients';

import { useCreateReconciliationDrawer } from '../create/use-create-reconciliation-drawer';

import { ReconciliationsList } from './reconciliations-list';

export function ReconciliationsScreen({ clientId }: { clientId: string }) {
  const detailQuery = useClientDetail(clientId);
  const creation = useCreateReconciliationDrawer(clientId);

  return (
    <>
      <ReconciliationsList
        clientId={clientId}
        accounts={detailQuery.data?.accounts ?? []}
        onCreateClick={creation.open}
      />
      {creation.drawer}
    </>
  );
}
