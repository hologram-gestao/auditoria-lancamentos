/**
 * Hooks do lançamento no Omie (Sprint 7 / FRONT 07.6 · 07.7).
 *
 * Peças:
 *   - `useOmieCategorias` — lista COMPLETA das categorias do cliente da sessão,
 *     usada pelo combobox de classificação. `staleTime` alto de propósito: o
 *     backend já cacheia por 6 h por cliente, e o combobox filtra localmente;
 *     refetch por foco de janela só geraria ida ao Omie sem mudar a lista.
 *   - `usePostOmieLancamentos` — o envio do lote. `useMutation` (e não query)
 *     porque é ação do usuário, com `isPending` para o botão async e efeito
 *     externo IRREVERSÍVEL: nunca pode ser disparado por prefetch/refetch.
 *   - 86e3n70q0 (bloco C): `useOmieDepartamentos` (rateio), `useFindOmieFornecedor`
 *     (busca por CNPJ, ação do botão) e `useOmieFornecedorByCode` (nome do
 *     fornecedor padrão do cliente).
 *
 * Invalidação após o envio: o backend muda a linha (no direto, `sem_omie` →
 * `conciliado` com `omie_lancamento_id`; na provisão, a linha segue `sem_omie` e
 * muda o `omie_posting`), resolve a anomalia `missing_in_omie` e recalcula
 * os contadores da sessão. Por isso invalida o prefixo `['review', sessionId]`
 * (movimentações + anomalias) **e** `['reconciliations', sessionId]` (detalhe/
 * status, que alimentam os totalizadores do topo).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import { invalidateClientSummary } from '@/hooks/use-client-summary';
import {
  fetchOmieSupplierSuggestions,
  findOmieFornecedor,
  listOmieCategorias,
  listOmieDepartamentos,
  postOmieLancamentos,
  searchOmieFornecedores,
  type FornecedorLookup,
  type OmieCategoriaListResponse,
  type OmieDepartamentoListResponse,
  type OmieFornecedorItem,
  type OmieFornecedorSearchResponse,
  type OmieFornecedorSuggestion,
  type OmiePostingBatchPayload,
  type OmiePostingLineRequest,
  type OmiePostingLineResult,
  type OmiePostingState,
} from '@/lib/api/omie-postings';

export const omiePostingKeys = {
  categorias: (sessionId: string) => ['omie-categorias', sessionId] as const,
  departamentos: (clientId: string) => ['omie-departamentos', clientId] as const,
  fornecedorByCode: (clientId: string, codigo: number) =>
    ['omie-fornecedor', clientId, codigo] as const,
  /** 86e3qhhrd — busca pelo nome; o prefixo sem o texto apaga todas do cliente. */
  fornecedorSearch: (clientId: string, nome?: string) =>
    nome === undefined
      ? (['omie-fornecedor-busca', clientId] as const)
      : (['omie-fornecedor-busca', clientId, nome] as const),
  /** 86e3qhhrd — sugestões por linha, pelo conjunto de linhas (ordenado). */
  supplierSuggestions: (sessionId: string, entryIds: readonly string[]) =>
    ['omie-fornecedor-sugestoes', sessionId, [...entryIds].sort().join(',')] as const,
  /**
   * 86e3n70qj — linhas concluídas NESTA visita (id da linha → estado). Fora dos
   * prefixos `['review', …]` e `['reconciliations', …]` de propósito: a
   * invalidação depois do envio não pode apagar o que acabou de acontecer.
   */
  postedInSession: (sessionId: string) => ['omie-posted', sessionId] as const,
};

/** Linha → estado da intenção concluída, montado a partir do resumo do lote. */
export type PostedInSession = Record<string, OmiePostingState>;

/**
 * O resultado de uma linha CONCLUÍDA, na forma do estado que o servidor manda na
 * listagem (`omie_posting`). É o que deixa o selo pintar igual antes e depois do
 * refetch.
 */
export function postingStateFromResult(line: OmiePostingLineResult): OmiePostingState {
  return {
    kind: line.kind ?? 'direto',
    status: 'confirmed',
    step: line.step ?? null,
    attempts: 0,
    supplier_code: line.supplier_code ?? null,
    payable_id: line.payable_id ?? null,
    payment_id: line.payment_id ?? null,
    omie_lancamento_id: line.omie_lancamento_id ?? null,
    error_code: null,
    error_message: null,
  };
}

