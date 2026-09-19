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
  listPlatformAdmins,
  updateOrganization,
  type CreateOrganizationPayload,
  type ListOrganizationsParams,
  type OrganizationItem,
  type OrganizationListResponse,
  type PlatformAdminItem,
  type UpdateOrganizationPayload,
} from '@/lib/api/organizations';

export const organizationsKeys = {
  all: ['organizations'] as const,
  list: (params: ListOrganizationsParams) => ['organizations', 'list', params] as const,
};

/**
 * Raiz PRÓPRIA, fora de `['organizations']` de propósito: criar, renomear ou
 * suspender uma organização não muda quem é plataforma (quem muda isso é o
 * script de promoção, fora do app). Pendurar esta chave na raiz das
 * organizações faria toda mutação disparar um GET que não pode ter mudado.
 */
export const platformAdminsKeys = {
  all: ['platform-admins'] as const,
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

export function usePlatformAdminsList(options: { enabled?: boolean } = {}) {
  return useQuery<PlatformAdminItem[]>({
    queryKey: platformAdminsKeys.all,
    queryFn: listPlatformAdmins,
    enabled: options.enabled ?? true,
  });
}
