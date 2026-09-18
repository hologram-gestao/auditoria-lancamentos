/**
 * Hooks de TanStack Query do módulo de organizações (86e36ecwa).
 *
 * Toda mutação invalida a raiz `['organizations']` E `['clients']`: o nome da
 * organização aparece na coluna "Organização" da lista de clientes, então
 * renomear precisa refletir lá também — e suspender uma organização muda o que
 * a plataforma pode criar nela.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { clientsKeys } from '@/hooks/use-clients';
import {
  createOrganization,
  listOrganizations,
  updateOrganization,
  type CreateOrganizationPayload,
  type ListOrganizationsParams,
  type OrganizationItem,
  type OrganizationListResponse,
  type UpdateOrganizationPayload,
} from '@/lib/api/organizations';

export const organizationsKeys = {
  all: ['organizations'] as const,
  list: (params: ListOrganizationsParams) => ['organizations', 'list', params] as const,
};

export function useOrganizationsList(
  params: ListOrganizationsParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<OrganizationListResponse>({
    queryKey: organizationsKeys.list(params),
    queryFn: () => listOrganizations(params),
    // Sem flash da tabela ao paginar/buscar (mesmo padrão da lista de usuários).
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

function useInvalidateOrganizations() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: organizationsKeys.all });
    void qc.invalidateQueries({ queryKey: clientsKeys.all });
  };
}

export function useCreateOrganization() {
  const invalidate = useInvalidateOrganizations();
  return useMutation<OrganizationItem, Error, CreateOrganizationPayload>({
    mutationFn: createOrganization,
    onSuccess: invalidate,
  });
}

export function useUpdateOrganization(id: string) {
  const invalidate = useInvalidateOrganizations();
  return useMutation<OrganizationItem, Error, UpdateOrganizationPayload>({
    mutationFn: (payload) => updateOrganization(id, payload),
    onSuccess: invalidate,
  });
}
