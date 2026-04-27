/**
 * Helpers tipados do módulo clientes BPO — S6.
 *
 * Espelha `apps/api/app/modules/clients/{routes,schemas}.py`. Convenções:
 *   - Listagem responde com `{ data, pagination }` e não é desempacotada
 *     pelo `client.ts` (envelope tem 2 chaves) — caller lê o objeto inteiro.
 *   - Demais endpoints retornam o objeto direto (sem envelope `{ data }`).
 *   - Credenciais Omie NUNCA aparecem em `Client` — backend nem expõe esses
 *     campos no schema de resposta (CLAUDE.md §3).
 *   - `test-connection` devolve 200 com `ok=false` em todos os modos de falha;
 *     somente erros de transporte/auth de sessão lançam `ApiError`.
 */
import { apiGet, apiPatch, apiPost } from './client';

export interface ManagerSummary {
  id: string;
  name: string;
  email: string;
}

export interface Client {
  id: string;
  name: string;
  active: boolean;
  created_at: string;
  updated_at: string;
  responsible_manager: ManagerSummary | null;
  reconciliation_count: number;
}

export interface Pagination {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}

export interface ClientListResponse {
  data: Client[];
  pagination: Pagination;
}

export interface ListClientsParams {
  page?: number;
  pageSize?: number;
  search?: string;
}

export interface CreateClientPayload {
  name: string;
  omie_app_key: string;
  omie_app_secret: string;
}

export interface UpdateClientPayload {
  name?: string;
  active?: boolean;
  omie_app_key?: string;
  omie_app_secret?: string;
}

export interface TestConnectionPayload {
  omie_app_key: string;
  omie_app_secret: string;
}

export interface TestConnectionResult {
  ok: boolean;
  message: string;
}

export interface AssignClientPayload {
  user_id: string;
}

function buildQuery(params: ListClientsParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  const search = params.search?.trim();
  if (search) sp.set('search', search);
  return sp.toString();
}

export async function listClients(params: ListClientsParams = {}): Promise<ClientListResponse> {
  return apiGet<ClientListResponse>(`/api/v1/clients?${buildQuery(params)}`);
}

export async function createClient(payload: CreateClientPayload): Promise<Client> {
  return apiPost<Client>('/api/v1/clients', payload);
}

export async function testConnection(
  payload: TestConnectionPayload,
): Promise<TestConnectionResult> {
  return apiPost<TestConnectionResult>('/api/v1/clients/test-connection', payload);
}

export async function updateClient(id: string, payload: UpdateClientPayload): Promise<Client> {
  return apiPatch<Client>(`/api/v1/clients/${id}`, payload);
}

export async function assignClient(id: string, payload: AssignClientPayload): Promise<Client> {
  return apiPatch<Client>(`/api/v1/clients/${id}/assign`, payload);
}
