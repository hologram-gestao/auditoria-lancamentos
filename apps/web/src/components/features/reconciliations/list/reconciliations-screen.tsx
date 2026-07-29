'use client';

/**
 * Orquestrador da tela principal do cliente: Lista de Conciliações + gaveta de
 * criação (Sprint 4 / R1 + R2).
 *
 * O fluxo desacoplado inteiro passa por aqui:
 *   1. "Criar conciliação" abre a GAVETA (nunca mais uma página bloqueante);
 *   2. ao confirmar, o backend devolve o `session_id` na hora → a gaveta fecha,
 *      sai o **toast** e a lista é invalidada;
 *   3. a lista refetcha, o item novo aparece "Em processamento" e o polling de
 *      3 s (em `useReconciliationsList`) o acompanha até "Processada"/"Erro";
 *   4. a criação é registrada em `usePendingCreations` para que, se a pessoa
 *      sair para outra rota antes do término, o `autor_navegou_fora` seja
 *      emitido — o evento que **prova o outcome** da sprint.
 *
 * As contas vêm do cache do `<ClientShell>` (mesma query key), sem 2º request.
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
} from '../create/create-reconciliation-drawer';

import { ReconciliationsList } from './reconciliations-list';

export function ReconciliationsScreen({ clientId }: { clientId: string }) {
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

  return (
    <>
      <ReconciliationsList
        clientId={clientId}
        accounts={accounts}
        onCreateClick={() => setDrawerOpen(true)}
      />
      <CreateReconciliationDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        clientId={clientId}
        accounts={accounts}
        onCreated={handleCreated}
      />
    </>
  );
}
