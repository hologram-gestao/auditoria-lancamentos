/**
 * Helpers tipados do módulo reconciliations — espelha
 * `apps/api/app/modules/reconciliations/{routes,schemas}.py`.
 *
 * S8 (FRONT 6.1) cobre o `check-duplicate`.
 * S9 (FRONT 7.2) adiciona o `parse` (extração via Claude).
 * S10 (FRONT 8.7) adiciona `createReconciliation` + `getSessionStatus`
 * (criação assíncrona da sessão e polling da tela de progresso).
 *
 * Convenções (CLAUDE.md §6):
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
import { apiGet, apiPost, apiPostMultipart } from './client';

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
  /** 1 a 7 dias (Doc §11). */
  date_tolerance_days: number;
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
