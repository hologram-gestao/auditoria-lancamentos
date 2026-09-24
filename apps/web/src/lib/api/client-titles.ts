/**
 * Helpers tipados de `/api/v1/clients/{client_id}/titles` — a CARTEIRA de
 * títulos em aberto (Sprint 11 — BACK 11.5 / R2 · R3 · R4).
 *
 * Todo shape vem do contrato gerado (`lib/contracts`) — nenhuma `interface`
 * espelhando o endpoint à mão. O router e os schemas do backend
 * (`apps/api/app/modules/client_titles/`) foram lidos ANTES de consumir, e três
 * detalhes quebrariam em runtime se fossem chutados:
 *
 *   - a LISTA responde `{ data: [...], pagination }` — duas chaves, então o
 *     auto-unwrap de `{data}` do `rawFetch` NÃO dispara e o envelope chega
 *     inteiro;
 *   - o SUMMARY e o SYNC respondem `{ data: {...} }` — chave única, então os
 *     dois já chegam desempacotados. Declarar o envelope aqui daria um objeto
 *     com um `data` a mais e a tela leria `undefined` em todos os baldes, sem
 *     erro de compilação (`apiGet<T>` é genérico: ele acredita no tipo
 *     declarado — defeito conhecido do papel, e o gate em browser é quem pega);
 *   - o `POST .../sync` **não tem corpo nem query**: o cliente é o tenant da
 *     ROTA, e a permissão é do servidor.
 *
 * `client_id` nunca vai no body — ele é o tenant da rota, validado contra a
 * linha do usuário (§3.15).
 */
import type {
  ClientTitlesListResponse,
  ListClientTitlesQuery,
  ReceivablesReport,
  TitleContext,
  TitleContextCreateRequest,
  TitlesSummary,
  TitlesSyncResult,
} from '@/lib/contracts';

import { apiGet, apiPost } from './client';

export type ListClientTitlesParams = ListClientTitlesQuery;

function basePath(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}/titles`;
}

/**
 * `page`/`pageSize` vão SEMPRE, mesmo nos defaults (regra do papel: param
 * condicional é o que gera 422 na carga inicial). O mesmo vale para
 * `sortBy`/`sortOrder`, que o servidor declara com default mas que a tela
 * sempre conhece — mandar a ordenação explícita deixa a URL do request igual
 * ao estado da tela.
 *
 * `type`, `situation` e `bucket` são omitidos quando ausentes: mandar
 * `situation=` vazio não é "sem filtro", é um valor fora do `Literal` do
 * servidor, que responde **400 `VALIDATION_ERROR`**.
 */
export function buildClientTitlesQuery(params: ListClientTitlesParams): string {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  sp.set('sortBy', params.sortBy ?? 'due_date');
  sp.set('sortOrder', params.sortOrder ?? 'asc');
  if (params.type) sp.set('type', params.type);
  if (params.situation) sp.set('situation', params.situation);
  if (params.bucket) sp.set('bucket', params.bucket);
  // Sprint 15: só manda `hasNoContext=true` quando marcado — `false` não é
  // "sem filtro" para o servidor, é "só os que TÊM contexto" (o inverso).
  if (params.hasNoContext) sp.set('hasNoContext', 'true');
  return sp.toString();
}

export async function listClientTitles(
  clientId: string,
  params: ListClientTitlesParams = {},
): Promise<ClientTitlesListResponse> {
  return apiGet<ClientTitlesListResponse>(
    `${basePath(clientId)}?${buildClientTitlesQuery(params)}`,
  );
}

/**
 * Agregados e aging sobre a carteira INTEIRA do cliente. Rota própria de
 * propósito: somar a página no navegador daria um número que muda ao paginar e
 * ao filtrar — e o aging errado foi exatamente o problema do relatório manual
 * que motivou a sprint.
 */
export async function getClientTitlesSummary(clientId: string): Promise<TitlesSummary> {
  return apiGet<TitlesSummary>(`${basePath(clientId)}/summary`);
}

/**
 * Sincroniza a carteira com a origem.
 *
 * Devolve as contagens do ciclo **e** o `summary` resultante — por isso a tela
 * não dispara um segundo request só para redesenhar os baldes que acabaram de
 * mudar. Cliente encerrado responde 409; cliente sem origem capaz responde 409
 * com um dos três códigos da taxonomia da S9, que a tela trata como ESTADO.
 */
export async function syncClientTitles(clientId: string): Promise<TitlesSyncResult> {
  return apiPost<TitlesSyncResult>(`${basePath(clientId)}/sync`);
}

/**
 * Histórico COMPLETO de contexto de um título (Sprint 15 — BACK 15.1), mais
 * recente primeiro, sem paginação. `client_id` na rota é o tenant — título de
 * outro cliente responde 404 sem revelar que existe alhures.
 */
export async function listTitleContext(clientId: string, titleId: string): Promise<TitleContext[]> {
  // `{ data: [...] }` tem UMA chave só: o `apiGet` já desembrulha.
  return apiGet<TitleContext[]>(`${basePath(clientId)}/${encodeURIComponent(titleId)}/context`);
}

/**
 * Registra uma entrada de contexto sobre um título. Append-only: registrar de
 * novo NÃO apaga o histórico. Cliente encerrado: 409. `type` fora do
 * vocabulário fechado: 400 `VALIDATION_ERROR`.
 */
export async function registerTitleContext(
  clientId: string,
  titleId: string,
  payload: TitleContextCreateRequest,
): Promise<TitleContext> {
  return apiPost<TitleContext>(
    `${basePath(clientId)}/${encodeURIComponent(titleId)}/context`,
    payload,
  );
}

/**
 * Relatório de recebíveis (Sprint 15 — BACK 15.2): separa, no servidor e sobre
 * a carteira INTEIRA, inadimplência real de vencido-com-contexto, para os dois
 * lados (a pagar / a receber). Mesma permissão de leitura da carteira
 * (`view_client_receivables`) — não expõe texto decifrado nem título.
 */
export async function getReceivablesReport(clientId: string): Promise<ReceivablesReport> {
  return apiGet<ReceivablesReport>(`${basePath(clientId)}/receivables-report`);
}
