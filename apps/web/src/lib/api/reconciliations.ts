/**
 * Helpers tipados do módulo reconciliations — espelha
 * `apps/api/app/modules/reconciliations/{routes,schemas}.py`.
 *
 * S8 (FRONT 6.1) cobre o `check-duplicate`.
 * S9 (FRONT 7.2) adiciona o `parse` (extração via Claude).
 * S10 (FRONT 8.7) adiciona `createReconciliation` + `getSessionStatus`
 * (criação assíncrona da sessão e polling da tela de progresso).
 *
 * Convenções (CLAUDE.md §7):
 *   - O envelope `{ data: ... }` com chave única é desempacotado em
 *     `apiGet`/`apiPostMultipart`, então as funções devolvem o payload direto.
 *   - O backend aceita o hash em case-insensitive, mas armazena lowercase;
 *     normalizamos antes de mandar para evitar regex mismatch (422) e
 *     para deixar o contrato explícito.
 *   - Valores monetários (`amount`, `balance`, `opening_balance`,
 *     `closing_balance`) chegam como `string` porque o backend usa
 *     `Decimal` e Pydantic v2 serializa Decimal como string em JSON
 *     (preserva precisão, evita o R$ 1.23 → 1.2299999 do float). A
 *     formatação para BRL é responsabilidade do consumidor (ver
 *     `lib/format.ts`).
 */
import { apiGet, apiPatch, apiPost, apiPostBlob, apiPostMultipart } from './client';
import type { BlobResponse } from './client';

export interface Pagination {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}

export interface CheckDuplicateParams {
  client_id: string;
  omie_conta_id: number;
  /** Mês de referência no formato `YYYY-MM`. */
  month: string;
  /** SHA-256 hex (64 caracteres lowercase). */
  hash: string;
}

export interface CheckDuplicateResult {
  duplicate: boolean;
}

export async function checkDuplicate(params: CheckDuplicateParams): Promise<CheckDuplicateResult> {
  const sp = new URLSearchParams({
    client_id: params.client_id,
    omie_conta_id: String(params.omie_conta_id),
    month: params.month,
    hash: params.hash.toLowerCase(),
  });
  return apiGet<CheckDuplicateResult>(`/api/v1/reconciliations/check-duplicate?${sp.toString()}`);
}

/**
 * Tipo do extrato extraído — espelha `ExtractedStatement` do back
 * (apps/api/app/integrations/anthropic/schemas.py).
 *
 * Datas: `YYYY-MM-DD` (ISO 8601 estrito, parsing manual no front pra
 * evitar timezone-shift do `new Date('2026-04-01')`).
 *
 * `account_type`: union literal idêntico ao back. Se um dia o back aceitar
 * um terceiro tipo, o `Literal` lá explode antes de chegar aqui.
 */
export type ParsedAccountType = 'checking' | 'credit_card';

export interface ParsedTransaction {
  /** Data ISO 8601 (YYYY-MM-DD). */
  date: string;
  /** Descrição preservada do documento. */
  description: string;
  /** Valor com sinal (positivo = crédito, negativo = débito). String porque é Decimal no back. */
  amount: string;
  /** Saldo após a transação. Pode ser null em faturas de cartão. */
  balance: string | null;
}

export interface ParsedStatement {
  bank_name: string;
  account_type: ParsedAccountType;
  /** Início do período (YYYY-MM-DD). */
  period_start: string;
  /** Fim do período (YYYY-MM-DD). */
  period_end: string;
  opening_balance: string;
  closing_balance: string;
  transactions: ParsedTransaction[];
}

export interface ParseStatementParams {
  client_id: string;
  file: File;
}

/**
 * `POST /api/v1/reconciliations/parse` — manda arquivo + client_id em
 * `multipart/form-data` e devolve o `ExtractedStatement`. Stateless: nada
 * persiste no back até o usuário confirmar (S10).
 *
 * Erros conhecidos do back (resposta JSON envelope `{error}` → `ApiError`):
 *   - 400 `INVALID_FILE`: extensão fora do allowlist, magic bytes não bate,
 *     arquivo vazio, .xls não suportado.
 *   - 400 `FILE_TOO_LARGE`: > MAX_UPLOAD_SIZE_MB.
 *   - 404: cliente inacessível (manager fora da carteira ou inexistente).
 *   - 422 `PARSE_ERROR`: IA não devolveu tool_use válido ou validação
 *     pós-IA falhou.
 *   - 502: falha de auth na Claude API.
 *   - 504: timeout (60 s) na Claude API.
 */
