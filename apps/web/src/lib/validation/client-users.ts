/**
 * Schemas Zod dos usuários DO CLIENTE — espelham
 * `apps/api/app/modules/users/schemas.py`
 * (`CreateClientUserRequest` / `UpdateClientUserRequest`) 1:1.
 *
 * Limites copiados do backend, campo a campo:
 *   - `name`: 1..150
 *   - `email`: EmailStr
 *   - `password`: 10..128 — o mínimo de 10 é da Sprint 5 (`CLIENT_USER_MIN_
 *     PASSWORD_LENGTH`), MAIS estrito que os 8 do usuário do sistema. Errar
 *     para menos aqui devolveria um 422 do servidor que o form não previu.
 *   - `role`: apenas `client_manager`/`client_operator` — a whitelist que
 *     impede escalação. O union é derivado do contrato (`ClientUserRole`), e o
 *     `satisfies` abaixo faz o `tsc` reclamar se o backend mudar os valores.
 */
import { z } from 'zod';

import type { ClientUserRole } from '@/lib/contracts';

/** Mesma constante do backend (`CLIENT_USER_MIN_PASSWORD_LENGTH`). */
export const CLIENT_USER_MIN_PASSWORD_LENGTH = 10;

export const clientUserRoleSchema = z.enum(['client_manager', 'client_operator']);

export type ClientUserRoleFormValue = z.infer<typeof clientUserRoleSchema>;

/**
 * Trava de contrato bidirecional: papel novo (ou renomeado) no backend derruba
 * a compilação aqui. A checagem só de atribuição não bastaria — ela deixaria
 * passar um papel do contrato que o formulário esqueceu de oferecer.
 */
type AssertSameUnion<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const roleUnionMatchesContract: AssertSameUnion<ClientUserRoleFormValue, ClientUserRole> = true;
void roleUnionMatchesContract;

/** Rótulos PT-BR de cada papel — fonte única para select, tabela e badges. */
export const CLIENT_USER_ROLE_LABELS: Record<ClientUserRoleFormValue, string> = {
  client_manager: 'Gerente do cliente',
  client_operator: 'Operador do cliente',
};

const nameField = z
  .string()
  .trim()
  .min(1, 'Informe o nome.')
  .max(150, 'Nome muito longo (máx. 150).');

const emailField = z.string().trim().min(1, 'Informe o e-mail.').email('E-mail inválido.');

export const createClientUserSchema = z.object({
  name: nameField,
  email: emailField,
  password: z
    .string()
    .min(
      CLIENT_USER_MIN_PASSWORD_LENGTH,
      `A senha precisa ter pelo menos ${CLIENT_USER_MIN_PASSWORD_LENGTH} caracteres.`,
    )
    .max(128, 'Senha muito longa (máx. 128).'),
  role: clientUserRoleSchema,
});

export type CreateClientUserFormValues = z.infer<typeof createClientUserSchema>;

/**
 * Edição não mexe em senha (não há reset por autoatendimento no MVP) nem em
 * `active` (isso é ativar/desativar, endpoint dedicado).
 */
export const updateClientUserSchema = z.object({
  name: nameField,
  email: emailField,
  role: clientUserRoleSchema,
});

export type UpdateClientUserFormValues = z.infer<typeof updateClientUserSchema>;
