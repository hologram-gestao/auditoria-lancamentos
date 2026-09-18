/**
 * Helpers tipados do módulo de organizações — camada multi-BPO (86e36ecnp).
 *
 * Espelha `apps/api/app/modules/organizations/{routes,schemas}.py`, com os
 * tipos vindos do contrato gerado (`lib/contracts`) — nada redeclarado à mão.
 *
 * Convenções:
 *   - Todas as quatro rotas são `ManagePlatformDep` no backend: só
 *     `platform_admin`. Para qualquer outro papel a resposta é 403 — a tela
 *     nem oferece o caminho (`lib/authz`), mas a autoridade é o servidor.
 *   - A listagem devolve `{ data, pagination }` (o `apiGet` só desempacota
 *     envelope de chave única), com `pageSize` no alias camelCase.
 *   - 409 `CONFLICT` em POST/PATCH = já existe organização com o nome (sem
 *     distinção de caixa).
 *   - `active: false` SUSPENDE a organização: o staff dela cai no request
 *     seguinte e a plataforma deixa de criar cliente nela. Reativar desfaz.
 */
import type {
  ListOrganizationsQuery,
  OrganizationCreate,
  OrganizationItem,
  OrganizationListResponse,
  OrganizationUpdate,
} from '@/lib/contracts';

import { apiGet, apiPatch, apiPost } from './client';

export type { OrganizationItem, OrganizationListResponse };
export type CreateOrganizationPayload = OrganizationCreate;
export type UpdateOrganizationPayload = OrganizationUpdate;

/** Os parâmetros REAIS da rota, vindos do contrato — não redigitados. */
export type ListOrganizationsParams = ListOrganizationsQuery;

function buildQuery(params: ListOrganizationsParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  const search = params.search?.trim();
  if (search) sp.set('search', search);
  return sp.toString();
}

export async function listOrganizations(
  params: ListOrganizationsParams = {},
): Promise<OrganizationListResponse> {
  return apiGet<OrganizationListResponse>(`/api/v1/organizations?${buildQuery(params)}`);
}

export async function createOrganization(
  payload: CreateOrganizationPayload,
): Promise<OrganizationItem> {
  return apiPost<OrganizationItem>('/api/v1/organizations', payload);
}

export async function updateOrganization(
  id: string,
  payload: UpdateOrganizationPayload,
): Promise<OrganizationItem> {
  return apiPatch<OrganizationItem>(`/api/v1/organizations/${id}`, payload);
}
