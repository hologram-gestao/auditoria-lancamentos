/**
 * Helpers tipados do módulo clientes BPO — S6 + S7.
 *
 * Espelha `apps/api/app/modules/clients/{routes,schemas}.py`. Convenções:
 *   - Listagem responde com `{ data, pagination }` e não é desempacotada
 *     pelo `client.ts` (envelope tem 2 chaves) — caller lê o objeto inteiro.
 *   - Demais endpoints retornam o objeto direto (sem envelope `{ data }`).
 *   - Credenciais Omie NUNCA aparecem em `Client` — backend nem expõe esses
 *     campos no schema de resposta (CLAUDE.md §3).
 *   - `test-connection` devolve 200 com `ok=false` em todos os modos de falha;
 *     somente erros de transporte/auth de sessão lançam `ApiError`.
 *   - S7: `account_type` é mantido como `string` (não union literal) porque o
 *     backend pode introduzir novos tipos do Omie antes do front (memória
 *     `feedback_pydantic` — strict in / lenient out).
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

// ---------------------------------------------------------------------------
// S7 — detalhe + cache L1 de contas + histórico de conciliações
// ---------------------------------------------------------------------------

export interface BankAccount {
  id: string;
  omie_conta_id: number;
  name: string;
  bank_name: string;
  /** 'CC' (conta corrente) ou 'CA' (cartão). Tratamos como string para tolerar tipos novos do Omie. */
  account_type: string;
  synced_at: string;
}

export interface ClientDetail extends Client {
  accounts: BankAccount[];
  /** MAX(synced_at) das contas; null se nenhuma conta foi sincronizada ainda. */
  accounts_synced_at: string | null;
}

/** Estados possíveis de uma sessão de conciliação (Doc §10.1). */
export type ReconciliationStatus = 'processing' | 'reviewing' | 'done' | 'error';

export interface ReconciliationSessionSummary {
  id: string;
  omie_conta_id: number;
  /** ISO date `YYYY-MM-DD` representando o primeiro dia do mês de referência. */
  reference_month: string;
  status: string;
  created_at: string;
  total_file_entries: number;
  conciliated_count: number;
  sem_omie_count: number;
  omie_sem_arquivo_count: number;
  anomaly_count: number;
  error_message: string | null;
}

export interface ReconciliationsListParams {
  page?: number;
  pageSize?: number;
  /** Filtro por conta Omie (`nCodCC`). */
  omie_conta_id?: number;
  /** Mês no formato `YYYY-MM` (mesmo formato do `<input type="month">`). */
  month?: string;
}

export interface ReconciliationsListResponse {
  data: ReconciliationSessionSummary[];
  pagination: Pagination;
}

export async function getClientDetail(id: string): Promise<ClientDetail> {
  return apiGet<ClientDetail>(`/api/v1/clients/${id}`);
}

export async function syncClientAccounts(id: string): Promise<ClientDetail> {
  // PATCH /sync-accounts não tem body; apiPatch sempre serializa, então
  // mandamos um objeto vazio — o backend ignora.
  return apiPatch<ClientDetail>(`/api/v1/clients/${id}/sync-accounts`, {});
}

function buildReconciliationsQuery(params: ReconciliationsListParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 10));
  if (params.omie_conta_id !== undefined) {
    sp.set('omie_conta_id', String(params.omie_conta_id));
  }
  const month = params.month?.trim();
  if (month) sp.set('month', month);
  return sp.toString();
}

export async function listReconciliations(
  id: string,
  params: ReconciliationsListParams = {},
): Promise<ReconciliationsListResponse> {
  return apiGet<ReconciliationsListResponse>(
    `/api/v1/clients/${id}/reconciliations?${buildReconciliationsQuery(params)}`,
  );
}
