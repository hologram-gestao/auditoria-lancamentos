/**
 * Hooks de TanStack Query para o módulo clients (S6 + S7).
 *
 * Convenções:
 *   - Query keys segmentadas: `['clients', 'list', params]`,
 *     `['clients', 'detail', id]`, `['clients', 'reconciliations', id, params]`.
 *   - Mutations invalidam `['clients']` raiz (atinge listagem e detalhe).
 *   - `placeholderData: keepPreviousData` evita flash em paginação/busca.
 *   - `useTestConnection` NÃO invalida nada — é só uma checagem de credenciais.
 *   - `useSyncAccounts` recebe a resposta (ClientDetail atualizado) e atualiza
 *     o cache do detalhe diretamente via `setQueryData`. Isso evita um refetch
 *     adicional após o PATCH (otimização — o back já devolveu o estado novo).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  assignClient,
  createClient,
  getClientDetail,
  listClients,
  listReconciliations,
  syncClientAccounts,
  testConnection,
  updateClient,
  type AssignClientPayload,
  type Client,
  type ClientDetail,
  type ClientListResponse,
  type CreateClientPayload,
  type ListClientsParams,
  type ReconciliationsListParams,
  type ReconciliationsListResponse,
  type TestConnectionPayload,
  type TestConnectionResult,
  type UpdateClientPayload,
} from '@/lib/api/clients';

export const clientsKeys = {
  all: ['clients'] as const,
  list: (params: ListClientsParams) => ['clients', 'list', params] as const,
  detail: (id: string) => ['clients', 'detail', id] as const,
  /** Prefixo de TODAS as páginas/filtros da lista de um cliente — use este
   *  para invalidar (o `queryKey` completo inclui os `params` e nunca casaria
   *  com um `invalidateQueries` de outra combinação de filtros). */
  reconciliationsAll: (id: string) => ['clients', 'reconciliations', id] as const,
  reconciliations: (id: string, params: ReconciliationsListParams) =>
    ['clients', 'reconciliations', id, params] as const,
};

export function useClientsList(params: ListClientsParams) {
  return useQuery<ClientListResponse>({
    queryKey: clientsKeys.list(params),
    queryFn: () => listClients(params),
    placeholderData: keepPreviousData,
  });
}

export function useCreateClient() {
  const qc = useQueryClient();
  return useMutation<Client, Error, CreateClientPayload>({
    mutationFn: createClient,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

export function useTestConnection() {
  return useMutation<TestConnectionResult, Error, TestConnectionPayload>({
    mutationFn: testConnection,
  });
}

export function useUpdateClient(id: string) {
  const qc = useQueryClient();
  return useMutation<Client, Error, UpdateClientPayload>({
    mutationFn: (payload) => updateClient(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

export function useAssignClient(id: string) {
  const qc = useQueryClient();
  return useMutation<Client, Error, AssignClientPayload>({
    mutationFn: (payload) => assignClient(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

interface UseClientDetailOptions {
  enabled?: boolean;
}

export function useClientDetail(id: string, opts: UseClientDetailOptions = {}) {
  return useQuery<ClientDetail>({
    queryKey: clientsKeys.detail(id),
    queryFn: () => getClientDetail(id),
    enabled: id.length > 0 && (opts.enabled ?? true),
  });
}

export function useSyncAccounts(id: string) {
  const qc = useQueryClient();
  return useMutation<ClientDetail, Error, void>({
    mutationFn: () => syncClientAccounts(id),
    onSuccess: (detail) => {
      // Atualiza o cache do detalhe sem refetch — back já devolveu o estado novo.
      qc.setQueryData(clientsKeys.detail(id), detail);
      // Invalida o restante (listagens, contadores, etc).
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

/**
 * Cadência do polling de LISTA (Sprint 4 / R2). Trabalho NOVO: o app já tinha
 * poll de UMA sessão (`useSessionStatus`), nunca de nível-lista.
 *
 * 3 s é a mesma cadência do poll de sessão — a lista é o lugar onde a pessoa
 * espera ver "Em processamento" virar "Processada" sem recarregar.
 */
const LIST_POLL_INTERVAL_MS = 3000;

/**
 * Lista de conciliações do cliente, com **polling enquanto houver alguma linha
 * em `processing`** — e parando quando não houver.
 *
 * É isso que faz a conciliação recém-criada aparecer sozinha e mudar de status
 * na tela, sem o usuário ficar preso numa tela de progresso. Sem nenhuma linha
 * processando o intervalo vira `false`: nada de martelar o backend à toa.
 *
 * `refetchIntervalInBackground: false` — aba fora de foco não gera tráfego.
 */
export function useReconciliationsList(id: string, params: ReconciliationsListParams) {
  return useQuery<ReconciliationsListResponse>({
    queryKey: clientsKeys.reconciliations(id, params),
    queryFn: () => listReconciliations(id, params),
    enabled: id.length > 0,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const rows = query.state.data?.data;
      if (rows === undefined) return false;
      return rows.some((row) => row.status === 'processing') ? LIST_POLL_INTERVAL_MS : false;
    },
    refetchIntervalInBackground: false,
  });
}
