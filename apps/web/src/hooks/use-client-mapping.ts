/**
 * Hooks de TanStack Query do DE-PARA do cliente (Sprint 12 — FRONT 12.7).
 *
 * Convenções (as mesmas de `use-client-titles` e `use-client-chart-of-accounts`):
 *   - query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir o de-para do tenant anterior do cache;
 *   - `keepPreviousData` na lista evita o flash da tabela ao paginar e filtrar;
 *   - escrita de decisão invalida a árvore do DESTINO inteira (lista E prévia:
 *     a cobertura muda quando uma decisão muda); sincronizar a competência
 *     invalida o estado da base E a prévia daquela competência.
 *
 * **`retry: false` no estado da base e na prévia.** Os 409 desta sprint são
 * ESTADO esperado (base nunca sincronizada, competência sem movimento, anterior
 * à primeira vigência), não falha transitória: repetir só atrasa a instrução
 * certa na tela.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { BlobResponse } from '@/lib/api/client';
import {
  applyMappingImport,
  confirmInheritedDecisions,
  exportClientMapping,
  getMappingPreview,
  getMovementsSyncState,
  inheritMapping,
  listAllActiveMappingTargets,
  listClientMapping,
  listMappingDestinations,
  materializeMapping,
  previewMappingImport,
  syncMovements,
  writeMappingDecision,
  type ListClientMappingParams,
  type MappingImportApplyInput,
  type MappingImportInput,
} from '@/lib/api/client-mapping';
import type {
  ConfirmInheritedRequest,
  ConfirmInheritedResult,
  DecisionWriteRequest,
  DecisionWriteResult,
  InheritRequest,
  InheritResult,
  MappingDestination,
  MappingImportApplyResult,
  MappingImportPreview,
  MappingListResponse,
  MappingPreview,
  MappingTarget,
  MaterializationRequest,
  MaterializationResult,
  MovementsSyncResult,
  MovementsSyncState,
} from '@/lib/contracts';

export const clientMappingKeys = {
  all: (clientId: string) => ['client-mapping', clientId] as const,
  destination: (clientId: string, destinationType: string) =>
    ['client-mapping', clientId, destinationType] as const,
  list: (clientId: string, destinationType: string, params: ListClientMappingParams) =>
    ['client-mapping', clientId, destinationType, 'list', params] as const,
  preview: (clientId: string, destinationType: string, competence: string) =>
    ['client-mapping', clientId, destinationType, 'preview', competence] as const,
  syncState: (clientId: string, competence: string) =>
    ['client-movements', clientId, 'sync-state', competence] as const,
};

export const mappingCatalogKeys = {
  destinations: (organizationId: string | null) =>
    ['mapping-destinations', organizationId ?? 'own'] as const,
  targets: (destinationId: string) => ['mapping-targets', destinationId] as const,
};

export function useMappingDestinations(
  organizationId: string | null,
  options: { enabled?: boolean } = {},
) {
  return useQuery<MappingDestination[]>({
    queryKey: mappingCatalogKeys.destinations(organizationId),
    queryFn: () => listMappingDestinations(organizationId),
    enabled: options.enabled ?? true,
  });
}

export function useMappingTargets(destinationId: string, options: { enabled?: boolean } = {}) {
  return useQuery<MappingTarget[]>({
    queryKey: mappingCatalogKeys.targets(destinationId),
    queryFn: () => listAllActiveMappingTargets(destinationId),
    enabled: (options.enabled ?? true) && destinationId !== '',
  });
}

export function useClientMappingList(
  clientId: string,
  destinationType: string,
  params: ListClientMappingParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<MappingListResponse>({
    queryKey: clientMappingKeys.list(clientId, destinationType, params),
    queryFn: () => listClientMapping(clientId, destinationType, params),
    placeholderData: keepPreviousData,
    enabled: (options.enabled ?? true) && destinationType !== '',
  });
}

export function useMovementsSyncState(
  clientId: string,
  competence: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery<MovementsSyncState>({
    queryKey: clientMappingKeys.syncState(clientId, competence),
    queryFn: () => getMovementsSyncState(clientId, competence),
    enabled: (options.enabled ?? true) && competence !== '',
    retry: false,
  });
}

export function useMappingPreview(
  clientId: string,
  destinationType: string,
  competence: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery<MappingPreview>({
    queryKey: clientMappingKeys.preview(clientId, destinationType, competence),
    queryFn: () => getMappingPreview(clientId, destinationType, competence),
    enabled: (options.enabled ?? true) && destinationType !== '' && competence !== '',
    retry: false,
  });
}

/** Sincroniza a competência: muda o estado da base E a prévia de todo destino. */
export function useSyncMovements(clientId: string) {
  const qc = useQueryClient();
  return useMutation<MovementsSyncResult, Error, string>({
    mutationFn: (competence) => syncMovements(clientId, competence),
    onSuccess: (result, competence) => {
      qc.setQueryData(clientMappingKeys.syncState(clientId, competence), result.state);
      void qc.invalidateQueries({ queryKey: clientMappingKeys.all(clientId) });
    },
  });
}

