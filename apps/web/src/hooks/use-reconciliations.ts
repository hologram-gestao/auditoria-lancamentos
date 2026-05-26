/**
 * Hooks de TanStack Query para o módulo reconciliations.
 *
 * S8 (FRONT 6.1): `useCheckDuplicate`.
 * S9 (FRONT 7.2): `useParseStatement`.
 * S10 (FRONT 8.7): `useCreateReconciliation` + `useSessionStatus`.
 *
 * Por que `useMutation` para o primeiro grupo:
 *   Tanto a checagem de duplicata quanto o parse e a criação da sessão são
 *   on-demand (acionados no submit do form), dependem de input que só existe
 *   após interação do usuário e não devem ficar em cache — se o usuário
 *   trocar de arquivo, queremos recalcular sem prefetch acidental do TanStack.
 *
 * Por que `useQuery` para o status:
 *   Polling. `refetchInterval` aceita uma função que recebe a `Query` atual
 *   e devolve o intervalo (ms) ou `false` para parar. Devolver `false`
 *   quando `status !== 'processing'` evita continuar o polling depois que
 *   a sessão entra em `reviewing`/`done`/`error`.
 */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';

import type { BlobResponse } from '@/lib/api/client';
import {
  checkDuplicate,
  createAnomaly,
  createReconciliation,
  exportReconciliation,
  getOmieLancamentos,
  getSessionDetail,
  getSessionStatus,
  listAnomalies,
  listAnomalyTypes,
  listAvailableOmieEntries,
  listFileEntries,
  listOmieEntries,
  discardReconciliation,
  parseStatement,
  patchAnomaly,
  patchFileEntry,
  patchOmieEntry,
  reprocessReconciliation,
  type AnomalyItem,
  type AnomalyListResult,
  type AnomalyTypeItem,
  type AvailableOmieEntry,
  type CheckDuplicateParams,
  type CheckDuplicateResult,
  type CreateAnomalyPayload,
  type CreateReconciliationPayload,
  type CreateReconciliationResult,
  type FileEntryItem,
  type FileEntryListResult,
  type ListAnomaliesParams,
  type ListFileEntriesParams,
  type ListOmieEntriesParams,
  type OmieEntryItem,
  type OmieEntryListResult,
  type OmieLancamentoItem,
  type ParsedStatement,
  type ParseStatementParams,
  type PatchAnomalyPayload,
  type PatchFileEntryPayload,
  type PatchOmieEntryPayload,
  type SessionDetail,
  type SessionStatusResult,
} from '@/lib/api/reconciliations';

export function useCheckDuplicate() {
  return useMutation<CheckDuplicateResult, Error, CheckDuplicateParams>({
    mutationFn: checkDuplicate,
  });
}

export function useParseStatement() {
  return useMutation<ParsedStatement, Error, ParseStatementParams>({
    mutationFn: parseStatement,
  });
}

export function useCreateReconciliation() {
  return useMutation<CreateReconciliationResult, Error, CreateReconciliationPayload>({
    mutationFn: createReconciliation,
  });
}

/**
 * "Tentar novamente" de uma sessão em `status='error'`.
 *
 * Após sucesso, invalida o detail e o status da sessão (e a lista de
 * conciliações do cliente) pra refletir `status='processing'` na UI sem
 * refresh manual. O caller pode usar o `onSuccess` extra pra redirecionar
 * pra tela de processing.
 */
export function useReprocessReconciliation(sessionId: string, clientId?: string) {
  const queryClient = useQueryClient();
  return useMutation<CreateReconciliationResult, Error, void>({
    mutationFn: () => reprocessReconciliation(sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reconciliations', sessionId] });
      if (clientId !== undefined) {
        void queryClient.invalidateQueries({
          queryKey: ['clients', clientId, 'reconciliations'],
        });
      }
    },
  });
}

/**
 * "Descartar" uma sessão em `status='error'` (soft-delete).
 *
 * Após sucesso, invalida a lista de conciliações do cliente (a sessão
 * descartada some) e também invalida o detail da sessão — se o caller
 * estiver na tela de revisão de uma sessão descartada, vai detectar
 * 404 e levar pra fora.
 */
export function useDiscardReconciliation(sessionId: string, clientId: string) {
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: () => discardReconciliation(sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['clients', clientId, 'reconciliations'],
      });
      void queryClient.invalidateQueries({ queryKey: ['reconciliations', sessionId] });
      // O contador de conciliações na lista de clientes pode mudar.
      void queryClient.invalidateQueries({ queryKey: ['clients'] });
    },
  });
}

