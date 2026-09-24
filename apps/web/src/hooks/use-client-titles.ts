/**
 * Hooks de TanStack Query da carteira de títulos em aberto (Sprint 11 / R4).
 *
 * Convenções (as mesmas de `use-client-chart-of-accounts` e `use-glossary`):
 *   - query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir a carteira do tenant anterior do cache;
 *   - `keepPreviousData` evita o flash da tabela ao paginar, filtrar e ordenar;
 *   - a sincronização invalida a árvore INTEIRA daquele cliente: ela muda a
 *     lista E os agregados, e um aging velho ao lado de uma lista nova é pior
 *     do que um instante de carregamento.
 *
 * Os agregados são uma query SEPARADA da lista, espelhando as duas rotas: o
 * aging é sobre a carteira inteira e não pode mudar conforme a página aberta.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getClientTitlesSummary,
  getReceivablesReport,
  listClientTitles,
  listTitleContext,
  registerTitleContext,
  syncClientTitles,
  type ListClientTitlesParams,
} from '@/lib/api/client-titles';
import type {
  ClientTitlesListResponse,
  ReceivablesReport,
  TitleContext,
  TitleContextCreateRequest,
  TitlesSummary,
  TitlesSyncResult,
} from '@/lib/contracts';

export const clientTitlesKeys = {
  all: (clientId: string) => ['client-titles', clientId] as const,
  list: (clientId: string, params: ListClientTitlesParams) =>
    ['client-titles', clientId, 'list', params] as const,
  summary: (clientId: string) => ['client-titles', clientId, 'summary'] as const,
  receivablesReport: (clientId: string) =>
    ['client-titles', clientId, 'receivables-report'] as const,
};

/** Sprint 15 — histórico de contexto de UM título, sempre sob o `clientId`. */
export const titleContextKeys = {
  history: (clientId: string, titleId: string) => ['title-context', clientId, titleId] as const,
};

export function useClientTitlesList(
  clientId: string,
  params: ListClientTitlesParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<ClientTitlesListResponse>({
    queryKey: clientTitlesKeys.list(clientId, params),
    queryFn: () => listClientTitles(clientId, params),
    placeholderData: keepPreviousData,
    enabled: options.enabled ?? true,
  });
}

export function useClientTitlesSummary(clientId: string, options: { enabled?: boolean } = {}) {
  return useQuery<TitlesSummary>({
    queryKey: clientTitlesKeys.summary(clientId),
    queryFn: () => getClientTitlesSummary(clientId),
    enabled: options.enabled ?? true,
  });
}

/**
 * "Sincronizar agora". Não tem `force`: ao contrário do plano de contas, o
 * servidor não serve a carteira de uma janela de validade — cada chamada vai à
 * origem, e a cadência automática é do job diário (R5), não desta tela.
 */
export function useSyncClientTitles(clientId: string) {
  const qc = useQueryClient();
  return useMutation<TitlesSyncResult, Error, void>({
    mutationFn: () => syncClientTitles(clientId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientTitlesKeys.all(clientId) });
    },
  });
}

/**
 * Relatório de recebíveis (Sprint 15 — BACK 15.2). Query PRÓPRIA (não deriva
 * da lista/summary): os grupos são calculados no servidor sobre a carteira
 * inteira e dependem do contexto mais recente de cada título.
 */
export function useReceivablesReport(clientId: string, options: { enabled?: boolean } = {}) {
  return useQuery<ReceivablesReport>({
    queryKey: clientTitlesKeys.receivablesReport(clientId),
    queryFn: () => getReceivablesReport(clientId),
    enabled: options.enabled ?? true,
  });
}

/** Histórico completo de contexto de UM título (Sprint 15 — BACK 15.1). */
export function useTitleContextHistory(
  clientId: string,
  titleId: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery<TitleContext[]>({
    queryKey: titleContextKeys.history(clientId, titleId),
    queryFn: () => listTitleContext(clientId, titleId),
    enabled: options.enabled ?? true,
  });
}

/**
 * Registra uma entrada de contexto. Invalida o histórico DESTE título, a
 * carteira INTEIRA do cliente (o indicador de linha e o filtro
 * "vencidos sem contexto" dependem dela) e o relatório de recebíveis (o
 * contexto muda o grupo em que o título entra).
 */
export function useRegisterTitleContext(clientId: string, titleId: string) {
  const qc = useQueryClient();
  return useMutation<TitleContext, Error, TitleContextCreateRequest>({
    mutationFn: (payload) => registerTitleContext(clientId, titleId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: titleContextKeys.history(clientId, titleId) });
      void qc.invalidateQueries({ queryKey: clientTitlesKeys.all(clientId) });
    },
  });
}
