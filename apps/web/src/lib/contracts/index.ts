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
/**
 * Whitelist de papel aceita na API de usuários DO SISTEMA (`admin` | `manager`).
 *
 * `platform_admin` NÃO está aqui de propósito: ele nasce só por script
 * (`scripts/promote_platform_admin.py`) e não entra em whitelist de API
 * nenhuma. Redigitar a união no formulário criaria a chance de oferecer um
 * papel que o servidor recusa com 422.
 */
export type SystemUserRole = Schemas['SystemUserRole'];
/**
 * Um staff de organização como a API o devolve — inclui `scope`,
 * `organization_id` e `organization_name` desde a 86e36ecqz. O `lib/api/users`
 * estreita só o `role` (o OpenAPI o expõe como `string`).
 */
export type UserResponse = Schemas['UserResponse'];
/** Body de `POST /users/{id}/transfer` — só plataforma (86e3bvbfx). */
export type TransferUserRequest = Schemas['TransferUserRequest'];

// ---------------------------------------------------------------------------
// Organizações — camada multi-BPO (épico 86e36ec0q)
// ---------------------------------------------------------------------------

/** Uma organização na listagem/detalhe da plataforma (com as duas contagens). */
export type OrganizationItem = Schemas['OrganizationItem'];
export type OrganizationListResponse = Schemas['OrganizationListResponse'];
export type OrganizationCreate = Schemas['OrganizationCreate'];
export type OrganizationUpdate = Schemas['OrganizationUpdate'];
/** A organização dona do cliente, como aparece na lista e no detalhe. */
export type OrganizationSummary = Schemas['OrganizationSummary'];
/** Query real de `GET /organizations` (`page`, `pageSize`, `search`). */
export type ListOrganizationsQuery = NonNullable<
  paths['/api/v1/organizations']['get']['parameters']['query']
>;
/**
 * Quem administra a PLATAFORMA. Não é staff de organização nenhuma: `GET
 * /users` filtra `scope='system'` e nunca devolve estas pessoas.
 */
export type PlatformAdminItem = Schemas['PlatformAdminItem'];
export type PlatformAdminListResponse = Schemas['PlatformAdminListResponse'];

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
/** Categoria do cliente como sai na lista/detalhe (86e34jd8m); `null` = sem categoria. */
export type ClientCategorySummary = Schemas['ClientCategorySummary'];

// ---------------------------------------------------------------------------
// Origens de dado do cliente — `client_connections` (Sprint 9 / R1 · R3 · R5)
// ---------------------------------------------------------------------------

/**
 * Uma origem conectada ao cliente. **Não tem campo de credencial** — nem
 * mascarado: o backend não o declara no schema, então nem o `tsc` deixa alguém
 * tentar lê-lo aqui.
 */
export type ClientConnection = Schemas['ClientConnectionResponse'];
/** `{ data: { connections } }` — envelope de UMA chave (o `client.ts` desembrulha). */
export type ClientConnectionListPayload = Schemas['ClientConnectionListPayload'];
/**
 * `ativa` | `inativa` | `erro` — enum FECHADO. Só `ativa` opera: `inativa` foi
 * desligada de propósito e `erro` teve a credencial recusada.
 */
export type ConnectionStatus = Schemas['ConnectionStatus'];
/**
 * O que uma origem sabe fazer. É **dado consultável**, não exceção: a tela
 * pergunta antes de oferecer a ação, em vez de descobrir pelo 409.
 */
export type ProviderCapability = Schemas['Capability'];
/**
 * Estado da ORIGEM do cliente, derivado das conexões (nunca coluna):
 * `sem_origem` (conectar) · `ativa` (nada a fazer) · `erro` (reconectar).
 */
export type OriginStatus = Schemas['OriginStatus'];
export type CreateConnectionRequest = Schemas['CreateConnectionRequest'];
export type UpdateConnectionRequest = Schemas['UpdateConnectionRequest'];
export type ConnectionDeletedPayload = Schemas['ConnectionDeletedPayload'];

// ---------------------------------------------------------------------------
// Carteira compartilhada — quem tem ACESSO ao cliente (86e390m4c)
// ---------------------------------------------------------------------------

/** Uma pessoa com acesso ao cliente; `is_responsible` marca o único responsável. */
export type ClientManagerResponse = Schemas['ClientManagerResponse'];
export type ClientManagerListResponse = Schemas['ClientManagerListResponse'];
export type AddClientManagerRequest = Schemas['AddClientManagerRequest'];

// ---------------------------------------------------------------------------
// Catálogo de categorias de cliente (86e34jd8m)
// ---------------------------------------------------------------------------

