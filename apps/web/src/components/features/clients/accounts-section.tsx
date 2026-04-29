'use client';

/**
 * Seção "Contas Bancárias" da tela de detalhe — Doc §10.1.
 *
 * Renderiza:
 *   - Grid responsivo (1/2/3 colunas) de cards de conta.
 *   - Skeletons enquanto carrega (loading inicial).
 *   - Mensagem de erro com botão "Tentar novamente" (chama `onRetry`).
 *   - Estado vazio quando o Omie devolveu zero contas.
 *   - Timestamp "Sincronizado há Xh" — se < 1min mostra "Sincronizado agora";
 *     se `accounts_synced_at` é null e a lista ainda está vazia, exibe
 *     "Sincronizando contas pela primeira vez…" (cliente novo).
 *
 * Não cuida do botão "Sincronizar contas" — ele vive no header da tela
 * (mais visível) e dispara o refetch via TanStack invalidate.
 */

import { formatDistanceToNow } from 'date-fns';
import { ptBR } from 'date-fns/locale';
import { AlertTriangle } from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { BankAccount } from '@/lib/api/clients';

import { AccountCard } from './account-card';

interface AccountsSectionProps {
  accounts: BankAccount[];
  syncedAt: string | null;
  isLoading: boolean;
  isError: boolean;
  errorMessage?: string;
  onRetry: () => void;
}

export function AccountsSection({
  accounts,
  syncedAt,
  isLoading,
  isError,
  errorMessage,
  onRetry,
}: AccountsSectionProps) {
  return (
    <section aria-labelledby="accounts-heading" className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="accounts-heading" className="text-lg font-semibold">
          Contas no Omie
        </h2>
      </div>

      {isLoading ? (
        <AccountsSkeleton />
      ) : isError ? (
        <ErrorState message={errorMessage} onRetry={onRetry} />
      ) : accounts.length === 0 ? (
        <EmptyState syncedAt={syncedAt} />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {accounts.map((account) => (
            <AccountCard key={account.id} account={account} />
          ))}
        </div>
      )}

      {!isError && !isLoading && accounts.length > 0 && (
        <p className="text-muted-foreground text-xs" aria-live="polite">
          {formatSyncedAt(syncedAt)}
        </p>
      )}
    </section>
  );
}

function formatSyncedAt(syncedAt: string | null): string {
  if (syncedAt === null) return 'Ainda não sincronizado.';
  const date = new Date(syncedAt);
  const diffMs = Date.now() - date.getTime();
  // < 60s: "agora"; senão: "há X minutos/horas/dias".
  if (diffMs < 60_000) return 'Sincronizado agora';
  return `Sincronizado ${formatDistanceToNow(date, { addSuffix: true, locale: ptBR })}`;
}

function AccountsSkeleton() {
  return (
    <div
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
      aria-label="Carregando contas"
      aria-busy="true"
    >
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
          <div className="bg-muted h-4 w-2/3 animate-pulse rounded" />
          <div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
          <div className="bg-muted h-3 w-1/3 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}

function ErrorState({ message, onRetry }: { message?: string; onRetry: () => void }) {
  return (
    <div className="bg-destructive/5 text-destructive border-destructive/30 flex flex-col items-start gap-3 rounded-lg border p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-2 text-sm">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{message ?? 'Não foi possível carregar as contas.'}</span>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}

function EmptyState({ syncedAt }: { syncedAt: string | null }) {
  return (
    <div className="text-muted-foreground rounded-lg border border-dashed p-6 text-center text-sm">
      {syncedAt === null
        ? 'Sincronizando contas pela primeira vez…'
        : 'Nenhuma conta cadastrada no Omie.'}
    </div>
  );
}
