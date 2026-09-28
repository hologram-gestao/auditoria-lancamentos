/**
 * Hooks de TanStack Query do plano de contas CONTÁBIL do cliente e da conta do
 * banco (Sprint 16 — BACK 16.1 · 16.3).
 *
 * Convenções (as de `use-client-chart-of-accounts`):
 *   - query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir o plano do tenant anterior do cache;
 *   - `keepPreviousData` na lista evita o flash da tabela ao paginar/filtrar;
 *   - importar invalida a árvore INTEIRA do plano E as contas de origem (o nome
 *     da conta do banco associada pode ter mudado), e o de-para do cliente
 *     (a prévia do `conta_contabil` lê código e nome do plano);
 *   - associar a conta do banco atualiza as contas de origem e o de-para (a
 *     prévia lista as contas de origem pendentes).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  importAccountingChart,
  listAccountingChart,
  listSourceAccounts,
  setSourceAccountBinding,
  type ListAccountingChartParams,
} from '@/lib/api/client-accounting-chart';
import type {
  AccountingChartImportResult,
  AccountingChartListResponse,
  SourceAccountBindingPayload,
  SourceAccountBindingRequest,
  SourceAccountEntry,
} from '@/lib/contracts';

import { clientMappingKeys } from './use-client-mapping';

export const accountingChartKeys = {
  all: (clientId: string) => ['accounting-chart', clientId] as const,
  list: (clientId: string, params: ListAccountingChartParams) =>
    ['accounting-chart', clientId, 'list', params] as const,
  sourceAccounts: (clientId: string) => ['accounting-chart', clientId, 'source-accounts'] as const,
};

export function useAccountingChartList(
  clientId: string,
  params: ListAccountingChartParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<AccountingChartListResponse>({
    queryKey: accountingChartKeys.list(clientId, params),
    queryFn: () => listAccountingChart(clientId, params),
    placeholderData: keepPreviousData,
    enabled: clientId.length > 0 && (options.enabled ?? true),
  });
}

export function useImportAccountingChart(clientId: string) {
  const qc = useQueryClient();
  return useMutation<AccountingChartImportResult, Error, File>({
    mutationFn: (file) => importAccountingChart(clientId, file),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: accountingChartKeys.all(clientId) });
      void qc.invalidateQueries({ queryKey: clientMappingKeys.all(clientId) });
    },
  });
}

export function useSourceAccounts(clientId: string, options: { enabled?: boolean } = {}) {
  return useQuery<SourceAccountEntry[]>({
    queryKey: accountingChartKeys.sourceAccounts(clientId),
    queryFn: () => listSourceAccounts(clientId),
    enabled: clientId.length > 0 && (options.enabled ?? true),
  });
}

export function useSetSourceAccountBinding(clientId: string) {
  const qc = useQueryClient();
  return useMutation<SourceAccountBindingPayload, Error, SourceAccountBindingRequest>({
    mutationFn: (payload) => setSourceAccountBinding(clientId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: accountingChartKeys.sourceAccounts(clientId) });
      void qc.invalidateQueries({ queryKey: clientMappingKeys.all(clientId) });
    },
  });
}
