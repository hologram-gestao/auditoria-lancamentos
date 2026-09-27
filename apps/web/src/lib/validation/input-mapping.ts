/**
 * Schema Zod do editor de MAPEAMENTO DE ENTRADA (Sprint 14 — FRONT 14.5),
 * espelhando `apps/api/app/modules/client_input_mappings/schemas.py`
 * (`InputMappingFields._coherent`) e os CHECKs de `client_input_mappings` 1:1.
 *
 * A validação aqui é UX; a autoridade é o servidor. Mas ali a forma inválida é
 * o **400 `VALIDATION_ERROR` genérico** (§4.8), sem ecoar campo nem mensagem —
 * então é AQUI que a pessoa descobre o que falta. As regras espelhadas:
 *
 *   - `csv` exige delimitador e codificação; `xlsx` não os aceita (a conversão
 *     para o corpo já os zera, então aqui só se exige em `csv`);
 *   - `classificacao_livre` exige a coluna de categoria;
 *   - a convenção de sinal é OBRIGATÓRIA — nunca inferida (invariante do PRD);
 *   - `valor_com_sinal` e `coluna_natureza` exigem a coluna de valor;
 *     `coluna_natureza` exige ainda a coluna de natureza e os dois literais,
 *     DIFERENTES entre si; `colunas_separadas` exige as duas colunas,
 *     DIFERENTES entre si, e dispensa a coluna de valor;
 *   - nomes de coluna e literais têm os tetos do banco
 *     (`MAX_INPUT_COLUMN_NAME_CHARS`, `MAX_NATURE_LITERAL_CHARS`).
 *
 * Os enums são travados contra o contrato pelo `AssertSameUnion` (mesmo padrão
 * de `client-mapping.ts`): valor novo no backend derruba a compilação aqui.
 */
import { z } from 'zod';

import type {
  CategoryMode,
  CsvDelimiter,
  DecimalSeparator,
  InputDateFormat,
  InputEncoding,
  InputFileFormat,
  SignConvention,
} from '@/lib/contracts';
import type { InputMappingFormValues } from '@/lib/input-mapping';

/** Espelho de `MAX_INPUT_COLUMN_NAME_CHARS` (backend, `db/models/client_input_mapping.py`). */
export const MAX_INPUT_COLUMN_NAME_CHARS = 100;
/** Espelho de `MAX_NATURE_LITERAL_CHARS`. */
export const MAX_NATURE_LITERAL_CHARS = 30;

type AssertSameUnion<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;

export const fileFormatSchema = z.enum(['csv', 'xlsx']);
export const csvDelimiterSchema = z.enum([';', ',', '|']);
export const encodingSchema = z.enum(['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']);
export const dateFormatSchema = z.enum(['dd/mm/yyyy', 'dd-mm-yyyy', 'yyyy-mm-dd', 'dd/mm/yy']);
export const decimalSeparatorSchema = z.enum([',', '.']);
export const signConventionSchema = z.enum([
  'valor_com_sinal',
  'coluna_natureza',
  'colunas_separadas',
]);
export const categoryModeSchema = z.enum(['coluna_categoria', 'classificacao_livre']);

const fileFormatMatches: AssertSameUnion<z.infer<typeof fileFormatSchema>, InputFileFormat> = true;
const delimiterMatches: AssertSameUnion<z.infer<typeof csvDelimiterSchema>, CsvDelimiter> = true;
const encodingMatches: AssertSameUnion<z.infer<typeof encodingSchema>, InputEncoding> = true;
const dateFormatMatches: AssertSameUnion<z.infer<typeof dateFormatSchema>, InputDateFormat> = true;
const decimalMatches: AssertSameUnion<
  z.infer<typeof decimalSeparatorSchema>,
  DecimalSeparator
> = true;
const signMatches: AssertSameUnion<z.infer<typeof signConventionSchema>, SignConvention> = true;
const categoryModeMatches: AssertSameUnion<z.infer<typeof categoryModeSchema>, CategoryMode> = true;
void fileFormatMatches;
void delimiterMatches;
void encodingMatches;
void dateFormatMatches;
void decimalMatches;
void signMatches;
void categoryModeMatches;

