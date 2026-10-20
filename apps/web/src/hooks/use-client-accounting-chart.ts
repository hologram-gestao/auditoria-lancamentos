/**
 * Hooks de TanStack Query do plano de contas CONTÁBIL do cliente e da conta do
 * banco (Sprint 16 — BACK 16.1 · 16.3).
 *
 * Convenções (as de `use-client-chart-of-accounts`):
 *   - query key SEMPRE prefixada pelo `clientId` — trocar de cliente não pode
 *     servir o plano do tenant anterior do cache;
 *   - `keepPreviousData` na lista evita o flash da tabela ao paginar/filtrar;
 *   - a PRÉVIA da importação e a EXPORTAÇÃO (86e3qhhrm) não gravam: não invalidam;
 *   - importar invalida a árvore INTEIRA do plano E as contas de origem (o nome
 *     da conta do banco associada pode ter mudado), e o de-para do cliente
 *     (a prévia do `conta_contabil` lê código e nome do plano);
 *   - incluir ou editar UMA conta (86e3nb816) invalida o mesmo que importar: a
 *     lista, as contas de origem (nome da conta do banco) e o de-para;
 *   - associar a conta do banco atualiza as contas de origem e o de-para (a
 *     prévia lista as contas de origem pendentes);
 *   - importar uma relação de clientes/fornecedores (86e3qhhrn) invalida a árvore
 *     do plano (os vínculos são colunas da conta); listar vínculos é leitura;
 *   - vincular ou desfazer o vínculo de UMA conta à mão (86e3tttaf) invalida o mesmo
 *     que importar a relação: a árvore do plano (lista dos vínculos e o resumo);
 *   - a prévia e o download da planilha da Omie (86e3qhhrr) são leitura: a prévia
 *     mora na árvore do plano (importar ou editar a refaz) e nunca fica em cache
 *     entre aberturas do diálogo (`staleTime: 0`).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { BlobResponse } from '@/lib/api/client';
import {
  createAccountingAccount,
  exportAccountingChart,
  exportOmieChart,
  importAccountingChart,
  importAccountingParties,
  listAccountingChart,
  listAccountingParties,
  listSourceAccounts,
  previewAccountingChartImport,
  previewOmieChartExport,
  removeAccountingPartyLink,
  setAccountingPartyLink,
  setSourceAccountBinding,
  updateAccountingAccount,
  type ListAccountingChartParams,
  type ListAccountingPartiesParams,
} from '@/lib/api/client-accounting-chart';
import type {
  AccountingAccount,
  AccountingAccountCreateRequest,
  AccountingAccountUpdateRequest,
  AccountingChartImportPreview,
  AccountingChartImportResult,
  AccountingChartListResponse,
  AccountingPartiesImportResult,
  AccountingPartyLink,
  AccountingPartyLinkRequest,
  AccountingPartyLinksListResponse,
  OmieExportPreview,
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
  parties: (clientId: string, params: ListAccountingPartiesParams) =>
    ['accounting-chart', clientId, 'parties', params] as const,
  omieExportPreview: (clientId: string) =>
    ['accounting-chart', clientId, 'omie-export-preview'] as const,
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

/** Prévia da importação (86e3qhhrm): não grava, então não invalida nada. */
export function usePreviewAccountingChartImport(clientId: string) {
  return useMutation<AccountingChartImportPreview, Error, File>({
    mutationFn: (file) => previewAccountingChartImport(clientId, file),
  });
}

/** Baixar o plano em CSV (86e3qhhrm): leitura, sem invalidação. */
export function useExportAccountingChart(clientId: string) {
  return useMutation<BlobResponse, Error, void>({
    mutationFn: () => exportAccountingChart(clientId),
  });
}

export function useCreateAccountingAccount(clientId: string) {
  const qc = useQueryClient();
  return useMutation<AccountingAccount, Error, AccountingAccountCreateRequest>({
    mutationFn: (payload) => createAccountingAccount(clientId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: accountingChartKeys.all(clientId) });
      void qc.invalidateQueries({ queryKey: clientMappingKeys.all(clientId) });
    },
  });
}

export interface UpdateAccountingAccountVariables {
  accountId: string;
  payload: AccountingAccountUpdateRequest;
}

export function useUpdateAccountingAccount(clientId: string) {
  const qc = useQueryClient();
  return useMutation<AccountingAccount, Error, UpdateAccountingAccountVariables>({
    mutationFn: ({ accountId, payload }) => updateAccountingAccount(clientId, accountId, payload),
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

/** As contas com vínculo a cliente/fornecedor (86e3qhhrn). Só para quem gere o plano. */
export function useAccountingPartiesList(
  clientId: string,
  params: ListAccountingPartiesParams,
  options: { enabled?: boolean } = {},
) {
  return useQuery<AccountingPartyLinksListResponse>({
    queryKey: accountingChartKeys.parties(clientId, params),
    queryFn: () => listAccountingParties(clientId, params),
    placeholderData: keepPreviousData,
    enabled: clientId.length > 0 && (options.enabled ?? true),
  });
}

/** Importa uma relação do Domínio (86e3qhhrn): o vínculo mora na conta, então a árvore inteira invalida. */
export function useImportAccountingParties(clientId: string) {
  const qc = useQueryClient();
  return useMutation<AccountingPartiesImportResult, Error, File>({
    mutationFn: (file) => importAccountingParties(clientId, file),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: accountingChartKeys.all(clientId) });
    },
  });
}

/** Vincula (ou troca o vínculo de) UMA conta à mão (86e3tttaf). */
export function useSetAccountingPartyLink(clientId: string) {
  const qc = useQueryClient();
  return useMutation<
    AccountingPartyLink,
    Error,
    { accountId: string; payload: AccountingPartyLinkRequest }
  >({
    mutationFn: ({ accountId, payload }) => setAccountingPartyLink(clientId, accountId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: accountingChartKeys.all(clientId) });
    },
  });
}

/** Desfaz o vínculo de UMA conta (86e3tttaf). */
export function useRemoveAccountingPartyLink(clientId: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (accountId) => removeAccountingPartyLink(clientId, accountId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: accountingChartKeys.all(clientId) });
    },
  });
}

/** A prévia da planilha da Omie (86e3qhhrr): só com o diálogo aberto, sempre fresca. */
export function useOmieChartExportPreview(clientId: string, options: { enabled: boolean }) {
  return useQuery<OmieExportPreview>({
    queryKey: accountingChartKeys.omieExportPreview(clientId),
    queryFn: () => previewOmieChartExport(clientId),
    enabled: options.enabled,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}

/**
 * Conferir de novo o cadastro no Omie (86e3qhhrr): a prévia com `refresh=true`, que relê
 * o diretório da Omie, e o resultado vira o dado da prévia aberta.
 */
export function useRefreshOmieChartExportPreview(clientId: string) {
  const qc = useQueryClient();
  return useMutation<OmieExportPreview, Error, void>({
    mutationFn: () => previewOmieChartExport(clientId, { refresh: true }),
    onSuccess: (data) => {
      qc.setQueryData(accountingChartKeys.omieExportPreview(clientId), data);
    },
  });
}

/** Baixar a planilha da Omie (86e3qhhrr): leitura, sem invalidação. */
export function useExportOmieChart(clientId: string) {
  return useMutation<BlobResponse, Error, void>({
    mutationFn: () => exportOmieChart(clientId),
  });
}
