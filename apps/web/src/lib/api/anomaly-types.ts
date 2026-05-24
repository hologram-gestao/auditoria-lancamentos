/**
 * Helpers tipados do CRUD admin de tipos de anomalia — S15 FRONT 11.2.
 *
 * Espelha `apps/api/app/modules/anomaly_types/{routes,schemas}.py`.
 *
 * Convenções:
 *   - GET sem `?page` retorna envelope legado `{ data: [...] }` (consumido pela
 *     tela de revisão via `lib/api/reconciliations.ts#listAnomalyTypes`). A admin
 *     UI sempre passa `?page=` para receber `{ data, pagination }`.
 *   - PATCH é parcial: omitir campo = manter; `code` é IMUTÁVEL no backend.
 *   - DELETE devolve 204 → caller ignora retorno.
 *   - 409 com `code = CONFLICT` em duas situações distintas:
 *       1) POST: `code` já existe → `userMessage` "Já existe um tipo...".
 *       2) DELETE: tipo em uso por anomalias → `userMessage` "Este tipo está em uso...".
 *     O front decide qual mensagem mostrar a partir do *contexto da ação*, não do
 *     payload do erro (o backend manda `userMessage` distinto, mas o callsite já
 *     sabe se está criando ou deletando, então usamos o `userMessage` direto).
 */
import { apiDelete, apiGet, apiPatch, apiPost } from './client';

export type AnomalySeverity = 'critical' | 'moderate' | 'info';

export interface AnomalyType {
  id: string;
  code: string;
  name: string;
  description: string;
  /** Lenient out: o BACK valida o enum no input, mas devolve string crua. */
  severity: string;
  active: boolean;
}

export interface Pagination {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}

export interface AnomalyTypeListResponse {
  data: AnomalyType[];
  pagination: Pagination;
}

export interface ListAnomalyTypesParams {
  page?: number;
  pageSize?: number;
  includeInactive?: boolean;
}

export interface CreateAnomalyTypePayload {
  code: string;
  name: string;
  description: string;
  severity: AnomalySeverity;
  active?: boolean;
}

export interface UpdateAnomalyTypePayload {
  name?: string;
  description?: string;
  severity?: AnomalySeverity;
  active?: boolean;
}

function buildQuery(params: ListAnomalyTypesParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 50));
  if (params.includeInactive) sp.set('include_inactive', 'true');
  return sp.toString();
}

/**
 * Lista paginada — admin UI. Sempre passa `?page=` para forçar o envelope
 * `{ data, pagination }` (o `apiGet` não auto-unwrap em envelopes com mais
 * de uma chave; ver `client.ts`).
 */
export async function listAnomalyTypes(
  params: ListAnomalyTypesParams = {},
): Promise<AnomalyTypeListResponse> {
  return apiGet<AnomalyTypeListResponse>(`/api/v1/anomaly-types?${buildQuery(params)}`);
}

export async function createAnomalyType(payload: CreateAnomalyTypePayload): Promise<AnomalyType> {
  return apiPost<AnomalyType>('/api/v1/anomaly-types', payload);
}

export async function updateAnomalyType(
  id: string,
  payload: UpdateAnomalyTypePayload,
): Promise<AnomalyType> {
  return apiPatch<AnomalyType>(`/api/v1/anomaly-types/${id}`, payload);
}

export async function deleteAnomalyType(id: string): Promise<void> {
  await apiDelete<void>(`/api/v1/anomaly-types/${id}`);
}