export type ClientCategoryItem = Schemas['ClientCategoryItem'];
export type ClientCategoryListResponse = Schemas['ClientCategoryListResponse'];
export type ClientCategoryCreate = Schemas['ClientCategoryCreate'];
export type ClientCategoryUpdate = Schemas['ClientCategoryUpdate'];
export type ClientCategoryTone = Schemas['ClientCategoryTone'];

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
export type MarkAllReadPayload = Schemas['MarkAllReadPayload'];
export type MarkReadPayload = Schemas['MarkReadPayload'];

// ---------------------------------------------------------------------------
// Anomalias da revisão (BACK 9.7–9.9 · veredito do revisor na BACK 06.5)
// ---------------------------------------------------------------------------

/**
 * Item da lista de anomalias. Redeclarado à mão até a Sprint 6 — o campo
 * `review_verdict` (BACK 06.5) foi o gatilho para trazer o shape para o
 * contrato: acrescentá-lo à interface manual repetiria o erro que o CLAUDE.md
 * proíbe (shape "esperançoso" espelhando endpoint).
 */
export type AnomalyItem = Schemas['AnomalyItem'];
export type AnomalyTypeRef = Schemas['AnomalyTypeRef'];
export type AnomalyRelatedFileEntry = Schemas['AnomalyRelatedFileEntry'];
export type AnomalyRelatedOmieEntry = Schemas['AnomalyRelatedOmieEntry'];
export type AnomalyListResponse = Schemas['AnomalyListResponse'];
/**
 * Body do PATCH da anomalia — DOIS eixos independentes e ambos opcionais
 * (`resolved` = alguém agiu; `review_verdict` = o flag devia ter sido
 * levantado). Corpo vazio é 422: o servidor exige ao menos um.
 */
export type ResolveAnomalyRequest = Schemas['ResolveAnomalyRequest'];
/** `procedente` | `improcedente` — o julgamento que alimenta a métrica (R4). */
export type AnomalyReviewVerdict = NonNullable<
  NonNullable<Schemas['AnomalyItem']['review_verdict']>
>;

// ---------------------------------------------------------------------------
// Glossário do tenant (Sprint 6 / R1 · R2 — BACK 06.2 · 06.3)
// ---------------------------------------------------------------------------

/**
 * Uma entrada do glossário já DECIFRADA, como a API devolve.
 *
 * `decryptFailed` chega em **camelCase** (o backend declara o alias no
 * `Field(..., alias="decryptFailed")`) — o resto do payload é snake_case. Ler o
 * campo do contrato em vez de redigitá-lo é o que impede o erro clássico de
 * chutar `decrypt_failed` e receber `undefined` (falsy) em toda linha, o que
 * esconderia justamente a entrada indecifrável.
 */
export type GlossaryEntry = Schemas['GlossaryEntryResponse'];
/** `categoria` | `fornecedor` | `regra` — enum FECHADO (não é "lenient out"). */
export type GlossaryEntryKind = Schemas['GlossaryEntryKind'];
/** `{ data: { entries, version }, pagination }` — duas chaves, sem auto-unwrap. */
export type GlossaryListResponse = Schemas['GlossaryListResponse'];
export type GlossaryListPayload = Schemas['GlossaryListPayload'];
export type CreateGlossaryEntryRequest = Schemas['CreateGlossaryEntryRequest'];
export type UpdateGlossaryEntryRequest = Schemas['UpdateGlossaryEntryRequest'];
/** Corpo do DELETE: `{ id, deleted, version }` — a versão NOVA do glossário. */
export type GlossaryDeletedPayload = Schemas['GlossaryDeletedPayload'];

/** Query params reais de `GET /clients/{client_id}/glossary` (`page`/`pageSize`). */
export type ListGlossaryQuery = NonNullable<
  paths['/api/v1/clients/{client_id}/glossary']['get']['parameters']['query']
>;

// ---------------------------------------------------------------------------
// Plano de contas do cliente (Sprint 10 / R1 · R3 — BACK 10.1 · 10.3)
// ---------------------------------------------------------------------------

/**
 * Uma linha do plano de contas, como a API a devolve.
 *
 * ⚠️ **Os campos vêm em camelCase** (`categoryCode`, `dreCode`, `naoExibir`…) —
 * o backend declara `alias=` em cada um. O resto do produto é snake_case, então
 * chutar `category_code` compilaria aqui como `undefined` em toda linha e a
 * coluna Código apareceria vazia sem erro nenhum. Ler do contrato é o que
 * impede isso.
 *
 * `name` e `dreName` são resolvidos em RUNTIME pelo mesmo cache de categorias da
 * tela de revisão e chegam `null` quando a origem não respondeu (fail-soft) —
 * nome de categoria nunca persiste (§4.5). `dreCode` nulo é **sem destino
 * declarado**: informação, não pendência.
 */