/**
 * A linha está concluída no Omie depois deste lote: lançada agora, ou já estava
 * (`ja_lancada` — direto vinculado, ou provisão com título).
 */
export function isPostingResultConcluded(line: OmiePostingLineResult): boolean {
  return line.status === 'lancada' || line.reason === 'ja_lancada';
}

/** Linhas concluídas no lote, no formato do estado da listagem. */
export function collectPosted(payload: OmiePostingBatchPayload): PostedInSession {
  const out: PostedInSession = {};
  payload.lines.forEach((line) => {
    if (isPostingResultConcluded(line)) out[line.file_entry_id] = postingStateFromResult(line);
  });
  return out;
}

/**
 * O estado que a linha mostra: o do SERVIDOR (verdade do banco), salvo quando o
 * cache desta visita já sabe que ela terminou e o refetch ainda não chegou.
 */
export function pickPostingState(
  server: OmiePostingState | null | undefined,
  cached: OmiePostingState | undefined,
): OmiePostingState | null {
  if (server?.status === 'confirmed') return server;
  return cached ?? server ?? null;
}

const EMPTY_POSTED: PostedInSession = {};

/**
 * O que foi concluído no Omie nesta visita, para as DUAS abas que oferecem
 * "Lançar no Omie" pintarem o selo NO INSTANTE do envio, antes de a listagem
 * voltar do refetch.
 *
 * Desde o bloco C da 86e3n70q0 o selo persistente vem do SERVIDOR (`omie_posting`
 * na linha, lido de `reconciliation_omie_postings`). Este cache é só a ponte entre
 * o envio e o refetch (`pickPostingState`); recarregar a página o esquece sem
 * perder nada. É cache de cliente sem `queryFn` de rede: só o envio escreve nele.
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

/** 30 min, como as categorias: o servidor mantém 6 h; aqui só não refazemos por navegação. */
const DEPARTAMENTOS_STALE_MS = 30 * 60 * 1000;

/**
 * Departamentos ativos do cliente para o rateio (86e3n70q0, bloco C). Só busca com
 * a gaveta aberta: no MISS do cache do servidor custa uma ida ao Omie.
 */
export function useOmieDepartamentos(clientId: string, options: { enabled?: boolean } = {}) {
  return useQuery<OmieDepartamentoListResponse>({
    queryKey: omiePostingKeys.departamentos(clientId),
    queryFn: () => listOmieDepartamentos(clientId),
    enabled: clientId.length > 0 && (options.enabled ?? true),
    staleTime: DEPARTAMENTOS_STALE_MS,
    refetchOnWindowFocus: false,
  });
}

/**
 * Busca do fornecedor por CNPJ: AÇÃO da pessoa (botão "Buscar no Omie"), nunca
 * por tecla. `retry: false`: o 404 "sem cadastro" é resposta, não falha.
 */
export function useFindOmieFornecedor(clientId: string) {
  return useMutation<OmieFornecedorItem, Error, FornecedorLookup>({
    mutationFn: (lookup) => findOmieFornecedor(clientId, lookup),
    retry: false,
  });
}

/** Mínimo de caracteres úteis da busca pelo nome (o mesmo do servidor). */
export const SUPPLIER_SEARCH_MIN_CHARS = 3;

/** O diretório do servidor vale 6 h; a busca igual na mesma visita não volta lá. */
const SUPPLIER_SEARCH_STALE_MS = 5 * 60 * 1000;

/** Letras e dígitos do texto, sem acento: o que o servidor conta como "útil". */
export function supplierSearchUsefulLength(text: string): number {
  return text
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^0-9a-z]/gi, '').length;
}

/**
 * Busca de fornecedor pelo NOME ao digitar (86e3qhhrd). Quem chama passa o texto
 * JÁ com debounce (`useDebouncedValue`); a busca só sai com 3 caracteres úteis.
 * O resultado anterior fica na tela enquanto o novo chega (`keepPreviousData`),
 * para a lista não piscar a cada tecla. `retry: false`: o erro é mostrado com
 * "Tentar de novo", não repetido em silêncio.
 */