const columnName = z
  .string()
  .trim()
  .max(
    MAX_INPUT_COLUMN_NAME_CHARS,
    `Nome de coluna longo demais (máx. ${MAX_INPUT_COLUMN_NAME_CHARS}).`,
  );

const natureLiteral = z
  .string()
  .trim()
  .max(MAX_NATURE_LITERAL_CHARS, `Literal longo demais (máx. ${MAX_NATURE_LITERAL_CHARS}).`);

export const inputMappingFormSchema = z
  .object({
    fileFormat: fileFormatSchema,
    csvDelimiter: csvDelimiterSchema.or(z.literal('')),
    encoding: encodingSchema.or(z.literal('')),
    dateColumn: columnName.min(1, 'Escolha a coluna da data.'),
    descriptionColumn: columnName.min(1, 'Escolha a coluna da descrição.'),
    amountColumn: columnName,
    categoryColumn: columnName,
    categoryMode: categoryModeSchema,
    accountColumn: columnName,
    documentColumn: columnName,
    dateFormat: dateFormatSchema,
    decimalSeparator: decimalSeparatorSchema,
    signConvention: signConventionSchema.or(z.literal('')),
    natureColumn: columnName,
    debitValue: natureLiteral,
    creditValue: natureLiteral,
    debitColumn: columnName,
    creditColumn: columnName,
  })
  .superRefine((values, ctx) => {
    const issue = (path: keyof InputMappingFormValues, message: string) =>
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: [path], message });

    // Espelho do CHECK `csv_coherent`.
    if (values.fileFormat === 'csv') {
      if (values.csvDelimiter === '') issue('csvDelimiter', 'Declare o delimitador do CSV.');
      if (values.encoding === '') issue('encoding', 'Declare a codificação do CSV.');
    }

    // Espelho do CHECK `category_coherent`.
    if (values.categoryMode === 'classificacao_livre' && values.categoryColumn === '') {
      issue(
        'categoryColumn',
        'A classificação livre precisa de uma coluna de origem para a categoria.',
      );
    }

    // Espelho do CHECK `sign_coherent` — e do invariante "sinal não se infere".
    switch (values.signConvention) {
      case '':
        issue('signConvention', 'Declare como o arquivo indica débito e crédito.');
        break;
      case 'valor_com_sinal':
        if (values.amountColumn === '') issue('amountColumn', 'Escolha a coluna do valor.');
        break;
      case 'coluna_natureza':
        if (values.amountColumn === '') issue('amountColumn', 'Escolha a coluna do valor.');
        if (values.natureColumn === '') {
          issue('natureColumn', 'Escolha a coluna que diz se a linha é débito ou crédito.');
        }
        if (values.debitValue === '') issue('debitValue', 'Informe o texto que significa débito.');
        if (values.creditValue === '') {
          issue('creditValue', 'Informe o texto que significa crédito.');
        }
        if (
          values.debitValue !== '' &&
          values.creditValue !== '' &&
          values.debitValue === values.creditValue
        ) {
          issue('creditValue', 'Os textos de débito e de crédito precisam ser diferentes.');
        }
        break;
      case 'colunas_separadas':
        if (values.debitColumn === '') issue('debitColumn', 'Escolha a coluna de débito.');
        if (values.creditColumn === '') issue('creditColumn', 'Escolha a coluna de crédito.');
        if (
          values.debitColumn !== '' &&
          values.creditColumn !== '' &&
          values.debitColumn === values.creditColumn
        ) {
          issue('creditColumn', 'As colunas de débito e de crédito precisam ser diferentes.');
        }
        break;
    }
  });

/**
 * Trava: o schema e o tipo do formulário (`lib/input-mapping.ts`) são a mesma
 * forma — campo novo num deles derruba a compilação no outro.
 */
const formMatches: AssertSameUnion<
  keyof z.infer<typeof inputMappingFormSchema>,
  keyof InputMappingFormValues
> = true;
void formMatches;
