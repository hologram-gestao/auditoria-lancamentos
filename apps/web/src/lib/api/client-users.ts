/**
 * Helpers tipados de `/api/v1/clients/{client_id}/users` (BACK 05.5 / R5).
 *
 * Todo shape vem do contrato gerado (`lib/contracts`) — nenhuma `interface`
 * espelhando o endpoint à mão. O shape real foi conferido no router
 * (`apps/api/app/modules/users/client_routes.py`) antes de consumir:
 *   - a lista responde `{ data, pagination }` (duas chaves, o `apiGet` NÃO
 *     desempacota) com `pageSize`/`totalPages` em camelCase;
 *   - as demais rotas respondem o `ClientUserResponse` cru, sem envelope;
 *   - ativar/desativar são **POST** em sub-rotas dedicadas, não PATCH `active`.
 *
 * `client_id` nunca vai no body: o servidor o fixa a partir do tenant da rota e
 * o `CreateClientUserRequest` é `extra="forbid"` — enviá-lo daria 422.
 */
import type {
  ClientUserListResponse,
  ClientUserResponse,
  CreateClientUserRequest,
  ListClientUsersQuery,
  UpdateClientUserRequest,
} from '@/lib/contracts';

import { apiGet, apiPatch, apiPost } from './client';

export type ListClientUsersParams = ListClientUsersQuery;

function basePath(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}/users`;
}

function buildQuery(params: ListClientUsersParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  const search = params.search?.trim();
  if (search) sp.set('search', search);
  return sp.toString();
}

export async function listClientUsers(
  clientId: string,
  params: ListClientUsersParams = {},
): Promise<ClientUserListResponse> {
  return apiGet<ClientUserListResponse>(`${basePath(clientId)}?${buildQuery(params)}`);
}

export async function createClientUser(
  clientId: string,
  payload: CreateClientUserRequest,
): Promise<ClientUserResponse> {
  return apiPost<ClientUserResponse>(basePath(clientId), payload);
}

export async function updateClientUser(
  clientId: string,
  userId: string,
  payload: UpdateClientUserRequest,
): Promise<ClientUserResponse> {
  return apiPatch<ClientUserResponse>(
    `${basePath(clientId)}/${encodeURIComponent(userId)}`,
    payload,
  );
}

export async function activateClientUser(
  clientId: string,
  userId: string,
): Promise<ClientUserResponse> {
  return apiPost<ClientUserResponse>(
    `${basePath(clientId)}/${encodeURIComponent(userId)}/activate`,
  );
}

export async function deactivateClientUser(
  clientId: string,
  userId: string,
): Promise<ClientUserResponse> {
  return apiPost<ClientUserResponse>(
    `${basePath(clientId)}/${encodeURIComponent(userId)}/deactivate`,
  );
}
