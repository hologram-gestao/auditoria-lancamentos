/**
 * Schema Zod do formulário de categoria de cliente — 86e34jd8m.
 *
 * Espelha `ClientCategoryCreate`/`ClientCategoryUpdate` do backend: nome 1–60
 * (normalizado com trim) e tom dentro do enum semântico.
 */
import { z } from 'zod';

import { organizationTargetField } from './organizations';

export const clientCategoryToneSchema = z.enum([
  'neutral',
  'primary',
  'info',
  'success',
  'warning',
]);

/**
 * Fábrica: o catálogo é POR organização (86e36ecqz) e só a plataforma escolhe
 * em qual criar (86e36ed1d). Na EDIÇÃO o campo não aparece — a categoria não
 * muda de organização, e o backend resolve o alvo por PK dentro do alcance.
 */
export function makeClientCategorySchema({ requireOrganization = false } = {}) {
  return z.object({
    name: z
      .string()
      .trim()
      .min(1, 'Informe o nome da categoria.')
      .max(60, 'Nome muito longo (máx. 60).'),
    tone: clientCategoryToneSchema,
    organization_id: organizationTargetField(requireOrganization),
  });
}

export type ClientCategoryFormValues = z.infer<ReturnType<typeof makeClientCategorySchema>>;
