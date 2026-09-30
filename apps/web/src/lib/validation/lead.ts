/**
 * Schema do formulário de contato da landing (86e3fr9vz).
 *
 * Os limites são os MESMOS do backend (`apps/api/app/db/models/lead.py`, que são os
 * das colunas). Validar aqui é conforto para quem digita; quem decide é o servidor
 * (§3.8), que devolve o 400 genérico para qualquer forma inválida.
 */
import { z } from 'zod';

import { contact } from '@/components/landing/content';

export const LEAD_NAME_MIN = 2;
export const LEAD_NAME_MAX = 120;
export const LEAD_EMAIL_MAX = 254;
export const LEAD_COMPANY_MAX = 120;
export const LEAD_WHATSAPP_MAX = 20;
export const LEAD_MESSAGE_MAX = 1000;

/** Mesmo padrão do backend: dígitos, `+`, espaço, parênteses e hífen. */
const WHATSAPP_PATTERN = /^[0-9+() -]+$/;

const optionalText = (max: number) => z.string().trim().max(max, contact.errors.tooLong(max));

export const leadSchema = z.object({
  name: z
    .string()
    .trim()
    .min(LEAD_NAME_MIN, contact.errors.name)
    .max(LEAD_NAME_MAX, contact.errors.tooLong(LEAD_NAME_MAX)),
  email: z
    .string()
    .trim()
    .min(1, contact.errors.email)
    .max(LEAD_EMAIL_MAX, contact.errors.tooLong(LEAD_EMAIL_MAX))
    .email(contact.errors.email),
  company: optionalText(LEAD_COMPANY_MAX),
  whatsapp: optionalText(LEAD_WHATSAPP_MAX).refine(
    (value) => value === '' || WHATSAPP_PATTERN.test(value),
    contact.errors.whatsapp,
  ),
  message: optionalText(LEAD_MESSAGE_MAX),
  consent: z.boolean().refine((value) => value, contact.errors.consent),
  // Honeypot: pessoa nenhuma vê nem preenche. Sem regra aqui de propósito: o bot
  // que o preenche precisa chegar ao servidor e receber a mesma resposta de sucesso.
  website: z.string(),
});

export type LeadFormValues = z.infer<typeof leadSchema>;

export const LEAD_FORM_DEFAULTS: LeadFormValues = {
  name: '',
  email: '',
  company: '',
  whatsapp: '',
  message: '',
  consent: false,
  website: '',
};
