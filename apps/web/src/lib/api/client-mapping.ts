/**
 * Helpers tipados do DE-PARA do cliente (Sprint 12 — BACK 12.2 a 12.6):
 *
 *   - `/api/v1/mapping-destinations…`                  catálogo da ORGANIZAÇÃO (leitura)
 *   - `/api/v1/clients/{id}/movements/…`               base de movimentos da competência
 *   - `/api/v1/clients/{id}/mapping/{tipo}…`           decisões, prévia, materialização,
 *                                                      exportar e importar
 *
 * Todo shape vem do contrato gerado (`lib/contracts`). O router do backend foi
 * lido ANTES de consumir (`modules/client_mapping/routes.py`,
 * `modules/client_movements/routes.py`, `modules/mapping_catalog/routes.py`), e
 * quatro detalhes quebrariam em runtime se fossem chutados:
 *
 *   - a LISTA do de-para responde `{ data, pagination, competence }` e a dos
 *     alvos `{ data, pagination }` — mais de uma chave, então o auto-unwrap de
 *     `{data}` do `rawFetch` NÃO dispara e o envelope chega inteiro. Todo o
 *     resto responde `{ data: … }` com chave ÚNICA e já chega desempacotado;
 *   - o destino é endereçado pelo TIPO (`demonstrativo_contabil`), não pelo id
 *     — o id só serve à rota de alvos do catálogo;
 *   - a exportação é `GET` e devolve `.xlsx` (não JSON);
 *   - importar é `multipart/form-data` com os campos `file`, `effectiveFrom`,
 *     `confirm` e `confirmRetroactive` (aliases do `Form` no FastAPI).
 *
 * `client_id` nunca vai no body — ele é o tenant da ROTA, validado contra a
 * linha do usuário no servidor (§3.15).
 */
import type {
  ConfirmInheritedRequest,
  ConfirmInheritedResult,
  DecisionWriteRequest,
  DecisionWriteResult,
  InheritRequest,
  InheritResult,
  ListClientMappingQuery,
  MappingDestination,
  MappingImportApplyResult,
  MappingImportPreview,
  MappingListResponse,
  MappingPreview,
  MappingTarget,
  MappingTargetListResponse,
  MaterializationRequest,
  MaterializationResult,
  MovementsSyncResult,
  MovementsSyncState,
} from '@/lib/contracts';

import { apiGet, apiGetBlob, apiPost, apiPostMultipart, type BlobResponse } from './client';

export type ListClientMappingParams = ListClientMappingQuery;

/** Teto do `pageSize` no servidor (`le=100`) — acima dele é 400. */
const MAX_PAGE_SIZE = 100;

