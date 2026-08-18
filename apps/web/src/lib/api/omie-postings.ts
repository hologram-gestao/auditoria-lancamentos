/**
 * Lançamento de compras da fatura no Omie (Sprint 7 / BACK 07.3 · 07.4).
 *
 * ⚠️ **É a única escrita do ADL no ERP do cliente.** Todo o resto do sistema
 * lê o Omie; este módulo grava movimento financeiro na contabilidade do
 * cliente. O backend é quem monta o `IncluirLancCC`, deduplica por linha e
 * decide o desfecho — daqui sai apenas `{file_entry_id, cod_categoria}`.
 *
 * Contrato (tipos gerados, nunca redigitados — ver `lib/contracts/index.ts`):
 *   - `POST /reconciliations/{id}/omie-postings` → `{ data: {...} }` com chave
 *     única, então o `apiPost` desempacota e devolve o `OmiePostingBatchPayload`;
 *   - `GET /omie/categorias` → `{ data, total }`, DUAS chaves: o auto-unwrap
 *     não dispara e o objeto chega inteiro.
 *
 * O envio é sempre em LOTE (mesmo com uma compra só): um caminho de request,
 * um formato de resumo. Uma segunda rota "individual" teria de repetir a
 * mesma leitura de resultado na tela.
 */
import type {
  OmieCategoriaListResponse,
  OmiePostingBatchPayload,
  OmiePostingLineRequest,
} from '@/lib/contracts';

import { apiGet, apiPost } from './client';

export type {
  OmieCategoriaItem,
  OmieCategoriaListResponse,
  OmiePostingBatchPayload,
  OmiePostingLineReason,
  OmiePostingLineRequest,
  OmiePostingLineResult,
  OmiePostingLineStatus,
} from '@/lib/contracts';

/**
 * Categorias do cliente da sessão para o combobox de classificação (R2).
 *
 * O tenant vem da SESSÃO (o backend recusa `client_id` vindo da URL), por isso
 * o `session_id` é obrigatório na query. A lista vem completa e cacheada por
 * 6 h no servidor — a busca acontece no cliente, sem ida ao servidor por tecla.
 *
 * Erros do envelope `{error}` (→ `ApiError`): 404 (sessão de outro tenant ou
 * inexistente), 502/504 (Omie fora), 409 (credencial do cliente ausente).
 */
export async function listOmieCategorias(
  sessionId: string,
  options: { refresh?: boolean } = {},
): Promise<OmieCategoriaListResponse> {
  const sp = new URLSearchParams({ session_id: sessionId });
  if (options.refresh === true) sp.set('refresh', 'true');
  return apiGet<OmieCategoriaListResponse>(`/api/v1/omie/categorias?${sp.toString()}`);
}

/**
 * Lança no Omie, em lote, as compras `sem_omie` de uma conciliação de cartão.
 *
 * **Idempotente por linha no backend**: reenviar a mesma compra não duplica o
 * lançamento — a linha volta como `bloqueada/ja_lancada`. É o que torna seguro
 * reexecutar um lote em que só algumas linhas falharam.
 *
 * A resposta é **200 mesmo com falhas parciais**: o desfecho de cada linha vem
 * em `lines[]` (`lancada` / `bloqueada` / `erro` + `reason` categórico). Só
 * vira exceção o que impede o lote inteiro:
 *   - 409 `CONFLICT`: `OMIE_POSTING_ENABLED=false` (recurso desligado no
 *     ambiente) ou chave/lançamento em conflito;
 *   - 400 `VALIDATION_ERROR`: sessão que não é de cartão, ou lote acima do teto
 *     do servidor (`OMIE_POSTING_MAX_BATCH`) — o `userMessage` traz o número;
 *   - 404: sessão de outro tenant ou inexistente;
 *   - 5xx: Omie indisponível **sem nenhuma linha lançada** (com alguma lançada,
 *     o backend devolve 200 e marca as demais como `omie_indisponivel`).
 */
export async function postOmieLancamentos(
  sessionId: string,
  lines: OmiePostingLineRequest[],
): Promise<OmiePostingBatchPayload> {
  return apiPost<OmiePostingBatchPayload>(`/api/v1/reconciliations/${sessionId}/omie-postings`, {
    lines,
  });
}