/**
 * Geração do relatório Excel (S14 BACK 10.1).
 *
 * Por que `useMutation` e não `useQuery`:
 *   Export é uma ação manual (clique do usuário) que precisa rodar SEM
 *   cache — duas sessões diferentes devem gerar arquivos diferentes
 *   sem disputar a mesma key. Mutation também entrega `isPending` pro
 *   botão e `onError`/`onSuccess` para o toast.
 *
 * Caller fica responsável pelo download (ex: criar `<a>` com
 * `URL.createObjectURL(blob)`) — o hook devolve `{ blob, filename }`.
 */
export function useExportReconciliation(sessionId: string) {
  return useMutation<BlobResponse, Error, void>({
    mutationFn: () => exportReconciliation(sessionId),
  });
}

/** Polling cadence — Doc §13.1 manda 3s; também citado em CLAUDE.md S10. */
const STATUS_POLL_INTERVAL_MS = 3000;

interface UseSessionStatusOptions {
  /**
   * Quando `false`, o query é desabilitado (útil pra parar o polling após
   * timeout do front — pitfall §6 do briefing). Default: `true`.
   */
  enabled?: boolean;
}

/**
 * Polling de status da sessão. Intervalo dinâmico:
 *   - 3s enquanto `status === 'processing'`.
 *   - `false` (para o polling) assim que vira `reviewing`, `done` ou `error`.
 *
 * Em erro de rede no `/status`, o TanStack mantém `data` anterior (não vira
 * `undefined`) e o consumidor renderiza os steps no estado prévio até o
 * próximo poll bem-sucedido — pitfall §contrato do briefing.
 */
/**
 * Detalhe estático da sessão para o header da Tela de Revisão — `reference_month`,
 * `omie_conta_id`, `total_file_entries`, contadores. Substitui o scan O(N)
 * que fazia `useReconciliationsList(clientId, {pageSize:100}) + .find()` e
 * quebrava em clientes com > 100 sessões.
 *
 * Cache padrão (sem polling): os contadores vivos vêm do `useSessionStatus`,
 * que invalida o status key em mutations. Aqui só queremos o "shape" da
 * sessão (mês, conta, total) que muda raramente.
 */
export function useSessionDetail(sessionId: string) {
  return useQuery<SessionDetail>({
    queryKey: ['reconciliations', sessionId, 'detail'],
    queryFn: () => getSessionDetail(sessionId),
    enabled: sessionId.length > 0,
    refetchOnWindowFocus: false,
  });
}

export function useSessionStatus(sessionId: string, options: UseSessionStatusOptions = {}) {
  const enabled = options.enabled ?? true;

  const queryOptions: UseQueryOptions<SessionStatusResult, Error, SessionStatusResult, string[]> = {
    queryKey: ['reconciliations', sessionId, 'status'],
    queryFn: () => getSessionStatus(sessionId),
    enabled,
    // `refetchInterval` recebe a Query inteira; a tipagem do callback é estreita
    // em relação ao `data` (`undefined` no primeiro fetch). Função pura: sem
    // efeitos colaterais — só decide intervalo a partir do último status visto.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === undefined) {
        // Primeiro fetch ainda não voltou — agenda o próximo na cadência normal.
        return STATUS_POLL_INTERVAL_MS;
      }
      return status === 'processing' ? STATUS_POLL_INTERVAL_MS : false;
    },
    // Não precisamos de cache pós-tela: a sessão muda de UI assim que sai do
    // processing, e o status final fica visível pelo `data` em memória do
    // próprio componente. `gcTime` curto evita guardar lixo.
    gcTime: 30_000,
    // Polling no fundo já basta — refetch on focus duplica trabalho.
    refetchOnWindowFocus: false,
    // O contrato do back é "lenient out": mesmo que o status mude, é só
    // avançar; não precisa retry agressivo de TanStack em cima.
    retry: 1,
  };

  return useQuery(queryOptions);
}

// ----------------------------------------------------------------------
// S11 — Tela de Revisão
// ----------------------------------------------------------------------

