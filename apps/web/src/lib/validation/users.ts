/**
 * Schemas Zod do módulo users — espelham
 * `apps/api/app/modules/users/schemas.py` (CreateUserRequest / UpdateUserRequest).
 */
import { z } from 'zod';

export const userRoleSchema = z.enum(['admin', 'manager']);

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
