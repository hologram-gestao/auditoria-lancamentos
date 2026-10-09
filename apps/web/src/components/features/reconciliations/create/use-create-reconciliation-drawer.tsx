'use client';

/**
 * A gaveta de criação de conciliação com o fluxo desacoplado inteiro, para
 * QUALQUER tela que ofereça "Nova conciliação" (a lista e, desde a 86e3k1q54, o
 * painel do cliente). Uma cópia só:
 *   1. ao confirmar, o backend devolve o `session_id` na hora → a gaveta fecha,
 *      sai o **toast** e a lista do cliente é invalidada;
 *   2. a criação é registrada em `usePendingCreations` para que, se a pessoa
 *      sair para outra rota antes do término, o `autor_navegou_fora` seja
 *      emitido — o evento que **prova o outcome** da Sprint 4.
 *
 * As contas vêm do cache do `useClientDetail` (mesma query key), sem 2º request.
 */
import { useQueryClient } from '@tanstack/react-query';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';

import { clientsKeys, useClientDetail } from '@/hooks/use-clients';
import { usePendingCreations } from '@/stores/pending-creations';

import {
  CreateReconciliationDrawer,
  type CreatedReconciliation,
} from './create-reconciliation-drawer';

export function useCreateReconciliationDrawer(clientId: string): {
  open: () => void;
  drawer: React.ReactNode;
} {
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const detailQuery = useClientDetail(clientId);
  const track = usePendingCreations((s) => s.track);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const accounts = detailQuery.data?.accounts ?? [];

  function handleCreated({ sessionId, totalFiles }: CreatedReconciliation) {
    setDrawerOpen(false);
    track({ sessionId, clientId, createdAtMs: Date.now(), originPath: pathname });
    toast.success(
      totalFiles > 1
        ? `Conciliação criada com ${totalFiles} arquivos. Você será avisado quando terminar.`
        : 'Conciliação criada. Você será avisado quando terminar.',
    );
    // Refetch imediato: o item novo entra na lista sem reload manual, e o
    // polling de 3 s assume a partir daí.
    void queryClient.invalidateQueries({ queryKey: clientsKeys.reconciliationsAll(clientId) });
  }

  return {
    open: () => setDrawerOpen(true),
    drawer: (
      <CreateReconciliationDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        clientId={clientId}
        accounts={accounts}
        clientCardPostingDateMode={detailQuery.data?.card_posting_date_mode ?? 'purchase_date'}
        onCreated={handleCreated}
      />
    ),
  };
}