function clientBase(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}`;
}

function mappingBase(clientId: string, destinationType: string): string {
  return `${clientBase(clientId)}/mapping/${encodeURIComponent(destinationType)}`;
}

// ---------------------------------------------------------------------------
// Catálogo (leitura)
// ---------------------------------------------------------------------------

/**
 * Destinos do catálogo. `organizationId` SÓ para a plataforma, que enxerga
 * todas as organizações: staff que manda o parâmetro recebe 403 se for outra
 * org, e usuário de cliente recebe 403 sempre (`resolve_organization_filter`).
 */
export async function listMappingDestinations(
  organizationId?: string | null,
): Promise<MappingDestination[]> {
  const qs = organizationId ? `?organizationId=${encodeURIComponent(organizationId)}` : '';
  return apiGet<MappingDestination[]>(`/api/v1/mapping-destinations${qs}`);
}

/**
 * TODOS os alvos ATIVOS de um destino, para o seletor da decisão. O servidor
 * pagina com teto de 100; um catálogo maior que isso é percorrido página a
 * página em vez de oferecer um seletor que esconde alvos sem avisar.
 */
export async function listAllActiveMappingTargets(destinationId: string): Promise<MappingTarget[]> {
  const all: MappingTarget[] = [];
  let page = 1;
  for (;;) {
    const sp = new URLSearchParams({
      page: String(page),
      pageSize: String(MAX_PAGE_SIZE),
      active: 'true',
    });
    const res = await apiGet<MappingTargetListResponse>(
      `/api/v1/mapping-destinations/${encodeURIComponent(destinationId)}/targets?${sp.toString()}`,
    );
    all.push(...res.data);
    if (page >= res.pagination.totalPages) return all;
    page += 1;
  }
}

// ---------------------------------------------------------------------------
// Base de movimentos da competência (R0)
// ---------------------------------------------------------------------------

export async function getMovementsSyncState(
  clientId: string,
  competence: string,
): Promise<MovementsSyncState> {
  return apiGet<MovementsSyncState>(
    `${clientBase(clientId)}/movements/sync-state?competence=${encodeURIComponent(competence)}`,
  );
}

/**
 * Sincroniza UMA competência (síncrono, como a carteira). Cliente sem origem
 * capaz responde 409 com a taxonomia da S9, que a tela trata como ESTADO.
 */
export async function syncMovements(
  clientId: string,
  competence: string,
): Promise<MovementsSyncResult> {
  return apiPost<MovementsSyncResult>(`${clientBase(clientId)}/movements/sync`, { competence });
}

// ---------------------------------------------------------------------------
// De-para por destino (R2 · R4 · R6 · R7)
// ---------------------------------------------------------------------------

/**
 * `page`/`pageSize` vão SEMPRE (param condicional é o que gera erro na carga
 * inicial). `situation` e `code` são omitidos quando ausentes: `situation=`
 * vazio é valor fora do `Literal` do servidor (400), não "sem filtro".
 */
export function buildClientMappingQuery(params: ListClientMappingParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  if (params.situation) sp.set('situation', params.situation);
  if (params.code) sp.set('code', params.code);
  return sp.toString();
}

export async function listClientMapping(
  clientId: string,
  destinationType: string,
  params: ListClientMappingParams = {},
): Promise<MappingListResponse> {
  return apiGet<MappingListResponse>(
    `${mappingBase(clientId, destinationType)}?${buildClientMappingQuery(params)}`,
  );
}

/** UMA decisão (alvo do catálogo ou `nao_mapear`), com vigência declarada. */
export async function writeMappingDecision(
  clientId: string,
  destinationType: string,
  payload: DecisionWriteRequest,
): Promise<DecisionWriteResult> {
  return apiPost<DecisionWriteResult>(
    `${mappingBase(clientId, destinationType)}/decisions`,
    payload,
  );
}

/**
 * Confirmação em LOTE das herdadas vigentes. Com `confirm=false` nada é gravado
 * — é assim que o diálogo mostra a quantidade afetada ANTES de confirmar.
 */
export async function confirmInheritedDecisions(
  clientId: string,
  destinationType: string,
  payload: ConfirmInheritedRequest,
): Promise<ConfirmInheritedResult> {
  return apiPost<ConfirmInheritedResult>(
    `${mappingBase(clientId, destinationType)}/decisions/confirm-inherited`,
    payload,
  );
}

/** "Iniciar de-para" — só o `demonstrativo_contabil` herda do plano de contas. */
export async function inheritMapping(
  clientId: string,
  destinationType: string,
  payload: InheritRequest,
): Promise<InheritResult> {
  return apiPost<InheritResult>(`${mappingBase(clientId, destinationType)}/inherit`, payload);
}

// ---------------------------------------------------------------------------
// Prévia e materialização (R5)
// ---------------------------------------------------------------------------

export async function getMappingPreview(
  clientId: string,
  destinationType: string,
  competence: string,
): Promise<MappingPreview> {
  return apiGet<MappingPreview>(
    `${mappingBase(clientId, destinationType)}/preview?competence=${encodeURIComponent(competence)}`,
  );
}

export async function materializeMapping(
  clientId: string,
  destinationType: string,
  payload: MaterializationRequest,
): Promise<MaterializationResult> {
  return apiPost<MaterializationResult>(
    `${mappingBase(clientId, destinationType)}/materializations`,
    payload,
  );
}

// ---------------------------------------------------------------------------
// Portabilidade (R6)
// ---------------------------------------------------------------------------

export async function exportClientMapping(
  clientId: string,
  destinationType: string,
): Promise<BlobResponse> {
  return apiGetBlob(`${mappingBase(clientId, destinationType)}/export`);
}

export interface MappingImportInput {
  file: File;
  /** `YYYY-MM`; ausente = competência corrente do servidor. */
  effectiveFrom?: string | null;
}

export interface MappingImportApplyInput extends MappingImportInput {
  confirmRetroactive?: boolean;
}

function importForm(input: MappingImportInput): FormData {
  const form = new FormData();
  form.append('file', input.file);
  if (input.effectiveFrom) form.append('effectiveFrom', input.effectiveFrom);
  return form;
}

/** Prévia da importação — o servidor NÃO grava nada. */
export async function previewMappingImport(
  clientId: string,
  destinationType: string,
  input: MappingImportInput,
): Promise<MappingImportPreview> {
  return apiPostMultipart<MappingImportPreview>(
    `${mappingBase(clientId, destinationType)}/import/preview`,
    importForm(input),
  );
}

/**
 * Aplica a importação. `confirm=true` vai sempre: esta função só é chamada
 * depois que a pessoa viu a prévia e confirmou (sem ele o servidor devolve 409).
 */
export async function applyMappingImport(
  clientId: string,
  destinationType: string,
  input: MappingImportApplyInput,
): Promise<MappingImportApplyResult> {
  const form = importForm(input);
  form.append('confirm', 'true');
  if (input.confirmRetroactive) form.append('confirmRetroactive', 'true');
  return apiPostMultipart<MappingImportApplyResult>(
    `${mappingBase(clientId, destinationType)}/import`,
    form,
  );
}
