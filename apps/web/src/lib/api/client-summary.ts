/**
 * `GET /api/v1/clients/{client_id}/summary` — o resumo do cliente (86e3k1q3j).
 *
 * A resposta é `{ data: {...} }`, chave ÚNICA: o `apiGet` já entrega o miolo, e
 * declarar o envelope aqui daria um `data` a mais (o `apiGet<T>` acredita no tipo
 * declarado). `month` é `YYYY-MM`; sem ele, o servidor usa o mês corrente no fuso
 * do Brasil, nunca o relógio do navegador.
 */
import type { ClientSummary } from '@/lib/contracts';

import { apiGet } from './client';

export async function getClientSummary(clientId: string, month?: string): Promise<ClientSummary> {
  const query = month ? `?month=${encodeURIComponent(month)}` : '';
  return apiGet<ClientSummary>(`/api/v1/clients/${encodeURIComponent(clientId)}/summary${query}`);
}
