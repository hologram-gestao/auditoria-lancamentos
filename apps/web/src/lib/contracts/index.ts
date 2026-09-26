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
// Carteira de títulos em aberto (Sprint 11 / R2 · R3 · R4 — BACK 11.5)
// ---------------------------------------------------------------------------

/**
 * Um título da carteira, como a API o devolve.
 *
 * ⚠️ **camelCase**, como o plano de contas (`titleType`, `dueDate`,
 * `supplierNameResolved`…): o backend declara `alias=` campo a campo. E
 * `amount` é **string** — `Decimal` serializado pelo Pydantic v2 —, então
 * `formatBRL` recebe a string crua e nenhuma conta acontece no navegador.
 *
 * `supplierName` é resolvido em RUNTIME (nunca persiste, §4.5) e vem `null`
 * quando a origem não respondeu. Quem diz o que esse `null` significa é
 * `supplierNameResolved`: `false` = mostre o código marcado como não resolvido;
 * `true` com `supplierCode` nulo = o título simplesmente não tem devedor.
 */
export type ClientTitle = Schemas['ClientTitleResponse'];
/** `{ data: [...], pagination }` — DUAS chaves, então o `apiGet` NÃO desembrulha. */
export type ClientTitlesListResponse = Schemas['ClientTitlesListResponse'];
/**
 * Agregados e aging da carteira INTEIRA, calculados no servidor. `neverSynced`
 * é campo explícito de propósito: "não deve nada" e "ninguém nunca consultou a
 * origem" são estados diferentes, e derivar isso de `syncedAt == null` em cada
 * tela é como a regra se perde.
 */
export type TitlesSummary = Schemas['TitlesSummaryResponse'];
/** `{ data: {...} }` — chave ÚNICA: o `apiGet` já entrega o miolo. */
export type TitlesSummaryEnvelope = Schemas['TitlesSummaryEnvelope'];
/** Os totais de UM tipo (a pagar OU a receber), com os quatro baldes nomeados. */
export type AgingTotals = Schemas['AgingTotalsResponse'];
/** Contagens do ciclo de sincronização + o `summary` resultante (evita 2º request). */
export type TitlesSyncResult = Schemas['TitlesSyncResponse'];
/** `a_pagar` | `a_receber` — enum FECHADO (a UI ramifica nos dois). */
export type TitleType = Schemas['TitleType'];
/** `em_aberto` | `liquidado` | `ausente_na_origem` — os dois últimos são SAÍDAS. */
export type TitleStatus = Schemas['TitleStatus'];
/** `a_vencer` | `1_30` | `31_60` | `61_90` | `90_mais` — baldes do aging. */
export type AgingBucket = Schemas['AgingBucket'];
/**
 * Query real de `GET /clients/{id}/titles`: `page`, `pageSize`, `type`,
 * `situation`, `bucket`, `sortBy` e `sortOrder`. Os filtros são `Literal` no
 * servidor — valor fora do vocabulário é **400**, não lista vazia.
 */
export type ListClientTitlesQuery = NonNullable<
  paths['/api/v1/clients/{client_id}/titles']['get']['parameters']['query']
>;

// ---------------------------------------------------------------------------
// Contexto do título e relatório de recebíveis (Sprint 15 — BACK 15.1 · 15.2)
// ---------------------------------------------------------------------------

/**
 * `acordo_de_pagamento` | `pagamento_antecipado` | `nota_a_cancelar` |
 * `cobranca_suspensa` | `perda_provavel` | `outro` — vocabulário FECHADO
 * (`Literal` no backend); valor fora dele é 400, não 422.
 */
export type TitleContextType = Schemas['TitleContextType'];
/** Body de `POST .../titles/{title_id}/context`. */
export type TitleContextCreateRequest = Schemas['TitleContextCreateRequest'];
/**
 * Uma entrada de contexto, já decifrada. `decryptFailed=true` é o marcador de
 * falha de decifragem (mesma convenção do glossário) — a célula NUNCA fica
 * vazia em silêncio. `author` é o objeto ENXUTO e mascarado por escopo (mesmo
 * precedente da autoria de sessão), nunca a linha de `users`.
 */
export type TitleContext = Schemas['TitleContextResponse'];
/** `{ data: {...} }` — chave ÚNICA: o `apiPost` já entrega o miolo. */
export type TitleContextEnvelope = Schemas['TitleContextEnvelope'];
/**
 * `{ data: [...] }` — histórico COMPLETO de um título, mais recente primeiro,
 * SEM paginação (é o registro de um título, não uma coleção sem teto).
 */
export type TitleContextListResponse = Schemas['TitleContextListResponse'];
/**
 * Os agregados de UM grupo (`inadimplencia` OU `vencidoComContexto`) de UM
 * lado — todos vencidos por construção, sem balde `a_vencer`.
 */