export async function parseStatement(params: ParseStatementParams): Promise<ParsedStatement> {
  const fd = new FormData();
  fd.append('client_id', params.client_id);
  fd.append('file', params.file);
  return apiPostMultipart<ParsedStatement>('/api/v1/reconciliations/parse', fd);
}

// ----------------------------------------------------------------------
// S10 — POST /api/v1/reconciliations
// ----------------------------------------------------------------------

/**
 * Payload do POST /api/v1/reconciliations — espelha `CreateReconciliationRequest`.
 *
 * O nome do campo `statement` segue o backend (não `parsed_statement`):
 * é o `ParsedStatement` devolvido por `/parse`, revalidado no servidor
 * via `ReconciliationStatementInput`.
 *
 * `reference_month` no contrato do back é `date` (`YYYY-MM-01`); o front
 * normaliza o `YYYY-MM` do input do usuário para o 1º dia aqui antes de
 * mandar — o backend tem um `field_validator` que normaliza para o dia 1
 * de qualquer forma, mas mandar já normalizado deixa o tráfego previsível.
 */
export interface CreateReconciliationPayload {
  client_id: string;
  omie_conta_id: number;
  /** ISO `YYYY-MM-DD` — sempre dia 1 do mês de referência. */
  reference_month: string;
  /** SHA-256 hex (64 chars, lowercase). */
  file_hash: string;
  statement: ParsedStatement;
}

export interface CreateReconciliationResult {
  session_id: string;
  /** Sempre `'processing'` no retorno do POST (back enfileira o job antes de responder). */
  status: 'processing';
}

export async function createReconciliation(
  payload: CreateReconciliationPayload,
): Promise<CreateReconciliationResult> {
  return apiPost<CreateReconciliationResult>('/api/v1/reconciliations', payload);
}

// ----------------------------------------------------------------------
// S11.fix — POST /api/v1/reconciliations/{id}/reprocess
// ----------------------------------------------------------------------

/**
 * "Tentar novamente" de uma sessão que terminou em `status='error'`.
 *
 * Backend reseta a sessão pra `status='processing'`, mantém as `file_entries`
 * (resultado do parse Anthropic) e reagenda o processamento em background.
 * Resposta é idêntica ao create — front pode reusar a UI de processing/polling.
 *
 * Erros relevantes:
 *   - 404: sessão não existe / manager fora da carteira.
 *   - 409 (`CONFLICT`): sessão NÃO está em `error` (já processando, em
 *     revisão ou concluída) — caller deve refrescar o detail antes de
 *     mostrar o botão de novo.
 */
export async function reprocessReconciliation(
  sessionId: string,
): Promise<CreateReconciliationResult> {
  return apiPost<CreateReconciliationResult>(`/api/v1/reconciliations/${sessionId}/reprocess`, {});
}

/**
 * Descarta (soft-delete) uma sessão em `status='error'`.
 *
 * Backend marca `deleted_at=now()` — sessão some das listagens, libera a
 * tupla UNIQUE de idempotência (mesmo arquivo+mês pode virar uma sessão
 * nova). Retorna 204 No Content.
 *
 * Erros relevantes:
 *   - 404: sessão não existe / manager fora da carteira.
 *   - 409 (`CONFLICT`): sessão NÃO está em error (já em revisão ou
 *     concluída) — descarte só vale pra sessões mortas.
 */
export async function discardReconciliation(sessionId: string): Promise<void> {
  await apiPost<void>(`/api/v1/reconciliations/${sessionId}/discard`, {});
}

// ----------------------------------------------------------------------
// S10 — GET /api/v1/reconciliations/{id}/status
// ----------------------------------------------------------------------

/**
 * Estados possíveis da sessão (Doc §17.1).
 *
 * O backend retorna o status como `str` "lenient out" (memória
 * `feedback_pydantic_strict_input_lenient_output`), então mantemos uma
 * union literal aqui pra checagem em `switch`/`if`, ciente de que um
 * estado novo introduzido no back pode aparecer como string desconhecida.
 */
