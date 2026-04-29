'use client';

/**
 * Seção "Histórico de Conciliações" — Doc §10.1.
 *
 * Filtros locais via `useState` (não persistem em URL — pode evoluir num
 * próximo passo). Quando filtros mudam, `setPage(1)` mantém UX consistente
 * (sem ficar em página inexistente).
 *
 * Paginação: 10 por página fixo (Doc §10.1). `keepPreviousData` evita
 * flash de skeleton quando o usuário navega entre páginas.
 *
 * Resolução do nome da conta: o filtro tem `accounts` do detalhe; cada card
 * recebe o nome resolvido via lookup. Conta inexistente → "Conta #{id}"
 * (provavelmente removida no Omie depois da conciliação ter rodado).
 */

import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useReconciliationsList } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import type { BankAccount, ReconciliationsListParams } from '@/lib/api/clients';

import { ReconciliationCard } from './reconciliation-card';

const PAGE_SIZE = 10;
const ALL_ACCOUNTS_VALUE = '__all__';

interface ReconciliationsSectionProps {
  clientId: string;
  accounts: BankAccount[];
}

export function ReconciliationsSection({ clientId, accounts }: ReconciliationsSectionProps) {
  const [accountFilter, setAccountFilter] = useState<string>(ALL_ACCOUNTS_VALUE);
  const [monthFilter, setMonthFilter] = useState<string>('');
  const [page, setPage] = useState(1);

  // Reseta paginação quando filtros mudam — evita ficar em página inexistente.
  useEffect(() => {
    setPage(1);
  }, [accountFilter, monthFilter]);

  const queryParams = useMemo<ReconciliationsListParams>(() => {
    const params: ReconciliationsListParams = { page, pageSize: PAGE_SIZE };
    if (accountFilter !== ALL_ACCOUNTS_VALUE) {
      const parsed = Number(accountFilter);
      if (Number.isFinite(parsed)) params.omie_conta_id = parsed;
    }
    const month = monthFilter.trim();
    if (month) params.month = month;
    return params;
  }, [page, accountFilter, monthFilter]);

  const { data, isLoading, isFetching, isError, error } = useReconciliationsList(
    clientId,
    queryParams,
  );

  const accountLookup = useMemo(() => {
    const map = new Map<number, string>();
    for (const account of accounts) {
      map.set(account.omie_conta_id, account.name);
    }
    return map;
  }, [accounts]);

  const sessions = data?.data ?? [];
  const totalPages = data?.pagination.totalPages ?? 0;
  const total = data?.pagination.total ?? 0;
  const hasFilters = accountFilter !== ALL_ACCOUNTS_VALUE || monthFilter.length > 0;

  function clearFilters() {
    setAccountFilter(ALL_ACCOUNTS_VALUE);
    setMonthFilter('');
  }

  return (
    <section aria-labelledby="reconciliations-heading" className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="reconciliations-heading" className="text-lg font-semibold">
          Conciliações
        </h2>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:gap-4">
        <div className="flex-1 space-y-1">
          <label htmlFor="account-filter" className="text-muted-foreground text-xs font-medium">
            Conta
          </label>
          <Select value={accountFilter} onValueChange={setAccountFilter}>
            <SelectTrigger id="account-filter" aria-label="Filtrar por conta">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_ACCOUNTS_VALUE}>Todas</SelectItem>
              {accounts.map((account) => (
                <SelectItem key={account.id} value={String(account.omie_conta_id)}>
                  {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex-1 space-y-1">
          <label htmlFor="month-filter" className="text-muted-foreground text-xs font-medium">
            Mês de referência
          </label>
          <input
            id="month-filter"
            type="month"
            value={monthFilter}
            onChange={(e) => setMonthFilter(e.target.value)}
            className="border-input bg-background ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Filtrar por mês de referência"
          />
        </div>

        {hasFilters && (
          <Button variant="ghost" size="sm" onClick={clearFilters} className="sm:mb-0.5">
            Limpar filtros
          </Button>
        )}
      </div>

      <div className="space-y-3" aria-busy={isFetching} aria-live="polite">
        {isLoading ? (
          <SessionsSkeleton />
        ) : isError ? (
          <div className="bg-destructive/5 text-destructive border-destructive/30 rounded-lg border p-4 text-sm">
            {error instanceof ApiError
              ? error.userMessage
              : 'Não foi possível carregar o histórico.'}
          </div>
        ) : sessions.length === 0 ? (
          <div className="text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
            {hasFilters
              ? 'Nenhuma conciliação encontrada com esses filtros.'
              : "Nenhuma conciliação realizada ainda. Clique em 'Nova Conciliação' para começar."}
          </div>
        ) : (
          sessions.map((session) => (
            <ReconciliationCard
              key={session.id}
              session={session}
              accountName={
                accountLookup.get(session.omie_conta_id) ?? `Conta #${session.omie_conta_id}`
              }
            />
          ))
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-muted-foreground text-xs">
            {total} sessã{total === 1 ? 'o' : 'es'}
            {isFetching ? ' · atualizando...' : ''}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || isLoading}
              aria-label="Página anterior"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              Anterior
            </Button>
            <span className="text-muted-foreground text-sm">
              Página {page} de {totalPages}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => p + 1)}
              disabled={page >= totalPages || isLoading}
              aria-label="Próxima página"
            >
              Próxima
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function SessionsSkeleton() {
  return (
    <>
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
          <div className="flex justify-between">
            <div className="bg-muted h-4 w-1/3 animate-pulse rounded" />
            <div className="bg-muted h-5 w-24 animate-pulse rounded-full" />
          </div>
          <div className="bg-muted h-3 w-2/3 animate-pulse rounded" />
          <div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
        </div>
      ))}
    </>
  );
}
