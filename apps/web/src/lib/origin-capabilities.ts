/**
 * O que a origem do cliente SABE FAZER — decidido pela capacidade, num lugar só
 * (Sprint 9 / R3 · R6, generalizado na Sprint 14 / R5).
 *
 * Espelho do predicado ÚNICO do servidor
 * (`modules/client_connections/capability.py`): uma conexão é capaz quando
 * está **ativa E o tipo declara a capacidade**; a taxonomia é fechada em três
 * (`sem_conexao` → conectar · `origem_com_erro` → reconectar ·
 * `capacidade_ausente` → nada a consertar), e a ordem do diagnóstico importa.
 *
 * Capacidade é DADO (vem em `connections[].capabilities`), e é por isso que a
 * tela consegue esconder a ação em vez de oferecê-la e receber o 409 depois.
 * **Isto não é segurança** (o backend decide de qualquer forma); é a regra da
 * §4.9: mostrar ação que o servidor nega é defeito.
 *
 * Por que aqui e não `if (provider_type === 'arquivo')` nas telas: a origem por
 * arquivo (S14) é o primeiro provedor que não faz TUDO — não lista contas, não
 * lista títulos, não verifica credencial —, e cada tela que perguntasse pelo
 * tipo repetiria a tabela de capacidades do adaptador à mão. Perguntar pela
 * capacidade é o que faz o terceiro provedor entrar sem tocar em tela nenhuma.
 */
import { FILE_PROVIDER_TYPE } from '@/lib/api/client-connections';
import type { ClientConnection, OriginStatus, ProviderCapability } from '@/lib/contracts';
import type { OriginErrorCode } from '@/lib/origin-state';

type ConnectionLike = Pick<ClientConnection, 'provider_type' | 'status' | 'capabilities'>;

/**
 * O TIPO da conexão declara a capacidade, esteja ela ativa ou não? Para o que
 * DESCREVE a conexão (a linha "Sincronizado há X" de uma Omie em erro continua
 * verdadeira), não para oferecer ação — ação usa `connectionSupports`.
 */
export function connectionDeclares(
  connection: Pick<ClientConnection, 'capabilities'>,
  capability: ProviderCapability,
): boolean {
  return connection.capabilities.includes(capability);
}

/** A conexão, sozinha, é capaz disso? (ativa **e** o tipo declara) — `connection_supports`. */
export function connectionSupports(
  connection: ConnectionLike,
  capability: ProviderCapability,
): boolean {
  return connection.status === 'ativa' && connection.capabilities.includes(capability);
}

/**
 * A conexão que atenderia à capacidade, ou `null`. A PRIMEIRA ativa que
 * declara, na ordem em que a API devolveu — a mesma escolha de
 * `select_capable_connection`, para a tela não apontar para uma origem
 * diferente da que o servidor vai usar.
 */
export function selectCapableConnection<T extends ConnectionLike>(
  connections: readonly T[],
  capability: ProviderCapability,
): T | null {
  return connections.find((c) => connectionSupports(c, capability)) ?? null;
}

export function originHasCapability(
  connections: readonly ConnectionLike[],
  capability: ProviderCapability,
): boolean {
  return selectCapableConnection(connections, capability) !== null;
}

/**
 * A conexão do tipo ARQUIVO do cliente (em qualquer estado), ou `null`. É o
 * ÚNICO lugar que compara o tipo: a aba "Origem por arquivo" e o envio
 * perguntam aqui, nunca `provider_type === 'arquivo'` na tela.
 */
export function fileConnectionOf<T extends ConnectionLike>(connections: readonly T[]): T | null {
  return connections.find((c) => c.provider_type === FILE_PROVIDER_TYPE) ?? null;
}

/**
 * O cliente tem uma origem por ARQUIVO conectada (em qualquer estado)? É o que
 * decide se a aba "Origem por arquivo" existe no menu do cliente — a aba é onde
 * o mapeamento se configura, então ela precisa aparecer mesmo com a conexão
 * inativa ou com erro.
 */
export function hasFileConnection(connections: readonly ConnectionLike[]): boolean {
  return fileConnectionOf(connections) !== null;
}

/**
 * Os tipos que a gaveta de CONECTAR pode oferecer neste cliente (86e3g9u3w).
 *
 * Espelho da trava do servidor (`ORIGEM_JA_CONECTADA`, §4.8): um cliente tem um
 * tipo de origem de LANÇAMENTOS só, então, se já existe uma conexão que lista
 * lançamentos (em qualquer estado — a trava não olha status), todo tipo que
 * também lista e é DIFERENTE dela seria 409. Mostrar ação que o servidor nega é
 * defeito (§4.9), e aqui o custo era preencher a gaveta inteira para descobrir
 * no submit.
 *
 * O tipo da conexão existente CONTINUA na lista: o servidor só recusa tipo
 * diferente — duas conexões Omie com rótulos distintos são permitidas.
 * Tipo que não lista lançamentos nunca é escondido.
 */
export function connectableProviderTypes<T extends { value: string; listsLedger: boolean }>(
  options: readonly T[],
  connections: readonly ConnectionLike[],
): T[] {
  const ledger = connections.find((c) => c.capabilities.includes('listar_lancamentos')) ?? null;
  if (ledger === null) return [...options];
  return options.filter((o) => !o.listsLedger || o.value === ledger.provider_type);
}

/**
 * A base de movimentos deste cliente é alimentada pelo ENVIO do arquivo, não
 * por sincronização? Espelho da checagem do servidor em
 * `ClientMovementsSyncService.sync` (S14): a conexão que atende
 * `listar_lancamentos` é do tipo `arquivo` → `POST …/movements/sync` responde
 * 409 `ORIGEM_POR_ARQUIVO`, e a tela troca "Sincronizar" por "Enviar arquivo".
 */
export function originIsFileBased(connections: readonly ConnectionLike[]): boolean {
  const capable = selectCapableConnection(connections, 'listar_lancamentos');
  return capable !== null && capable.provider_type === FILE_PROVIDER_TYPE;
}

/**
 * O código da taxonomia que o servidor responderia a uma ação que precisa de
 * `capability`, ou `null` quando a ação está liberada — decidido ANTES do
 * clique, a partir do detalhe do cliente.
 *
 * `origin_status` (derivado no servidor) responde pelos dois primeiros estados;
 * `CAPACIDADE_AUSENTE` só sai quando a lista de conexões veio E tem alguma
 * ativa E nenhuma delas declara a capacidade. Sem a lista (detalhe antigo em
 * cache, fixture de teste) ou com a lista vazia apesar de `ativa` (a conexão
 * legada sintetizada, S9), a decisão fica com o servidor — a tela não pode
 * contradizer o `origin_status` que ele mesmo calculou.
 */
export function originCodeFor(
  originStatus: OriginStatus,
  connections: readonly ConnectionLike[] | undefined,
  capability: ProviderCapability,
): OriginErrorCode | null {
  if (originStatus === 'sem_origem') return 'SEM_CONEXAO';
  if (originStatus === 'erro') return 'ORIGEM_COM_ERRO';
  if (connections === undefined) return null;
  const active = connections.filter((c) => c.status === 'ativa');
  if (active.length === 0) return null;
  return active.some((c) => c.capabilities.includes(capability)) ? null : 'CAPACIDADE_AUSENTE';
}
