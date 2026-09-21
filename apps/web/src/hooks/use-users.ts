/**
 * Hooks de TanStack Query para o módulo users.
 *
 * Convenções:
 *   - Query key: `['users', 'list', { page, pageSize, search }]`.
 *   - Mutations invalidam `['users']` para forçar refetch da lista atual.
 *   - `placeholderData: keepPreviousData` evita flash da tabela em paginação/busca.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { clientsKeys } from '@/hooks/use-clients';
import { organizationsKeys } from '@/hooks/use-organizations';
import {
  activateUser,
  createUser,
  deactivateUser,
  listUsers,
  transferUser,
  updateUser,
  type CreateUserPayload,
  type ListUsersParams,
  type TransferUserPayload,
  type UpdateUserPayload,
  type User,
  type UserListResponse,
} from '@/lib/api/users';

export const usersKeys = {
  all: ['users'] as const,
  list: (params: ListUsersParams) => ['users', 'list', params] as const,
};

export function useUsersList(params: ListUsersParams, options: { enabled?: boolean } = {}) {
  return useQuery<UserListResponse>({
    queryKey: usersKeys.list(params),
    queryFn: () => listUsers(params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation<User, Error, CreateUserPayload>({
    mutationFn: createUser,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersKeys.all });
    },
  });
}

export function useUpdateUser(id: string) {
  const qc = useQueryClient();
  return useMutation<User, Error, UpdateUserPayload>({
    mutationFn: (payload) => updateUser(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersKeys.all });
    },
  });
}

export function useActivateUser() {
  const qc = useQueryClient();
  return useMutation<User, Error, string>({
    mutationFn: activateUser,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersKeys.all });
    },
  });
}

export function useDeactivateUser() {
  const qc = useQueryClient();
  return useMutation<User, Error, string>({
    mutationFn: deactivateUser,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersKeys.all });
    },
  });
}

/**
 * Transferência (86e3bvbfx) invalida TRÊS raízes de propósito: a lista de
 * usuários (a organização da linha mudou), a de organizações (o `users_count`
 * das duas mudou) e a de clientes (a carteira de colaborador saiu e a coluna
 * "gerente responsável" pode ter mudado de leitura para a plataforma).
 */
export function useTransferUser(id: string) {
  const qc = useQueryClient();
  return useMutation<User, Error, TransferUserPayload>({
    mutationFn: (payload) => transferUser(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersKeys.all });
      void qc.invalidateQueries({ queryKey: organizationsKeys.all });
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}
