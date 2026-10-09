/**
 * Schemas Zod da tela "Plano contábil" (Sprint 16 — FRONT 16.5).
 *
 * A planilha é validada pelo SERVIDOR (tipo pelo conteúdo, cabeçalho, linha a
 * linha — recusas tipadas). Aqui só o que dá para dizer antes de enviar: que há
 * um arquivo e que a extensão é selecionável; o `accept` do input sai da mesma
 * lista (`hasSelectableExtension`, o da S14), com o `.xls` que o Domínio grava
 * (lido pelo servidor desde 86e3n70p6).
 */
import { z } from 'zod';

import { hasSelectableExtension } from './file-origin';

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
        message: 'Escolha a planilha do plano contábil (.csv, .xlsx ou .xls).',
      });
      return;
    }
    if (!hasSelectableExtension(file.name)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['file'],
        message: 'Envie a planilha em CSV (separado por ;), XLSX ou XLS.',
      });
    }
  });

export type AccountingChartImportValues = z.infer<typeof accountingChartImportSchema>;
