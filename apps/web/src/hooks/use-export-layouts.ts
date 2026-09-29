/**
 * Hooks de TanStack Query dos layouts de exportação — Sprint 13 (FRONT 13.5).
 *
 * A chave da lista carrega o recorte de organização: a plataforma troca o
 * filtro e cada recorte tem o seu cache (o filtro é SERVER-SIDE). Criar
 * invalida a raiz, para a lista de qualquer recorte — e o seletor de layout da
 * geração do arquivo, que consome a mesma lista — verem o layout novo.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createExportLayoutFromTemplate,
  getExportLayout,
  listExportLayouts,
  listExportLayoutTemplates,
  type ExportLayoutDetail,
  type ExportLayoutFromTemplate,
  type ExportLayoutItem,
  type ExportLayoutTemplateItem,
} from '@/lib/api/export-layouts';

export const exportLayoutsKeys = {
  all: ['export-layouts'] as const,
  list: (organizationId: string | null) =>
    ['export-layouts', 'list', organizationId ?? 'all'] as const,
  detail: (layoutId: string) => ['export-layouts', 'detail', layoutId] as const,
  templates: ['export-layouts', 'templates'] as const,
};

export function useExportLayouts(
  organizationId: string | null,
  options: { enabled?: boolean } = {},
) {
  return useQuery<ExportLayoutItem[]>({
    queryKey: exportLayoutsKeys.list(organizationId),
    queryFn: () => listExportLayouts(organizationId ? { organizationId } : {}),
    enabled: options.enabled ?? true,
  });
}

export function useExportLayout(layoutId: string | null) {
  return useQuery<ExportLayoutDetail>({
    queryKey: exportLayoutsKeys.detail(layoutId ?? ''),
    queryFn: () => getExportLayout(layoutId ?? ''),
    enabled: layoutId !== null,
  });
}

export function useExportLayoutTemplates(options: { enabled?: boolean } = {}) {
  return useQuery<ExportLayoutTemplateItem[]>({
    queryKey: exportLayoutsKeys.templates,
    queryFn: listExportLayoutTemplates,
    enabled: options.enabled ?? true,
    // Modelos são dado do CÓDIGO: só mudam com deploy.
    staleTime: 5 * 60_000,
  });
}

export function useCreateExportLayoutFromTemplate() {
  const qc = useQueryClient();
  return useMutation<ExportLayoutDetail, Error, ExportLayoutFromTemplate>({
    mutationFn: createExportLayoutFromTemplate,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: exportLayoutsKeys.all });
    },
  });
}
