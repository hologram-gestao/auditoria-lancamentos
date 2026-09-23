/**
 * A taxonomia de origem, num lugar só (Sprint 9 / R3 · R7).
 *
 * O backend fecha em **três** códigos 409 porque são três situações com
 * remédios diferentes para o usuário — e o front existe para mostrar o remédio,
 * não o erro. Um toast genérico com o `userMessage` juntaria os três num
 * "deu ruim" e mandaria o parceiro reconectar uma origem que nunca existiu.
 *
 * ⚠️ **Os valores são MAIÚSCULOS.** O PRD escreve `sem_conexao` em minúsculas,
 * mas o que viaja no corpo é `ErrorCode.SEM_CONEXAO`
 * (`apps/api/app/core/exceptions.py`) — `"SEM_CONEXAO"`. Ler o `code` do
 * servidor em vez do texto do PRD é a diferença entre tratar e cair no toast.
 *
 * Os `OriginStatus` do detalhe do cliente (`sem_origem` · `ativa` · `erro`)
 * são DERIVADOS das conexões e, de propósito, espelham os mesmos três estados:
 * a tela decide pelo estado o que oferecer, e o 409 só aparece se ela oferecer
 * errado.
 */
import { ApiError } from '@/lib/api/client';
import type { OriginStatus } from '@/lib/contracts';

/** Os três códigos 409 da taxonomia fechada do R3. */
export const ORIGIN_ERROR_CODES = ['SEM_CONEXAO', 'ORIGEM_COM_ERRO', 'CAPACIDADE_AUSENTE'] as const;

export type OriginErrorCode = (typeof ORIGIN_ERROR_CODES)[number];

/**
 * O código da taxonomia embutido no erro, ou `null` quando o erro é outra
 * coisa (aí sim o caller degrada com toast/`ErrorState`).
 *
 * Recebe `unknown` de propósito: é chamada direto sobre o `error` do TanStack
 * Query, que não é tipado como `ApiError`.
 */
export function originErrorCode(error: unknown): OriginErrorCode | null {
  if (!(error instanceof ApiError)) return null;
  const match = ORIGIN_ERROR_CODES.find((code) => code === error.code);
  return match ?? null;
}

/** Açúcar para `onError`/`isError`: "isto é estado de origem, não falha?". */
export function isOriginError(error: unknown): boolean {
  return originErrorCode(error) !== null;
}

interface OriginCopy {
  /** Título curto — é o que distingue os três na tela. */
  title: string;
  /** O que aconteceu e por quê, sem jargão de framework. */
  description: string;
  /**
   * Rótulo da ação, ou `null` quando **não há nada a consertar**
   * (`CAPACIDADE_AUSENTE`: o provedor conectado simplesmente não faz aquilo).
   */
  actionLabel: string | null;
}

/**
 * Copy por código — distinta nos três, como o PRD cobra.
 *
 * Não reaproveita o `userMessage` do servidor: ele é uma frase só, escrita para
 * quem chamou a API crua. Aqui a tela tem título, explicação e caminho de saída,
 * e o texto muda conforme quem está olhando pode ou não conectar.
 */
export const ORIGIN_ERROR_COPY: Record<OriginErrorCode, OriginCopy> = {
  SEM_CONEXAO: {
    title: 'Este cliente não tem origem conectada',
    description:
      'Nenhum sistema foi conectado a este cliente ainda, então não há de onde buscar os lançamentos.',
    actionLabel: 'Conectar origem',
  },
  ORIGEM_COM_ERRO: {
    title: 'A origem deste cliente está com erro',
    description:
      'A origem existe, mas não está ativa — normalmente porque a credencial foi recusada ou trocada no sistema de origem.',
    actionLabel: 'Reconectar origem',
  },
  CAPACIDADE_AUSENTE: {
    title: 'A origem conectada não faz esta operação',
    description:
      'A origem deste cliente está ativa, mas o sistema conectado não oferece esta operação. Use outra origem ou outro caminho.',
    actionLabel: null,
  },
};

/**
 * Copy do BLOCO de origem no painel do cliente, por `origin_status`.
 *
 * `erro` diz **"origem com erro"** e oferece **Reconectar** — nunca "sem
 * origem", que mandaria o usuário criar uma conexão que já existe (R7).
 */
export const ORIGIN_STATUS_COPY: Record<
  OriginStatus,
  { title: string; description: string; actionLabel: string | null }
> = {
  sem_origem: {
    title: 'Sem origem conectada',
    description:
      'Este cliente é um cadastro completo e funciona sem sistema conectado. Conecte uma origem quando houver uma para ligar.',
    actionLabel: 'Conectar origem',
  },
  ativa: {
    title: 'Origem ativa',
    description: 'Há uma origem ativa neste cliente. As telas que dependem dela estão liberadas.',
    actionLabel: null,
  },
  erro: {
    title: 'Origem com erro',
    description:
      'A origem deste cliente existe, mas nenhuma está ativa. Atualize a credencial ou teste a conexão novamente.',
    actionLabel: 'Reconectar',
  },
};

/**
 * Para onde o usuário vai resolver — o painel do cliente, que é a tela onde a
 * origem se conecta (e a única que responde 200 sem origem, por decisão do R6).
 */
export function originFixPath(clientId: string): string {
  return `/clientes/${clientId}/painel`;
}
