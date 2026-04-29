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

export function useReconciliationsList(id: string, params: ReconciliationsListParams) {
  return useQuery<ReconciliationsListResponse>({
    queryKey: clientsKeys.reconciliations(id, params),
    queryFn: () => listReconciliations(id, params),
    enabled: id.length > 0,
    placeholderData: keepPreviousData,
  });
}
