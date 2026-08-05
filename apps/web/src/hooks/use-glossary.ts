/**
 * Hooks de TanStack Query do glossário do tenant (BACK 06.3 / R2).
 *
 * Convenções (as mesmas de `use-client-users`):
 *   - Query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir o glossário do tenant anterior do cache. Aqui isso não é só
 *     higiene: o glossário É o vocabulário contábil do cliente, e mostrá-lo sob
 *     outro tenant seria vazamento com cara de bug de cache.
 *   - Mutations invalidam só a árvore daquele cliente. Toda escrita (inclusive
 *     a remoção) incrementa `version` no servidor, então a listagem precisa ser
 *     refeita para a tela não exibir uma versão que não existe mais.
 *   - `keepPreviousData` evita o flash da tabela ao paginar.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createGlossaryEntry,
  deleteGlossaryEntry,
  listGlossaryEntries,
  updateGlossaryEntry,
  type ListGlossaryParams,
} from '@/lib/api/glossary';
import type {
  CreateGlossaryEntryRequest,
  GlossaryDeletedPayload,
  GlossaryEntry,
  GlossaryListResponse,
  UpdateGlossaryEntryRequest,
} from '@/lib/contracts';

export const glossaryKeys = {
  all: (clientId: string) => ['glossary', clientId] as const,
  list: (clientId: string, params: ListGlossaryParams) =>
    ['glossary', clientId, 'list', params] as const,
};

export function useGlossaryList(
  clientId: string,
  params: ListGlossaryParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<GlossaryListResponse>({
    queryKey: glossaryKeys.list(clientId, params),
    queryFn: () => listGlossaryEntries(clientId, params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

export function useCreateGlossaryEntry(clientId: string) {
  const qc = useQueryClient();
  return useMutation<GlossaryEntry, Error, CreateGlossaryEntryRequest>({
    mutationFn: (payload) => createGlossaryEntry(clientId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: glossaryKeys.all(clientId) });
    },
  });
}

export function useUpdateGlossaryEntry(clientId: string, entryId: string) {
  const qc = useQueryClient();
  return useMutation<GlossaryEntry, Error, UpdateGlossaryEntryRequest>({
    mutationFn: (payload) => updateGlossaryEntry(clientId, entryId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: glossaryKeys.all(clientId) });
    },
  });
}

export function useDeleteGlossaryEntry(clientId: string) {
  const qc = useQueryClient();
  return useMutation<GlossaryDeletedPayload, Error, { entryId: string }>({
    mutationFn: ({ entryId }) => deleteGlossaryEntry(clientId, entryId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: glossaryKeys.all(clientId) });
    },
  });
}
