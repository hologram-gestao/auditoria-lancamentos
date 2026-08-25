/**
 * Notificações in-app (Sprint 4 / R4 — BACK 04.4).
 *
 * A pergunta da reunião de 07/07 foi literal: *"a pessoa sai dessa tela — como
 * é que ela sabe que acabou?"*. Estas rotas são a resposta: quando a sessão
 * entra em `reviewing` (Processada) ou `error` (Erro), o backend cria uma
 * notificação para quem iniciou a conciliação.
 *
 * **Entrega por POLLING**, não SSE/WebSocket: o app já faz polling
 * (`useSessionStatus`), e infra nova de streaming não se justifica neste volume.
 * O badge consulta `unread-count` a cada 15 s, só com a aba em foco.
 *
 * **Nenhum texto vem do servidor.** A resposta traz `tipo`, `omie_conta_id`,
 * `reference_month` e `error_code` — a frase é montada no front. É de propósito:
 * texto pronto no banco seria a porta por onde PII do conteúdo do arquivo
 * vazaria para o aviso.
 */
import type {
  MarkAllReadPayload,
  MarkReadPayload,
  NotificationItem,
  NotificationListResponse,
  UnreadCountPayload,
} from '@/lib/contracts';

import { apiGet, apiPost } from './client';

export type Notification = NotificationItem;
export type NotificationListResult = NotificationListResponse;

/** `processada` | `erro` — os dois únicos momentos que interrompem quem trabalha. */
export type NotificationTipo = 'processada' | 'erro';

/**
 * Contagem de não lidas — é o badge do sino. Barato por construção no backend
 * (índice parcial sobre `read_at IS NULL`), então pode ser consultado de 15 em
 * 15 s sem peso.
 */
export async function getUnreadCount(): Promise<number> {
  const payload = await apiGet<UnreadCountPayload>('/api/v1/notifications/unread-count');
  return payload.unread;
}

export interface ListNotificationsParams {
  page?: number;
  pageSize?: number;
  unreadOnly?: boolean;
}

export async function listNotifications(
  params: ListNotificationsParams = {},
): Promise<NotificationListResult> {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page ?? 1));
  sp.set('pageSize', String(params.pageSize ?? 20));
  if (params.unreadOnly === true) sp.set('unreadOnly', 'true');
  return apiGet<NotificationListResult>(`/api/v1/notifications?${sp.toString()}`);
}

/**
 * Marca como lida. **Idempotente por contrato**: reenviar responde 200 com
 * `already_read=true` e preserva o timestamp da 1ª leitura — lida não reaparece
 * no contador.
 */
export async function markNotificationRead(notificationId: string): Promise<MarkReadPayload> {
  return apiPost<MarkReadPayload>(`/api/v1/notifications/${notificationId}/read`);
}

/**
 * Marca TODAS as não lidas como lidas (86e2u513q — o botão do sino).
 * **Idempotente**: a 2ª chamada devolve `marked=0`, nunca erro.
 */
export async function markAllNotificationsRead(): Promise<MarkAllReadPayload> {
  return apiPost<MarkAllReadPayload>('/api/v1/notifications/read-all');
}
