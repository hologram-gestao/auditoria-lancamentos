/**
 * Schemas Zod do módulo clients — espelham
 * `apps/api/app/modules/clients/schemas.py` (CreateClientRequest /
 * UpdateClientRequest / TestConnectionRequest).
 *
 * O update tem um refinement extra: se UMA credencial vier preenchida, a outra
 * também precisa vir. O backend já retorna 400 `IncompleteCredentialsError`
 * nesse caso (Doc §9.3), mas a validação client-side dá feedback imediato.
 */
import { z } from 'zod';

const omieKeyField = z
  .string()
  .min(1, 'Informe a App Key Omie.')
  .max(200, 'App Key muito longa (máx. 200).');

const omieSecretField = z
  .string()
  .min(1, 'Informe a App Secret Omie.')
  .max(200, 'App Secret muito longo (máx. 200).');

export const createClientSchema = z.object({
  name: z.string().min(1, 'Informe o nome do cliente.').max(200, 'Nome muito longo (máx. 200).'),
  omie_app_key: omieKeyField,
  omie_app_secret: omieSecretField,
});

export type CreateClientFormValues = z.infer<typeof createClientSchema>;

export const updateClientSchema = z
  .object({
    name: z.string().min(1, 'Informe o nome do cliente.').max(200, 'Nome muito longo (máx. 200).'),
    active: z.enum(['active', 'inactive']),
    // Edição mostra placeholder `••••••••` e os campos começam vazios. Apenas
    // recriptografa se o usuário preencher. Ambos opcionais individualmente,
    // mas o refinement abaixo garante que vêm sempre juntos.
    omie_app_key: z.string().max(200, 'App Key muito longa (máx. 200).').optional().default(''),
    omie_app_secret: z
      .string()
      .max(200, 'App Secret muito longo (máx. 200).')
      .optional()
      .default(''),
    manager_id: z.string().uuid().optional(),
  })
  .superRefine((vals, ctx) => {
    const keyFilled = (vals.omie_app_key ?? '').length > 0;
    const secretFilled = (vals.omie_app_secret ?? '').length > 0;
    if (keyFilled && !secretFilled) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['omie_app_secret'],
        message: 'Preencha também a App Secret para atualizar as credenciais.',
      });
    }
    if (secretFilled && !keyFilled) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['omie_app_key'],
        message: 'Preencha também a App Key para atualizar as credenciais.',
      });
    }
  });

export type UpdateClientFormValues = z.infer<typeof updateClientSchema>;

export const testConnectionSchema = z.object({
  omie_app_key: omieKeyField,
  omie_app_secret: omieSecretField,
});
