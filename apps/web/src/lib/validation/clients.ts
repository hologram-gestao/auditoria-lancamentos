/**
 * Schemas Zod do módulo clients — espelham
 * `apps/api/app/modules/clients/schemas.py` (CreateClientRequest /
 * UpdateClientRequest / TestConnectionRequest).
 *
 * ⚠️ **Sprint 9 (R4/R5) mudou os dois formulários:**
 *   - na CRIAÇÃO a credencial virou opcional (o cliente é entidade plena sem
 *     origem); as duas continuam vindo **juntas ou nenhuma**, com a mensagem
 *     do próprio backend (`IncompleteCredentialsError`, 400);
 *   - na EDIÇÃO a credencial **saiu**: `PATCH /clients/{id}` agora responde 422
 *     apontando as rotas de conexão. Manter os campos aqui seria oferecer um
 *     caminho que o servidor recusa.
 */
import { z } from 'zod';

import { organizationTargetField } from './organizations';

const omieKeyField = z
  .string()
  .min(1, 'Informe a App Key Omie.')
  .max(200, 'App Key muito longa (máx. 200).');

const omieSecretField = z
  .string()
  .min(1, 'Informe a App Secret Omie.')
  .max(200, 'App Secret muito longo (máx. 200).');

/**
 * VERBATIM de `IncompleteCredentialsError.default_user_message`
 * (`apps/api/app/core/exceptions.py:436`) — é o texto que o usuário leria se
 * postasse direto na API. Duas frases diferentes para a mesma regra fariam
 * parecer duas regras.
 */
export const INCOMPLETE_CREDENTIALS_MESSAGE =
  'Para atualizar as credenciais, envie tanto a App Key quanto o App Secret.';

/**
 * Fábrica em vez de constante porque um campo depende de QUEM está criando
 * (86e36ed1d): a plataforma precisa escolher a organização de destino, o staff
 * não vê o campo. O resto do formulário é idêntico nos dois casos.
 *
 * S9: as credenciais são **opcionais** — sem elas o cliente nasce pleno e sem
 * origem. Preencher UMA só é o mesmo 400 do backend, antecipado aqui.
 */
export function makeCreateClientSchema({ requireOrganization = false } = {}) {
  return z
    .object({
      name: z
        .string()
        .min(1, 'Informe o nome do cliente.')
        .max(200, 'Nome muito longo (máx. 200).'),
      omie_app_key: z.string().max(200, 'App Key muito longa (máx. 200).').optional().default(''),
      omie_app_secret: z
        .string()
        .max(200, 'App Secret muito longo (máx. 200).')
        .optional()
        .default(''),
      // 86e34jd8m — id do catálogo ou a sentinela 'none' (o Select não aceita '').
      category_id: z.string().optional(),
      organization_id: organizationTargetField(requireOrganization),
    })
    .superRefine((vals, ctx) => {
      const keyFilled = (vals.omie_app_key ?? '').trim().length > 0;
      const secretFilled = (vals.omie_app_secret ?? '').trim().length > 0;
      if (keyFilled && !secretFilled) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['omie_app_secret'],
          message: INCOMPLETE_CREDENTIALS_MESSAGE,
        });
      }
      if (secretFilled && !keyFilled) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['omie_app_key'],
          message: INCOMPLETE_CREDENTIALS_MESSAGE,
        });
      }
    });
}

export type CreateClientFormValues = z.infer<ReturnType<typeof makeCreateClientSchema>>;

/**
 * Edição do cliente — **sem credencial desde a S9 (R5)**.
 *
 * `omie_app_key`/`omie_app_secret` saíram do schema porque saíram da rota: o
 * `PATCH /clients/{id}` responde 422 só pela PRESENÇA da chave no corpo,
 * apontando `POST/PATCH /clients/{id}/connections`. Uma segunda via de escrita
 * gravaria na coluna antiga enquanto a leitura vem da conexão — e o sistema
 * autenticaria com a credencial velha em silêncio.
 */
export const updateClientSchema = z.object({
  name: z.string().min(1, 'Informe o nome do cliente.').max(200, 'Nome muito longo (máx. 200).'),
  active: z.enum(['active', 'inactive']),
  // A carteira (responsável e colaboradores) NÃO é campo do formulário desde a
  // 86e390m4c: é gerida na seção "Gerentes com acesso", ação a ação.
  // 86e34jd8m — id do catálogo ou a sentinela 'none'.
  category_id: z.string().optional(),
});

export type UpdateClientFormValues = z.infer<typeof updateClientSchema>;

export const testConnectionSchema = z.object({
  omie_app_key: omieKeyField,
  omie_app_secret: omieSecretField,
});
