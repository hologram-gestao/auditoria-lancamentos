'use client';

/**
 * Conciliações que ESTA aba criou e que ainda não terminaram.
 *
 * Existe por um motivo só: emitir `autor_navegou_fora` — o evento que prova o
 * outcome da Sprint 4 ("o operador criou e foi fazer outra coisa em vez de
 * ficar olhando a tela processar"). Para saber que houve "navegou fora antes do
 * término" é preciso lembrar de duas coisas que só a aba sabe: **quem** criou e
 * **quando**.
 *
 * Deliberadamente em memória (sem `persist`): o evento descreve o comportamento
 * DESTA sessão de trabalho. Recarregar a página e voltar depois não é "navegar
 * fora sem esperar" — é outra história, e inventá-la sujaria a métrica.
 *
 * Ciclo de vida de uma entrada:
 *   `track()`  ao criar a conciliação (a gaveta registra);
 *   `settle()` quando (a) o evento foi emitido, ou (b) a lista viu a sessão sair
 *              de `processing` com a pessoa ainda na tela — nesse caso ela
 *              ESPEROU, e emitir seria falso.
 */
import { create } from 'zustand';

export interface PendingCreation {
  sessionId: string;
  clientId: string;
  /** `Date.now()` do momento da criação — base do `segundos_apos_criar`. */
  createdAtMs: number;
  /** Rota onde a criação aconteceu; ficar nela não conta como "navegou fora". */
  originPath: string;
}

interface PendingCreationsState {
  pending: Record<string, PendingCreation>;
  track: (creation: PendingCreation) => void;
  settle: (sessionId: string) => void;
}

export const usePendingCreations = create<PendingCreationsState>((set) => ({
  pending: {},
  track: (creation) =>
    set((state) => ({ pending: { ...state.pending, [creation.sessionId]: creation } })),
  settle: (sessionId) =>
    set((state) => {
      if (state.pending[sessionId] === undefined) return state;
      const next = { ...state.pending };
      delete next[sessionId];
      return { pending: next };
    }),
}));