export type ReceivablesGroup = Schemas['ReceivablesGroupResponse'];
/** Os dois grupos de UM lado (a pagar OU a receber). */
export type ReceivablesSide = Schemas['ReceivablesSideResponse'];
/**
 * Body de `GET .../titles/receivables-report` — os DOIS lados, cada um com os
 * DOIS grupos, calculados no servidor sobre a carteira INTEIRA. Cliente sem
 * nenhum contexto: tudo em `inadimplencia`, sem erro — é o baseline.
 */
export type ReceivablesReport = Schemas['ReceivablesReportResponse'];
/** `{ data: {...} }` — chave ÚNICA: o `apiGet` já entrega o miolo. */
export type ReceivablesReportEnvelope = Schemas['ReceivablesReportEnvelope'];

// ---------------------------------------------------------------------------
// De-para multi-destino (Sprint 12 — BACK 12.2 a 12.6)
// ---------------------------------------------------------------------------

/**
 * Estado da base de movimentos de UMA competência. `neverSynced` é CAMPO (não
 * "zero movimentos"): competência sem movimento e competência que ninguém
 * consultou são respostas diferentes, e a tela não decide isso contando linhas.
 */
export type MovementsSyncState = Schemas['MovementsSyncStateResponse'];
/** Contagens do ciclo + o `state` resultante (evita 2º request). */
export type MovementsSyncResult = Schemas['MovementsSyncResponse'];
export type MovementsSyncRequest = Schemas['MovementsSyncRequest'];

/** Um destino do catálogo da ORGANIZAÇÃO (tipo + nome + situação). */
export type MappingDestination = Schemas['MappingDestinationItem'];
/** `{ data: [...] }` — chave ÚNICA: o `apiGet` já entrega o array. */
export type MappingDestinationListResponse = Schemas['MappingDestinationListResponse'];
/** Um alvo do destino — código de catálogo + nome. */
export type MappingTarget = Schemas['MappingTargetItem'];
/** `{ data, pagination }` — DUAS chaves: o envelope chega inteiro. */
export type MappingTargetListResponse = Schemas['MappingTargetListResponse'];

/**
 * Uma categoria do universo do cliente com a decisão VIGENTE no destino. O nome
 * é de runtime (`categoryNameResolved=false` ⇒ a tela mostra o código).
 */
export type MappingListItem = Schemas['MappingListItem'];
/** `{ data, pagination, competence }` — TRÊS chaves: o envelope chega inteiro. */
export type MappingListResponse = Schemas['MappingListResponse'];
/** `herdada` | `confirmada` | `nao_mapear` | `sem_decisao` — enum FECHADO. */
export type MappingSituation = Schemas['MappingListItem']['situation'];
/** Query real de `GET /clients/{id}/mapping/{tipo}` (`page`, `pageSize`, `situation`, `code`). */
export type ListClientMappingQuery = NonNullable<
  paths['/api/v1/clients/{client_id}/mapping/{destination_type}']['get']['parameters']['query']
>;

/** `alvo` | `nao_mapear` — `nao_mapear` é DECISÃO; "sem decisão" é ausência dela. */
export type MappingDecisionType = Schemas['DecisionItemRequest']['decision'];
export type DecisionWriteRequest = Schemas['DecisionWriteRequest'];
export type DecisionWriteResult = Schemas['DecisionWriteResponse'];
/** `confirm=false` só CONTA as herdadas afetadas; `true` grava. */
export type ConfirmInheritedRequest = Schemas['ConfirmInheritedRequest'];
export type ConfirmInheritedResult = Schemas['ConfirmInheritedResponse'];
export type InheritRequest = Schemas['InheritRequest'];
/** `ok` | `sem_plano_de_contas` | `destino_sem_heranca` — nenhum é erro. */
export type InheritResult = Schemas['InheritResponse'];

/** Prévia da competência: as QUATRO situações, cobertura e contra-métrica. */
export type MappingPreview = Schemas['MappingPreviewResponse'];
export type MappingSituationTotal = Schemas['SituationTotalResponse'];
export type UndecidedCategory = Schemas['UndecidedCategoryResponse'];
export type MaterializationRequest = Schemas['MaterializationRequest'];
/** A versão N+1 criada — imutável. */
export type MaterializationResult = Schemas['MaterializationResponse'];

/** Prévia da importação: criadas · alteradas · ignoradas + recusadas com motivo. */
export type MappingImportPreview = Schemas['ImportPreviewResponse'];
export type MappingImportApplyResult = Schemas['ImportApplyResponse'];
export type MappingImportRejectedLine = Schemas['ImportRejectedLine'];
/** Motivo da recusa — enum FECHADO (a UI rotula cada um). */
export type MappingImportRejectReason = Schemas['ImportRejectedLine']['reason'];

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
