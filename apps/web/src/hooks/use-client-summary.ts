/**
 * Hook do resumo do cliente (86e3k1q3j): contagens de pendência do mês. Quem
 * consome é o menu (contadores, 86e3k1q3x) e o painel (86e3k1q54).
 *
 * A query key começa pelo `clientId` (trocar de cliente não serve o resumo do
 * anterior) e leva o mês pedido; sem mês, a chave usa `'atual'`, porque quem
 * decide o mês corrente é o SERVIDOR.
 */
import { type QueryClient, useQuery } from '@tanstack/react-query';

import { getClientSummary } from '@/lib/api/client-summary';
import type { ClientSummary } from '@/lib/contracts';

export const clientSummaryKeys = {
  root: ['client-summary'] as const,
  all: (clientId: string) => ['client-summary', clientId] as const,
  month: (clientId: string, month?: string) =>
    ['client-summary', clientId, month ?? 'atual'] as const,
};

export function useClientSummary(
  clientId: string,
  month?: string,
  options: { enabled?: boolean } = {},
) {
  return useQuery<ClientSummary>({
    queryKey: clientSummaryKeys.month(clientId, month),
    queryFn: () => getClientSummary(clientId, month),
    enabled: (options.enabled ?? true) && clientId !== '',
  });
}

/**
 * Invalida o resumo depois de uma mutação que muda as contagens (criar ou
 * reprocessar conciliação, revisar, lançar no Omie, sincronizar a carteira,
 * decidir no de-para). Sem `clientId` (mutação que só conhece a sessão), cai
 * no prefixo de todos os clientes: só o cliente aberto tem query ativa, então
 * o custo é o mesmo refetch. Sem polling: o menu e o painel acompanham a
 * mutação, não o relógio.
 */
export function invalidateClientSummary(qc: QueryClient, clientId?: string): void {
  void qc.invalidateQueries({
    queryKey: clientId !== undefined ? clientSummaryKeys.all(clientId) : clientSummaryKeys.root,
  });
}
