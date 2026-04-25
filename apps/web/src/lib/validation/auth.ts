/**
 * Schemas Zod do módulo auth — espelham `LoginRequest` em
 * `apps/api/app/modules/auth/schemas.py`.
 */
import { z } from 'zod';

export const loginSchema = z.object({
  email: z.string().min(1, 'Informe seu e-mail.').email('E-mail inválido.'),
  password: z.string().min(1, 'Informe sua senha.').max(128, 'Senha muito longa.'),
});

export type LoginFormValues = z.infer<typeof loginSchema>;
