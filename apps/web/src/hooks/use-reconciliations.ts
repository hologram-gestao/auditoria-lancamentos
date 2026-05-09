/**
 * Hooks de TanStack Query para o módulo reconciliations.
 *
 * S8 (FRONT 6.1): `useCheckDuplicate`.
 * S9 (FRONT 7.2): `useParseStatement`.
 * S10 (FRONT 8.7): `useCreateReconciliation` + `useSessionStatus`.
 *
 * Por que `useMutation` para o primeiro grupo:
 *   Tanto a checagem de duplicata quanto o parse e a criação da sessão são
 *   on-demand (acionados no submit do form), dependem de input que só existe
 *   após interação do usuário e não devem ficar em cache — se o usuário
 *   trocar de arquivo, queremos recalcular sem prefetch acidental do TanStack.
 *
 * Por que `useQuery` para o status:
 *   Polling. `refetchInterval` aceita uma função que recebe a `Query` atual
 *   e devolve o intervalo (ms) ou `false` para parar. Devolver `false`
 *   quando `status !== 'processing'` evita continuar o polling depois que
 *   a sessão entra em `reviewing`/`done`/`error`.
 */
import { useMutation, useQuery, type UseQueryOptions } from '@tanstack/react-query';

import {
  checkDuplicate,
  createReconciliation,
  getSessionStatus,
  parseStatement,
  type CheckDuplicateParams,
  type CheckDuplicateResult,
  type CreateReconciliationPayload,
  type CreateReconciliationResult,
  type ParsedStatement,
  type ParseStatementParams,
  type SessionStatusResult,
} from '@/lib/api/reconciliations';

export function useCheckDuplicate() {
  return useMutation<CheckDuplicateResult, Error, CheckDuplicateParams>({
    mutationFn: checkDuplicate,
  });
}

export function useParseStatement() {
  return useMutation<ParsedStatement, Error, ParseStatementParams>({
    mutationFn: parseStatement,
  });
}

export function useCreateReconciliation() {
  return useMutation<CreateReconciliationResult, Error, CreateReconciliationPayload>({
    mutationFn: createReconciliation,
  });
}

/** Polling cadence — Doc §13.1 manda 3s; também citado em CLAUDE.md S10. */
const STATUS_POLL_INTERVAL_MS = 3000;

interface UseSessionStatusOptions {
  /**
   * Quando `false`, o query é desabilitado (útil pra parar o polling após
   * timeout do front — pitfall §6 do briefing). Default: `true`.
   */
  enabled?: boolean;
}

/**
 * Polling de status da sessão. Intervalo dinâmico:
 *   - 3s enquanto `status === 'processing'`.
 *   - `false` (para o polling) assim que vira `reviewing`, `done` ou `error`.
 *
 * Em erro de rede no `/status`, o TanStack mantém `data` anterior (não vira
 * `undefined`) e o consumidor renderiza os steps no estado prévio até o
 * próximo poll bem-sucedido — pitfall §contrato do briefing.
 */
export function useSessionStatus(sessionId: string, options: UseSessionStatusOptions = {}) {
  const enabled = options.enabled ?? true;

  const queryOptions: UseQueryOptions<SessionStatusResult, Error, SessionStatusResult, string[]> = {
    queryKey: ['reconciliations', sessionId, 'status'],
    queryFn: () => getSessionStatus(sessionId),
    enabled,
    // `refetchInterval` recebe a Query inteira; a tipagem do callback é estreita
    // em relação ao `data` (`undefined` no primeiro fetch). Função pura: sem
    // efeitos colaterais — só decide intervalo a partir do último status visto.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === undefined) {
        // Primeiro fetch ainda não voltou — agenda o próximo na cadência normal.
        return STATUS_POLL_INTERVAL_MS;
      }
      return status === 'processing' ? STATUS_POLL_INTERVAL_MS : false;
    },
    // Não precisamos de cache pós-tela: a sessão muda de UI assim que sai do
    // processing, e o status final fica visível pelo `data` em memória do
    // próprio componente. `gcTime` curto evita guardar lixo.
    gcTime: 30_000,
    // Polling no fundo já basta — refetch on focus duplica trabalho.
    refetchOnWindowFocus: false,
    // O contrato do back é "lenient out": mesmo que o status mude, é só
    // avançar; não precisa retry agressivo de TanStack em cima.
    retry: 1,
  };

  return useQuery(queryOptions);
}
