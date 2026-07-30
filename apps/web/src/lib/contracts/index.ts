/**
 * Aliases centralizados do contrato gerado (OpenAPI → `schema.ts`).
 *
 * **Regra (CLAUDE.md / ADR-000):** o front NUNCA redeclara à mão o shape de um
 * endpoint. Todo tipo de request/response entra aqui como alias de
 * `components['schemas'][...]`; se o backend mudar um campo, o `tsc` acusa em
 * quem consome — não em runtime.
 *
 * Fluxo de atualização (rodar na MESMA task em que o backend mudou):
 *
 * ```bash
 * pnpm dev:api                       # API em http://localhost:8000
 * pnpm --filter @auditoria/web gen:types
 * ```
 *
 * `schema.ts` é gerado — nunca editar à mão.
 *
 * Convenções que valem para todos os aliases abaixo:
 *   - Valores monetários chegam como `string` (Decimal do Pydantic v2 —
 *     preserva precisão). Formatação só na exibição (`lib/format.ts`).
 *   - Datas de mês/dia chegam como `YYYY-MM-DD`; timestamps como ISO 8601.
 *   - Campos de status/tipo são `string` no contrato ("lenient out"): um valor
 *     novo no backend não derruba a UI. Os unions locais existem só para
 *     `switch`/mapeamento e sempre têm fallback.
 */
import type { components, paths } from './schema';

type Schemas = components['schemas'];

// ---------------------------------------------------------------------------
// Comum
// ---------------------------------------------------------------------------

export type PaginationMeta = Schemas['PaginationMeta'];

// ---------------------------------------------------------------------------
// Sessão, papéis e escopo de tenancy (Sprint 5 / R1 · R2)
// ---------------------------------------------------------------------------

/**
 * Usuário autenticado devolvido por `/auth/login` e `/auth/refresh`.
 *
 * `role`/`scope`/`client_id` vêm do backend como **fonte única** — o gating de
 * UI (`lib/authz.ts`) deriva daqui. Redigitar a união de strings aqui seria
 * exatamente o "shape esperançoso" que o CLAUDE.md proíbe: um papel novo no
 * backend passaria despercebido em vez de virar erro de compilação.
 */
export type AuthenticatedUser = Schemas['AuthenticatedUser'];

/** `admin` | `manager` | `client_manager` | `client_operator`. */
export type UserRole = Schemas['UserRole'];
/** `system` (equipe Hologram) | `client` (usuário DO tenant). */
export type UserScope = Schemas['UserScope'];
/** Whitelist de papel aceita na API de usuários DO CLIENTE. */
export type ClientUserRole = Schemas['ClientUserRole'];

// ---------------------------------------------------------------------------
// Usuários DO CLIENTE — tenant (BACK 05.5 / R5)
// ---------------------------------------------------------------------------

export type ClientUserResponse = Schemas['ClientUserResponse'];
export type ClientUserListResponse = Schemas['ClientUserListResponse'];
export type CreateClientUserRequest = Schemas['CreateClientUserRequest'];
export type UpdateClientUserRequest = Schemas['UpdateClientUserRequest'];

/**
 * Query params reais de `GET /clients/{client_id}/users`, lidos do contrato.
 * `pageSize` é o alias camelCase que o backend declara — não inventar `page_size`.
 */
export type ListClientUsersQuery = NonNullable<
  paths['/api/v1/clients/{client_id}/users']['get']['parameters']['query']
>;

// ---------------------------------------------------------------------------
// Clientes / contas (S6 · S7)
// ---------------------------------------------------------------------------

export type ClientResponse = Schemas['ClientResponse'];
export type ClientDetailResponse = Schemas['ClientDetailResponse'];
export type ClientListResponse = Schemas['ClientListResponse'];
export type BankAccountResponse = Schemas['BankAccountResponse'];
export type ManagerSummary = Schemas['ManagerSummary'];

// ---------------------------------------------------------------------------
// Lista de conciliações do cliente (BACK 04.3)
// ---------------------------------------------------------------------------

export type ReconciliationSessionSummary = Schemas['ReconciliationSessionSummary'];
export type ReconciliationSessionListResponse = Schemas['ReconciliationSessionListResponse'];

/**
 * Vocabulário de status do FILTRO da lista — é o do produto, não o do banco:
 * `processed` cobre `reviewing` e `done` (ver docstring do endpoint).
 * Extraído do próprio contrato para o dia em que um valor for adicionado.
 */
export type ReconciliationStatusFilter = NonNullable<
  NonNullable<
    paths['/api/v1/clients/{client_id}/reconciliations']['get']['parameters']['query']
  >['status']
>;

// ---------------------------------------------------------------------------
// Conciliação: criação, partes (arquivos) e detalhe (BACK 04.2 · 04.3)
// ---------------------------------------------------------------------------

export type ExtractedStatement = Schemas['ExtractedStatement'];
export type ChecksumResult = Schemas['ChecksumResult'];
export type CreateReconciliationRequest = Schemas['CreateReconciliationRequest'];
export type CreateReconciliationPayload = Schemas['CreateReconciliationPayload'];
export type ReconciliationFileInput = Schemas['ReconciliationFileInput'];
export type ReconciliationStatementInput = Schemas['ReconciliationStatementInput'];
export type AttachFilesRequest = Schemas['AttachFilesRequest'];
export type AttachFilesPayload = Schemas['AttachFilesPayload'];
export type SessionFileItem = Schemas['SessionFileItem'];
export type SessionFilesPayload = Schemas['SessionFilesPayload'];
export type SessionDetailPayload = Schemas['SessionDetailPayload'];
export type SessionStatusPayload = Schemas['SessionStatusPayload'];

// ---------------------------------------------------------------------------
// Notificações — sino do header (BACK 04.4)
// ---------------------------------------------------------------------------

export type NotificationItem = Schemas['NotificationItem'];
export type NotificationListResponse = Schemas['NotificationListResponse'];
export type UnreadCountPayload = Schemas['UnreadCountPayload'];
export type MarkReadPayload = Schemas['MarkReadPayload'];

// ---------------------------------------------------------------------------
// Instrumentação de outcome (BACK 04.1)
// ---------------------------------------------------------------------------

export type AutorNavegouForaRequest = Schemas['AutorNavegouForaRequest'];
export type NotificacaoEntregueRequest = Schemas['NotificacaoEntregueRequest'];
/** O body é um union discriminado por `event` — o `tsc` cobra o par certo. */
export type UsageEventRequest = AutorNavegouForaRequest | NotificacaoEntregueRequest;
export type UsageEventPayload = Schemas['UsageEventPayload'];
