/**
 * Hooks de TanStack Query para o módulo clients.
 *
 * Convenções:
 *   - Query key: `['clients', 'list', { page, pageSize, search }]`.
 *   - Mutations invalidam `['clients']` (atinge listagem e detalhe S7).
 *   - `placeholderData: keepPreviousData` evita flash da tabela em paginação/busca.
 *   - `useTestConnection` NÃO invalida nada — é só uma checagem de credenciais.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  assignClient,
  createClient,
  listClients,
  testConnection,
  updateClient,
  type AssignClientPayload,
  type Client,
  type ClientListResponse,
  type CreateClientPayload,
  type ListClientsParams,
  type TestConnectionPayload,
  type TestConnectionResult,
  type UpdateClientPayload,
} from '@/lib/api/clients';

export const clientsKeys = {
  all: ['clients'] as const,
  list: (params: ListClientsParams) => ['clients', 'list', params] as const,
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
