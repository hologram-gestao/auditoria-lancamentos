/**
 * Schemas Zod da tela "Plano contábil" (Sprint 16 — FRONT 16.5).
 *
 * A planilha é validada pelo SERVIDOR (tipo pelo conteúdo, cabeçalho, linha a
 * linha — recusas tipadas). Aqui só o que dá para dizer antes de enviar: que há
 * um arquivo e que a extensão é das duas aceitas; o `accept` do input e esta
 * lista são o mesmo par (`hasAcceptedExtension`, o da S14).
 */
import { z } from 'zod';

import { hasAcceptedExtension } from './file-origin';

/** `superRefine` no objeto pelo motivo de `file-origin.ts` (predicado de tipo do TS ≥ 5.5). */
export const accountingChartImportSchema = z
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
        message: 'Escolha a planilha do plano contábil (.csv ou .xlsx).',
      });
      return;
    }
    if (!hasAcceptedExtension(file.name)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['file'],
        message: 'Envie a planilha em CSV (separado por ;) ou XLSX.',
      });
    }
  });

export type AccountingChartImportValues = z.infer<typeof accountingChartImportSchema>;