export type SessionStatus = 'processing' | 'reviewing' | 'done' | 'error';

export interface SessionStatusResult {
  session_id: string;
  status: SessionStatus;
  conciliated_count: number;
  sem_omie_count: number;
  omie_sem_arquivo_count: number;
  anomaly_count: number;
  /** `null` quando não há erro; string com a causa quando `status === 'error'`. */
  error_message: string | null;
}

export async function getSessionStatus(sessionId: string): Promise<SessionStatusResult> {
  return apiGet<SessionStatusResult>(`/api/v1/reconciliations/${sessionId}/status`);
}

// ----------------------------------------------------------------------
// S11 — GET /api/v1/reconciliations/{id}  (header da Tela de Revisão)
// ----------------------------------------------------------------------

/**
 * Detalhe da sessão. Substitui o scan O(N) que a Tela de Revisão fazia via
 * `useReconciliationsList(clientId, {pageSize:100}) + .find()` — não cobria
 * clientes com > 100 sessões. Os campos abaixo são o que o header precisa;
 * `period_start/end` ficam internos ao back (review service usa em
 * `/available-omie-entries`).
 *
 * `status` em union literal para `switch`/`if`, ciente de que o back
 * serializa lenient — uma string desconhecida não derruba o consumidor.
 */
export interface SessionDetail {
  session_id: string;
  client_id: string;
  omie_conta_id: number;
  /** ISO `YYYY-MM-DD` (sempre dia 1 do mês de referência). */
  reference_month: string;
  status: SessionStatus;
  total_file_entries: number;
  conciliated_count: number;
  sem_omie_count: number;
  omie_sem_arquivo_count: number;
  anomaly_count: number;
  /**
   * `null` quando `status !== 'error'`. Front usa pra renderizar a página
   * de erro com mensagem amigável + botão "Tentar novamente" antes de
   * chamar os endpoints de revisão (que retornariam 409 ConflictError
   * com status='error').
   */
  error_message: string | null;
  /**
   * Saldos agregados calculados pós-matching. Decimal serializado como
   * `string` (mesma convenção do `amount` em FileEntry). `null` em sessões
   * legadas processadas antes do balance fix; UI exibe "Indisponível".
   */
  balance_start: string | null;
  balance_end_file: string | null;
  balance_end_omie: string | null;
  balance_difference: string | null;
}

export async function getSessionDetail(sessionId: string): Promise<SessionDetail> {
  return apiGet<SessionDetail>(`/api/v1/reconciliations/${sessionId}`);
}

// ----------------------------------------------------------------------
// S11 — Tela de Revisão (BACK 9.1 a 9.10)
// ----------------------------------------------------------------------

/**
 * Espelhos diretos dos schemas Pydantic em
 * `apps/api/app/modules/reconciliations/review/schemas.py`.
 *
 * Convenções (memória `feedback_pydantic_strict_input_lenient_output`):
 *   - Requests: union literais (Pydantic valida estrito → 422 se mudar).
 *   - Responses: `string` em campos como `situation`, `severity`, `omie_status`
 *     porque o back serializa em modo lenient. A UI faz mapping defensivo.
 *   - Decimal vem como `string`. Use `formatBRL` no consumidor.
 *   - Datas (`transaction_date`): `YYYY-MM-DD` (parse manual via `formatBRDate`).
 *   - `created_at`: ISO 8601 com timezone (`datetime`).
 */

// ---- 9.1 / 9.3 — File entries ----

export type FileEntrySituation = 'conciliado' | 'sem_omie' | 'ignorado';
export type FileEntryUserAction = 'confirm' | 'flag' | 'ignore';

export interface FileEntryItem {
  id: string;
  transaction_date: string;
  description: string;
  amount: string;
  balance: string | null;
  /** String lenient — pode ser `conciliado`, `sem_omie` ou `ignorado`. */
  situation: string;
  user_action: string | null;
  user_note: string | null;
  omie_lancamento_id: number | null;
}

export interface ListFileEntriesParams {
  sessionId: string;
  page?: number;
  pageSize?: number;
  situation?: 'all' | FileEntrySituation;
  type?: 'all' | 'credit' | 'debit';
  /** Search aplicado após descrypt no servidor. */
  search?: string;
}

