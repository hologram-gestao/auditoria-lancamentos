/**
 * Schemas Zod do módulo auth — espelham `LoginRequest` em
 * `apps/api/app/modules/auth/schemas.py`.
 */
import { z } from 'zod';

export const loginSchema = z.object({
  // `trim` ANTES de validar e de enviar (86e2n39eg): e-mail colado costuma vir
  // com espaço no fim, e isso virava "E-mail ou senha incorretos", que manda a
  // pessoa desconfiar da senha. Só espaço conta como vazio.
  email: z.string().trim().min(1, 'Informe seu e-mail.').email('E-mail inválido.'),
  password: z.string().min(1, 'Informe sua senha.').max(128, 'Senha muito longa.'),
});

export type LoginFormValues = z.infer<typeof loginSchema>;
