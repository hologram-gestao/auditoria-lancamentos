'use client';

/**
 * Estado de filtro/busca/paginação/aba na URL — convenção do design-system
 * (ADR-000: "Estado de filtro/busca/paginação/aba na URL").
 *
 * Por que URL e não `useState`:
 *   - a view fica LINKÁVEL (copiar/colar o link reproduz a mesma lista);
 *   - voltar/avançar do navegador funciona;
 *   - refresh não perde o recorte que a pessoa montou.
 *
 * `router.replace` (não `push`) com `scroll: false`: trocar um filtro não é
 * navegação — empilhar no histórico obrigaria N cliques em "voltar" para sair
 * da tela, e rolar pro topo a cada tecla digitada seria hostil.
 *
 * Chave com valor `null` (ou string vazia) é REMOVIDA da querystring: o
 * parâmetro ausente é o estado "sem filtro", e uma URL com `?conta=` vazio
 * confundiria tanto o backend quanto o botão "Limpar filtros".
 */
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback } from 'react';

export interface UrlState {
  /** Snapshot atual dos parâmetros (read-only). */
  params: URLSearchParams;
  /** Lê um parâmetro; `null` quando ausente. */
  get: (key: string) => string | null;
  /** Aplica um patch de parâmetros de uma vez (evita replaces em cascata). */
  setMany: (patch: Record<string, string | null>) => void;
  /** Remove todas as chaves informadas. */
  clear: (keys: string[]) => void;
}

export function useUrlState(): UrlState {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const setMany = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value === null || value === '') {
          next.delete(key);
        } else {
          next.set(key, value);
        }
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const clear = useCallback(
    (keys: string[]) => {
      setMany(Object.fromEntries(keys.map((k) => [k, null])));
    },
    [setMany],
  );

  const get = useCallback((key: string) => searchParams.get(key), [searchParams]);

  return { params: searchParams, get, setMany, clear };
}

/**
 * Lê um inteiro positivo da URL com fallback — usado por `page`/`pageSize`.
 * Valor inválido (texto, negativo, zero) degrada para o default em vez de
 * mandar lixo pro backend e colher um 422 na carga inicial.
 */
export function readPositiveInt(raw: string | null, fallback: number): number {
  if (raw === null) return fallback;
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed < 1) return fallback;
  return parsed;
}

/**
 * Lê um valor de enum da URL; qualquer coisa fora da lista vira `undefined`
 * (= sem filtro). Impede que uma URL editada à mão vire 400 no backend.
 */
export function readEnum<T extends string>(
  raw: string | null,
  allowed: readonly T[],
): T | undefined {
  if (raw === null) return undefined;
  return (allowed as readonly string[]).includes(raw) ? (raw as T) : undefined;
}
