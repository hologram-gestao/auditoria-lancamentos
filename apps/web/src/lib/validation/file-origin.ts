/**
 * Schema Zod do ENVIO do arquivo do mês (Sprint 14 — FRONT 14.6 / R2 · R5).
 *
 * O servidor valida a FORMA de `competence` (`YYYY-MM`) e de `declaredTotal`
 * (`^-?\d{1,12}([.,]\d{1,2})?$`) com o 400 genérico (§4.8) — sem dizer o que
 * está errado. Por isso os dois são conferidos AQUI, antes de enviar, com
 * mensagem. O arquivo em si é do servidor: a extensão é só conveniência (o
 * contêiner é decidido lá pelos magic bytes; PDF e HTML salvo como `.xls` são
 * recusados com motivo acionável, `FORMATO_NAO_SUPORTADO`).
 *
 * O total fica no estado como STRING DE CENTAVOS (`lib/money-input.ts`) — RAW
 * no estado, conversão só na borda (`centsToDecimalString`) e na exibição.
 */
import { z } from 'zod';

import { COMPETENCE_PATTERN } from '@/lib/competence';

/**
 * Extensões que o navegador deixa ESCOLHER e ENVIAR — fonte do `accept` do input
 * (`FILE_ACCEPT`), então os dois não divergem. O `.xls` é lido pelo servidor desde
 * 86e3n70p6 (é o que o Domínio grava); antes ele entrava aqui só para receber a
 * recusa tipada (86e3gkd50), porque fora do `accept` o seletor o escondia e quem
 * exportou do Domínio achava que o arquivo tinha sumido.
 */
export const SELECTABLE_FILE_EXTENSIONS = ['.csv', '.xlsx', '.xls'] as const;

export function hasSelectableExtension(fileName: string): boolean {
  const lower = fileName.toLowerCase();
  return SELECTABLE_FILE_EXTENSIONS.some((ext) => lower.endsWith(ext));
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
        message: 'Escolha o arquivo do mês (.csv, .xlsx ou .xls).',
      });
      return;
    }
    if (!hasSelectableExtension(file.name)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['file'],
        message: 'Envie o arquivo em CSV, XLSX ou XLS — PDF não tem colunas para mapear.',
      });
    }
  });

export type FileUploadFormValues = z.infer<typeof fileUploadFormSchema>;