/**
 * Query keys da revisão (todas escopadas por `sessionId` pra invalidações
 * cirúrgicas). Convenção:
 *   - `['review', sessionId, 'file-entries', params]` — listagem paginada.
 *   - `['review', sessionId, 'omie-entries', params]`.
 *   - `['review', sessionId, 'anomalies', params]`.
 *   - `['review', sessionId, 'available-omie', search]` — modal Trocar.
 *   - `['review', sessionId, 'omie-lancamentos', sortedIds]` — lookup batched.
 *   - `['anomaly-types']` — catálogo global, raramente muda.
 *
 * Invalidações:
 *   - PATCH file-entry: invalida file-entries + status (contadores).
 *   - PATCH omie-entry: invalida omie-entries (status não muda — back §9.6).
 *   - POST/PATCH anomaly: invalida anomalies + status.
 */
export const reviewKeys = {
  all: (sessionId: string) => ['review', sessionId] as const,
  fileEntries: (sessionId: string, params: Omit<ListFileEntriesParams, 'sessionId'>) =>
    ['review', sessionId, 'file-entries', params] as const,
  omieEntries: (sessionId: string, params: Omit<ListOmieEntriesParams, 'sessionId'>) =>
    ['review', sessionId, 'omie-entries', params] as const,
  anomalies: (sessionId: string, params: Omit<ListAnomaliesParams, 'sessionId'>) =>
    ['review', sessionId, 'anomalies', params] as const,
  availableOmie: (sessionId: string, search: string) =>
    ['review', sessionId, 'available-omie', search] as const,
  omieLancamentos: (sessionId: string, ids: number[]) =>
    ['review', sessionId, 'omie-lancamentos', ids] as const,
  anomalyTypes: () => ['anomaly-types'] as const,
};

const statusKey = (sessionId: string) => ['reconciliations', sessionId, 'status'] as const;

// ---- File entries ----

export function useFileEntries(
  sessionId: string,
  params: Omit<ListFileEntriesParams, 'sessionId'>,
) {
  return useQuery<FileEntryListResult>({
    queryKey: reviewKeys.fileEntries(sessionId, params),
    queryFn: () => listFileEntries({ sessionId, ...params }),
    enabled: sessionId.length > 0,
    placeholderData: keepPreviousData,
  });
}

interface PatchFileEntryVars {
  entryId: string;
  payload: PatchFileEntryPayload;
}

export function usePatchFileEntry(sessionId: string) {
  const qc = useQueryClient();
  return useMutation<FileEntryItem, Error, PatchFileEntryVars>({
    mutationFn: ({ entryId, payload }) => patchFileEntry(sessionId, entryId, payload),
    onSuccess: () => {
      // Invalida toda listagem paginada/filtrada (params variam por aba) e
      // os contadores do header — back recalcula em situations changes.
      void qc.invalidateQueries({
        queryKey: ['review', sessionId, 'file-entries'],
      });
      void qc.invalidateQueries({ queryKey: statusKey(sessionId) });
    },
  });
}

// ---- Omie entries (divergências) ----

export function useOmieEntries(
  sessionId: string,
  params: Omit<ListOmieEntriesParams, 'sessionId'>,
) {
  return useQuery<OmieEntryListResult>({
    queryKey: reviewKeys.omieEntries(sessionId, params),
    queryFn: () => listOmieEntries({ sessionId, ...params }),
    enabled: sessionId.length > 0,
    placeholderData: keepPreviousData,
  });
}

interface PatchOmieEntryVars {
  entryId: string;
  payload: PatchOmieEntryPayload;
}

export function usePatchOmieEntry(sessionId: string) {
  const qc = useQueryClient();
  return useMutation<OmieEntryItem, Error, PatchOmieEntryVars>({
    mutationFn: ({ entryId, payload }) => patchOmieEntry(sessionId, entryId, payload),
    onSuccess: () => {
      // omie_sem_arquivo_count é estático (back §9.6) — só invalida a aba.
      void qc.invalidateQueries({
        queryKey: ['review', sessionId, 'omie-entries'],
      });
    },
  });
}

// ---- Anomalies ----

export function useAnomalies(sessionId: string, params: Omit<ListAnomaliesParams, 'sessionId'>) {
  return useQuery<AnomalyListResult>({
    queryKey: reviewKeys.anomalies(sessionId, params),
    queryFn: () => listAnomalies({ sessionId, ...params }),
    enabled: sessionId.length > 0,
    placeholderData: keepPreviousData,
  });
}

