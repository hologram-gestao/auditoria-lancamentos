/**
 * Helpers tipados do ARQUIVO CONTÁBIL do cliente — Sprint 13 (FRONT 13.6).
 *
 * Espelha `apps/api/app/modules/accounting_files/routes.py`, com os tipos do
 * contrato gerado. Detalhes que quebrariam em runtime se fossem chutados:
 *
 *   - a LISTA responde `{ data, pagination }` — DUAS chaves, então o
 *     auto-unwrap de `{data}` do `rawFetch` NÃO dispara e o par chega inteiro;
 *   - GERAR é `POST` com `{ layoutId, competence, materializationId? }` e
 *     responde `{ data }` (chave única, chega desempacotado). Sem
 *     `materializationId` o servidor usa a ÚLTIMA materialização do destino
 *     Conta contábil na competência;
 *   - BAIXAR é `GET …/{generationId}/download` e devolve o arquivo (não JSON),
 *     com o nome no `Content-Disposition` (`lancamentos_<AAAA-MM>_v<versão>.csv`);
 *   - tudo pede `generate_accounting_file`. `client_id` é o tenant da ROTA,
 *     nunca vai no body (§3.15).
 */
import type {
  AccountingFileGenerationItem,
  AccountingFileGenerationListResponse,
  GenerateAccountingFileRequest,
  ListAccountingFilesQuery,
} from '@/lib/contracts';

import { apiGet, apiGetBlob, apiPost, type BlobResponse } from './client';

export type {
  AccountingFileGenerationItem,
  AccountingFileGenerationListResponse,
  GenerateAccountingFileRequest,
};

function base(clientId: string): string {
  return `/api/v1/clients/${encodeURIComponent(clientId)}/accounting-files`;
}

export async function listAccountingFiles(
  clientId: string,
  query: ListAccountingFilesQuery = {},
): Promise<AccountingFileGenerationListResponse> {
  const qs = new URLSearchParams();
  if (query.competence) qs.set('competence', query.competence);
  if (query.page) qs.set('page', String(query.page));
  if (query.pageSize) qs.set('pageSize', String(query.pageSize));
  const suffix = qs.toString() ? `?${qs.toString()}` : '';
  return apiGet<AccountingFileGenerationListResponse>(`${base(clientId)}${suffix}`);
}

export async function generateAccountingFile(
  clientId: string,
  body: GenerateAccountingFileRequest,
): Promise<AccountingFileGenerationItem> {
  return apiPost<AccountingFileGenerationItem>(base(clientId), body);
}

export async function downloadAccountingFile(
  clientId: string,
  generationId: string,
): Promise<BlobResponse> {
  return apiGetBlob(`${base(clientId)}/${encodeURIComponent(generationId)}/download`);
}
