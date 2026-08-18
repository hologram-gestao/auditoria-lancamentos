/**
 * Hooks do lançamento no Omie (Sprint 7 / FRONT 07.6 · 07.7).
 *
 * Duas peças:
 *   - `useOmieCategorias` — lista COMPLETA das categorias do cliente da sessão,
 *     usada pelo combobox de classificação. `staleTime` alto de propósito: o
 *     backend já cacheia por 6 h por cliente, e o combobox filtra localmente;
 *     refetch por foco de janela só geraria ida ao Omie sem mudar a lista.
 *   - `usePostOmieLancamentos` — o envio do lote. `useMutation` (e não query)
 *     porque é ação do usuário, com `isPending` para o botão async e efeito
 *     externo IRREVERSÍVEL: nunca pode ser disparado por prefetch/refetch.
 *
 * Invalidação após o envio: o backend muda a linha (`sem_omie` → `conciliado`
 * com `omie_lancamento_id`), resolve a anomalia `missing_in_omie` e recalcula
 * os contadores da sessão. Por isso invalida o prefixo `['review', sessionId]`
 * (movimentações + anomalias) **e** `['reconciliations', sessionId]` (detalhe/
 * status, que alimentam os totalizadores do topo).
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  listOmieCategorias,
  postOmieLancamentos,
  type OmieCategoriaListResponse,
  type OmiePostingBatchPayload,
  type OmiePostingLineRequest,
} from '@/lib/api/omie-postings';

export const omiePostingKeys = {
  categorias: (sessionId: string) => ['omie-categorias', sessionId] as const,
};

/** 30 min: o servidor mantém 6 h por cliente; aqui só evitamos refetch por navegação. */
const CATEGORIAS_STALE_MS = 30 * 60 * 1000;

interface UseOmieCategoriasOptions {
  /** Só busca quando a gaveta abre — categoria custa uma ida ao Omie no MISS. */
  enabled?: boolean;
}

export function useOmieCategorias(sessionId: string, options: UseOmieCategoriasOptions = {}) {
  return useQuery<OmieCategoriaListResponse>({
    queryKey: omiePostingKeys.categorias(sessionId),
    queryFn: () => listOmieCategorias(sessionId),
    enabled: sessionId.length > 0 && (options.enabled ?? true),
    staleTime: CATEGORIAS_STALE_MS,
    refetchOnWindowFocus: false,
  });
}

/**
 * Envio do lote (R1 · R5).
 *
 * `retry: false` é decisão de segurança, não de performance: o TanStack não
 * pode reenviar sozinho um POST que grava na contabilidade do cliente. O
 * backend tem dedup por linha, mas a proteção não pode depender dela — quem
 * decide reexecutar é o operador, olhando o resumo.
 */
export function usePostOmieLancamentos(sessionId: string) {
  const qc = useQueryClient();
  return useMutation<OmiePostingBatchPayload, Error, OmiePostingLineRequest[]>({
    mutationFn: (lines) => postOmieLancamentos(sessionId, lines),
    retry: false,
    onSuccess: () => {
      // Linhas lançadas saem de `sem_omie`, a anomalia `missing_in_omie` é
      // resolvida e os contadores mudam — os três vivem em prefixos distintos.
      void qc.invalidateQueries({ queryKey: ['review', sessionId] });
      void qc.invalidateQueries({ queryKey: ['reconciliations', sessionId] });
    },
  });
}
