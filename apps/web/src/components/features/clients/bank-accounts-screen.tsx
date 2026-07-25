'use client';

/**
 * Tela "Contas Bancárias" do cliente — Sprint 4 / R6.
 *
 * Antes era uma seção empilhada no detalhe do cliente; agora é um destino
 * próprio na navegação do cliente, com lista paginada e o botão de extração.
 *
 * **Paginação é client-side, de propósito.** O endpoint de contas
 * (`GET /clients/{id}` → cache L1) devolve o conjunto INTEIRO num payload só —
 * não há rota paginada de contas e inventar uma seria trabalho de backend fora
 * do escopo desta task. Como o universo é dezenas de contas (não milhares),
 * paginar em memória entrega o padrão de listas do design-system sem custo. Se
 * um dia a lista crescer, a troca é o `useQuery` — a UI não muda.
 *
 * "Extrair contas do Omie" reusa o `PATCH /clients/{id}/sync-accounts`, que
 * ignora o TTL do cache (mesmo comportamento do antigo "Sincronizar contas").
 * Botão async: `disabled` + spinner, reabilita em sucesso OU erro, duplo-clique
 * bloqueado pelo próprio `disabled`.
 *
 * Credencial Omie nunca aparece aqui — o backend nem devolve esses campos
 * (CLAUDE.md §3.2); a tela só mostra nome/banco/tipo/timestamp.
 */

import { RefreshCw, Loader2 } from 'lucide-react';
import { useMemo } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useClientDetail, useSyncAccounts } from '@/hooks/use-clients';
import { readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import { formatOmieAccountType, formatSyncedAt } from '@/lib/format';

const DEFAULT_PAGE_SIZE = 20;
const PARAM = { page: 'page', pageSize: 'pageSize' } as const;

export function BankAccountsScreen({ clientId }: { clientId: string }) {
  const url = useUrlState();
  const page = readPositiveInt(url.get(PARAM.page), 1);
  const pageSize = readPositiveInt(url.get(PARAM.pageSize), DEFAULT_PAGE_SIZE);

  const detailQuery = useClientDetail(clientId);
  const syncMutation = useSyncAccounts(clientId);

  const accounts = useMemo(() => {
    const list = detailQuery.data?.accounts ?? [];
    return [...list].sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'));
  }, [detailQuery.data]);

  const total = accounts.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  // Página fora do intervalo (URL colada de um conjunto maior) cai na última
  // válida em vez de renderizar uma tabela vazia sem explicação.
  const safePage = Math.min(page, totalPages);
  const pageItems = accounts.slice((safePage - 1) * pageSize, safePage * pageSize);

  async function handleSync() {
    try {
      await syncMutation.mutateAsync();
      toast.success('Contas extraídas do Omie.');
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível extrair as contas do Omie.',
      );
    }
  }

  const isSyncing = syncMutation.isPending;

  return (
    <section aria-labelledby="accounts-heading" className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-0.5">
          <h2 id="accounts-heading" className="text-lg font-semibold">
            Contas Bancárias
          </h2>
          <p className="text-muted-foreground text-xs" aria-live="polite">
            {formatSyncedAt(detailQuery.data?.accounts_synced_at)}
          </p>
        </div>
        <Button type="button" onClick={() => void handleSync()} disabled={isSyncing}>
          {isSyncing ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          )}
          {isSyncing ? 'Extraindo…' : 'Extrair contas do Omie'}
        </Button>
      </div>

      <div className="min-h-0 flex-1" aria-busy={detailQuery.isFetching}>
        {detailQuery.isLoading ? (
          <AccountsSkeleton />
        ) : detailQuery.isError ? (
          <div
            role="alert"
            className="bg-destructive/5 border-destructive/30 text-destructive flex flex-col items-start gap-3 rounded-lg border p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
          >
            <span>
              {detailQuery.error instanceof ApiError
                ? detailQuery.error.userMessage
                : 'Não foi possível carregar as contas.'}
            </span>
            <Button variant="outline" size="sm" onClick={() => void detailQuery.refetch()}>
              Tentar novamente
            </Button>
          </div>
        ) : total === 0 ? (
          <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center">
            <p className="text-muted-foreground text-sm">
              Nenhuma conta bancária sincronizada. Clique em &quot;Extrair contas do Omie&quot; para
              buscá-las.
            </p>
            <Button type="button" onClick={() => void handleSync()} disabled={isSyncing}>
              {isSyncing ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              {isSyncing ? 'Extraindo…' : 'Extrair contas do Omie'}
            </Button>
          </div>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Conta</TableHead>
                  <TableHead>Banco</TableHead>
                  <TableHead>Tipo</TableHead>
                  <TableHead>Sincronização</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pageItems.map((account) => (
                  <TableRow key={account.id}>
                    <TableCell className="font-medium">{account.name}</TableCell>
                    <TableCell className="text-muted-foreground">{account.bank_name}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatOmieAccountType(account.account_type)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatSyncedAt(account.synced_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {!detailQuery.isError && (
        <PaginationBar
          page={safePage}
          pageSize={pageSize}
          total={total}
          totalPages={totalPages}
          onPageChange={(next) => url.setMany({ [PARAM.page]: String(next) })}
          onPageSizeChange={(next) =>
            url.setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
          }
          disabled={detailQuery.isLoading}
          itemLabel="contas bancárias"
        />
      )}
    </section>
  );
}

function AccountsSkeleton() {
  return (
    <div className="space-y-2 rounded-lg border p-4" aria-label="Carregando contas bancárias">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex gap-4">
          <div className="bg-muted h-4 flex-1 animate-pulse rounded" />
          <div className="bg-muted h-4 w-32 animate-pulse rounded" />
          <div className="bg-muted h-4 w-28 animate-pulse rounded" />
          <div className="bg-muted h-4 w-36 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}
