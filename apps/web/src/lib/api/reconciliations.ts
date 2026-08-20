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
import type {
  AnomalyItem,
  AnomalyRelatedFileEntry,
  AnomalyRelatedOmieEntry,
  AnomalyReviewVerdict,
  AnomalyTypeRef,
  AttachFilesPayload,
  ChecksumResult as ChecksumContract,
  CreateReconciliationPayload as CreateReconciliationContract,
  CreateReconciliationRequest,
  ExtractedStatement,
  PaginationMeta,
  ReconciliationFileInput,
  ResolveAnomalyRequest,
  SessionDetailPayload,
  SessionFileItem,
  SessionFilesPayload,
  SessionStatusPayload,
} from '@/lib/contracts';

import { apiDelete, apiGet, apiPatch, apiPost, apiPostBlob, apiPostMultipart } from './client';
import type { BlobResponse } from './client';

export type Pagination = PaginationMeta;

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
 * `checking` | `credit_card` | `investment`, derivado do contrato: se o backend
 * aceitar um tipo novo, ele aparece aqui sozinho na próxima geração e o `tsc`
 * cobra os `switch` exaustivos que dependem dele.
 */
export type ParsedAccountType = ExtractedStatement['account_type'];

/**
 * Uma movimentação extraída (contrato `ExtractedTransaction`).
 *
 * `amount`/`balance` chegam como `string` (Decimal do Pydantic v2 — precisão
 * preservada). `is_payment` só é `true` nas linhas de PAGAMENTO da fatura
 * anterior (cartão), excluídas do checksum.
 */
export type ParsedTransaction = ExtractedStatement['transactions'][number];

/**
 * Extrato/fatura extraído pela IA (contrato `ExtractedStatement`). Datas em
 * `YYYY-MM-DD` — fazer parse manual no front (ver `lib/format`), nunca
 * `new Date(iso)`, que é UTC e volta um dia no Brasil.
 */
export type ParsedStatement = ExtractedStatement;

/**
 * Checksum de saldos (BACK 02.3 — contrato `ChecksumResult`).
 *
 * `applicable=false` significa que a identidade de saldo NÃO é verificável para
 * o tipo de conta (hoje só `investment`, cujo rendimento/IOF/IR entram no saldo
 * sem virar movimentação). Nesse caso `ok` é sempre `true` e a UI não deve
 * exibir veredito: não há o que afirmar.
 */
export type ChecksumResult = ChecksumContract;

/**
 * Resposta completa de `POST /parse` — statement extraído + checksum +
 * `file_hash`, IRMÃOS dentro do envelope. Por ter mais de uma chave, o
 * auto-unwrap de `{data}` do `rawFetch` não dispara e o objeto chega inteiro.
 *
 * `fileHash` é o hash **recalculado no servidor** (S0/A10: duplicata é sempre
 * por hash do servidor). O SHA-256 client-side existe só para a checagem
 * barata de duplicata ANTES de gastar uma chamada de IA — quem vai no payload
 * de criação é este aqui.
 */
