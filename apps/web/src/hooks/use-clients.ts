/**
 * Hooks de TanStack Query para o módulo clients (S6 + S7).
 *
 * Convenções:
 *   - Query keys segmentadas: `['clients', 'list', params]`,
 *     `['clients', 'detail', id]`, `['clients', 'reconciliations', id, params]`.
 *   - Mutations invalidam `['clients']` raiz (atinge listagem e detalhe).
 *   - `placeholderData: keepPreviousData` evita flash em paginação/busca.
 *   - `useTestConnection` NÃO invalida nada — é só uma checagem de credenciais.
 *   - `useSyncAccounts` recebe a resposta (ClientDetail atualizado) e atualiza
 *     o cache do detalhe diretamente via `setQueryData`. Isso evita um refetch
 *     adicional após o PATCH (otimização — o back já devolveu o estado novo).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  addClientManager,
  assignClient,
  createClient,
  closeClient,
  deleteClient,
  favoriteClient,
  getClientDetail,
  listClientManagers,
  listClients,
  listReconciliations,
  removeClientManager,
  syncClientAccounts,
  testConnection,
  unfavoriteClient,
  updateClient,
  type AddClientManagerPayload,
  type AssignClientPayload,
  type Client,
  type ClientDetail,
  type ClientManager,
  type ClientListResponse,
  type CreateClientPayload,
  type ListClientsParams,
  type ReconciliationsListParams,
  type ReconciliationsListResponse,
  type TestConnectionPayload,
  type TestConnectionResult,
  type UpdateClientPayload,
} from '@/lib/api/clients';

export const clientsKeys = {
  all: ['clients'] as const,
  /** Prefixo de TODAS as páginas/filtros da listagem — para invalidar. */
  lists: ['clients', 'list'] as const,
  list: (params: ListClientsParams) => ['clients', 'list', params] as const,
  detail: (id: string) => ['clients', 'detail', id] as const,
  /** Quem tem acesso ao cliente (86e390m4c) — responsável + colaboradores. */
  managers: (id: string) => ['clients', 'managers', id] as const,
  /** Prefixo de TODAS as páginas/filtros da lista de um cliente — use este
   *  para invalidar (o `queryKey` completo inclui os `params` e nunca casaria
   *  com um `invalidateQueries` de outra combinação de filtros). */
  reconciliationsAll: (id: string) => ['clients', 'reconciliations', id] as const,
  reconciliations: (id: string, params: ReconciliationsListParams) =>
    ['clients', 'reconciliations', id, params] as const,
};

export function useClientsList(params: ListClientsParams) {
  return useQuery<ClientListResponse>({
    queryKey: clientsKeys.list(params),
    queryFn: () => listClients(params),
    placeholderData: keepPreviousData,
  });
}

