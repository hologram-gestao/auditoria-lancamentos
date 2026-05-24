/**
 * Hooks de TanStack Query para o CRUD admin de anomaly types — S15 FRONT 11.2.
 *
 * Convenções:
 *   - Query key: `['anomaly-types', 'list', { page, pageSize, includeInactive }]`.
 *   - Mutations invalidam `['anomaly-types']`, forçando refetch da lista admin
 *     E também o cache não-paginado consumido pela tela de revisão
 *     (`reconciliations.ts#listAnomalyTypes` cacheado por outras keys, então
 *     também precisamos invalidar essas — ver helper abaixo).
 *   - `placeholderData: keepPreviousData` evita flash em paginação/filter.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createAnomalyType,
  deleteAnomalyType,
  listAnomalyTypes,
  updateAnomalyType,
  type AnomalyType,
  type AnomalyTypeListResponse,
  type CreateAnomalyTypePayload,
  type ListAnomalyTypesParams,
  type UpdateAnomalyTypePayload,
} from '@/lib/api/anomaly-types';

export const anomalyTypesKeys = {
  all: ['anomaly-types'] as const,
  list: (params: ListAnomalyTypesParams) => ['anomaly-types', 'list', params] as const,
};

export function useAnomalyTypesList(
  params: ListAnomalyTypesParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<AnomalyTypeListResponse>({
    queryKey: anomalyTypesKeys.list(params),
    queryFn: () => listAnomalyTypes(params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

/**
 * Invalida AMBOS os caches: `['anomaly-types']` (admin UI) e a versão
 * não-paginada consumida pela tela de revisão. A tela de revisão usa
 * `['reconciliation', sessionId, 'anomaly-types']` (ver
 * `reconciliations.ts#listAnomalyTypes`); buscamos qualquer query cuja key
 * termine em `'anomaly-types'` para pegar as duas.
 */
function useInvalidateAnomalyTypes() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: anomalyTypesKeys.all });
    void qc.invalidateQueries({
      predicate: (query) => {
        const last = query.queryKey[query.queryKey.length - 1];
        return last === 'anomaly-types';
      },
    });
  };
}

export function useCreateAnomalyType() {
  const invalidate = useInvalidateAnomalyTypes();
  return useMutation<AnomalyType, Error, CreateAnomalyTypePayload>({
    mutationFn: createAnomalyType,
    onSuccess: invalidate,
  });
}

export function useUpdateAnomalyType(id: string) {
  const invalidate = useInvalidateAnomalyTypes();
  return useMutation<AnomalyType, Error, UpdateAnomalyTypePayload>({
    mutationFn: (payload) => updateAnomalyType(id, payload),
    onSuccess: invalidate,
  });
}

export function useDeleteAnomalyType() {
  const invalidate = useInvalidateAnomalyTypes();
  return useMutation<void, Error, string>({
    mutationFn: deleteAnomalyType,
    onSuccess: invalidate,
  });
}
