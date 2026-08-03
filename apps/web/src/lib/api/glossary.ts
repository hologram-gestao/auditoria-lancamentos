/**
 * Helpers tipados de `/api/v1/clients/{client_id}/glossary` (BACK 06.3 / R2).
 *
 * Todo shape vem do contrato gerado (`lib/contracts`) — nenhuma `interface`
 * espelhando o endpoint à mão. O shape real foi conferido no router e nos
 * schemas do backend (`apps/api/app/modules/glossary/routes.py` e
 * `schemas.py`) ANTES de consumir, e é diferente do de usuários em três pontos
 * que quebrariam em runtime se fossem chutados:
 *
 *   - a lista responde `{ data: { entries, version }, pagination }` — o `data`
 *     é um OBJETO com a versão do glossário dentro, não o array direto. Como o
 *     envelope tem duas chaves, o `apiGet` NÃO desempacota;
 *   - criar/editar respondem `{ data: <entrada> }` (chave única) → o `apiPost`/
 *     `apiPatch` já devolvem a entrada desempacotada;
 *   - remover é **lógico** e responde `{ data: { id, deleted, version } }` — a
 *     versão NOVA vem junto, então o DELETE também é uma escrita que muda o
 *     estado do glossário, não um 204 vazio.
 *
 * O `PATCH` substitui o registro INTEIRO (o texto é cifrado no servidor;
 * alterar um campo isolado exigiria decifrar o resto) — por isso o payload de
 * edição tem os mesmos campos obrigatórios do de criação.
 *
 * `client_id` nunca vai no body: o servidor o fixa a partir do tenant da rota e
 * os requests são `extra="forbid"` — enviá-lo daria 422. É a mesma regra dos
 * usuários do cliente, e é o que impede escrita cruzada entre tenants.
 */
import type {
  CreateGlossaryEntryRequest,
  GlossaryDeletedPayload,
  GlossaryEntry,
  GlossaryListResponse,
  ListGlossaryQuery,
  UpdateGlossaryEntryRequest,
} from '@/lib/contracts';

import { apiDelete, apiGet, apiPatch, apiPost } from './client';

export type ListGlossaryParams = ListGlossaryQuery;

function basePath(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}/glossary`;
}

/**
 * `page`/`pageSize` vão SEMPRE, mesmo nos defaults: o endpoint declara os dois
 * com `Query(...)`, e mandar só quando "tem valor" é a armadilha que gera 422
 * na carga inicial (regra do CLAUDE.md do papel).
 */
function buildQuery(params: ListGlossaryParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  return sp.toString();
}

export async function listGlossaryEntries(
  clientId: string,
  params: ListGlossaryParams = {},
): Promise<GlossaryListResponse> {
  return apiGet<GlossaryListResponse>(`${basePath(clientId)}?${buildQuery(params)}`);
}

export async function createGlossaryEntry(
  clientId: string,
  payload: CreateGlossaryEntryRequest,
): Promise<GlossaryEntry> {
  return apiPost<GlossaryEntry>(basePath(clientId), payload);
}

export async function updateGlossaryEntry(
  clientId: string,
  entryId: string,
  payload: UpdateGlossaryEntryRequest,
): Promise<GlossaryEntry> {
  return apiPatch<GlossaryEntry>(`${basePath(clientId)}/${encodeURIComponent(entryId)}`, payload);
}

export async function deleteGlossaryEntry(
  clientId: string,
  entryId: string,
): Promise<GlossaryDeletedPayload> {
  return apiDelete<GlossaryDeletedPayload>(`${basePath(clientId)}/${encodeURIComponent(entryId)}`);
}
