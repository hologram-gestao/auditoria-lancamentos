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
 * Tipos de origem que o registry do backend resolve (`supported_provider_types`)
 * — tipo desconhecido é 400. As constantes existem para a tela não digitar a
 * string solta em quatro lugares.
 */
export const OMIE_PROVIDER_TYPE = 'omie';
/**
 * Sprint 14 (R1): o primeiro provedor que não é um ERP — a planilha/extrato
 * que o cliente manda, lida pelo mapeamento de colunas dele. Nasce SEM
 * credencial (o adaptador não declara `verificar_credencial`).
 */
export const FILE_PROVIDER_TYPE = 'arquivo';

export interface ProviderTypeOption {
  value: string;
  label: string;
  /** Uma frase para o seletor: o que a pessoa está conectando. */
  description: string;
  /**
   * Espelho de `requires_credentials(tipo)` do backend (`providers/registry.py`):
   * lá é derivado da capacidade `verificar_credencial` do adaptador; aqui, na
   * CRIAÇÃO, a conexão ainda não existe para perguntar à capacidade — então a
   * tabela declara, num lugar só. Para uma conexão EXISTENTE a pergunta certa é
   * `connectionRequiresCredentials`, pela capacidade que a API devolveu.
   */
  requiresCredentials: boolean;
  /**
   * Este tipo LISTA LANÇAMENTOS (`Capability.LISTAR_LANCAMENTOS`)? Mesma razão do
   * `requiresCredentials`: na CRIAÇÃO a conexão ainda não existe, então não há
   * capacidade a consultar e a tabela declara. É o que permite à gaveta não
   * oferecer um tipo que o servidor recusaria com 409 `ORIGEM_JA_CONECTADA` —
   * um cliente tem um tipo de origem de lançamentos só (§4.8, ADR-083-BE).
   *
   * Provedor novo entra aqui E no backend, como o resto desta tabela.
   */
  listsLedger: boolean;
}

/**
 * O seletor da gaveta de conexão — a ÚNICA lista de tipos do front. Provedor
 * novo entra aqui e no backend; nenhum `if (provider_type === …)` na tela.
 */
export const PROVIDER_TYPES: readonly ProviderTypeOption[] = [
  {
    value: OMIE_PROVIDER_TYPE,
    label: 'Omie',
    description: 'ERP com credencial (App Key e App Secret), verificada antes de salvar.',
    requiresCredentials: true,
    listsLedger: true,
  },
  {
    value: FILE_PROVIDER_TYPE,
    label: 'Arquivo (planilha ou extrato)',
    description:
      'Sem credencial: a planilha do mês é lida no envio, pelo mapeamento de colunas do cliente.',
    requiresCredentials: false,
    listsLedger: true,
  },
];

/** Rótulo padrão do tipo, espelho de `_DEFAULT_LABELS` (`client_connections/service.py`). */
export const DEFAULT_PROVIDER_LABEL: Record<string, string> = {
  [OMIE_PROVIDER_TYPE]: 'Omie',
  [FILE_PROVIDER_TYPE]: 'Arquivo',
};

/**
 * Este TIPO guarda um segredo? Tipo fora da tabela devolve `true`: pedir uma
 * credencial que o servidor recusa é um 400 visível; deixar de pedir uma que
 * ele exige seria um formulário que nunca salva.
 */
export function providerRequiresCredentials(providerType: string): boolean {
  return (
    PROVIDER_TYPES.find((option) => option.value === providerType)?.requiresCredentials ?? true
  );
}

/**
 * Esta CONEXÃO tem credencial para testar/trocar? Pela capacidade que a API
 * devolveu — o mesmo predicado do servidor (`requires_credentials`): quem sabe
 * VERIFICAR credencial tem credencial. Para `arquivo` é `false`, e é por isso
 * que "Testar novamente" e os campos de App Key/Secret somem.
 */
export function connectionRequiresCredentials(
  connection: Pick<ClientConnection, 'capabilities'>,
): boolean {
  return connection.capabilities.includes('verificar_credencial');
}

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
