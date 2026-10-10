/**
 * Helpers tipados dos layouts de exportação — Sprint 13 (FRONT 13.5).
 *
 * Espelha `apps/api/app/modules/export_layouts/routes.py`, com os tipos do
 * contrato gerado (`lib/contracts`) — nada redeclarado à mão.
 *
 * Convenções:
 *   - A lista e o detalhe vêm em envelope de chave única (`{ data }`): o
 *     `apiGet` desempacota e o caller recebe o payload direto. A lista não é
 *     paginada (são poucos layouts por organização).
 *   - LER pede `manage_export_layouts` OU `generate_accounting_file` (quem gera
 *     escolhe o layout); ESCREVER pede só `manage_export_layouts`.
 *   - `organizationId` é da PLATAFORMA: na lista recorta no servidor, na
 *     criação é obrigatório. O admin omite (o servidor usa a organização da
 *     linha; outra é 403).
 *   - Erros que orientam: 409 `LAYOUT_NOME_DUPLICADO` (nome repetido na
 *     organização), 409 de organização suspensa, 404 de modelo inexistente,
 *     422 `LAYOUT_INVALIDO` com `details.field`, 409 `LAYOUT_EM_USO` ao excluir
 *     layout que já gerou arquivo (86e3nuuub). A tela mostra o `userMessage`.
 */
import type {
  ExportLayoutDetail,
  ExportLayoutFromTemplate,
  ExportLayoutItem,
  ExportLayoutTemplateItem,
  ListExportLayoutsQuery,
} from '@/lib/contracts';

import { apiDelete, apiGet, apiPost } from './client';

export type {
  ExportLayoutDetail,
  ExportLayoutFromTemplate,
  ExportLayoutItem,
  ExportLayoutTemplateItem,
  ListExportLayoutsQuery,
};

const BASE = '/api/v1/export-layouts';

export async function listExportLayoutTemplates(): Promise<ExportLayoutTemplateItem[]> {
  return apiGet<ExportLayoutTemplateItem[]>('/api/v1/export-layout-templates');
}

export async function listExportLayouts(
  query: ListExportLayoutsQuery = {},
): Promise<ExportLayoutItem[]> {
  const qs = query.organizationId
    ? `?organizationId=${encodeURIComponent(query.organizationId)}`
    : '';
  return apiGet<ExportLayoutItem[]>(`${BASE}${qs}`);
}

export async function getExportLayout(layoutId: string): Promise<ExportLayoutDetail> {
  return apiGet<ExportLayoutDetail>(`${BASE}/${encodeURIComponent(layoutId)}`);
}

export async function createExportLayoutFromTemplate(
  payload: ExportLayoutFromTemplate,
): Promise<ExportLayoutDetail> {
  return apiPost<ExportLayoutDetail>(`${BASE}/from-template`, payload);
}

/** Exclui um layout que nunca gerou arquivo (204). Com geração: 409 `LAYOUT_EM_USO`. */
export async function deleteExportLayout(layoutId: string): Promise<void> {
  await apiDelete<void>(`${BASE}/${encodeURIComponent(layoutId)}`);
}
