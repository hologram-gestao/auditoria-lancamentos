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
import type {
  BankAccountResponse,
  ClientDetailResponse,
  ClientListResponse as ClientListContract,
  ClientResponse,
  ManagerSummary as ManagerSummaryContract,
  PaginationMeta,
  ReconciliationSessionListResponse,
  ReconciliationSessionSummary as ReconciliationSessionSummaryContract,
  ReconciliationStatusFilter,
} from '@/lib/contracts';

import { apiGet, apiPatch, apiPost } from './client';

export type ManagerSummary = ManagerSummaryContract;
export type Client = ClientResponse;
export type Pagination = PaginationMeta;
export type ClientListResponse = ClientListContract;

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

/**
 * Conta bancária do cache L1 do cliente (contrato: `BankAccountResponse`).
 *
 * `account_type` é o código de 2 letras do Omie: `CC` (conta corrente), `CR`
 * (cartão de crédito), `CA` (conta aplicação/investimento), etc. ⚠️ `CA` ≠
 * cartão (auditoria M-1) — para detectar cartão use `isCreditCardAccount`.
 */
export type BankAccount = BankAccountResponse;

/**
 * Detecta conta de cartão de crédito pelo `account_type` do Omie.
 *
 * Cartão = `CR`. ⚠️ NÃO usar `CA` (Conta Aplicação/investimento — bug M-1,
 * auditoria 20/05/2026). Normaliza espaço/caixa que o Omie às vezes devolve.
 */
export function isCreditCardAccount(accountType: string): boolean {
  return accountType.trim().toUpperCase() === 'CR';
}

/**
 * Detalhe do cliente + contas do cache L1 (contrato: `ClientDetailResponse`).
 *
 * `accounts` e `accounts_synced_at` são OPCIONAIS no contrato (cliente novo,
 * sem nenhuma conta cacheada) — consumir sempre com `?? []` / `?? null`.
 */
export type ClientDetail = ClientDetailResponse;

/** Estados possíveis de uma sessão de conciliação no banco (Doc §17.1). */
export type ReconciliationStatus = 'processing' | 'reviewing' | 'done' | 'error';

export type ReconciliationSessionSummary = ReconciliationSessionSummaryContract;

/**
 * Status no vocabulário do PRODUTO usado pelo filtro da lista (Sprint 4):
 * `processed` cobre `reviewing` e `done` no banco. Vem do contrato — valor
 * fora da lista devolve 400 no backend.
 */
export type ReconciliationStatusFilterValue = ReconciliationStatusFilter;

export interface ReconciliationsListParams {
  page?: number;
  pageSize?: number;
  /** Filtro por conta Omie (`nCodCC`). */
  omie_conta_id?: number;
  /** Mês no formato `YYYY-MM` (mesmo formato do `<input type="month">`). */
  month?: string;
  /** Status no vocabulário do produto (`processing` | `processed` | `error`). */
  status?: ReconciliationStatusFilterValue;
}

export type ReconciliationsListResponse = ReconciliationSessionListResponse;

export async function getClientDetail(id: string): Promise<ClientDetail> {
  return apiGet<ClientDetail>(`/api/v1/clients/${id}`);
}

export async function syncClientAccounts(id: string): Promise<ClientDetail> {
  // PATCH /sync-accounts não tem body; apiPatch sempre serializa, então
  // mandamos um objeto vazio — o backend ignora.
  return apiPatch<ClientDetail>(`/api/v1/clients/${id}/sync-accounts`, {});
}

/** Itens por página padrão da lista de conciliações (design-system: ~20). */
export const RECONCILIATIONS_DEFAULT_PAGE_SIZE = 20;

function buildReconciliationsQuery(params: ReconciliationsListParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? RECONCILIATIONS_DEFAULT_PAGE_SIZE));
  if (params.omie_conta_id !== undefined) {
    sp.set('omie_conta_id', String(params.omie_conta_id));
  }
  const month = params.month?.trim();
  if (month) sp.set('month', month);
  if (params.status !== undefined) sp.set('status', params.status);
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
