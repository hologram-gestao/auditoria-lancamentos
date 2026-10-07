/**
 * "Esta tela pode oferecer a criação de conciliação?" — a decisão ÚNICA que a
 * lista de conciliações e o painel do cliente consultam (86e3k1q54).
 *
 * Três motivos para a criação sumir, e cada um tem explicação própria na tela:
 *   - cliente ENCERRADO (86e36pm1z): o servidor nega com 409, o histórico fica só-leitura;
 *   - origem ausente ou com erro (S9, R6/R7): o `POST` responde 409 da taxonomia;
 *   - origem que não LISTA CONTAS (S14, `CAPACIDADE_AUSENTE`): uma conciliação é
 *     conta + mês, e a origem por arquivo não lista contas.
 *
 * Perguntar diferente em cada tela faria o painel oferecer "Nova conciliação" a
 * um cliente para quem a lista esconde o botão (§7 Frontend: ação oferecida tem
 * de existir no destino).
 *
 * `origin_status` ausente (detalhe ainda carregando) vale `ativa`: é o desenho
 * herdado da lista, que não esconde o botão enquanto o detalhe chega.
 */
import type { ClientDetail } from '@/lib/api/clients';
import { originCodeFor } from '@/lib/origin-capabilities';
import type { OriginErrorCode } from '@/lib/origin-state';

export interface ReconciliationCreation {
  isClosed: boolean;
  /** Código da taxonomia de origem que bloqueia a criação; `null` sem bloqueio (ou encerrado). */
  originCode: OriginErrorCode | null;
  canCreate: boolean;
}

export function reconciliationCreation(
  detail: Pick<ClientDetail, 'closed_at' | 'origin_status' | 'connections'> | undefined,
): ReconciliationCreation {
  const isClosed = detail?.closed_at != null;
  const originCode = isClosed
    ? null
    : originCodeFor(detail?.origin_status ?? 'ativa', detail?.connections, 'listar_contas');
  return { isClosed, originCode, canCreate: !isClosed && originCode === null };
}