export type ChartOfAccountEntry = Schemas['ChartOfAccountEntryResponse'];
/** `{ data: [...], pagination }` — DUAS chaves, então o `apiGet` NÃO desembrulha. */
export type ChartOfAccountsListResponse = Schemas['ChartOfAccountsListResponse'];
/**
 * As cinco contagens da cobertura + as duas datas de sincronização. Calculadas
 * no SERVIDOR sobre o conjunto inteiro do cliente: somar a página no navegador
 * daria um número que muda conforme a paginação.
 */
export type ChartOfAccountsCoverage = Schemas['ChartOfAccountsCoverageResponse'];
/** `{ data: {...} }` — chave ÚNICA: o `apiGet`/`apiPost` já entrega o miolo. */
export type ChartOfAccountsCoverageEnvelope = Schemas['ChartOfAccountsCoverageEnvelope'];
/** `ativa` | `inativa` | `ausente_na_origem` — enum FECHADO (a UI ramifica nos três). */
export type ChartOfAccountsStatus = Schemas['ChartOfAccountsStatus'];
/**
 * Query real de `GET /clients/{id}/chart-of-accounts`: `page`, `pageSize`,
 * `status`, `parentCode` e `code`. **Não existe busca por nome** — o nome não
 * está no banco, e inventar o parâmetro aqui daria 422.
 */
export type ListChartOfAccountsQuery = NonNullable<
  paths['/api/v1/clients/{client_id}/chart-of-accounts']['get']['parameters']['query']
>;
/** Query real do `POST .../sync` — só `force` ("Sincronizar agora"). */
export type SyncChartOfAccountsQuery = NonNullable<
  paths['/api/v1/clients/{client_id}/chart-of-accounts/sync']['post']['parameters']['query']
>;

// ---------------------------------------------------------------------------
// Lançamento no Omie (Sprint 7 / R1 · R2 · R5 — BACK 07.3 · 07.4)
// ---------------------------------------------------------------------------

/** Par `{codigo, descricao}` do combobox de categoria — `codigo` é o `cCodCateg`. */
export type OmieCategoriaItem = Schemas['OmieCategoriaItem'];
/**
 * `{ data, total }` — DUAS chaves, então o auto-unwrap de `{data}` do
 * `rawFetch` não dispara e o payload chega inteiro. A lista é COMPLETA (o
 * backend não pagina: o consumidor é um combobox com busca local).
 */
export type OmieCategoriaListResponse = Schemas['OmieCategoriaListResponse'];

/** Uma linha do lote: a compra + a categoria que o operador escolheu. */
export type OmiePostingLineRequest = Schemas['OmiePostingLineRequest'];
export type OmiePostingBatchRequest = Schemas['OmiePostingBatchRequest'];
/** Resumo do lote: linha a linha + os três agregados. */
export type OmiePostingBatchPayload = Schemas['OmiePostingBatchPayload'];
export type OmiePostingLineResult = Schemas['OmiePostingLineResult'];

/**
 * `lancada` | `bloqueada` | `erro` — enum FECHADO no contrato (a UI ramifica
 * nos três). Derivado do próprio item para o dia em que um quarto aparecer.
 */
export type OmiePostingLineStatus = Schemas['OmiePostingLineResult']['status'];
/**
 * Motivo CATEGÓRICO do desfecho. O backend o declara fechado justamente para a
 * tela decidir o que oferecer (reclassificar? conferir no Omie? tentar de
 * novo?) sem casar a frase em português, que pode mudar.
 */
export type OmiePostingLineReason = NonNullable<Schemas['OmiePostingLineResult']['reason']>;

/** Query real de `GET /omie/categorias` (`session_id` obrigatório, `refresh`). */
export type ListOmieCategoriasQuery = NonNullable<
  paths['/api/v1/omie/categorias']['get']['parameters']['query']
>;

// ---------------------------------------------------------------------------
// Instrumentação de outcome (BACK 04.1)
// ---------------------------------------------------------------------------

export type AutorNavegouForaRequest = Schemas['AutorNavegouForaRequest'];
export type NotificacaoEntregueRequest = Schemas['NotificacaoEntregueRequest'];
/** O body é um union discriminado por `event` — o `tsc` cobra o par certo. */
export type UsageEventRequest = AutorNavegouForaRequest | NotificacaoEntregueRequest;
export type UsageEventPayload = Schemas['UsageEventPayload'];
