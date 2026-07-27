/**
 * Instrumentação de outcome da Sprint 4 — `POST /api/v1/usage-events`.
 *
 * Sink dos eventos que medem se a sprint resolveu a dor ("o operador deixa de
 * ficar preso na tela"). Dois eventos saem DAQUI, do front:
 *
 *   - `autor_navegou_fora`   — **prova o outcome**: o autor criou a conciliação
 *     e foi fazer outra coisa antes de ela terminar.
 *   - `notificacao_entregue` — mede se o aviso ALCANÇA a pessoa (via + latência).
 *
 * Contrato (BACK 04.1): o body é um union discriminado por `event`, o `event` é
 * validado contra um enum FECHADO e as chaves de `props` contra uma whitelist —
 * nenhum campo aceita texto livre, então **não há como vazar PII** por aqui.
 *
 * **É idempotente por contrato:** reenviar o mesmo evento para a mesma sessão
 * responde 201 com `recorded=false` e não duplica linha. Isso é o que permite o
 * front emitir sem coordenar estado global perfeito.
 *
 * **Nunca quebra a UI.** Telemetria falhando não pode virar toast de erro nem
 * derrubar uma navegação; por isso `recordUsageEvent` engole a exceção e devolve
 * `false`. O `console.debug` fica para diagnóstico local.
 */
import type { UsageEventPayload, UsageEventRequest } from '@/lib/contracts';

import { apiPost } from './client';

export type { UsageEventRequest };

/**
 * Emite um evento de uso. Devolve `true` se o backend aceitou (mesmo que
 * `recorded=false` por idempotência), `false` se a chamada falhou.
 */
export async function recordUsageEvent(payload: UsageEventRequest): Promise<boolean> {
  try {
    await apiPost<UsageEventPayload>('/api/v1/usage-events', payload);
    return true;
  } catch (err) {
    // Silencioso de propósito: a métrica é do produto, não da pessoa usando.
    // eslint-disable-next-line no-console
    console.debug('usage-event não registrado', payload.event, err);
    return false;
  }
}

/** `autor_navegou_fora` — o evento que prova o outcome da sprint. */
export async function recordAutorNavegouFora(
  sessionId: string,
  segundosAposCriar: number,
): Promise<boolean> {
  return recordUsageEvent({
    event: 'autor_navegou_fora',
    session_id: sessionId,
    // O contrato limita a 30 dias (2 592 000 s) e exige inteiro ≥ 0; clampar
    // aqui evita um 400 por causa de relógio do cliente fora de hora.
    props: { segundos_apos_criar: clampSeconds(segundosAposCriar) },
  });
}

/** `notificacao_entregue` — mede se o aviso chegou (e em quanto tempo). */
export async function recordNotificacaoEntregue(
  sessionId: string,
  via: 'sino' | 'toast',
  latenciaSegundos: number,
): Promise<boolean> {
  return recordUsageEvent({
    event: 'notificacao_entregue',
    session_id: sessionId,
    props: { via, latencia_s: clampSeconds(latenciaSegundos) },
  });
}

/** Máximo aceito pelo contrato: 30 dias em segundos. */
const MAX_SECONDS = 2_592_000;

function clampSeconds(value: number): number {
  if (!Number.isFinite(value) || value < 0) return 0;
  return Math.min(Math.round(value), MAX_SECONDS);
}
