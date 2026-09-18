/**
 * Schemas Zod do módulo users — espelham
 * `apps/api/app/modules/users/schemas.py` (CreateUserRequest / UpdateUserRequest).
 */
import { z } from 'zod';

import type { SystemUserRole } from '@/lib/contracts';

/**
 * A whitelist de papel do CONTRATO, virada em tupla para o `z.enum` — a lista
 * não é redigitada (86e36ecwa). O `Record<SystemUserRole, true>` é a trava de
 * exaustividade: papel novo na whitelist do backend quebra a compilação aqui
 * até alguém decidir o rótulo e a posição dele no formulário.
 */
const SYSTEM_USER_ROLE_SET: Record<SystemUserRole, true> = { admin: true, manager: true };

export const SYSTEM_USER_ROLES = Object.keys(SYSTEM_USER_ROLE_SET) as [
  SystemUserRole,
  ...SystemUserRole[],
];

export const userRoleSchema = z.enum(SYSTEM_USER_ROLES);

export const createUserSchema = z.object({
  name: z.string().min(1, 'Informe o nome.').max(150, 'Nome muito longo (máx. 150).'),
  email: z.string().min(1, 'Informe o e-mail.').email('E-mail inválido.'),
  password: z
    .string()
    .min(8, 'A senha precisa ter pelo menos 8 caracteres.')
    .max(128, 'Senha muito longa (máx. 128).'),
  role: userRoleSchema,
});

export type CreateUserFormValues = z.infer<typeof createUserSchema>;

export const updateUserSchema = z.object({
  name: z.string().min(1, 'Informe o nome.').max(150, 'Nome muito longo (máx. 150).'),
  email: z.string().min(1, 'Informe o e-mail.').email('E-mail inválido.'),
  role: userRoleSchema,
});

export type UpdateUserFormValues = z.infer<typeof updateUserSchema>;
