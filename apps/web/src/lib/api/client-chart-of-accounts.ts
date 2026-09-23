/**
 * Helpers tipados de `/api/v1/clients/{client_id}/chart-of-accounts`
 * (Sprint 10 — BACK 10.3 / R3 · R4).
 *
 * Todo shape vem do contrato gerado (`lib/contracts`) — nenhuma `interface`
 * espelhando o endpoint à mão. O router e os schemas do backend
 * (`apps/api/app/modules/client_chart_of_accounts/`) foram lidos ANTES de
 * consumir, e três detalhes quebrariam em runtime se fossem chutados:
 *
 *   - a LISTA responde `{ data: [...], pagination }` — duas chaves, então o
 *     auto-unwrap do `rawFetch` NÃO dispara e o envelope chega inteiro;
 *   - a COBERTURA e o SYNC respondem `{ data: {...} }` — chave única, então os
 *     dois já chegam desempacotados na `ChartOfAccountsCoverage`. Declarar o
 *     envelope aqui daria um objeto com um `data` a mais e a tela leria
 *     `undefined` em todas as contagens, sem erro de compilação (`apiGet<T>` é
 *     genérico: ele acredita no tipo declarado — defeito conhecido do papel);
 *   - o `force` do "Sincronizar agora" é **query string**, não body: o endpoint
 *     é um `POST` sem corpo.
 *
 * `client_id` nunca vai no body — ele é o tenant da ROTA, e o servidor o valida
 * contra a linha do usuário (§3.15). Não existe busca por NOME: o nome não é
 * persistido (§4.5), e o servidor só aceita `code`.
 */
import type {
  ChartOfAccountsCoverage,
  ChartOfAccountsListResponse,
  ListChartOfAccountsQuery,
} from '@/lib/contracts';

import { apiGet, apiPost } from './client';

export type ListChartOfAccountsParams = ListChartOfAccountsQuery;

function basePath(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}/chart-of-accounts`;
}

/**
 * `page`/`pageSize` vão SEMPRE, mesmo nos defaults (regra do papel: param
 * condicional é o que gera 422 na carga inicial). `status`, `parentCode` e
 * `code` são omitidos quando vazios — mandar `status=` vazio não é "sem
 * filtro", é um valor fora do `Literal` do servidor, que responde 422.
 */
function buildQuery(params: ListChartOfAccountsParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  if (params.status) sp.set('status', params.status);
  if (params.parentCode) sp.set('parentCode', params.parentCode);
  if (params.code) sp.set('code', params.code);
  return sp.toString();
}

export async function listChartOfAccounts(
  clientId: string,
  params: ListChartOfAccountsParams = {},
): Promise<ChartOfAccountsListResponse> {
  return apiGet<ChartOfAccountsListResponse>(`${basePath(clientId)}?${buildQuery(params)}`);
}

/**
 * As cinco contagens sobre o conjunto INTEIRO do cliente. Rota própria de
 * propósito: a cobertura não pode mudar conforme a página, e somá-la no
 * navegador daria exatamente esse número errado.
 */
export async function getChartOfAccountsCoverage(
  clientId: string,
): Promise<ChartOfAccountsCoverage> {
  return apiGet<ChartOfAccountsCoverage>(`${basePath(clientId)}/coverage`);
}

/**
 * Sincroniza com a origem e devolve a cobertura resultante.
 *
 * `force=true` é o "Sincronizar agora": ignora a validade de 24 h. Sem ele, o
 * servidor serve do armazenamento local dentro da janela — a tela não precisa
 * saber quando a janela vence, só qual botão o usuário apertou.
 */
export async function syncChartOfAccounts(
  clientId: string,
  options: { force?: boolean } = {},
): Promise<ChartOfAccountsCoverage> {
  const sp = new URLSearchParams();
  sp.set('force', String(options.force ?? false));
  return apiPost<ChartOfAccountsCoverage>(`${basePath(clientId)}/sync?${sp.toString()}`);
}
