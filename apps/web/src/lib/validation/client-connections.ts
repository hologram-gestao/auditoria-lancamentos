/**
 * Schemas Zod das origens do cliente (Sprint 9 / R3 · R5; Sprint 14 / R1).
 *
 * Espelham `apps/api/app/modules/client_connections/schemas.py` 1:1:
 *   - rótulo com `.trim()` antes do `min(1)` — o backend usa `_clean_label`, e
 *     `"   "` é 400 lá; aceitar aqui só empurraria o erro para o servidor;
 *   - teto de 100 caracteres = `MAX_CONNECTION_LABEL_CHARS`
 *     (`db/models/client_connection.py:97`);
 *   - na EDIÇÃO os dois blocos são independentes (renomear sem mexer na
 *     credencial e vice-versa), mas as duas credenciais vêm **juntas ou
 *     nenhuma**: a cifra é do mapa inteiro, então não existe patch de uma chave
 *     só (o backend diz isso na docstring do `UpdateConnectionRequest`).
 *   - **credencial obrigatória para quem a tem, proibida para quem não tem**
 *     (`assert_credentials_shape`, S14): a origem `arquivo` nasce sem segredo,
 *     então App Key/Secret só são exigidas quando o TIPO escolhido as usa —
 *     decidido por `providerRequiresCredentials`, a tabela única do front.
 *
 * O rótulo é `optional` na criação porque o backend usa o padrão do tipo
 * ("Omie", "Arquivo") quando o cliente ainda não tem conexão daquele tipo. A
 * partir da segunda ele é obrigatório — e quem sabe disso é o servidor (409 de
 * par repetido); a tela sugere o padrão em vez de adivinhar a regra.
 */
import { z } from 'zod';

import { providerRequiresCredentials } from '@/lib/api/client-connections';

/** Espelho de `MAX_CONNECTION_LABEL_CHARS` (backend). */
export const MAX_CONNECTION_LABEL_CHARS = 100;

const labelField = z
  .string()
  .trim()
  .max(MAX_CONNECTION_LABEL_CHARS, `Rótulo muito longo (máx. ${MAX_CONNECTION_LABEL_CHARS}).`);

const appKeyField = z.string().trim().max(200, 'App Key muito longa (máx. 200).');

const appSecretField = z.string().trim().max(200, 'App Secret muito longo (máx. 200).');

/**
 * Criação: a credencial é obrigatória quando o tipo a exige (é ela que a origem
 * existe para guardar) e fica de fora quando não (`arquivo`).
 */
export const createConnectionSchema = z
  .object({
    provider_type: z.string().min(1, 'Selecione o tipo de origem.'),
    label: labelField.optional().default(''),
    app_key: appKeyField.optional().default(''),
    app_secret: appSecretField.optional().default(''),
  })
  .superRefine((vals, ctx) => {
    if (!providerRequiresCredentials(vals.provider_type)) return;
    if ((vals.app_key ?? '').length === 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['app_key'],
        message: 'Informe a App Key Omie.',
      });
    }
    if ((vals.app_secret ?? '').length === 0) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['app_secret'],
        message: 'Informe a App Secret Omie.',
      });
    }
  });

export type CreateConnectionFormValues = z.infer<typeof createConnectionSchema>;

/**
 * Edição: tudo opcional, mas o formulário precisa pedir ALGO — o backend
 * responde 400 a corpo vazio, e um "Salvar" que não salva nada é engano de quem
 * clicou, não no-op silencioso.
 */
export const updateConnectionSchema = z
  .object({
    label: labelField.optional().default(''),
    app_key: appKeyField.optional().default(''),
    app_secret: appSecretField.optional().default(''),
  })
  .superRefine((vals, ctx) => {
    const keyFilled = (vals.app_key ?? '').length > 0;
    const secretFilled = (vals.app_secret ?? '').length > 0;
    if (keyFilled && !secretFilled) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['app_secret'],
        message: 'Preencha também a App Secret para trocar as credenciais.',
      });
    }
    if (secretFilled && !keyFilled) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['app_key'],
        message: 'Preencha também a App Key para trocar as credenciais.',
      });
    }
  });

export type UpdateConnectionFormValues = z.infer<typeof updateConnectionSchema>;
