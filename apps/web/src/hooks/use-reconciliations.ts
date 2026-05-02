/**
 * Hooks de TanStack Query para o módulo reconciliations.
 *
 * S8 (FRONT 6.1): `useCheckDuplicate`.
 * S9 (FRONT 7.2): `useParseStatement`.
 *
 * Por que `useMutation` e não `useQuery`:
 *   Tanto a checagem de duplicata quanto o parse são on-demand (acionados
 *   no submit do form), dependem de input que só existe após interação do
 *   usuário e não devem ficar em cache — se o usuário trocar de arquivo,
 *   queremos recalcular sem prefetch acidental do TanStack.
 */
import { useMutation } from '@tanstack/react-query';

import {
  checkDuplicate,
  parseStatement,
  type CheckDuplicateParams,
  type CheckDuplicateResult,
  type ParsedStatement,
  type ParseStatementParams,
} from '@/lib/api/reconciliations';

export function useCheckDuplicate() {
  return useMutation<CheckDuplicateResult, Error, CheckDuplicateParams>({
    mutationFn: checkDuplicate,
  });
}

export function useParseStatement() {
  return useMutation<ParsedStatement, Error, ParseStatementParams>({
    mutationFn: parseStatement,
  });
}
