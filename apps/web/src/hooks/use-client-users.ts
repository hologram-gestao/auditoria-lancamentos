/**
 * Hooks de TanStack Query dos usuários DO CLIENTE (BACK 05.5 / R5).
 *
 * Convenções (as mesmas de `use-users`):
 *   - Query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir a lista do tenant anterior do cache.
 *   - Mutations invalidam só a árvore daquele cliente.
 *   - `keepPreviousData` evita o flash da tabela ao paginar/buscar.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  activateClientUser,
  createClientUser,
  deactivateClientUser,
  listClientUsers,
  updateClientUser,
  type ListClientUsersParams,
} from '@/lib/api/client-users';
import type {
  ClientUserListResponse,
  ClientUserResponse,
  CreateClientUserRequest,
  UpdateClientUserRequest,
} from '@/lib/contracts';

export const clientUsersKeys = {
  all: (clientId: string) => ['client-users', clientId] as const,
  list: (clientId: string, params: ListClientUsersParams) =>
    ['client-users', clientId, 'list', params] as const,
};

export function useClientUsersList(
  clientId: string,
  params: ListClientUsersParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<ClientUserListResponse>({
    queryKey: clientUsersKeys.list(clientId, params),
    queryFn: () => listClientUsers(clientId, params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

export function useCreateClientUser(clientId: string) {
  const qc = useQueryClient();
  return useMutation<ClientUserResponse, Error, CreateClientUserRequest>({
    mutationFn: (payload) => createClientUser(clientId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientUsersKeys.all(clientId) });
    },
  });
}

export function useUpdateClientUser(clientId: string, userId: string) {
  const qc = useQueryClient();
  return useMutation<ClientUserResponse, Error, UpdateClientUserRequest>({
    mutationFn: (payload) => updateClientUser(clientId, userId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientUsersKeys.all(clientId) });
    },
  });
}

/**
 * Ativar/desativar num hook só: são a mesma ação com sinal trocado, e o
 * componente de confirmação já é compartilhado.
 */
export function useSetClientUserActive(clientId: string) {
  const qc = useQueryClient();
  return useMutation<ClientUserResponse, Error, { userId: string; active: boolean }>({
    mutationFn: ({ userId, active }) =>
      active ? activateClientUser(clientId, userId) : deactivateClientUser(clientId, userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientUsersKeys.all(clientId) });
    },
  });
}