export interface FileEntryListResult {
  data: FileEntryItem[];
  pagination: Pagination;
}

function buildFileEntriesQuery(params: ListFileEntriesParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  if (params.situation && params.situation !== 'all') sp.set('situation', params.situation);
  if (params.type && params.type !== 'all') sp.set('type', params.type);
  const search = params.search?.trim();
  if (search) sp.set('search', search);
  return sp.toString();
}

export async function listFileEntries(params: ListFileEntriesParams): Promise<FileEntryListResult> {
  return apiGet<FileEntryListResult>(
    `/api/v1/reconciliations/${params.sessionId}/file-entries?${buildFileEntriesQuery(params)}`,
  );
}

/**
 * Payload do PATCH /file-entries/{id}. Pydantic v2 distingue chave omitida
 * de chave com valor `null` via `model_fields_set` — para limpar o vínculo
 * Omie, mande `omie_lancamento_id: null` explicitamente; para "não tocar",
 * omita a chave do payload (faça `delete payload.omie_lancamento_id` ou
 * monte só os campos que mudaram).
 */
export interface PatchFileEntryPayload {
  situation?: FileEntrySituation;
  user_action?: FileEntryUserAction;
  user_note?: string | null;
  omie_lancamento_id?: number | null;
}

export async function patchFileEntry(
  sessionId: string,
  entryId: string,
  payload: PatchFileEntryPayload,
): Promise<FileEntryItem> {
  return apiPatch<FileEntryItem>(
    `/api/v1/reconciliations/${sessionId}/file-entries/${entryId}`,
    payload,
  );
}

// ---- 9.4 — Available Omie entries (para Trocar Modal) ----

export interface AvailableOmieEntry {
  omie_id: number;
  transaction_date: string;
  description: string;
  supplier: string | null;
  category: string | null;
  amount: string;
  status: string;
}

export async function listAvailableOmieEntries(
  sessionId: string,
  search?: string,
): Promise<AvailableOmieEntry[]> {
  const sp = new URLSearchParams();
  const trimmed = search?.trim();
  if (trimmed) sp.set('search', trimmed);
  const qs = sp.toString();
  const suffix = qs ? '?' + qs : '';
  return apiGet<AvailableOmieEntry[]>(
    `/api/v1/reconciliations/${sessionId}/available-omie-entries${suffix}`,
  );
}

// ---- 9.5 / 9.6 — Omie entries (divergências) ----

export type OmieEntryUserAction = 'flag' | 'ignore' | 'resolved';

export interface OmieEntryItem {
  id: string;
  omie_lancamento_id: number;
  transaction_date: string;
  omie_status: string;
  supplier: string | null;
  category: string | null;
  /** Pode ser null se o cache L2 não tem o lançamento. UI mostra '—'. */
  amount: string | null;
  user_action: string | null;
  user_note: string | null;
}

export interface ListOmieEntriesParams {
  sessionId: string;
  page?: number;
  pageSize?: number;
}

export interface OmieEntryListResult {
  data: OmieEntryItem[];
  pagination: Pagination;
}

export async function listOmieEntries(params: ListOmieEntriesParams): Promise<OmieEntryListResult> {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  return apiGet<OmieEntryListResult>(
    `/api/v1/reconciliations/${params.sessionId}/omie-entries?${sp.toString()}`,
  );
}

export interface PatchOmieEntryPayload {
  user_action?: OmieEntryUserAction;
  user_note?: string | null;
}

export async function patchOmieEntry(
  sessionId: string,
  entryId: string,
  payload: PatchOmieEntryPayload,
): Promise<OmieEntryItem> {
  return apiPatch<OmieEntryItem>(
    `/api/v1/reconciliations/${sessionId}/omie-entries/${entryId}`,
    payload,
  );
}

// ---- 9.7 / 9.8 / 9.9 — Anomalies ----

export type AnomalySeverity = 'critical' | 'moderate' | 'info';
export type AnomalyDetectedBy = 'ai' | 'manual';

export interface AnomalyTypeRef {
  id: string;
  code: string;
  name: string;
  /** Lenient: `critical` / `moderate` / `info`. */
  severity: string;
}

export interface AnomalyRelatedFileEntry {
  id: string;
  transaction_date: string;
  description: string;
  amount: string;
}

