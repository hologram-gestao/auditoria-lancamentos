'use client';

/**
 * Seção "Conta do banco" da tela Plano contábil (Sprint 16 — FRONT 16.5 / R3).
 *
 * Lista as contas de ORIGEM do cliente — as que aparecem na base de
 * movimentos, as já associadas e, quando o SERVIDOR o oferece, o slot da conta
 * padrão (o arquivo sem coluna de conta; o backend decide pela capacidade da
 * origem, e a tela não reinventa essa regra) — cada uma com a conta contábil do
 * banco associada ou PENDENTE, em destaque por token `warning`.
 *
 * Pendente importa porque a materialização do de-para no destino Conta contábil
 * é recusada (409) enquanto houver linha com alvo vinda de conta sem banco. É
 * para cá que o de-para manda (`#conta-do-banco`).
 *
 * Editar pede `manage_client_accounting_chart` e cliente aberto; a ação SOME
 * para quem não pode (nunca desabilitada). A leitura é de todos.
 */

import { AlertTriangle, Pencil } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useSourceAccounts } from '@/hooks/use-client-accounting-chart';
import { ApiError } from '@/lib/api/client';
import type { SourceAccountEntry } from '@/lib/contracts';
import { cn } from '@/lib/utils';

import { accountingAccountLabel } from './accounting-account-combobox';
import { BankAccountBindingSheet } from './bank-account-binding-sheet';
import {
  sourceAccountDetail,
  sourceAccountName,
  useOmieAccountNames,
} from './source-account-label';

export const BANK_ACCOUNTS_SECTION_ID = 'conta-do-banco';

const baseBadge =
  'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

interface BankAccountsSectionProps {
  clientId: string;
  /** `manage_client_accounting_chart` E cliente aberto. */
  canEdit: boolean;
  /** O cliente já tem plano contábil? Sem plano, não há conta para escolher. */
  hasPlan: boolean;
}

function entryKey(entry: SourceAccountEntry): string {
  return `${entry.sourceType}:${entry.sourceAccountId ?? '__default__'}`;
}

export function BankAccountsSection({ clientId, canEdit, hasPlan }: BankAccountsSectionProps) {
  const query = useSourceAccounts(clientId);
  const [editing, setEditing] = useState<SourceAccountEntry | null>(null);
  const [editKey, setEditKey] = useState(0);

  const omieNames = useOmieAccountNames(clientId);

  const entries = query.data ?? [];
  const pendingCount = entries.filter((entry) => entry.pending).length;
  const showActions = canEdit && hasPlan;

  function openEditor(entry: SourceAccountEntry) {
    setEditKey((k) => k + 1);
    setEditing(entry);
  }

  return (
    <section
      id={BANK_ACCOUNTS_SECTION_ID}
      aria-labelledby="bank-accounts-heading"
      className="scroll-mt-6 space-y-3"
      data-testid="bank-accounts-section"
    >
      <div className="space-y-1">
        <h3 id="bank-accounts-heading" className="text-base font-semibold">
          Conta do banco
        </h3>
        <p className="text-muted-foreground text-sm">
          Para cada conta de origem, a conta contábil do banco — o lado fixo da partida. Conta de
          origem sem associação não cai na conta padrão: enquanto estiver pendente, o de-para no
          destino Conta contábil não materializa as linhas dela.
        </p>
      </div>

      {pendingCount > 0 && (
        <p
          role="status"
          data-testid="bank-accounts-pending"
          className="bg-warning-muted text-warning ring-warning/30 flex items-start gap-2 rounded-lg p-3 text-sm ring-1 ring-inset"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {pendingCount === 1
              ? '1 conta de origem sem conta do banco.'
              : `${pendingCount} contas de origem sem conta do banco.`}
            {!hasPlan && ' Importe o plano contábil para poder associar.'}
            {hasPlan && !canEdit && ' Peça a alguém do escritório para associar.'}
          </span>
        </p>
      )}

      {query.isError ? (
        <div
          role="alert"
          className="bg-destructive-muted text-destructive space-y-2 rounded-lg p-4 text-sm"
        >
          <p className="font-medium">Não foi possível carregar as contas de origem</p>
          <p>
            {query.error instanceof ApiError
              ? query.error.userMessage
              : 'Tente novamente em instantes.'}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={() => void query.refetch()}>
            Tentar novamente
          </Button>
        </div>
      ) : (
        <TableCard>
          <Table scrollRegionLabel="Contas de origem e conta do banco (rolável)">
            <TableHeader>
              <TableRow>
                <TableHead>Conta de origem</TableHead>
                <TableHead>Conta do banco</TableHead>
                {showActions && (
                  <TableHead className="w-24 text-right">
                    <span className="sr-only">Ações</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.isLoading
                ? Array.from({ length: 2 }).map((_, index) => (
                    <TableRow key={index} aria-hidden="true">
                      {Array.from({ length: showActions ? 3 : 2 }).map((__, cell) => (
                        <TableCell key={cell}>
                          <div className="bg-muted h-4 w-full animate-pulse rounded" />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                : entries.map((entry) => (
                    <TableRow key={entryKey(entry)} data-pending={entry.pending || undefined}>
                      <TableCell className="min-w-40 whitespace-normal">
                        <span className="font-medium">{sourceAccountName(entry, omieNames)}</span>
                        <span className="text-muted-foreground block text-xs">
                          {sourceAccountDetail(entry, omieNames)}
                        </span>
                      </TableCell>
                      <TableCell className="min-w-40 whitespace-normal">
                        {entry.bankAccount != null ? (
                          <div className="space-y-1">
                            <span className="tabular-nums">
                              {accountingAccountLabel(entry.bankAccount)}
                            </span>
                            {!entry.bankAccount.postable && (
                              <span
                                className={cn(
                                  baseBadge,
                                  'bg-muted text-muted-foreground ring-border',
                                )}
                              >
                                Não lançável hoje
                              </span>
                            )}
                          </div>
                        ) : (
                          <span
                            className={cn(
                              baseBadge,
                              'bg-warning-muted text-warning ring-warning/30',
                            )}
                          >
                            <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                            Pendente
                          </span>
                        )}
                      </TableCell>
                      {showActions && (
                        <TableCell className="text-right">
                          <Button
                            type="button"
                            variant={entry.pending ? 'secondary' : 'ghost'}
                            size="sm"
                            onClick={() => openEditor(entry)}
                            aria-label={`${entry.pending ? 'Associar' : 'Trocar'} conta do banco de ${sourceAccountName(entry, omieNames)}`}
                          >
                            <Pencil className="h-4 w-4" aria-hidden="true" />
                            {entry.pending ? 'Associar' : 'Trocar'}
                          </Button>
                        </TableCell>
                      )}
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
          {!query.isLoading && entries.length === 0 && (
            <TableEmpty>
              <p className="text-muted-foreground text-center text-sm">
                Nenhuma conta de origem ainda. Elas aparecem aqui quando a base de movimentos do
                cliente for sincronizada ou o primeiro arquivo for enviado.
              </p>
            </TableEmpty>
          )}
        </TableCard>
      )}

      {showActions && (
        <BankAccountBindingSheet
          key={editKey}
          clientId={clientId}
          entry={editing}
          omieNames={omieNames}
          onOpenChange={(open) => {
            if (!open) setEditing(null);
          }}
        />
      )}
    </section>
  );
}
