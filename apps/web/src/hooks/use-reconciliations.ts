/**
 * Hooks de TanStack Query para o módulo reconciliations.
 *
 * S8 (FRONT 6.1): apenas `useCheckDuplicate`. Hooks de listagem/criação
 * entram em S9+.
 *
 * Por que `useMutation` e não `useQuery`:
 *   A checagem é on-demand (acionada ao clicar "Processar"), depende de um
 *   hash que só existe após cálculo client-side e não deve ficar em cache —
 *   se o usuário trocar de arquivo, queremos recalcular sem prefetch
 *   acidental do TanStack.
 */
import { useMutation } from '@tanstack/react-query';

import {
  checkDuplicate,
  type CheckDuplicateParams,
  type CheckDuplicateResult,
} from '@/lib/api/reconciliations';

export function useCheckDuplicate() {
  return useMutation<CheckDuplicateResult, Error, CheckDuplicateParams>({
    mutationFn: checkDuplicate,
  });
}
