/**
 * Schema Zod do ENVIO do arquivo do mês (Sprint 14 — FRONT 14.6 / R2 · R5).
 *
 * O servidor valida a FORMA de `competence` (`YYYY-MM`) e de `declaredTotal`
 * (`^-?\d{1,12}([.,]\d{1,2})?$`) com o 400 genérico (§4.8) — sem dizer o que
 * está errado. Por isso os dois são conferidos AQUI, antes de enviar, com
 * mensagem. O arquivo em si é do servidor: a extensão é só conveniência (PDF
 * é recusado lá com motivo acionável, `FORMATO_NAO_SUPORTADO`).
 *
 * O total fica no estado como STRING DE CENTAVOS (`lib/money-input.ts`) — RAW
 * no estado, conversão só na borda (`centsToDecimalString`) e na exibição.
 */
import { z } from 'zod';

import { COMPETENCE_PATTERN } from '@/lib/competence';

/** Extensões aceitas no navegador — o `accept` do input e esta lista são o mesmo par. */
const ACCEPTED_EXTENSIONS = ['.csv', '.xlsx'] as const;

export function hasAcceptedExtension(fileName: string): boolean {
  const lower = fileName.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/**
 * `superRefine` no objeto, não `.refine` no campo: o formulário nasce com
 * `file: null` e o TS ≥ 5.5 inferiria predicado de tipo de um
 * `refine((v) => v instanceof File)` (mesma lição de `client-mapping.ts`).
 */
export const fileUploadFormSchema = z
  .object({
    competence: z.string().regex(COMPETENCE_PATTERN, 'Escolha a competência (mês e ano).'),
    /** Centavos crus; `''` = não informado (o servidor não confere o total). */
    declaredTotalCents: z.string(),
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
        message: 'Escolha o arquivo do mês (.csv ou .xlsx).',
      });
      return;
    }
    if (!hasAcceptedExtension(file.name)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['file'],
        message: 'Envie o arquivo em CSV ou XLSX — PDF e XLS não têm colunas para mapear.',
      });
    }
  });

export type FileUploadFormValues = z.infer<typeof fileUploadFormSchema>;
