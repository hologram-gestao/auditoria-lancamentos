/**
 * Schema Zod do "Criar a partir do modelo Domínio" — Sprint 13 (FRONT 13.5).
 *
 * Espelha `ExportLayoutFromTemplate` do backend: nome OPCIONAL (omitido = o
 * nome do modelo), até `MAX_EXPORT_LAYOUT_NAME_CHARS` (120), com trim; e a
 * organização de destino pela fábrica única (`organizationTargetField`) —
 * obrigatória para a plataforma, ausente para o admin.
 */
import { z } from 'zod';

import { organizationTargetField } from './organizations';

export const MAX_EXPORT_LAYOUT_NAME_LENGTH = 120;

export function makeExportLayoutFromTemplateSchema({ requireOrganization = false } = {}) {
  return z.object({
    name: z
      .string()
      .trim()
      .max(
        MAX_EXPORT_LAYOUT_NAME_LENGTH,
        `Nome muito longo (máx. ${MAX_EXPORT_LAYOUT_NAME_LENGTH}).`,
      ),
    organization_id: organizationTargetField(requireOrganization),
  });
}

export type ExportLayoutFromTemplateFormValues = z.infer<
  ReturnType<typeof makeExportLayoutFromTemplateSchema>
>;
