'use client';

/**
 * Hooks do sino de notificações (Sprint 4 / R4).
 *
 * Cadência declarada no plano da sprint: **15 s**, só com a aba em foco.
 * 15 s é imperceptível para "acabou o processamento" e não martela o backend
 * como os 3 s do polling de uma sessão ativa fariam num poll global de header,
 * que roda em TODAS as telas o tempo inteiro.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getUnreadCount,
  listNotifications,
  markNotificationRead,
  type ListNotificationsParams,
  type NotificationListResult,
} from '@/lib/api/notifications';
import type { MarkReadPayload } from '@/lib/contracts';

const UNREAD_POLL_INTERVAL_MS = 15_000;

export const notificationKeys = {
  all: ['notifications'] as const,
  unreadCount: () => ['notifications', 'unread-count'] as const,
  list: (params: ListNotificationsParams) => ['notifications', 'list', params] as const,
};

/**
 * Contador de não lidas com polling de 15 s.
 *
 * `refetchIntervalInBackground: false` é o detalhe que importa: com a aba fora
 * de foco o polling PARA. Ninguém precisa ser avisado de nada enquanto está em
 * outro programa — e um app aberto a tarde toda em segundo plano geraria
 * milhares de requests inúteis.
 */
export function useUnreadNotificationsCount() {
  return useQuery<number>({
    queryKey: notificationKeys.unreadCount(),
    queryFn: getUnreadCount,
    refetchInterval: UNREAD_POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    // Ao voltar para a aba, atualiza na hora em vez de esperar o próximo tick.
    refetchOnWindowFocus: true,
    // Contador é informativo: uma falha isolada não deve encher a tela de erro.
    retry: 1,
  });
}

interface UseNotificationsOptions {
  /** Só busca a lista quando o sino está aberto — o badge já basta fechado. */
  enabled?: boolean;
}

export function useNotifications(
  params: ListNotificationsParams,
  options: UseNotificationsOptions = {},
) {
  return useQuery<NotificationListResult>({
    queryKey: notificationKeys.list(params),
    queryFn: () => listNotifications(params),
    enabled: options.enabled ?? true,
    placeholderData: keepPreviousData,
  });
}

/**
 * Marca como lida e invalida contador + lista, para o item sair do badge e não
 * reaparecer. A idempotência é do backend — reenviar não estraga nada.
 */
export function useMarkNotificationRead() {
  const qc = useQueryClient();
  return useMutation<MarkReadPayload, Error, string>({
    mutationFn: markNotificationRead,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}
