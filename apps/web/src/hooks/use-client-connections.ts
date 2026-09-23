/**
 * Hooks de TanStack Query das origens do cliente (Sprint 9 / R3 · R5).
 *
 * Toda mutação invalida a raiz `['clients']` e não só a lista de conexões: o
 * `origin_status` vive no `ClientResponse` (lista) **e** no detalhe, e as
 * `connections` viajam no detalhe. Invalidar só a chave de conexões deixaria o
 * painel dizendo "sem origem" logo depois de conectar uma — exatamente o
 * defeito que o R7 existe para evitar.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  createConnection,
  deleteConnection,
  listConnections,
  testStoredConnection,
  updateConnection,
  type CreateConnectionPayload,
  type UpdateConnectionPayload,
} from '@/lib/api/client-connections';
import type { ClientConnection, ConnectionDeletedPayload } from '@/lib/contracts';

import { clientsKeys } from './use-clients';

export const connectionsKeys = {
  list: (clientId: string) => ['clients', 'connections', clientId] as const,
};

export function useClientConnections(clientId: string, opts: { enabled?: boolean } = {}) {
  return useQuery<ClientConnection[]>({
    queryKey: connectionsKeys.list(clientId),
    queryFn: () => listConnections(clientId),
    enabled: clientId.length > 0 && (opts.enabled ?? true),
  });
}

/** Invalidação comum às quatro escritas — a raiz, pelos motivos do cabeçalho. */
function useInvalidateClientTree(clientId: string): () => void {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: connectionsKeys.list(clientId) });
    void qc.invalidateQueries({ queryKey: clientsKeys.all });
  };
}

export function useCreateConnection(clientId: string) {
  const invalidate = useInvalidateClientTree(clientId);
  return useMutation<ClientConnection, Error, CreateConnectionPayload>({
    mutationFn: (payload) => createConnection(clientId, payload),
    onSuccess: invalidate,
  });
}

export function useUpdateConnection(clientId: string, connectionId: string) {
  const invalidate = useInvalidateClientTree(clientId);
  return useMutation<ClientConnection, Error, UpdateConnectionPayload>({
    mutationFn: (payload) => updateConnection(clientId, connectionId, payload),
    onSuccess: invalidate,
  });
}

/**
 * Reteste da credencial já gravada. Invalida igual às outras: o resultado muda
 * o `status` da conexão e, com ele, o `origin_status` do cliente.
 */
export function useTestStoredConnection(clientId: string) {
  const invalidate = useInvalidateClientTree(clientId);
  return useMutation<ClientConnection, Error, string>({
    mutationFn: (connectionId) => testStoredConnection(clientId, connectionId),
    onSuccess: invalidate,
  });
}

export function useDeleteConnection(clientId: string) {
  const invalidate = useInvalidateClientTree(clientId);
  return useMutation<ConnectionDeletedPayload, Error, string>({
    mutationFn: (connectionId) => deleteConnection(clientId, connectionId),
    onSuccess: invalidate,
  });
}
