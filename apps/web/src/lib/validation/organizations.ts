/**
 * Schema Zod do formulário de organização — espelha
 * `apps/api/app/modules/organizations/schemas.py` (`OrganizationCreate`).
 *
 * O `max` vem de `MAX_ORGANIZATION_NAME_CHARS` (120) no backend: é o tamanho
 * que cabe numa linha de tabela e num seletor. O `trim` evita que " Prospecta "
 * e "Prospecta" virem duas organizações diferentes na leitura de quem cria — a
 * unicidade sem caixa é do servidor.
 */
import { z } from 'zod';

export const MAX_ORGANIZATION_NAME_LENGTH = 120;

export const organizationSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Informe o nome da organização.')
    .max(MAX_ORGANIZATION_NAME_LENGTH, `Nome muito longo (máx. ${MAX_ORGANIZATION_NAME_LENGTH}).`),
});

export type OrganizationFormValues = z.infer<typeof organizationSchema>;
