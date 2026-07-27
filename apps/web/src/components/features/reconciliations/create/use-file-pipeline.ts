'use client';

/**
 * Pipeline por ARQUIVO do Step 2 da gaveta (Sprint 4 / R2 + R5).
 *
 * Uma conciliação é *uma conta + um mês* com N partes. Cada parte percorre o
 * mesmo caminho das validações V1–V3 do §11.3, agora N vezes e com feedback
 * individual:
 *
 *   V1 `invalid`   — extensão/tamanho/vazio (Zod, client-side, instantâneo);
 *   V2 `hashing`   — SHA-256 no navegador (o arquivo NÃO trafega para isso);
 *   V3 `checking`  — `/check-duplicate` (conta+mês+hash): gate BARATO antes de
 *                    gastar uma chamada de IA;
 *   V4 `parsing`   — `/parse`, que devolve o statement, o checksum e o
 *                    **file_hash recalculado no servidor** (S0/A10 — é esse que
 *                    vai no payload; o hash do cliente serve só ao V3).
 *
 * **Sequencial de propósito.** `/parse` tem rate limit de 10/min por usuário e
 * cada chamada custa dinheiro na Anthropic; disparar 5 uploads em paralelo
 * renderia 429 no meio do lote e deixaria a gaveta num estado difícil de
 * explicar. Uma parte de cada vez também deixa o progresso legível.
 *
 * **Duplicata NÃO vira parte com erro.** Ela é rejeitada na UI e some do
 * payload: mandar duas partes com o mesmo hash devolveria 422 e derrubaria o
 * lote inteiro ("parte nova é aceita" é critério de aceite). Já falha de
 * EXTRAÇÃO vira parte com `error_code` — é assim que a conciliação registra
 * qual arquivo falhou em vez de nascer silenciosamente incompleta.
 */
import { useCallback, useRef, useState } from 'react';

import { ApiError } from '@/lib/api/client';
import {
  checkDuplicate,
  parseStatement,
  type ParseResult,
  type ReconciliationFilePart,
} from '@/lib/api/reconciliations';
import { sha256Hex } from '@/lib/crypto/hash';
import { fileRulesSchema } from '@/lib/validation/reconciliations';

export type UploadStatus =
  | 'queued'
  | 'hashing'
  | 'checking'
  | 'parsing'
  | 'parsed'
  | 'duplicate'
  | 'invalid'
  | 'error';

export interface UploadItem {
  /** ID local (o backend só conhece o arquivo depois da criação). */
  id: string;
  file: File;
  status: UploadStatus;
  /** SHA-256 client-side — usado no V3 e como fallback nas partes que falharam. */
  clientHash?: string;
  result?: ParseResult;
  /** Código CANÔNICO do erro (vai para o backend). Nunca mensagem interna. */
  errorCode?: string;
  /** Mensagem PT-BR já pronta do backend, para a pessoa ler. */
  errorMessage?: string;
}

/**
 * Códigos aceitos pelo backend em `ReconciliationFileInput.error_code` — é o
 * enum `ErrorCode` de `app/core/exceptions.py`. Qualquer outra coisa (inclusive
 * um `code` novo que o backend passe a devolver) cai em `PARSE_ERROR`, que é
 * verdadeiro: a extração daquela parte não completou.
 *
 * ⚠️ `INVALID_FILE` / `FILE_TOO_LARGE` **não existem** neste enum (a validação
 * de arquivo devolve `VALIDATION_ERROR`) — conferido no código, não de memória.
 */
const CANONICAL_ERROR_CODES = new Set([
  'VALIDATION_ERROR',
  'CONFLICT',
  'DUPLICATE_FILE',
  'RATE_LIMITED',
  'ANTHROPIC_AUTH_ERROR',
  'ANTHROPIC_TIMEOUT',
  'PARSE_ERROR',
  'INTERNAL_ERROR',
]);

const FALLBACK_ERROR_CODE = 'PARSE_ERROR';

function toCanonicalCode(err: unknown): string {
  if (err instanceof ApiError && CANONICAL_ERROR_CODES.has(err.code)) return err.code;
  return FALLBACK_ERROR_CODE;
}

function toUserMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.userMessage : fallback;
}

export interface FilePipelineParams {
  clientId: string;
  omieContaId: number;
  /** `YYYY-MM` (valor do month picker). */
  month: string;
}

export interface FilePipeline {
  items: UploadItem[];
  /** `true` enquanto alguma parte está no meio do caminho. */
  isProcessing: boolean;
  addFiles: (files: File[], params: FilePipelineParams) => Promise<void>;
  remove: (id: string) => void;
  reset: () => void;
  /** Partes prontas para o payload — só as extraídas OK e as que falharam. */
  toParts: () => ReconciliationFilePart[];
}

const RUNNING: UploadStatus[] = ['queued', 'hashing', 'checking', 'parsing'];

