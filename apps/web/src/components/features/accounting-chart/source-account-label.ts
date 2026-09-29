/**
 * Como uma conta de ORIGEM aparece na tela (Sprint 16 — FRONT 16.5 / R3).
 *
 * O servidor devolve só identificadores (`sourceType` + `sourceAccountId`,
 * `null` = o slot da conta PADRÃO). O nome legível vem de onde já existe:
 *   - Omie: o `sourceAccountId` é o `nCodCC` da conta corrente, e o nome vem do
 *     cache L1 de contas que o detalhe do cliente já traz (`accounts`);
 *   - arquivo (e qualquer tipo sem cadastro de contas): o próprio identificador,
 *     que é o valor da coluna de conta da planilha.
 * O tipo é comparado AQUI e só aqui (ADR-042-FE).
 */
import { useMemo } from 'react';

import { useClientDetail } from '@/hooks/use-clients';
import { DEFAULT_PROVIDER_LABEL, OMIE_PROVIDER_TYPE } from '@/lib/api/client-connections';

export interface SourceAccountRef {
  sourceType: string;
  sourceAccountId?: string | null;
}

/** Contas correntes do Omie (`nCodCC` → nome), do detalhe do cliente. */
export type OmieAccountNames = ReadonlyMap<string, string>;

/**
 * `nCodCC` → nome, do cache L1 de contas que o detalhe do cliente já traz (S7;
 * o shell carregou o detalhe, então vem do cache, sem segundo request).
 */
export function useOmieAccountNames(clientId: string): OmieAccountNames {
  const clientDetail = useClientDetail(clientId);
  const accounts = clientDetail.data?.accounts;
  return useMemo(() => {
    const map = new Map<string, string>();
    for (const account of accounts ?? []) map.set(String(account.omie_conta_id), account.name);
    return map;
  }, [accounts]);
}

export function providerLabel(sourceType: string): string {
  return DEFAULT_PROVIDER_LABEL[sourceType] ?? sourceType;
}

/** O nome principal da linha: conta corrente, identificador ou o slot padrão. */
export function sourceAccountName(ref: SourceAccountRef, omieNames: OmieAccountNames): string {
  const id = ref.sourceAccountId ?? null;
  if (id === null) return 'Conta padrão (arquivo sem coluna de conta)';
  if (ref.sourceType === OMIE_PROVIDER_TYPE) {
    const name = omieNames.get(id);
    if (name !== undefined) return name;
  }
  return id;
}

/**
 * A linha de apoio: o tipo e, quando o nome veio do cadastro, o identificador
 * — é ele que a pessoa confere contra a origem.
 */
export function sourceAccountDetail(ref: SourceAccountRef, omieNames: OmieAccountNames): string {
  const id = ref.sourceAccountId ?? null;
  const type = providerLabel(ref.sourceType);
  if (id === null) return `${type} · linhas sem conta de origem`;
  if (ref.sourceType === OMIE_PROVIDER_TYPE && omieNames.has(id)) return `${type} · conta ${id}`;
  return type;
}
