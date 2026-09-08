/**
 * Hooks de TanStack Query do catálogo de categorias de cliente — 86e34jd8m.
 *
 * Toda mutação invalida o catálogo E a raiz `['clients']`: o chip da lista e do
 * detalhe carrega o NOME da categoria, então renomear/excluir precisa refletir
 * lá também, não só na tela de configurações.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { clientsKeys } from '@/hooks/use-clients';
import {
  createClientCategory,
  deleteClientCategory,
  listClientCategories,
  updateClientCategory,
  type ClientCategoryItem,
  type CreateClientCategoryPayload,
  type UpdateClientCategoryPayload,
} from '@/lib/api/client-categories';

export const clientCategoriesKeys = {
  all: ['client-categories'] as const,
  list: ['client-categories', 'list'] as const,
};

export function useClientCategories(options: { enabled?: boolean } = {}) {
  return useQuery<ClientCategoryItem[]>({
    queryKey: clientCategoriesKeys.list,
    queryFn: listClientCategories,
    enabled: options.enabled ?? true,
    // Catálogo muda raramente; o filtro da lista e o select dos modais
    // reaproveitam o mesmo cache sem refetch a cada abertura.
    staleTime: 60_000,
  });
}

function useInvalidateClientCategories() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: clientCategoriesKeys.all });
    void qc.invalidateQueries({ queryKey: clientsKeys.all });
  };
}

export function useCreateClientCategory() {
  const invalidate = useInvalidateClientCategories();
  return useMutation<ClientCategoryItem, Error, CreateClientCategoryPayload>({
    mutationFn: createClientCategory,
    onSuccess: invalidate,
  });
}

export function useUpdateClientCategory(id: string) {
  const invalidate = useInvalidateClientCategories();
  return useMutation<ClientCategoryItem, Error, UpdateClientCategoryPayload>({
    mutationFn: (payload) => updateClientCategory(id, payload),
    onSuccess: invalidate,
  });
}

export function useDeleteClientCategory() {
  const invalidate = useInvalidateClientCategories();
  return useMutation<void, Error, string>({
    mutationFn: deleteClientCategory,
    onSuccess: invalidate,
  });
}
