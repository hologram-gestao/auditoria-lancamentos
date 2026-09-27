/**
 * Hooks de TanStack Query da ORIGEM POR ARQUIVO do cliente (Sprint 14 — FRONT
 * 14.5 · 14.6).
 *
 * Convenções (as mesmas de `use-client-mapping`):
 *   - query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir o mapeamento do tenant anterior do cache;
 *   - salvar o mapeamento grava a resposta direto no cache (o servidor já
 *     devolveu a linha nova) — sem refetch;
 *   - processar um arquivo invalida a LISTA de processados E a árvore do
 *     de-para do cliente (`client-mapping` + o estado da base em
 *     `client-movements`): as linhas viraram movimentos, e a prévia da
 *     competência mudou.
 *
 * `retry: false` no mapeamento: 200 com `mapping: null` é o estado normal, e
 * um erro ali é de rede/permissão — repetir só atrasa a mensagem.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getInputMapping,
  inspectFile,
  listFileImports,
  processFile,
  saveInputMapping,
  type InspectFileInput,
  type ProcessFileInput,
} from '@/lib/api/client-file-origin';
import type {
  FileImportItem,
  FileInspectResult,
  FileProcessResult,
  InputMappingPayload,
  InputMappingRequest,
  InputMappingWritePayload,
} from '@/lib/contracts';

import { clientMappingKeys } from './use-client-mapping';

export const fileOriginKeys = {
  all: (clientId: string) => ['client-file-origin', clientId] as const,
  mapping: (clientId: string) => ['client-file-origin', clientId, 'mapping'] as const,
  importsAll: (clientId: string) => ['client-file-origin', clientId, 'imports'] as const,
  imports: (clientId: string, competence: string | null) =>
    ['client-file-origin', clientId, 'imports', competence ?? 'all'] as const,
};

export function useInputMapping(clientId: string, options: { enabled?: boolean } = {}) {
  return useQuery<InputMappingPayload>({
    queryKey: fileOriginKeys.mapping(clientId),
    queryFn: () => getInputMapping(clientId),
    enabled: clientId.length > 0 && (options.enabled ?? true),
    retry: false,
  });
}

/** Cria ou SUBSTITUI o mapeamento; a resposta entra direto no cache do GET. */
export function useSaveInputMapping(clientId: string) {
  const qc = useQueryClient();
  return useMutation<InputMappingWritePayload, Error, InputMappingRequest>({
    mutationFn: (payload) => saveInputMapping(clientId, payload),
    onSuccess: (result) => {
      qc.setQueryData<InputMappingPayload>(fileOriginKeys.mapping(clientId), {
        mapping: result.mapping,
      });
    },
  });
}

/** Inspeção não grava nada — não invalida nada. */
export function useInspectFile(clientId: string) {
  return useMutation<FileInspectResult, Error, InspectFileInput>({
    mutationFn: (input) => inspectFile(clientId, input),
  });
}

export function useProcessFile(clientId: string) {
  const qc = useQueryClient();
  return useMutation<FileProcessResult, Error, ProcessFileInput>({
    mutationFn: (input) => processFile(clientId, input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: fileOriginKeys.importsAll(clientId) });
      // As linhas viraram movimentos da base: prévia, lista do de-para e o
      // estado da base da competência mudaram (mesma árvore que o sync invalida).
      void qc.invalidateQueries({ queryKey: clientMappingKeys.all(clientId) });
      void qc.invalidateQueries({ queryKey: ['client-movements', clientId] });
    },
  });
}

export function useFileImports(
  clientId: string,
  competence: string | null = null,
  options: { enabled?: boolean } = {},
) {
  return useQuery<FileImportItem[]>({
    queryKey: fileOriginKeys.imports(clientId, competence),
    queryFn: () => listFileImports(clientId, competence),
    enabled: clientId.length > 0 && (options.enabled ?? true),
  });
}