export function useCreateClient() {
  const qc = useQueryClient();
  return useMutation<Client, Error, CreateClientPayload>({
    mutationFn: createClient,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

export function useTestConnection() {
  return useMutation<TestConnectionResult, Error, TestConnectionPayload>({
    mutationFn: testConnection,
  });
}

export function useUpdateClient(id: string) {
  const qc = useQueryClient();
  return useMutation<Client, Error, UpdateClientPayload>({
    mutationFn: (payload) => updateClient(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

/**
 * Define o RESPONSÁVEL (86e390m4c). Invalida a raiz: a lista muda o nome da
 * coluna e a seção de gerentes troca o selo — ninguém é removido.
 */
export function useAssignClient(id: string) {
  const qc = useQueryClient();
  return useMutation<Client, Error, AssignClientPayload>({
    mutationFn: (payload) => assignClient(id, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

// ---------------------------------------------------------------------------
// Carteira compartilhada (86e390m4c)
// ---------------------------------------------------------------------------

export function useClientManagers(id: string, opts: { enabled?: boolean } = {}) {
  return useQuery<ClientManager[]>({
    queryKey: clientsKeys.managers(id),
    queryFn: () => listClientManagers(id),
    enabled: id.length > 0 && (opts.enabled ?? true),
  });
}

/**
 * Adicionar/remover acesso: o servidor devolve a lista inteira, que entra
 * direto no cache da seção (sem refetch); a LISTAGEM é invalidada porque o
 * `manager_count` de cada linha muda ("Fulana +1").
 */
export function useAddClientManager(id: string) {
  const qc = useQueryClient();
  return useMutation<ClientManager[], Error, AddClientManagerPayload>({
    mutationFn: (payload) => addClientManager(id, payload),
    onSuccess: (managers) => {
      qc.setQueryData(clientsKeys.managers(id), managers);
      void qc.invalidateQueries({ queryKey: clientsKeys.lists });
    },
  });
}

export function useRemoveClientManager(id: string) {
  const qc = useQueryClient();
  return useMutation<ClientManager[], Error, string>({
    mutationFn: (userId) => removeClientManager(id, userId),
    onSuccess: (managers) => {
      qc.setQueryData(clientsKeys.managers(id), managers);
      void qc.invalidateQueries({ queryKey: clientsKeys.lists });
    },
  });
}

/**
 * Favoritar / desfavoritar (86e34jd5a). `true` marca, `false` desmarca.
 *
 * Invalida só a LISTAGEM (o favorito muda a ordem) e corrige o `is_favorite`
 * do detalhe em cache pelo que o servidor devolveu — sem refetch do detalhe,
 * que passaria pelo cache de contas do Omie à toa.
 */
export function useSetFavorite(id: string) {
  const qc = useQueryClient();
  return useMutation<Client, Error, boolean>({
    mutationFn: (favorite) => (favorite ? favoriteClient(id) : unfavoriteClient(id)),
    onSuccess: (updated) => {
      qc.setQueryData<ClientDetail>(clientsKeys.detail(id), (old) =>
        old ? { ...old, is_favorite: updated.is_favorite } : old,
      );
      void qc.invalidateQueries({ queryKey: clientsKeys.lists });
    },
  });
}

/**
 * Exclusão definitiva (86e34jd1d). No sucesso o detalhe sai do cache (a rota
 * passa a devolver 404) e as listagens recarregam.
 */
export function useDeleteClient(id: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: () => deleteClient(id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: clientsKeys.detail(id) });
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

/**
 * Encerramento com retenção (86e36pm1z). Diferente da exclusão, o cliente
 * CONTINUA existindo (anonimizado, só-leitura): o detalhe é refetchado — o
 * nome, o selo "Encerrado" e o sumiço das ações vêm do servidor.
 */
export function useCloseClient(id: string) {
  const qc = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: () => closeClient(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

interface UseClientDetailOptions {
  enabled?: boolean;
}

export function useClientDetail(id: string, opts: UseClientDetailOptions = {}) {
  return useQuery<ClientDetail>({
    queryKey: clientsKeys.detail(id),
    queryFn: () => getClientDetail(id),
    enabled: id.length > 0 && (opts.enabled ?? true),
  });
}

export function useSyncAccounts(id: string) {
  const qc = useQueryClient();
  return useMutation<ClientDetail, Error, void>({
    mutationFn: () => syncClientAccounts(id),
    onSuccess: (detail) => {
      // Atualiza o cache do detalhe sem refetch — back já devolveu o estado novo.
      qc.setQueryData(clientsKeys.detail(id), detail);
      // Invalida o restante (listagens, contadores, etc).
      void qc.invalidateQueries({ queryKey: clientsKeys.all });
    },
  });
}

/**
 * Cadência do polling de LISTA (Sprint 4 / R2). Trabalho NOVO: o app já tinha
 * poll de UMA sessão (`useSessionStatus`), nunca de nível-lista.
 *
 * 3 s é a mesma cadência do poll de sessão — a lista é o lugar onde a pessoa
 * espera ver "Em processamento" virar "Processada" sem recarregar.
 */
const LIST_POLL_INTERVAL_MS = 3000;

/**
 * Lista de conciliações do cliente, com **polling enquanto houver alguma linha
 * em `processing`** — e parando quando não houver.
 *
 * É isso que faz a conciliação recém-criada aparecer sozinha e mudar de status
 * na tela, sem o usuário ficar preso numa tela de progresso. Sem nenhuma linha
 * processando o intervalo vira `false`: nada de martelar o backend à toa.
 *
 * `refetchIntervalInBackground: false` — aba fora de foco não gera tráfego.
 */
export function useReconciliationsList(id: string, params: ReconciliationsListParams) {
  return useQuery<ReconciliationsListResponse>({
    queryKey: clientsKeys.reconciliations(id, params),
    queryFn: () => listReconciliations(id, params),
    enabled: id.length > 0,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const rows = query.state.data?.data;
      if (rows === undefined) return false;
      return rows.some((row) => row.status === 'processing') ? LIST_POLL_INTERVAL_MS : false;
    },
    refetchIntervalInBackground: false,
  });
}