export interface AnomalyRelatedOmieEntry {
  id: string;
  transaction_date: string;
  omie_lancamento_id: number;
}

export interface AnomalyItem {
  id: string;
  anomaly_type: AnomalyTypeRef;
  /** Lenient: `ai` ou `manual`. */
  detected_by: string;
  resolved: boolean;
  context: string | null;
  resolution_note: string | null;
  created_at: string;
  related_file_entry: AnomalyRelatedFileEntry | null;
  related_omie_entry: AnomalyRelatedOmieEntry | null;
}

export interface ListAnomaliesParams {
  sessionId: string;
  page?: number;
  pageSize?: number;
  resolved?: 'all' | 'true' | 'false';
  severity?: 'all' | AnomalySeverity;
}

export interface AnomalyListResult {
  data: AnomalyItem[];
  pagination: Pagination;
}

export async function listAnomalies(params: ListAnomaliesParams): Promise<AnomalyListResult> {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  if (params.resolved && params.resolved !== 'all') sp.set('resolved', params.resolved);
  if (params.severity && params.severity !== 'all') sp.set('severity', params.severity);
  return apiGet<AnomalyListResult>(
    `/api/v1/reconciliations/${params.sessionId}/anomalies?${sp.toString()}`,
  );
}

export interface CreateAnomalyPayload {
  anomaly_type_id: string;
  /** Mande UM dos dois (file_entry_id XOR omie_entry_id). Nunca os dois. */
  file_entry_id?: string;
  omie_entry_id?: string;
  context?: string;
}

export async function createAnomaly(
  sessionId: string,
  payload: CreateAnomalyPayload,
): Promise<AnomalyItem> {
  return apiPost<AnomalyItem>(`/api/v1/reconciliations/${sessionId}/anomalies`, payload);
}

export interface PatchAnomalyPayload {
  resolved: boolean;
  /** Obrigatório com ≥ 10 chars quando `resolved=true`. */
  resolution_note?: string;
}

export async function patchAnomaly(
  sessionId: string,
  anomalyId: string,
  payload: PatchAnomalyPayload,
): Promise<AnomalyItem> {
  return apiPatch<AnomalyItem>(
    `/api/v1/reconciliations/${sessionId}/anomalies/${anomalyId}`,
    payload,
  );
}

// ---- 9.2 — Omie lançamentos (lookup batched de supplier/category) ----

export interface OmieLancamentoItem {
  omie_id: number;
  transaction_date: string;
  description: string;
  supplier: string | null;
  category: string | null;
  amount: string;
  status: string;
}

export async function getOmieLancamentos(
  sessionId: string,
  ids: number[],
): Promise<OmieLancamentoItem[]> {
  if (ids.length === 0) return [];
  const sp = new URLSearchParams();
  sp.set('ids', ids.join(','));
  sp.set('session_id', sessionId);
  return apiGet<OmieLancamentoItem[]>(`/api/v1/omie/lancamentos?${sp.toString()}`);
}

// ---- 9.10 — Anomaly types catalog ----

export interface AnomalyTypeItem {
  id: string;
  code: string;
  name: string;
  description: string;
  /** Lenient: `critical` / `moderate` / `info`. */
  severity: string;
}

// ---- S14 BACK 10.1 — Excel export ----

/**
 * Gera o relatório Excel da sessão. Espelha
 * `POST /api/v1/reconciliations/{session_id}/export` (S14).
 *
 * Erros mapeados (backend usa o envelope padrão `{ error: { code, ... } }`):
 *   - 404 NOT_FOUND     → sessão inexistente, soft-deletada ou fora da
 *                         carteira do manager (probing-safe).
 *   - 409 CONFLICT      → status `processing` ou `error` (não exportável).
 *   - 401 UNAUTHORIZED  → cookies inválidos/ausentes.
 *
 * Retorno: blob XLSX + filename ASCII vindo do `Content-Disposition`. O
 * caller é responsável por disparar o download (ex: anchor + objectURL).
 */
export async function exportReconciliation(sessionId: string): Promise<BlobResponse> {
  return apiPostBlob(`/api/v1/reconciliations/${sessionId}/export`);
}

export async function listAnomalyTypes(): Promise<AnomalyTypeItem[]> {
  return apiGet<AnomalyTypeItem[]>('/api/v1/anomaly-types');
}
