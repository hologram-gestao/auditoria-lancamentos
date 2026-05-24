/**
 * Schemas Zod do CRUD de anomaly types — S15 FRONT 11.2.
 *
 * Espelham `apps/api/app/modules/anomaly_types/schemas.py`:
 *   - `code` regex `^[a-z][a-z0-9_]*$`, max 50.
 *   - `name` 1–150, `description` ≥ 1, `severity` enum.
 */
import { z } from 'zod';

export const anomalySeveritySchema = z.enum(['critical', 'moderate', 'info']);

const CODE_REGEX = /^[a-z][a-z0-9_]*$/;

export const createAnomalyTypeSchema = z.object({
  code: z
    .string()
    .min(1, 'Informe o código.')
    .max(50, 'Código muito longo (máx. 50).')
    .regex(CODE_REGEX, 'Use snake_case_lower (letras minúsculas, números e _).'),
  name: z.string().min(1, 'Informe o nome.').max(150, 'Nome muito longo (máx. 150).'),
  description: z.string().min(1, 'Informe a descrição.'),
  severity: anomalySeveritySchema,
});

export type CreateAnomalyTypeFormValues = z.infer<typeof createAnomalyTypeSchema>;

export const updateAnomalyTypeSchema = z.object({
  name: z.string().min(1, 'Informe o nome.').max(150, 'Nome muito longo (máx. 150).'),
  description: z.string().min(1, 'Informe a descrição.'),
  severity: anomalySeveritySchema,
});

export type UpdateAnomalyTypeFormValues = z.infer<typeof updateAnomalyTypeSchema>;
