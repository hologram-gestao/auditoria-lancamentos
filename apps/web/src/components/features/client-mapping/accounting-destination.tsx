'use client';

/**
 * O destino `conta_contabil` do de-para (Sprint 16 — FRONT 16.6 / R2 · R3 · R4).
 *
 * Nesse destino — e SÓ nele — o alvo é uma conta do plano contábil DO CLIENTE
 * (não o catálogo da organização), a decisão leva um histórico padrão, e a
 * prévia mostra a partida completa (conta, conta do banco, histórico). Os
 * outros destinos seguem exatamente como na S12: tudo o que muda passa por
 * `isAccountingDestination`, o ÚNICO lugar em que o tipo é comparado (mesma
 * regra do `INHERITING_DESTINATION_TYPE`).
 *
 * Aqui moram as peças que a lista, a gaveta e a prévia compartilham: o
 * histórico truncado com dica acessível, o selo de decisão legada, o estado das
 * contas de origem sem conta do banco (prévia e 409) e o leitor desse 409.
 */

import { AlertTriangle, ArrowRight } from 'lucide-react';
import Link from 'next/link';

import {
  sourceAccountDetail,
  sourceAccountName,
  useOmieAccountNames,
} from '@/components/features/accounting-chart/source-account-label';
import { accountingChartPath } from '@/components/features/navigation/nav-items';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { ApiError } from '@/lib/api/client';
import type { PendingSourceAccount } from '@/lib/contracts';
import { cn } from '@/lib/utils';

/** O destino cujo alvo é o plano contábil do cliente (BACK 16.2). */
export const ACCOUNTING_DESTINATION_TYPE = 'conta_contabil';

export function isAccountingDestination(destinationType: string): boolean {
  return destinationType === ACCOUNTING_DESTINATION_TYPE;
}

const baseBadge =
  'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

/**
 * Selo de decisão LEGADA: aponta o catálogo da organização, anterior ao plano
 * do cliente. Segue legível e sem conversão automática — precisa ser refeita.
 */
export function LegacyRedoBadge() {
  return (
    <span
      className={cn(baseBadge, 'bg-warning-muted text-warning ring-warning/30')}
      data-testid="mapping-legacy-badge"
    >
      <AlertTriangle className="h-3 w-3" aria-hidden="true" />
      Refazer no plano do cliente
    </span>
  );
}

/**
 * O histórico padrão numa célula estreita: truncado, com a íntegra numa dica
 * ACESSÍVEL — `role="img"` + `aria-label` com o texto inteiro + `tabIndex={0}`
 * (o padrão de `situation-badge`), nunca `title` nativo, que não aparece no
 * toque nem no teclado.
 */
export function TruncatedHistory({
  history,
  className,
}: {
  history: string | null | undefined;
  className?: string;
}) {
  if (history == null || history.trim() === '') {
    return <span className="text-muted-foreground text-sm">Sem histórico</span>;
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={`Histórico padrão: ${history}`}
            className={cn(
              'focus-visible:ring-ring block max-w-56 truncate rounded-sm text-sm focus-visible:outline-none focus-visible:ring-2',
              className,
            )}
          >
            {history}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm whitespace-pre-wrap text-xs leading-snug">
          {history}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function readPendingList(value: unknown): PendingSourceAccount[] {
  if (!Array.isArray(value)) return [];
  const list: PendingSourceAccount[] = [];
  for (const item of value) {
    if (typeof item !== 'object' || item === null) continue;
    const { sourceType, sourceAccountId } = item as {
      sourceType?: unknown;
      sourceAccountId?: unknown;
    };
    if (typeof sourceType !== 'string') continue;
    list.push({
      sourceType,
      sourceAccountId: typeof sourceAccountId === 'string' ? sourceAccountId : null,
    });
  }
  return list;
}

/**
 * O 409 `CONTA_DO_BANCO_PENDENTE` da materialização (BACK 16.3): as contas de
 * origem sem conta do banco, só por identificadores. `null` = outro erro.
 */
export function readBankAccountPending(error: unknown): PendingSourceAccount[] | null {
  if (!(error instanceof ApiError) || error.code !== 'CONTA_DO_BANCO_PENDENTE') return null;
  return readPendingList(error.details.pendingSourceAccounts);
}

/**
 * Contas de origem sem conta do BANCO — o estado que leva a pessoa a associar.
 * Duas entradas: a prévia (aviso antes de materializar) e o 409 da
 * materialização (recusa, `role="alert"`). Nomes pelo cache de contas do Omie
 * quando existem; senão o identificador. Nunca toast.
 *
 * A lista é de todos; o verbo "Associe" e o botão são só de quem pode associar
 * (`manage_client_accounting_chart`, cliente aberto). O `client_manager` chega
 * aqui pelo 409 (materializa com `manage_client_mapping`) e lê a quem pedir;
 * o encerrado lê o motivo real (ADR-053-FE).
 */
export function PendingSourceAccountsNotice({
  clientId,
  accounts,
  canManageChart,
  isClosed,
  refused = false,
}: {
  clientId: string;
  accounts: readonly PendingSourceAccount[];
  /** `manage_client_accounting_chart`. */
  canManageChart: boolean;
  isClosed: boolean;
  /** `true` = veio do 409 (nada foi gravado); `false` = aviso da prévia. */
  refused?: boolean;
}) {
  const omieNames = useOmieAccountNames(clientId);
  const canAssociate = canManageChart && !isClosed;
  const guidance = canAssociate
    ? 'Associe a conta do banco de cada uma em Plano contábil › Conta do banco.'
    : isClosed
      ? 'Cliente encerrado: a associação não pode mais ser alterada.'
      : 'Peça a quem administra o plano contábil do cliente no escritório para associar a conta do banco.';
  return (
    <div
      role={refused ? 'alert' : 'status'}
      data-testid={refused ? 'bank-pending-refusal' : 'bank-pending-notice'}
      className={cn(
        'space-y-3 rounded-lg p-4 text-sm ring-1 ring-inset',
        refused
          ? 'bg-destructive-muted text-destructive ring-destructive/30'
          : 'bg-warning-muted text-warning ring-warning/30',
      )}
    >
      <p className="flex items-start gap-2 font-medium">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>
          {refused
            ? 'Materialização recusada: há contas de origem sem conta contábil do banco — nada foi gravado.'
            : accounts.length === 1
              ? '1 conta de origem sem conta contábil do banco'
              : `${accounts.length} contas de origem sem conta contábil do banco`}
        </span>
      </p>
      <p>
        Sem a conta do banco a partida fica sem um dos lados, e a materialização neste destino é
        recusada enquanto houver linha com conta vinda delas. {guidance}
      </p>
      {accounts.length > 0 && (
        <ul aria-label="Contas de origem sem conta do banco" className="space-y-1">
          {accounts.map((account) => (
            <li
              key={`${account.sourceType}:${account.sourceAccountId ?? '__default__'}`}
              className="bg-background text-foreground rounded-md px-3 py-1.5"
            >
              <span className="font-medium">{sourceAccountName(account, omieNames)}</span>
              <span className="text-muted-foreground block text-xs">
                {sourceAccountDetail(account, omieNames)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {canAssociate && (
        <Button asChild variant="outline" size="sm">
          <Link href={accountingChartPath(clientId, 'conta-do-banco')}>
            Associar conta do banco
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </Button>
      )}
    </div>
  );
}
