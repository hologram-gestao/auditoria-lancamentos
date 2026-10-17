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
