/**
 * Origens de dado do cliente — `client_connections` (Sprint 9 / R3 · R5).
 *
 * Espelha `apps/api/app/modules/client_connections/{routes,schemas}.py`. As
 * cinco rotas vivem sob `/api/v1/clients/{clientId}/connections`.
 *
 * Convenções que valem para o módulo inteiro:
 *   - **Nenhuma resposta carrega credencial**, nem mascarada: o
 *     `ClientConnectionResponse` do contrato não tem o campo, então o `tsc`
 *     barra quem tentar lê-lo. O que sobe no corpo (`credentials`) nunca volta.
 *   - Todos os envelopes têm UMA chave (`{data}`), então o `rawFetch`
 *     desembrulha sozinho — o caller recebe o objeto/payload direto.
 *   - `client_id` **nunca** vai no body: quem decide o tenant é a rota, e os
 *     requests são `extra="forbid"` (mandar seria 422).
 *   - O método HTTP faz parte do contrato: cada operação usa o helper do
 *     método certo (`apiGet`/`apiPost`/`apiPatch`/`apiDelete`), nunca uma
 *     string `{method}` que o `tsc` não enxerga.
 */
import type {
  ClientConnection,
  ClientConnectionListPayload,
  ConnectionDeletedPayload,
  CreateConnectionRequest,
  UpdateConnectionRequest,
} from '@/lib/contracts';

import { apiDelete, apiGet, apiPatch, apiPost } from './client';

/**
 * Tipo da origem. Hoje o registry do backend só resolve `omie` — tipo
 * desconhecido é 422. A constante existe para a tela não digitar a string solta
 * em quatro lugares; quando o 2º provedor chegar, o seletor nasce aqui.
 */
export const OMIE_PROVIDER_TYPE = 'omie';

/** Rótulo padrão do tipo, espelho de `_DEFAULT_LABELS` (`service.py:67`). */
export const DEFAULT_PROVIDER_LABEL: Record<string, string> = {
  [OMIE_PROVIDER_TYPE]: 'Omie',
};

/**
 * Chaves de credencial que o adaptador do Omie aceita
 * (`OMIE_CREDENTIAL_KEYS`, `integrations/providers/omie_adapter.py:51`).
 *
 * ⚠️ São **snake_case** — `app_key`/`app_secret`. A docstring do schema do
 * backend diz `appKey`/`appSecret` e está errada: `credentials` é um
 * `dict[str, SecretStr]` livre, sem alias generator, e o adaptador procura
 * exatamente estas duas chaves (chave faltando é 422). Conferido contra
 * `tests/integration/test_client_connections.py` em 22/09/2026.
 */
export const OMIE_CREDENTIAL_KEYS = { appKey: 'app_key', appSecret: 'app_secret' } as const;

/** Monta o mapa de credenciais do Omie na forma que o adaptador exige. */
export function omieCredentials(appKey: string, appSecret: string): Record<string, string> {
  return {
    [OMIE_CREDENTIAL_KEYS.appKey]: appKey,
    [OMIE_CREDENTIAL_KEYS.appSecret]: appSecret,
  };
}

/** Chave de `ApiError.details` no 409 de `(tipo, rótulo)` repetido. */
export const EXISTING_CONNECTION_ID_KEY = 'existingConnectionId';

export type CreateConnectionPayload = CreateConnectionRequest;
export type UpdateConnectionPayload = UpdateConnectionRequest;

function basePath(clientId: string): string {
  return `/api/v1/clients/${clientId}/connections`;
}

/**
 * Origens do cliente (0..N). LEITURA liberada a todo papel com acesso ao
 * cliente — inclusive o operador, que precisa ver se a origem está ativa antes
 * de rodar uma conciliação.
 */
export async function listConnections(clientId: string): Promise<ClientConnection[]> {
  const payload = await apiGet<ClientConnectionListPayload>(basePath(clientId));
  return payload.connections;
}

/**
 * Conecta uma origem. A credencial é verificada contra o provedor ANTES de
 * qualquer escrita: recusada, nenhuma linha nasce. 409 com
 * `details.existingConnectionId` quando `(tipo, rótulo)` já existe.
 */
export async function createConnection(
  clientId: string,
  payload: CreateConnectionPayload,
): Promise<ClientConnection> {
  return apiPost<ClientConnection>(basePath(clientId), payload);
}

/**
 * Reverifica a credencial JÁ GRAVADA. Sucesso marca `ativa`; credencial
 * recusada marca `erro` **sem apagar a credencial**. Provedor fora do ar é 5xx
 * e não muda o estado — é transitório.
 */
export async function testStoredConnection(
  clientId: string,
  connectionId: string,
): Promise<ClientConnection> {
  return apiPost<ClientConnection>(`${basePath(clientId)}/${connectionId}/test`, undefined);
}

/**
 * Renomeia e/ou troca as credenciais. Os dois campos são independentes, mas o
 * corpo vazio é 422 — um PATCH que não pede nada é engano de quem chamou.
 */
export async function updateConnection(
  clientId: string,
  connectionId: string,
  payload: UpdateConnectionPayload,
): Promise<ClientConnection> {
  return apiPatch<ClientConnection>(`${basePath(clientId)}/${connectionId}`, payload);
}

/**
 * Remoção DEFINITIVA (a linha some). É isso que permite reconectar depois o
 * mesmo tipo com o mesmo rótulo; o registro do que aconteceu fica na trilha de
 * auditoria, não na linha.
 */
export async function deleteConnection(
  clientId: string,
  connectionId: string,
): Promise<ConnectionDeletedPayload> {
  return apiDelete<ConnectionDeletedPayload>(`${basePath(clientId)}/${connectionId}`);
}
