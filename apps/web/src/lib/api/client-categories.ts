/**
 * Helpers tipados do catálogo de categorias de cliente — 86e34jd8m.
 *
 * Espelha `apps/api/app/modules/client_categories/{routes,schemas}.py`, com os
 * tipos vindos do contrato gerado (`lib/contracts`) — nada redeclarado à mão.
 *
 * Convenções:
 *   - GET devolve envelope single-key `{ data: [...] }`: o `apiGet` desempacota
 *     e o caller recebe `ClientCategoryItem[]` direto. O catálogo é pequeno por
 *     natureza (nichos da carteira) — sem paginação.
 *   - Escrita é admin-only no backend (403 para o resto); leitura é da equipe.
 *   - 409 `CONFLICT` em POST/PATCH = nome já existe (sem distinção de caixa);
 *     em DELETE = categoria em uso por clientes (o `userMessage` traz a
 *     contagem e a orientação).
 */
import type {
  ClientCategoryCreate,
  ClientCategoryItem,
  ClientCategoryTone,
  ClientCategoryUpdate,
} from '@/lib/contracts';

import { apiDelete, apiGet, apiPatch, apiPost } from './client';

export type { ClientCategoryItem, ClientCategoryTone };
export type CreateClientCategoryPayload = ClientCategoryCreate;
export type UpdateClientCategoryPayload = ClientCategoryUpdate;

export async function listClientCategories(): Promise<ClientCategoryItem[]> {
  return apiGet<ClientCategoryItem[]>('/api/v1/client-categories');
}

export async function createClientCategory(
  payload: CreateClientCategoryPayload,
): Promise<ClientCategoryItem> {
  return apiPost<ClientCategoryItem>('/api/v1/client-categories', payload);
}

export async function updateClientCategory(
  id: string,
  payload: UpdateClientCategoryPayload,
): Promise<ClientCategoryItem> {
  return apiPatch<ClientCategoryItem>(`/api/v1/client-categories/${id}`, payload);
}

export async function deleteClientCategory(id: string): Promise<void> {
  await apiDelete<void>(`/api/v1/client-categories/${id}`);
}
