'use client';

/**
 * Orquestrador da tela `/clientes/{id}` — Doc §10.1.
 *
 * Estrutura:
 *   - Header: breadcrumb + nome + status + ações (Editar / Sincronizar / Nova).
 *   - Seção contas (cache L1).
 *   - Seção histórico de conciliações (filtros + paginação).
 *
 * O EditClientModal é reusado da S6 sem alterações; ele mesmo invalida
 * `['clients']` no sucesso, então o detalhe re-renderiza com o novo nome/status.
 *
 * Erros de carga do detalhe:
 *   - 404 (cliente inexistente / fora da carteira do manager) → mensagem +
 *     link de volta. O backend usa o mesmo código tanto para "não existe"
 *     quanto para "manager sem acesso", evitando vazamento de existência.
 *   - Outros erros → mensagem genérica + retry.
 *
 * "Nova Conciliação" e "Ver detalhes" levam pra rotas de S8/S12 que ainda não
 * existem — o link funciona, o destino vai 404 até essas sessões serem feitas.
 */

import { ChevronRight, Loader2, Plus, RefreshCw, SquarePen } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { useClientDetail, useSyncAccounts } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

import { AccountsSection } from './accounts-section';
import { ClientStatusBadge } from './client-status-badge';
import { EditClientModal } from './edit-client-modal';
import { ReconciliationsSection } from './reconciliations-section';

export function ClientDetailClient({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);

  const [editOpen, setEditOpen] = useState(false);

  const detailQuery = useClientDetail(clientId);
  const syncMutation = useSyncAccounts(clientId);

  if (currentUser === null) {
    // Layout pai redireciona; só pra satisfazer o type-checker.
    return null;
  }

  if (detailQuery.isLoading) {
    return <DetailSkeleton />;
  }

  if (detailQuery.isError) {
    const err = detailQuery.error;
    const isNotFound = err instanceof ApiError && err.status === 404;
    return (
      <ErrorState
        title={isNotFound ? 'Cliente não encontrado' : 'Não foi possível carregar o cliente'}
        message={
          err instanceof ApiError ? err.userMessage : 'Ocorreu um erro inesperado. Tente novamente.'
        }
        onRetry={() => void detailQuery.refetch()}
        showRetry={!isNotFound}
      />
    );
  }

  const client = detailQuery.data;
  if (!client) return null;

  async function handleSync() {
    try {
      await syncMutation.mutateAsync();
      toast.success('Contas sincronizadas.');
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível sincronizar as contas.';
      toast.error(message);
    }
  }

  const isSyncing = syncMutation.isPending;

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <nav aria-label="Breadcrumb" className="text-muted-foreground text-sm">
          <ol className="flex items-center gap-1.5">
            <li>
              <Link href="/clientes" className="hover:text-foreground hover:underline">
                Clientes
              </Link>
            </li>
            <li aria-hidden="true">
              <ChevronRight className="h-3.5 w-3.5" />
            </li>
            <li className="text-foreground truncate font-medium" aria-current="page">
              {client.name}
            </li>
          </ol>
        </nav>

        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold">{client.name}</h1>
            <ClientStatusBadge active={client.active} />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => setEditOpen(true)}>
              <SquarePen className="h-4 w-4" aria-hidden="true" />
              Editar
            </Button>
            <Button
              variant="outline"
              onClick={() => void handleSync()}
              disabled={isSyncing}
              aria-live="polite"
            >
              {isSyncing ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              {isSyncing ? 'Sincronizando…' : 'Sincronizar contas'}
            </Button>
            <Button asChild>
              <Link href={`/clientes/${clientId}/conciliacao/nova`}>
                <Plus className="h-4 w-4" aria-hidden="true" />
                Nova Conciliação
              </Link>
            </Button>
          </div>
        </div>
      </header>

      <AccountsSection
        accounts={client.accounts}
        syncedAt={client.accounts_synced_at}
        isLoading={false}
        isError={false}
        onRetry={() => void detailQuery.refetch()}
      />

      <ReconciliationsSection clientId={clientId} accounts={client.accounts} />

      <EditClientModal
        open={editOpen}
        onOpenChange={setEditOpen}
        client={client}
        currentUserRole={currentUser.role}
      />
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="space-y-8" aria-busy="true" aria-label="Carregando detalhe do cliente">
      <div className="space-y-3">
        <div className="bg-muted h-3 w-32 animate-pulse rounded" />
        <div className="flex items-center gap-3">
          <div className="bg-muted h-7 w-64 animate-pulse rounded" />
          <div className="bg-muted h-5 w-16 animate-pulse rounded-full" />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
            <div className="bg-muted h-4 w-2/3 animate-pulse rounded" />
            <div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
            <div className="bg-muted h-3 w-1/3 animate-pulse rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}

interface ErrorStateProps {
  title: string;
  message: string;
  onRetry: () => void;
  showRetry: boolean;
}

function ErrorState({ title, message, onRetry, showRetry }: ErrorStateProps) {
  return (
    <div className="space-y-4">
      <nav aria-label="Breadcrumb" className="text-muted-foreground text-sm">
        <Link href="/clientes" className="hover:text-foreground hover:underline">
          ← Voltar para clientes
        </Link>
      </nav>
      <div className="bg-destructive/5 border-destructive/30 text-destructive space-y-3 rounded-lg border p-6">
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="text-sm">{message}</p>
        {showRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Tentar novamente
          </Button>
        )}
      </div>
    </div>
  );
}
