/**
 * Hooks de TanStack Query do arquivo contábil — Sprint 13 (FRONT 13.6).
 *
 * - A chave é prefixada pelo `clientId` (trocar de cliente nunca serve o
 *   histórico do anterior) e leva a competência: a lista mostrada é a da
 *   competência da prévia.
 * - GERAR invalida o histórico do cliente inteiro (a geração aparece na lista).
 *   Se o histórico vier velho logo depois de gravar, é a 86e3fxqqa (a resposta
 *   da API sai antes do commit) — registrar, não contornar com timer.
 * - BAIXAR é mutation, não query: é ação manual, sem cache, e cada clique
 *   regenera no servidor (que confere o SHA-256).
 * - `retry: false` na lista: 403/404 são estado, não falha transitória.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  downloadAccountingFile,
  generateAccountingFile,
  listAccountingFiles,
  type AccountingFileGenerationItem,
  type AccountingFileGenerationListResponse,
  type GenerateAccountingFileRequest,
} from '@/lib/api/accounting-files';
import type { BlobResponse } from '@/lib/api/client';

/**
 * Uma competência não passa de poucas gerações; 100 é o teto do servidor e
 * dispensa paginação nesta lista (ela vive dentro da prévia, não é tela própria).
 */
export const ACCOUNTING_FILES_PAGE_SIZE = 100;

export const accountingFilesKeys = {
  all: (clientId: string) => ['accounting-files', clientId] as const,
  list: (clientId: string, competence: string) =>
    ['accounting-files', clientId, 'list', competence] as const,
};

export function useAccountingFiles(
  clientId: string,
  competence: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery<AccountingFileGenerationListResponse>({
    queryKey: accountingFilesKeys.list(clientId, competence),
    queryFn: () =>
      listAccountingFiles(clientId, {
        competence,
        page: 1,
        pageSize: ACCOUNTING_FILES_PAGE_SIZE,
      }),
    enabled: (options.enabled ?? true) && competence !== '',
    retry: false,
  });
}

export function useGenerateAccountingFile(clientId: string) {
  const qc = useQueryClient();
  return useMutation<AccountingFileGenerationItem, Error, GenerateAccountingFileRequest>({
    mutationFn: (body) => generateAccountingFile(clientId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: accountingFilesKeys.all(clientId) });
    },
  });
}

export function useDownloadAccountingFile(clientId: string) {
  return useMutation<BlobResponse, Error, string>({
    mutationFn: (generationId) => downloadAccountingFile(clientId, generationId),
  });
}
