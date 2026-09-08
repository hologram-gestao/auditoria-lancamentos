/**
 * Schema Zod do formulário de categoria de cliente — 86e34jd8m.
 *
 * Espelha `ClientCategoryCreate`/`ClientCategoryUpdate` do backend: nome 1–60
 * (normalizado com trim) e tom dentro do enum semântico.
 */
import { z } from 'zod';

export const clientCategoryToneSchema = z.enum([
  'neutral',
  'primary',
  'info',
  'success',
  'warning',
]);

export const clientCategorySchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Informe o nome da categoria.')
    .max(60, 'Nome muito longo (máx. 60).'),
  tone: clientCategoryToneSchema,
});

export type ClientCategoryFormValues = z.infer<typeof clientCategorySchema>;