export interface ParseResult {
  statement: ParsedStatement;
  checksum: ChecksumResult;
  fileHash: string;
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
 * Erros conhecidos do back (envelope `{error}` → `ApiError`), com o `code`
 * canônico do enum `ErrorCode` — conferidos no router, não de memória:
 *   - `VALIDATION_ERROR`: arquivo vazio, acima do teto, extensão/magic bytes
 *     fora do allowlist.
 *   - `DUPLICATE_FILE` (409): o CONTEÚDO já foi importado numa sessão ativa
 *     deste cliente. O `/parse` deduplica por hash recalculado no servidor
 *     ANTES de chamar a IA — a duplicata não custa dinheiro.
 *   - `NOT_FOUND` (404): cliente inacessível (fora da carteira ou inexistente).
 *   - `PARSE_ERROR` (422): a IA não devolveu `tool_use` válido, ou a validação
 *     pós-IA falhou.
 *   - `ANTHROPIC_AUTH_ERROR` (502) / `ANTHROPIC_TIMEOUT` (504).
 */
export async function parseStatement(params: ParseStatementParams): Promise<ParseResult> {
  const fd = new FormData();
  fd.append('client_id', params.client_id);
  fd.append('file', params.file);
  const res = await apiPostMultipart<{
    data: ParsedStatement;
    checksum: ChecksumResult;
    file_hash: string;
  }>('/api/v1/reconciliations/parse', fd);
  return { statement: res.data, checksum: res.checksum, fileHash: res.file_hash };
}

// ----------------------------------------------------------------------
// S10 — POST /api/v1/reconciliations
// ----------------------------------------------------------------------

/**
 * Payload do POST /api/v1/reconciliations (contrato `CreateReconciliationRequest`).
 *
 * **Sprint 4:** o campo canônico é `files` — uma conciliação é *uma conta + um
 * mês* com N partes consolidadas num só resumo. A forma legada (`file_hash` +
 * `statement` soltos) ainda é aceita pelo backend, mas o front não a usa mais.
 *
 * Cada parte traz OU `statement` (extração ok) OU `error_code` (extração
 * falhou) — nunca os dois. Registrar a parte que falhou é o que permite a tela
 * dizer QUAL arquivo deu problema e oferecer removê-lo, em vez de a conciliação
 * nascer silenciosamente incompleta.
 *
 * `reference_month` no contrato é `date` (`YYYY-MM-01`); o front normaliza o
 * `YYYY-MM` do input para o dia 1 antes de mandar (o backend normalizaria de
 * qualquer forma, mas assim o tráfego fica previsível).
 */
export type CreateReconciliationPayload = CreateReconciliationRequest;
export type ReconciliationFilePart = ReconciliationFileInput;
export type CreateReconciliationResult = CreateReconciliationContract;

export async function createReconciliation(
  payload: CreateReconciliationPayload,
): Promise<CreateReconciliationResult> {
  return apiPost<CreateReconciliationResult>('/api/v1/reconciliations', payload);
}

// ----------------------------------------------------------------------
// Sprint 4 / BACK 04.2 — partes (arquivos) de uma conciliação
// ----------------------------------------------------------------------

export type SessionFile = SessionFileItem;
export type SessionFilesResult = SessionFilesPayload;
export type AttachFilesResult = AttachFilesPayload;

/**
 * Anexa N partes a uma conciliação existente — cenário S-3 ("a parte 2 chegou
 * no dia seguinte"). Só as partes novas são incorporadas; o cruzamento Omie
 * roda UMA vez sobre o conjunto.
 *
 * `reprocessing=true` na resposta avisa que a sessão voltou para `processing`
 * (o cruzamento foi reagendado) — é o sinal para o polling da lista/detalhe
 * voltar a rodar.
 *
 * Erros: 409 `CONFLICT` (conciliação em processamento ou concluída), 409
 * `DUPLICATE_FILE` (parte já presente), 404 (fora da carteira).
 */
export async function attachSessionFiles(
  sessionId: string,
  files: ReconciliationFilePart[],
): Promise<AttachFilesResult> {
  return apiPost<AttachFilesResult>(`/api/v1/reconciliations/${sessionId}/files`, { files });
}

/**
 * Partes da conciliação com o nome DECIFRADO e o status de cada uma. É o que
 * permite dizer qual parte falhou (código, nunca a mensagem interna).
 * `filename` é `null` nas partes migradas da Sprint 3 — a UI mostra
 * "Arquivo N", não célula vazia.
 */
export async function listSessionFiles(sessionId: string): Promise<SessionFilesResult> {
  return apiGet<SessionFilesResult>(`/api/v1/reconciliations/${sessionId}/files`);
}

/**
 * Remove uma parte e re-consolida o restante. 409 quando a conciliação está em
 * `processing` ou quando é a ÚLTIMA parte com lançamentos (nesse caso o caminho
 * é excluir a conciliação inteira).
 */
export async function deleteSessionFile(
  sessionId: string,
  fileId: string,
): Promise<AttachFilesResult> {
  return apiDelete<AttachFilesResult>(`/api/v1/reconciliations/${sessionId}/files/${fileId}`);
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

/**
 * Cancela uma conciliação em `processing` — marca `status='error'` ("cancelado
 * pelo usuário"). A BackgroundTask em andamento não é interrompida, mas o
 * backend tem guarda pra não sobrescrever o cancelamento. Depois, a sessão fica
 * em `error` (pode reprocessar ou excluir). 204 No Content.
 *
 * Erros: 404 (inexistente / fora da carteira); 409 (`CONFLICT`) se NÃO está em
 * processamento (já em revisão/concluída/erro).
 */
export async function cancelReconciliation(sessionId: string): Promise<void> {
  await apiPost<void>(`/api/v1/reconciliations/${sessionId}/cancel`, {});
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

export type SessionStatusResult = SessionStatusPayload;

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
/**
 * Detalhe da sessão (contrato `SessionDetailPayload`).
 *
 * Substitui o scan O(N) que a Tela de Revisão fazia via
 * `useReconciliationsList(clientId, {pageSize:100}) + .find()` — não cobria
 * clientes com > 100 sessões.
 *
 * Notas de contrato:
 *   - `status` e `account_type` são `string` (lenient out) — a UI ramifica com
 *     fallback, sem quebrar se o backend introduzir um valor novo;
 *   - os saldos (`balance_*`) são Decimal serializado como `string` e podem ser
 *     `null` em sessões legadas (UI exibe "Indisponível");
 *   - `error_code` é o CÓDIGO canônico do desfecho (S2/R9) — a tela mostra
 *     "(cód. X)", nunca `error_message`, que é linguagem interna.
 */
export type SessionDetail = SessionDetailPayload;

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

export type FileEntrySituation =
  | 'conciliado'
  // FASE 1 — casou por valor com data divergente ≤3 dias. O backend aceita o
  // filtro desde a FASE 1; o front só ganhou a opção na 86e2u513b.
  | 'conciliado_data_divergente'
  | 'sem_omie'
  | 'ignorado';
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
  /**
   * 86e2n4pf1 — só linhas com anomalia de QUALIFICAÇÃO não resolvida (o mesmo
   * conjunto que exibe o badge da coluna Análise). Server-side: a paginação
   * conta sob o filtro — antes era client-side e o rodapé mentia.
   */
  onlySuspect?: boolean;
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
  if (params.onlySuspect) sp.set('onlySuspect', 'true');
  return sp.toString();
}

export async function listFileEntries(params: ListFileEntriesParams): Promise<FileEntryListResult> {
  return apiGet<FileEntryListResult>(
    `/api/v1/reconciliations/${params.sessionId}/file-entries?${buildFileEntriesQuery(params)}`,
  );
}

/**
 * Payload do PATCH /file-entries/{id}. O backend distingue chave omitida de
 * chave com valor `null` via `model_fields_set`, e a regra vale IGUAL para
 * todo campo que aceita `null`:
 *
 *   chave omitida → não mexe
 *   `null`        → limpa
 *   valor         → grava
 *
 * Vale para `omie_lancamento_id` (remover vínculo) e `user_note` (apagar
 * anotação). Monte só os campos que mudaram — mandar `user_note: null` num
 * PATCH que só queria mudar `situation` APAGA a anotação do analista.
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

/** Mesma convenção do {@link PatchFileEntryPayload}: `user_note: null` apaga. */
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

/**
 * Unions LOCAIS de severidade/origem: existem para `switch`/mapeamento de
 * rótulo, com fallback. O contrato declara os dois como `string` ("lenient
 * out"), de propósito — um valor novo no backend não pode derrubar a lista.
 */
export type AnomalySeverity = 'critical' | 'moderate' | 'info';
export type AnomalyDetectedBy = 'ai' | 'manual';

/**
 * Shapes vindos do **contrato gerado** (Sprint 6). Até a BACK 06.5 eram
 * `interface`s redigitadas aqui; o campo novo `review_verdict` foi o gatilho
 * para corrigir a origem em vez de acrescentar mais um campo à cópia — é
 * exatamente o "shape esperançoso espelhando endpoint" que o CLAUDE.md proíbe.
 * Os nomes exportados não mudaram, então nenhum consumidor precisou mexer.
 */
export type {
  AnomalyItem,
  AnomalyRelatedFileEntry,
  AnomalyRelatedOmieEntry,
  AnomalyReviewVerdict,
  AnomalyTypeRef,
};

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

/**
 * Body do PATCH da anomalia — do CONTRATO (BACK 06.5).
 *
 * `resolved` deixou de ser obrigatório: os dois eixos são independentes e
 * opcionais, e **omitir um campo significa "não mexa nele"**. Marcar um flag
 * como improcedente sem resolvê-lo é o caminho comum da Sprint 6; resolver sem
 * julgar continua valendo. Corpo vazio é 422 — o servidor exige ao menos um.
 * Enquanto isto era uma `interface` local com `resolved: boolean`, mandar só o
 * veredito nem compilava.
 */
export type PatchAnomalyPayload = ResolveAnomalyRequest;

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