export function useSearchOmieFornecedores(clientId: string, nome: string) {
  const term = nome.trim();
  return useQuery<OmieFornecedorSearchResponse>({
    queryKey: omiePostingKeys.fornecedorSearch(clientId, term),
    queryFn: ({ signal }) => searchOmieFornecedores(clientId, term, { signal }),
    enabled: clientId.length > 0 && supplierSearchUsefulLength(term) >= SUPPLIER_SEARCH_MIN_CHARS,
    staleTime: SUPPLIER_SEARCH_STALE_MS,
    placeholderData: keepPreviousData,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/**
 * "Atualizar lista": relê o diretório da Omie para o texto atual e descarta as
 * outras buscas guardadas do cliente (elas leram o diretório antigo).
 */
export function useRefreshOmieFornecedores(clientId: string) {
  const queryClient = useQueryClient();
  return useMutation<OmieFornecedorSearchResponse, Error, string>({
    mutationFn: (nome) => searchOmieFornecedores(clientId, nome.trim(), { refresh: true }),
    retry: false,
    onSuccess: (data, nome) => {
      queryClient.removeQueries({ queryKey: omiePostingKeys.fornecedorSearch(clientId) });
      queryClient.setQueryData(omiePostingKeys.fornecedorSearch(clientId, nome.trim()), data);
    },
  });
}

/**
 * Sugestões de fornecedor das linhas da gaveta, numa ida só (86e3qhhrd). Falha é
 * silenciosa de propósito: sugestão é conveniência, a busca manual continua ali.
 */
export function useOmieSupplierSuggestions(
  sessionId: string,
  entryIds: readonly string[],
  options: { enabled?: boolean } = {},
) {
  return useQuery<OmieFornecedorSuggestion[]>({
    queryKey: omiePostingKeys.supplierSuggestions(sessionId, entryIds),
    queryFn: () => fetchOmieSupplierSuggestions(sessionId, entryIds),
    enabled: sessionId.length > 0 && entryIds.length > 0 && (options.enabled ?? true),
    staleTime: SUPPLIER_SEARCH_STALE_MS,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

/** O fornecedor PADRÃO gravado no cliente, resolvido pelo código para mostrar o nome. */
export function useOmieFornecedorByCode(
  clientId: string,
  codigo: number | null | undefined,
  options: { enabled?: boolean } = {},
) {
  return useQuery<OmieFornecedorItem>({
    queryKey: omiePostingKeys.fornecedorByCode(clientId, codigo ?? 0),
    queryFn: () => findOmieFornecedor(clientId, { codigo: codigo ?? 0 }),
    enabled:
      clientId.length > 0 && codigo !== null && codigo !== undefined && (options.enabled ?? true),
    staleTime: DEPARTAMENTOS_STALE_MS,
    refetchOnWindowFocus: false,
    retry: false,
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
export function useOpenLancarDrawer<T>(
  sessionId: string,
  onReady: (targets: T[]) => void,
  clientId?: string,
) {
  const qc = useQueryClient();
  const [openingTargets, setOpeningTargets] = useState<T[] | null>(null);
  const inFlight = useRef(false);

  const open = useCallback(
    async (targets: T[]) => {
      if (inFlight.current || targets.length === 0) return;
      inFlight.current = true;
      setOpeningTargets(targets);
      try {
        // Categorias e departamentos em paralelo (são métodos diferentes no Omie,
        // sem disputa de rate limit). Falha de qualquer um não prende a gaveta:
        // cada um tem o próprio estado de erro, com "Tentar novamente".
        await Promise.allSettled([
          qc.ensureQueryData({
            queryKey: omiePostingKeys.categorias(sessionId),
            queryFn: () => listOmieCategorias(sessionId),
            staleTime: CATEGORIAS_STALE_MS,
          }),
          ...(clientId
            ? [
                qc.ensureQueryData({
                  queryKey: omiePostingKeys.departamentos(clientId),
                  queryFn: () => listOmieDepartamentos(clientId),
                  staleTime: DEPARTAMENTOS_STALE_MS,
                }),
              ]
            : []),
        ]);
      } finally {
        inFlight.current = false;
        setOpeningTargets(null);
        onReady(targets);
      }
    },
    [qc, sessionId, onReady, clientId],
  );

  return { open, openingTargets };
}
