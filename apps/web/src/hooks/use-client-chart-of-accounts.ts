/**
 * Hooks de TanStack Query do plano de contas do cliente (Sprint 10 / R3).
 *
 * Convenções (as mesmas de `use-glossary` e `use-client-users`):
 *   - query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir o plano de contas do tenant anterior do cache;
 *   - `keepPreviousData` evita o flash da tabela ao paginar/filtrar;
 *   - a sincronização invalida a árvore INTEIRA daquele cliente: ela muda a
 *     lista E as cinco contagens, e uma cobertura velha ao lado de uma lista
 *     nova é pior do que um instante de carregamento.
 *
 * A cobertura é uma query SEPARADA da lista, espelhando as duas rotas: é a
 * pergunta "quanto do de-para já vem pronto", cuja resposta não pode mudar
 * conforme a página aberta.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getChartOfAccountsCoverage,
  listChartOfAccounts,
  syncChartOfAccounts,
  type ListChartOfAccountsParams,
} from '@/lib/api/client-chart-of-accounts';
import type { ChartOfAccountsCoverage, ChartOfAccountsListResponse } from '@/lib/contracts';

export const chartOfAccountsKeys = {
  all: (clientId: string) => ['chart-of-accounts', clientId] as const,
  list: (clientId: string, params: ListChartOfAccountsParams) =>
    ['chart-of-accounts', clientId, 'list', params] as const,
  coverage: (clientId: string) => ['chart-of-accounts', clientId, 'coverage'] as const,
};

export function useChartOfAccountsList(
  clientId: string,
  params: ListChartOfAccountsParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<ChartOfAccountsListResponse>({
    queryKey: chartOfAccountsKeys.list(clientId, params),
    queryFn: () => listChartOfAccounts(clientId, params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

export function useChartOfAccountsCoverage(clientId: string, options: { enabled?: boolean } = {}) {
  return useQuery<ChartOfAccountsCoverage>({
    queryKey: chartOfAccountsKeys.coverage(clientId),
    queryFn: () => getChartOfAccountsCoverage(clientId),
    enabled: options.enabled ?? true,
  });
}

/**
 * "Sincronizar agora" — sempre com `force`, que é o contrato do botão: o
 * usuário clicou justamente porque não quer esperar a validade de 24 h.
 * A sincronização automática dentro da janela é do servidor, não desta tela.
 */
export function useSyncChartOfAccounts(clientId: string) {
  const qc = useQueryClient();
  return useMutation<ChartOfAccountsCoverage, Error, void>({
    mutationFn: () => syncChartOfAccounts(clientId, { force: true }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: chartOfAccountsKeys.all(clientId) });
    },
  });
}
