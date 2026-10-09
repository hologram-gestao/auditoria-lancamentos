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
import { useCallback, useRef, useState } from 'react';

import { invalidateClientSummary } from '@/hooks/use-client-summary';
import {
  listOmieCategorias,
  postOmieLancamentos,
  type OmieCategoriaListResponse,
  type OmiePostingBatchPayload,
  type OmiePostingLineRequest,
} from '@/lib/api/omie-postings';

export const omiePostingKeys = {
  categorias: (sessionId: string) => ['omie-categorias', sessionId] as const,
  /**
   * 86e3n70qj — linhas lançadas NESTA visita (id da linha → nº do lançamento).
   * Fora dos prefixos `['review', …]` e `['reconciliations', …]` de propósito: a
   * invalidação depois do envio não pode apagar o que acabou de acontecer.
   */
  postedInSession: (sessionId: string) => ['omie-posted', sessionId] as const,
};

/** Linha → nº do lançamento no Omie (`null` quando o servidor não devolveu o número). */
export type PostedInSession = Record<string, number | null>;

/**
 * Linhas com lançamento confirmado no lote — inclui a que já estava lançada
 * (`ja_lancada`), cujo número o servidor devolve junto.
 */
export function collectPosted(payload: OmiePostingBatchPayload): PostedInSession {
  const out: PostedInSession = {};
  payload.lines.forEach((line) => {
    if (line.status === 'lancada' || line.reason === 'ja_lancada') {
      out[line.file_entry_id] = line.omie_lancamento_id ?? null;
    }
  });
  return out;
}

const EMPTY_POSTED: PostedInSession = {};

/**
 * O que foi lançado no Omie nesta visita à sessão, para as DUAS abas que oferecem
 * "Lançar no Omie" (Movimentações e Anomalias) mostrarem "Lançado no Omie · nº X"
 * na linha, não importa de qual delas o lote saiu.
 *
 * ⚠️ Alcance declarado: o contrato da linha não diz "lançada pelo sistema"
 * (depois do envio ela é `conciliado` com `omie_lancamento_id`, igual a uma que o
 * cruzamento achou), então o dado é o RESUMO do lote observado aqui, nunca
 * inferido da listagem. Recarregar a página o esquece; o sinal persistente segue
 * sendo a ação indisponível com o motivo "já está vinculada a um lançamento".
 * É cache de cliente sem `queryFn` de rede: só o envio escreve nele.
 */
export function usePostedInSession(sessionId: string): PostedInSession {
  const query = useQuery<PostedInSession>({
    queryKey: omiePostingKeys.postedInSession(sessionId),
    queryFn: () => EMPTY_POSTED,
    initialData: EMPTY_POSTED,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  return query.data;
}

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
    onSuccess: (payload) => {
      qc.setQueryData<PostedInSession>(omiePostingKeys.postedInSession(sessionId), (prev) => ({
        ...(prev ?? EMPTY_POSTED),
        ...collectPosted(payload),
      }));
      // Linhas lançadas saem de `sem_omie`, a anomalia `missing_in_omie` é
      // resolvida e os contadores mudam — os três vivem em prefixos distintos.
      void qc.invalidateQueries({ queryKey: ['review', sessionId] });
      void qc.invalidateQueries({ queryKey: ['reconciliations', sessionId] });
      // As compras a lançar do resumo do cliente (painel) também mudam.
      invalidateClientSummary(qc);
    },
  });
}

/**
 * "Lançar no Omie" com retorno imediato (86e3n70qj: "cliquei e não sei o que
 * aconteceu"). A gaveta só é útil com as categorias do cliente, e no MISS do
 * cache do servidor elas custam uma ida ao Omie: em vez de abrir uma gaveta com o
 * combobox vazio, o botão clicado fica em "carregando" até elas chegarem, e a
 * gaveta abre já pronta. Falha não prende ninguém: a gaveta abre do mesmo jeito e
 * mostra o próprio estado de erro com "Tentar novamente".
 *
 * Um pedido por vez: clique repetido enquanto carrega não abre duas gavetas.
 */
export function useOpenLancarDrawer<T>(sessionId: string, onReady: (targets: T[]) => void) {
  const qc = useQueryClient();
  const [openingTargets, setOpeningTargets] = useState<T[] | null>(null);
  const inFlight = useRef(false);

  const open = useCallback(
    async (targets: T[]) => {
      if (inFlight.current || targets.length === 0) return;
      inFlight.current = true;
      setOpeningTargets(targets);
      try {
        await qc.ensureQueryData({
          queryKey: omiePostingKeys.categorias(sessionId),
          queryFn: () => listOmieCategorias(sessionId),
          staleTime: CATEGORIAS_STALE_MS,
        });
      } catch {
        // A gaveta tem o estado de erro das categorias, com "Tentar novamente".
      } finally {
        inFlight.current = false;
        setOpeningTargets(null);
        onReady(targets);
      }
    },
    [qc, sessionId, onReady],
  );

  return { open, openingTargets };
}
