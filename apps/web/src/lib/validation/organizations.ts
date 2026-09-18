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

/**
 * O campo "organização de destino" dos formulários de CRIAÇÃO (86e36ed1d).
 *
 * Escrito uma vez porque a regra é a mesma nos três formulários (cliente,
 * usuário, categoria) e vem de uma decisão única no backend
 * (`resolve_organization_for_creation`): a plataforma **escolhe**, e escolher é
 * obrigatório; o staff não escolhe nada — a organização vem da LINHA dele, e um
 * `organization_id` divergente no payload é 403, nunca ignorado.
 *
 * Por isso o campo é `optional()` para o staff (o formulário nem o monta) e
 * obrigatório para a plataforma, com a mensagem em português que o usuário lê
 * antes de o servidor precisar recusar.
 */
export function organizationTargetField(requireOrganization: boolean) {
  return requireOrganization
    ? z.string().uuid('Escolha a organização de destino.')
    : z.string().optional();
}
