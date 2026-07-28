'use client';

/**
 * Emissor do `autor_navegou_fora` — o evento que **prova o outcome** da Sprint 4.
 *
 * Fica montado no shell autenticado e observa a rota. Para cada conciliação que
 * esta aba criou e que ainda não terminou, se a pessoa sai para outra rota, o
 * evento é emitido com quantos segundos se passaram desde a criação.
 *
 * **Duas rotas NÃO contam como "navegou fora"**, e a distinção é a diferença
 * entre medir o outcome e inflar o número:
 *   1. a rota de origem (a lista onde ela criou) — ela não saiu de lugar nenhum;
 *   2. o detalhe DAQUELA conciliação — ir olhar o progresso é exatamente o
 *      "esperar olhando" que a sprint quer substituir. Contar isso como sucesso
 *      seria medir a máquina, não a pessoa (o mesmo erro que a métrica de
 *      outcome existe para evitar).
 *
 * Idempotência é do backend (mesmo evento + mesma sessão = 201 `recorded=false`),
 * então uma emissão repetida por re-render não duplica linha.
 *
 * `useEffect` aqui é legítimo: reage a uma NAVEGAÇÃO para produzir um efeito
 * colateral. A regra que o projeto proíbe é `useEffect` para BUSCAR dados.
 */

import { usePathname } from 'next/navigation';
import { useEffect } from 'react';

import { recordAutorNavegouFora } from '@/lib/api/usage-events';
import { usePendingCreations } from '@/stores/pending-creations';

export function NavigationOutcomeTracker() {
  const pathname = usePathname();
  const pending = usePendingCreations((s) => s.pending);
  const settle = usePendingCreations((s) => s.settle);

  useEffect(() => {
    for (const creation of Object.values(pending)) {
      if (pathname === creation.originPath) continue;
      if (pathname.endsWith(`/conciliacao/${creation.sessionId}`)) continue;

      const seconds = (Date.now() - creation.createdAtMs) / 1000;
      // `settle` ANTES do await: sem isso, um segundo render com o mesmo
      // `pending` dispararia a chamada de novo antes da primeira resolver.
      settle(creation.sessionId);
      void recordAutorNavegouFora(creation.sessionId, seconds);
    }
  }, [pathname, pending, settle]);

  return null;
}