/**
 * Carrega TODAS as anomalias da sessão paginando internamente (S19 FRONT 12.2).
 *
 * A aba Movimentações precisa de um lookup completo por `file_entry_id`
 * pra renderizar o indicador de qualificação em cada linha — o endpoint
 * limita `pageSize` a 50, então buscamos a primeira página e, se houver
 * `totalPages > 1`, requisitamos o restante em paralelo.
 *
 * Key compartilhada o prefixo `['review', sessionId, 'anomalies', ...]` com
 * `useAnomalies` para que `usePatchAnomaly` / `useCreateAnomaly` invalidem
 * ambos via prefix-match do TanStack.
 */
const ALL_ANOMALIES_PAGE_SIZE = 50;

export function useAllSessionAnomalies(sessionId: string) {
  return useQuery<AnomalyItem[]>({
    queryKey: ['review', sessionId, 'anomalies', 'all'],
    queryFn: async () => {
      const first = await listAnomalies({
        sessionId,
        page: 1,
        pageSize: ALL_ANOMALIES_PAGE_SIZE,
        resolved: 'all',
        severity: 'all',
      });
      const totalPages = first.pagination.totalPages;
      if (totalPages <= 1) return first.data;
      const remaining = await Promise.all(
        Array.from({ length: totalPages - 1 }, (_, i) =>
          listAnomalies({
            sessionId,
            page: i + 2,
            pageSize: ALL_ANOMALIES_PAGE_SIZE,
            resolved: 'all',
            severity: 'all',
          }),
        ),
      );
      return [first.data, ...remaining.map((r) => r.data)].flat();
    },
    enabled: sessionId.length > 0,
    placeholderData: keepPreviousData,
  });
}

export function useCreateAnomaly(sessionId: string) {
  const qc = useQueryClient();
  return useMutation<AnomalyItem, Error, CreateAnomalyPayload>({
    mutationFn: (payload) => createAnomaly(sessionId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['review', sessionId, 'anomalies'] });
      void qc.invalidateQueries({ queryKey: statusKey(sessionId) });
    },
  });
}

interface PatchAnomalyVars {
  anomalyId: string;
  payload: PatchAnomalyPayload;
}

export function usePatchAnomaly(sessionId: string) {
  const qc = useQueryClient();
  return useMutation<AnomalyItem, Error, PatchAnomalyVars>({
    mutationFn: ({ anomalyId, payload }) => patchAnomaly(sessionId, anomalyId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['review', sessionId, 'anomalies'] });
      void qc.invalidateQueries({ queryKey: statusKey(sessionId) });
    },
  });
}

// ---- Modal Trocar: lista de candidatos Omie ----

interface UseAvailableOmieEntriesOptions {
  /** Quando false, não dispara o query — útil enquanto o modal está fechado. */
  enabled?: boolean;
}

export function useAvailableOmieEntries(
  sessionId: string,
  search: string,
  options: UseAvailableOmieEntriesOptions = {},
) {
  return useQuery<AvailableOmieEntry[]>({
    queryKey: reviewKeys.availableOmie(sessionId, search),
    queryFn: () => listAvailableOmieEntries(sessionId, search),
    enabled: sessionId.length > 0 && (options.enabled ?? true),
    placeholderData: keepPreviousData,
  });
}

// ---- Lookup batched de supplier/category nas linhas conciliadas ----

/**
 * Ordena os IDs antes de virar query key — sem isso, [1,2] e [2,1] viram
 * fetches separados. Cache do TanStack indexa por key serializada, então
 * a estabilidade vem da ordenação.
 */
export function useOmieLancamentos(sessionId: string, omieIds: number[]) {
  const sortedIds = [...omieIds].sort((a, b) => a - b);
  return useQuery<OmieLancamentoItem[]>({
    queryKey: reviewKeys.omieLancamentos(sessionId, sortedIds),
    queryFn: () => getOmieLancamentos(sessionId, sortedIds),
    enabled: sessionId.length > 0 && sortedIds.length > 0,
    // Os dados Omie têm cache L2 de 2h no back; no front, 5 min é suficiente
    // pra evitar tempestade de requests durante navegação entre páginas.
    staleTime: 5 * 60 * 1000,
  });
}

// ---- Catálogo de tipos de anomalia ----

export function useAnomalyTypes() {
  return useQuery<AnomalyTypeItem[]>({
    queryKey: reviewKeys.anomalyTypes(),
    queryFn: listAnomalyTypes,
    // Catálogo praticamente estático — Infinity até refresh manual da página.
    staleTime: Infinity,
  });
}