export function useFilePipeline(): FilePipeline {
  const [items, setItems] = useState<UploadItem[]>([]);
  // Espelho síncrono: o processamento é sequencial e precisa consultar os
  // hashes já vistos DENTRO do laço, antes do React reconciliar o estado.
  const itemsRef = useRef<UploadItem[]>([]);
  const seq = useRef(0);

  const commit = useCallback((next: UploadItem[]) => {
    itemsRef.current = next;
    setItems(next);
  }, []);

  const patch = useCallback(
    (id: string, changes: Partial<UploadItem>) => {
      commit(itemsRef.current.map((it) => (it.id === id ? { ...it, ...changes } : it)));
    },
    [commit],
  );

  const runOne = useCallback(
    async (id: string, params: FilePipelineParams) => {
      const item = itemsRef.current.find((it) => it.id === id);
      if (item === undefined) return;

      // --- V2: hash client-side ---
      patch(id, { status: 'hashing' });
      let hash: string;
      try {
        hash = await sha256Hex(item.file);
      } catch {
        patch(id, {
          status: 'error',
          errorCode: FALLBACK_ERROR_CODE,
          errorMessage: 'Não foi possível ler o arquivo para calcular a assinatura.',
        });
        return;
      }

      // Mesmo conteúdo duas vezes no MESMO envio: o backend devolveria 422 e
      // derrubaria o lote inteiro. Barramos aqui, com a mensagem certa.
      const alreadyQueued = itemsRef.current.some(
        (it) => it.id !== id && it.clientHash === hash && it.status !== 'duplicate',
      );
      if (alreadyQueued) {
        patch(id, {
          status: 'duplicate',
          clientHash: hash,
          errorMessage: 'Este arquivo já foi adicionado a esta conciliação.',
        });
        return;
      }
      patch(id, { clientHash: hash });

      // --- V3: duplicata no servidor (barato, antes de pagar a IA) ---
      patch(id, { status: 'checking' });
      try {
        const { duplicate } = await checkDuplicate({
          client_id: params.clientId,
          omie_conta_id: params.omieContaId,
          month: params.month,
          hash,
        });
        if (duplicate) {
          patch(id, {
            status: 'duplicate',
            errorMessage: 'Este arquivo já faz parte da conciliação desta conta e mês.',
          });
          return;
        }
      } catch (err) {
        patch(id, {
          status: 'error',
          errorCode: toCanonicalCode(err),
          errorMessage: toUserMessage(err, 'Não foi possível verificar a duplicata.'),
        });
        return;
      }

      // --- V4: extração via IA ---
      patch(id, { status: 'parsing' });
      try {
        const result = await parseStatement({ client_id: params.clientId, file: item.file });
        patch(id, { status: 'parsed', result, clientHash: result.fileHash });
      } catch (err) {
        // O /parse também deduplica por CONTEÚDO contra as sessões ativas do
        // cliente — esse caso é duplicata, não falha de extração.
        if (err instanceof ApiError && err.code === 'DUPLICATE_FILE') {
          patch(id, { status: 'duplicate', errorMessage: err.userMessage });
          return;
        }
        patch(id, {
          status: 'error',
          errorCode: toCanonicalCode(err),
          errorMessage: toUserMessage(err, 'Não foi possível extrair as movimentações.'),
        });
      }
    },
    [patch],
  );

  const addFiles = useCallback(
    async (files: File[], params: FilePipelineParams) => {
      const queued: UploadItem[] = files.map((file) => {
        seq.current += 1;
        const parsed = fileRulesSchema.safeParse(file);
        return {
          id: `f${seq.current}`,
          file,
          status: parsed.success ? ('queued' as const) : ('invalid' as const),
          errorMessage: parsed.success ? undefined : parsed.error.issues[0]?.message,
        };
      });
      commit([...itemsRef.current, ...queued]);

      for (const item of queued) {
        if (item.status === 'invalid') continue;
        await runOne(item.id, params);
      }
    },
    [commit, runOne],
  );

  const remove = useCallback(
    (id: string) => {
      commit(itemsRef.current.filter((it) => it.id !== id));
    },
    [commit],
  );

  const reset = useCallback(() => {
    seq.current = 0;
    commit([]);
  }, [commit]);

  const toParts = useCallback((): ReconciliationFilePart[] => {
    const parts: ReconciliationFilePart[] = [];
    for (const item of itemsRef.current) {
      if (item.status === 'parsed' && item.result !== undefined) {
        parts.push({
          file_hash: item.result.fileHash,
          filename: item.file.name,
          statement: item.result.statement,
        });
        continue;
      }
      // Parte que falhou na EXTRAÇÃO entra registrada, para a tela poder dizer
      // qual foi e oferecer removê-la. Sem hash não há como registrá-la.
      if (item.status === 'error' && item.clientHash !== undefined) {
        parts.push({
          file_hash: item.clientHash,
          filename: item.file.name,
          error_code: item.errorCode ?? FALLBACK_ERROR_CODE,
        });
      }
    }
    return parts;
  }, []);

  return {
    items,
    isProcessing: items.some((it) => RUNNING.includes(it.status)),
    addFiles,
    remove,
    reset,
    toParts,
  };
}
