/**
 * Helpers tipados do módulo reconciliations — espelha
 * `apps/api/app/modules/reconciliations/{routes,schemas}.py`.
 *
 * S8 (FRONT 6.1) cobre apenas o `check-duplicate`. Sessões posteriores
 * adicionam criação, listagem de entries e exportação.
 *
 * Convenções (CLAUDE.md §6):
 *   - O envelope `{ data: ... }` com chave única é desempacotado em
 *     `apiGet`, então a função devolve `CheckDuplicateResult` direto.
 *   - O backend aceita o hash em case-insensitive, mas armazena lowercase;
 *     normalizamos antes de mandar para evitar regex mismatch (422) e
 *     para deixar o contrato explícito.
 */
import { apiGet } from './client';

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