function useInvalidateDestination(clientId: string, destinationType: string) {
  const qc = useQueryClient();
  return () =>
    void qc.invalidateQueries({
      queryKey: clientMappingKeys.destination(clientId, destinationType),
    });
}

export function useWriteMappingDecision(clientId: string, destinationType: string) {
  const invalidate = useInvalidateDestination(clientId, destinationType);
  return useMutation<DecisionWriteResult, Error, DecisionWriteRequest>({
    mutationFn: (payload) => writeMappingDecision(clientId, destinationType, payload),
    onSuccess: invalidate,
  });
}

/**
 * Serve às DUAS fases do lote: `confirm=false` conta (não grava, não
 * invalida nada), `confirm=true` grava e invalida.
 */
export function useConfirmInheritedDecisions(clientId: string, destinationType: string) {
  const invalidate = useInvalidateDestination(clientId, destinationType);
  return useMutation<ConfirmInheritedResult, Error, ConfirmInheritedRequest>({
    mutationFn: (payload) => confirmInheritedDecisions(clientId, destinationType, payload),
    onSuccess: (result) => {
      if (result.applied) invalidate();
    },
  });
}

export function useInheritMapping(clientId: string, destinationType: string) {
  const invalidate = useInvalidateDestination(clientId, destinationType);
  return useMutation<InheritResult, Error, InheritRequest>({
    mutationFn: (payload) => inheritMapping(clientId, destinationType, payload),
    onSuccess: invalidate,
  });
}

export function useMaterializeMapping(clientId: string, destinationType: string) {
  const invalidate = useInvalidateDestination(clientId, destinationType);
  return useMutation<MaterializationResult, Error, MaterializationRequest>({
    mutationFn: (payload) => materializeMapping(clientId, destinationType, payload),
    onSuccess: invalidate,
  });
}

export function useExportClientMapping(clientId: string, destinationType: string) {
  return useMutation<BlobResponse, Error, void>({
    mutationFn: () => exportClientMapping(clientId, destinationType),
  });
}

/** Prévia da importação: não grava — não invalida nada. */
export function usePreviewMappingImport(clientId: string, destinationType: string) {
  return useMutation<MappingImportPreview, Error, MappingImportInput>({
    mutationFn: (input) => previewMappingImport(clientId, destinationType, input),
  });
}

export function useApplyMappingImport(clientId: string, destinationType: string) {
  const invalidate = useInvalidateDestination(clientId, destinationType);
  return useMutation<MappingImportApplyResult, Error, MappingImportApplyInput>({
    mutationFn: (input) => applyMappingImport(clientId, destinationType, input),
    onSuccess: invalidate,
  });
}
