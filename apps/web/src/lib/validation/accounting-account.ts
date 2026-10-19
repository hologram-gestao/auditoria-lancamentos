/**
 * Schema Zod da conta do plano contábil incluída ou editada À MÃO (86e3nb816).
 *
 * Espelha `apps/api/app/modules/client_accounting_chart/sheet.py` (`_validate_row`,
 * a MESMA regra da importação) e os tetos das colunas de
 * `app/db/models/client_accounting_account.py`, lidos antes de escrever:
 *   - `code`: obrigatório, até 20, letras, dígitos, `.` e `-`, começando e
 *     terminando com letra ou dígito (o código vai cru para o arquivo contábil);
 *   - `name`: obrigatório, até 200 (o servidor apara e recusa só-espaços);
 *   - `type`: `analitica` | `sintetica`;
 *   - `classification`: opcional, até 40 (decide a posição na lista);
 *   - `nature` (86e3qhhrr): `automatica` (derivada da classificação pelo servidor) ou
 *     `devedora`/`credora` escolhida à mão (a reimportação passa a respeitá-la).
 *
 * É UX, não barreira: o servidor revalida tudo e ainda recusa o que só ele sabe
 * (código repetido, conta em uso).
 */
import { z } from 'zod';

import type { AccountingAccountNature, AccountingAccountType } from '@/lib/contracts';

export const ACCOUNT_MAX_CODE_CHARS = 20;
export const ACCOUNT_MAX_NAME_CHARS = 200;
export const ACCOUNT_MAX_CLASSIFICATION_CHARS = 40;

/** O `_CODE_PATTERN` do servidor, ancorado. */
const CODE_PATTERN = /^[0-9A-Za-z](?:[0-9A-Za-z.-]*[0-9A-Za-z])?$/;

export const accountTypeSchema = z.enum(['analitica', 'sintetica']);

type AssertSameUnion<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const typeUnionMatchesContract: AssertSameUnion<
  z.infer<typeof accountTypeSchema>,
  AccountingAccountType
> = true;
void typeUnionMatchesContract;

/** A natureza escolhida à mão, como o contrato a chama. */
export const accountNatureSchema = z.enum(['devedora', 'credora']);

const natureUnionMatchesContract: AssertSameUnion<
  z.infer<typeof accountNatureSchema>,
  AccountingAccountNature
> = true;
void natureUnionMatchesContract;

/** O campo do formulário: `automatica` não vai para o servidor como valor. */
export const NATURE_AUTOMATIC = 'automatica';
export const accountNatureFieldSchema = z.enum([NATURE_AUTOMATIC, 'devedora', 'credora']);

export const accountingAccountSchema = z.object({
  code: z
    .string()
    .trim()
    .min(1, 'Informe o código reduzido.')
    .max(ACCOUNT_MAX_CODE_CHARS, `Até ${ACCOUNT_MAX_CODE_CHARS} caracteres.`)
    .regex(
      CODE_PATTERN,
      'Use letras, números, ponto e hífen, começando e terminando com letra ou número.',
    ),
  name: z
    .string()
    .trim()
    .min(1, 'Informe o nome da conta.')
    .max(ACCOUNT_MAX_NAME_CHARS, `Até ${ACCOUNT_MAX_NAME_CHARS} caracteres.`),
  type: accountTypeSchema,
  classification: z
    .string()
    .trim()
    .max(ACCOUNT_MAX_CLASSIFICATION_CHARS, `Até ${ACCOUNT_MAX_CLASSIFICATION_CHARS} caracteres.`),
  active: z.boolean(),
  nature: accountNatureFieldSchema,
});

export type AccountingAccountFormValues = z.infer<typeof accountingAccountSchema>;
