/**
 * Plano de contas CONTÁBIL do cliente e conta do banco de cada conta de origem
 * (Sprint 16 — BACK 16.1 · 16.3).
 *
 * ⚠️ É o plano do sistema contábil de DESTINO (onde o escritório lança), não o
 * "Plano de Contas" da origem da S10 (`/chart-of-accounts`, categorias do Omie).
 * Os nomes da API são distintos de propósito: `accounting-chart` × `chart-of-accounts`.
 *
 * Espelha `modules/client_accounting_chart/routes.py` e
 * `modules/client_source_accounts/routes.py`, lidos ANTES de consumir:
 *
 *   - `GET  /clients/{id}/accounting-chart`        → `{ data: [...], pagination }` — DUAS
 *                                                     chaves: o envelope chega inteiro
 *   - `POST /clients/{id}/accounting-chart/import` → multipart `file`; `{ data: {contas,
 *                                                     contasNovas, contasInativadas} }`
 *   - `POST  /clients/{id}/accounting-chart/accounts`       → `{ data: conta }` (86e3nb816)
 *   - `PATCH /clients/{id}/accounting-chart/accounts/{id}`  → `{ data: conta }` (86e3nb816)
 *   - `GET  /clients/{id}/source-accounts`         → `{ data: [...] }` — chave única: o array
 *   - `PUT  /clients/{id}/source-accounts`         → `{ data: { entry, created } }`
 *
 * Recusas da importação são as MESMAS exceções tipadas da origem por arquivo
 * (S14) — `code` + `userMessage` + `details` —, lidas por
 * `lib/accounting-chart-errors.ts`. `client_id` nunca vai no corpo: o tenant é a
 * rota (§3.15).
 */
import type {
  AccountingAccount,
  AccountingAccountCreateRequest,
  AccountingAccountUpdateRequest,
  AccountingChartImportResult,
  AccountingChartListResponse,
  ListAccountingChartQuery,
  SourceAccountBindingPayload,
  SourceAccountBindingRequest,
  SourceAccountEntry,
} from '@/lib/contracts';
import type { components } from '@/lib/contracts/schema';

import { apiGet, apiPatch, apiPost, apiPostMultipart, apiPutJson } from './client';

export type ListAccountingChartParams = ListAccountingChartQuery;

type ImportBody =
  components['schemas']['Body_import_accounting_chart_api_v1_clients__client_id__accounting_chart_import_post'];

/** Nome do campo do multipart — travado no contrato pelo `satisfies`. */
const IMPORT_FIELD = { file: 'file' } as const satisfies Record<keyof ImportBody, string>;

/** Teto do `pageSize` no servidor (`le=100`); acima dele a rota responde 422. */
export const ACCOUNTING_CHART_MAX_PAGE_SIZE = 100;

function clientBase(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}`;
}

/**
 * `page`/`pageSize` vão SEMPRE (param condicional é o que gera 422 na carga
 * inicial). `code`, `type` e `status` são omitidos quando vazios — `status=`
 * vazio não é "sem filtro", é valor fora do `Literal` do servidor.
 */
function buildQuery(params: ListAccountingChartParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 50));
  if (params.code) sp.set('code', params.code);
  if (params.type) sp.set('type', params.type);
  if (params.status) sp.set('status', params.status);
  return sp.toString();
}

/** Leitura de todo papel com acesso ao cliente; encerrado continua legível. */
export async function listAccountingChart(
  clientId: string,
  params: ListAccountingChartParams = {},
): Promise<AccountingChartListResponse> {
  return apiGet<AccountingChartListResponse>(
    `${clientBase(clientId)}/accounting-chart?${buildQuery(params)}`,
  );
}

/**
 * Importa ou REIMPORTA o plano — tudo ou nada. Requer
 * `manage_client_accounting_chart`; cliente encerrado 409; recusas 422 tipadas.
 */
export async function importAccountingChart(
  clientId: string,
  file: File,
): Promise<AccountingChartImportResult> {
  const form = new FormData();
  form.append(IMPORT_FIELD.file, file);
  return apiPostMultipart<AccountingChartImportResult>(
    `${clientBase(clientId)}/accounting-chart/import`,
    form,
  );
}

/**
 * Inclui UMA conta no plano sem reimportar a planilha (86e3nb816). Código que o
 * cliente já tem: 409 `CONTA_CONTABIL_CODIGO_EXISTENTE`; campo que a planilha
 * recusaria: 422 `CONTA_CONTABIL_INVALIDA` com `details.field`.
 */
export async function createAccountingAccount(
  clientId: string,
  payload: AccountingAccountCreateRequest,
): Promise<AccountingAccount> {
  return apiPost<AccountingAccount>(`${clientBase(clientId)}/accounting-chart/accounts`, payload);
}

/**
 * Edita nome, tipo, classificação e situação de UMA conta (86e3nb816). Conta em
 * uso que viraria sintética ou inativa: 422 `CONTA_CONTABIL_EM_USO`.
 */
export async function updateAccountingAccount(
  clientId: string,
  accountId: string,
  payload: AccountingAccountUpdateRequest,
): Promise<AccountingAccount> {
  return apiPatch<AccountingAccount>(
    `${clientBase(clientId)}/accounting-chart/accounts/${encodeURIComponent(accountId)}`,
    payload,
  );
}

/** As contas de origem (e o slot padrão, quando o servidor o oferece) com a conta do banco. */
export async function listSourceAccounts(clientId: string): Promise<SourceAccountEntry[]> {
  return apiGet<SourceAccountEntry[]>(`${clientBase(clientId)}/source-accounts`);
}

/**
 * Define ou TROCA a conta do banco (upsert; configuração, não vigência).
 * Sintética ou inativa: 422 `CONTA_CONTABIL_NAO_LANCAVEL`.
 */
export async function setSourceAccountBinding(
  clientId: string,
  payload: SourceAccountBindingRequest,
): Promise<SourceAccountBindingPayload> {
  return apiPutJson<SourceAccountBindingPayload>(
    `${clientBase(clientId)}/source-accounts`,
    payload,
  );
}
