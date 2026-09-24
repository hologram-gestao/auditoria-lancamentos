/**
 * Schema Zod do formulário "Registrar contexto" (Sprint 15 — BACK 15.1),
 * espelhando `TitleContextCreateRequest`
 * (`apps/api/app/modules/client_titles/schemas.py`).
 *
 * `TITLE_CONTEXT_MAX_TEXT_CHARS` é a mesma constante do backend (2000 — teto
 * de `resolution_note` da revisão de anomalias, convenção da casa para texto
 * livre). A validação aqui é UX; a autoridade é o servidor.
 */
import { z } from 'zod';

import type { TitleContextType } from '@/lib/contracts';

export const TITLE_CONTEXT_MAX_TEXT_CHARS = 2000;

export const titleContextTypeSchema = z.enum([
  'acordo_de_pagamento',
  'pagamento_antecipado',
  'nota_a_cancelar',
  'cobranca_suspensa',
  'perda_provavel',
  'outro',
]);

export type TitleContextTypeFormValue = z.infer<typeof titleContextTypeSchema>;

/**
 * Trava de contrato bidirecional: tipo novo (ou renomeado) no backend derruba
 * a compilação aqui — o mesmo padrão do glossário (`lib/validation/glossary.ts`).
 */
type AssertSameUnion<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const typeUnionMatchesContract: AssertSameUnion<TitleContextTypeFormValue, TitleContextType> = true;
void typeUnionMatchesContract;

/** Rótulos PT-BR — fonte única para o select, o indicador e o histórico. */
export const TITLE_CONTEXT_TYPE_LABELS: Record<TitleContextTypeFormValue, string> = {
  acordo_de_pagamento: 'Acordo de pagamento',
  pagamento_antecipado: 'Pagamento antecipado',
  nota_a_cancelar: 'Nota a cancelar',
  cobranca_suspensa: 'Cobrança suspensa',
  perda_provavel: 'Perda provável',
  outro: 'Outro',
};

export const titleContextFormSchema = z.object({
  type: titleContextTypeSchema,
  text: z
    .string()
    .trim()
    .min(1, 'Descreva o contexto.')
    .max(TITLE_CONTEXT_MAX_TEXT_CHARS, `Texto muito longo (máx. ${TITLE_CONTEXT_MAX_TEXT_CHARS}).`),
});

export type TitleContextFormValues = z.infer<typeof titleContextFormSchema>;
