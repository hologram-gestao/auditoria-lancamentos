/**
 * Schema Zod da importação das relações de clientes e fornecedores do Domínio (86e3qhhrn).
 *
 * O relatório é validado pelo SERVIDOR (planilha pelo conteúdo, título, CNPJ da empresa,
 * cabeçalho, linha a linha). Aqui só o que dá para dizer antes de enviar: que há um
 * arquivo e que a extensão é selecionável — a MESMA lista do plano (`.csv`, `.xlsx`,
 * `.xls`): o CSV é recusado pelo servidor COM motivo, então aparece no seletor e é
 * enviado, nunca barrado em silêncio (86e3gkd50).
 */
import { z } from 'zod';

import { isValidCnpj } from '@/lib/cnpj';
import { isValidCpf } from '@/lib/cpf';

import { hasSelectableExtension } from './file-origin';

export const accountingPartiesImportSchema = z
  .object({
    file: z.custom<File | null>(
      (value) => value === null || (typeof File !== 'undefined' && value instanceof File),
    ),
  })
  .superRefine((values, ctx) => {
    const file = values.file;
    if (!(file instanceof File)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['file'],
        message: 'Escolha a relação exportada do Domínio (.xls ou .xlsx).',
      });
      return;
    }
    if (!hasSelectableExtension(file.name)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['file'],
        message: 'Envie a relação como o Domínio exporta: XLS ou XLSX.',
      });
    }
  });

export type AccountingPartiesImportValues = z.infer<typeof accountingPartiesImportSchema>;

/**
 * Teto do documento DIGITADO: o mesmo do servidor (`MAX_PARTY_DOCUMENT_INPUT_CHARS` em
 * `client_accounting_chart/schemas.py`, o CNPJ com máscara mais folga para espaços).
 */
export const PARTY_DOCUMENT_MAX_CHARS = 24;

/**
 * O documento digitado na gaveta "Vincular conta" (86e3tttaf): CNPJ (14 dígitos) ou CPF
 * (11), com ou sem máscara, pelo dígito verificador — espelho de
 * `parties.parse_typed_document` (que usa `cnpj.py`/`cpf.py`). O servidor confere de novo.
 */
export function isValidPartyDocument(raw: string): boolean {
  const digits = raw.replace(/\D/g, '');
  if (digits.length === 14) return isValidCnpj(raw);
  if (digits.length === 11) return isValidCpf(raw);
  return false;
}

export const partyLinkSchema = z.object({
  accountId: z
    .string({ required_error: 'Escolha a conta do plano contábil.' })
    .min(1, 'Escolha a conta do plano contábil.'),
  partyType: z.enum(['cliente', 'fornecedor']),
  document: z
    .string()
    .trim()
    .min(1, 'Informe o CNPJ ou o CPF.')
    .max(
      PARTY_DOCUMENT_MAX_CHARS,
      `O CNPJ ou CPF tem no máximo ${PARTY_DOCUMENT_MAX_CHARS} caracteres.`,
    )
    .refine(isValidPartyDocument, {
      message: 'CNPJ ou CPF inválido: confira os dígitos (14 para CNPJ, 11 para CPF).',
    }),
});

export type PartyLinkValues = z.infer<typeof partyLinkSchema>;
