/**
 * Schemas Zod dos formulários do DE-PARA (Sprint 12 — FRONT 12.7), espelhando
 * `apps/api/app/modules/client_mapping/schemas.py` e `portability.py`.
 *
 * A validação aqui é UX; a autoridade é o servidor. As regras espelhadas:
 *   - `alvo` EXIGE `targetCode`; `nao_mapear` NÃO aceita (validador do modelo);
 *   - competência `YYYY-MM` (`COMPETENCE_PATTERN` do backend);
 *   - importação: `.xlsx` de até 2 MB (`MAX_IMPORT_BYTES`), a mesma frase do
 *     `userMessage` do servidor para o arquivo inválido.
 */
import { z } from 'zod';

import { COMPETENCE_PATTERN } from '@/lib/competence';
import type { MappingDecisionType, MappingImportRejectReason } from '@/lib/contracts';

/** `MAX_IMPORT_BYTES` do backend (2 MB). */
export const MAPPING_IMPORT_MAX_BYTES = 2 * 1024 * 1024;
/** `MAX_IMPORT_ROWS` do backend — só entra na mensagem. */
export const MAPPING_IMPORT_MAX_ROWS = 2000;

export const competenceSchema = z
  .string()
  .regex(COMPETENCE_PATTERN, 'Escolha a competência de início (mês e ano).');

export const mappingDecisionTypeSchema = z.enum(['alvo', 'nao_mapear']);

/**
 * Trava de contrato bidirecional: decisão nova (ou renomeada) no backend
 * derruba a compilação aqui — o mesmo padrão de `title-context.ts`.
 */
type AssertSameUnion<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const decisionUnionMatchesContract: AssertSameUnion<
  z.infer<typeof mappingDecisionTypeSchema>,
  MappingDecisionType
> = true;
void decisionUnionMatchesContract;

export const mappingDecisionFormSchema = z
  .object({
    decision: mappingDecisionTypeSchema,
    targetCode: z.string(),
    effectiveFrom: competenceSchema,
  })
  .superRefine((values, ctx) => {
    if (values.decision === 'alvo' && values.targetCode.trim() === '') {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['targetCode'],
        message: 'Escolha um alvo do catálogo ou marque "Não mapear".',
      });
    }
  });

export type MappingDecisionFormValues = z.infer<typeof mappingDecisionFormSchema>;

export const mappingVigenciaFormSchema = z.object({ effectiveFrom: competenceSchema });

export type MappingVigenciaFormValues = z.infer<typeof mappingVigenciaFormSchema>;

const INVALID_FILE_MESSAGE = `Envie a planilha .xlsx exportada do de-para (até ${
  MAPPING_IMPORT_MAX_BYTES / (1024 * 1024)
} MB e ${MAPPING_IMPORT_MAX_ROWS} linhas).`;

/**
 * `superRefine` no objeto (e não `.refine` no campo) de propósito: o formulário
 * nasce com `file: null` (nada escolhido), e o TS ≥ 5.5 infere predicado de tipo
 * de um `refine((v) => v instanceof File)` — estreitaria o campo para `File` e o
 * `null` inicial deixaria de compilar.
 */
export const mappingImportFormSchema = z
  .object({
    file: z.custom<File | null>(
      (value) => value === null || (typeof File !== 'undefined' && value instanceof File),
    ),
    effectiveFrom: competenceSchema,
  })
  .superRefine((values, ctx) => {
    const file = values.file;
    if (!(file instanceof File)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['file'],
        message: 'Escolha a planilha do de-para.',
      });
      return;
    }
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > MAPPING_IMPORT_MAX_BYTES) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ['file'], message: INVALID_FILE_MESSAGE });
    }
  });

export type MappingImportFormValues = z.infer<typeof mappingImportFormSchema>;

/** Rótulos PT-BR dos motivos de recusa da importação — enum FECHADO do contrato. */
export const IMPORT_REJECT_REASON_LABELS: Record<MappingImportRejectReason, string> = {
  categoria_inexistente: 'Categoria inexistente no cliente',
  alvo_inexistente: 'Alvo inexistente ou inativo no catálogo',
  decisao_invalida: 'Decisão inválida',
  alvo_ausente: 'Decisão "alvo" sem código de alvo',
  alvo_nao_permitido: '"Não mapear" com código de alvo',
  destino_diferente: 'Linha de outro destino',
  linha_repetida: 'Categoria repetida na planilha',
  conflito_na_vigencia: 'Conflito com decisão da mesma vigência',
};
