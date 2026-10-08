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
 *
 * **A gestão de origens mora no topo desta tela** desde 08/10/2026 (decisão do
 * Pedro depois do uso do painel com dado real): é de onde as contas vêm. A
 * seção (`ClientConnectionsSection`, S9) é a mesma que o painel mostra quando o
 * cliente não tem origem ativa, e é ela que lê o `?conectar=<tipo>` que o
 * `originFixPath()` manda para cá. Com uma seção inteira acima da tabela, a
 * tela é do padrão em que a PÁGINA rola (`data-page-scroll` + `<Table
 * stickyHeader="page">`): no padrão FILL a tabela de contas ficaria espremida.
 */

import { RefreshCw, Loader2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { ClientConnectionsSection } from '@/components/features/clients/connections/client-connections-section';
import { OriginStateBlock } from '@/components/shared/origin-state-notice';
import { Button } from '@/components/ui/button';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useClientDetail, useSyncAccounts } from '@/hooks/use-clients';
import { readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import { formatOmieAccountType, formatSyncedAt } from '@/lib/format';
import { originCodeFor } from '@/lib/origin-capabilities';
import { isOriginError, originErrorCode } from '@/lib/origin-state';

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

  // S9 (R7): os três códigos da taxonomia de origem NÃO viram toast — viram
  // estado explicativo com o caminho de saída. Guardar o erro é o que permite
  // renderizá-lo no lugar da tabela.
  const [originError, setOriginError] = useState<unknown>(null);
  // Qualquer OUTRA falha do sync (Omie fora do ar, instabilidade, timeout)
  // fica na tela, como alerta, até a próxima tentativa: um toast some em
  // segundos e a pessoa ficava olhando a lista antiga sem saber que o Omie
  // estava fora (caso real de 27/09/2026, `418 API OFFLINE`).
  const [syncError, setSyncError] = useState<string | null>(null);

  async function handleSync() {
    setOriginError(null);
    setSyncError(null);
    try {
      await syncMutation.mutateAsync();
      toast.success('Contas extraídas do Omie.');
    } catch (err) {
      if (isOriginError(err)) {
        setOriginError(err);
        return;
      }
      setSyncError(
        err instanceof ApiError ? err.userMessage : 'Não foi possível extrair as contas do Omie.',
      );
    }
  }

  const isSyncing = syncMutation.isPending;
  // 86e36pm1z — encerrado não tem credenciais Omie: o servidor nega o sync
  // com 409 e o botão some (§4.9). O cache exibido foi purgado (lista vazia).
  const isClosed = detailQuery.data?.closed_at != null;
  // S9 (R4/R7): sem origem ATIVA o detalhe responde 200 com zero contas, e o
  // sync responderia 409. Oferecer "Extrair contas" aqui seria oferecer o que o
  // servidor nega (§4.9) — o lugar de agir é a seção de origens, no topo desta tela.
  // O código vem do detalhe (`origin_status` + a CAPACIDADE `listar_contas`
  // das conexões — a origem por arquivo não lista contas, S14) enquanto não
  // houve clique, e do erro real do sync depois dele. Um lugar só decide:
  // `originCodeFor` (`lib/origin-capabilities.ts`).
  const originStatus = detailQuery.data?.origin_status ?? 'sem_origem';
  const originCode =
    originErrorCode(originError) ??
    (isClosed ? null : originCodeFor(originStatus, detailQuery.data?.connections, 'listar_contas'));
  // Sem origem ou origem com erro, o bloco de estado da seção de origens logo
  // acima já diz isso e oferece a ação: repetir a caixa aqui seria a mesma frase
  // duas vezes. Ela fica para o que a seção não explica (a origem ativa não
  // lista contas, `CAPACIDADE_AUSENTE`) e para o erro real de um clique.
  const showOriginBlock =
    originCode !== null && (originCode === 'CAPACIDADE_AUSENTE' || originError !== null);

  return (
    // `data-page-scroll`: a página rola (§7 Frontend), o que faz o `ClientShell`
    // soltar a altura fixa do padrão FILL (86e3gkd80).
    <section aria-labelledby="accounts-heading" data-page-scroll className="flex flex-col gap-6">
      <div className="space-y-1">
        <h1 id="accounts-heading" className="text-xl font-semibold">
          Contas Bancárias
        </h1>
        <p className="text-muted-foreground text-sm">
          As origens de dado do cliente e as contas que vêm delas.
        </p>
      </div>

      {/* A gestão de origens: estado, lista, "Conectar origem" e a gaveta. Só
          depois do detalhe, para o `origin_status` padrão não piscar "Sem origem
          conectada" enquanto carrega. */}
      {detailQuery.data !== undefined && (
        <ClientConnectionsSection
          clientId={clientId}
          originStatus={originStatus}
          isClosed={isClosed}
        />
      )}

      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-0.5">
            <h2 className="text-base font-semibold">Contas sincronizadas</h2>
            <p className="text-muted-foreground text-xs" aria-live="polite">
              {formatSyncedAt(detailQuery.data?.accounts_synced_at)}
            </p>
          </div>
          {!isClosed && originCode === null && (
            <Button type="button" onClick={() => void handleSync()} disabled={isSyncing}>
              {isSyncing ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              )}
              {isSyncing ? 'Extraindo…' : 'Extrair contas do Omie'}
            </Button>
          )}
        </div>

        {/* R7: a ausência/falha de origem é ESTADO e aparece ANTES da tabela —
          com contas em cache a lista continua legível, e sem elas a tabela
          vazia deixaria de ser ambígua ("o Omie não tem contas?"). Sem link: a
          ação está na seção de origens desta mesma página. */}
        {!detailQuery.isLoading &&
          !detailQuery.isError &&
          showOriginBlock &&
          originCode !== null && (
            <OriginStateBlock code={originCode} clientId={clientId} showAction={false} />
          )}

        {/* A última extração falhou: a lista abaixo (se houver) é a do cache,
          e o alerta fica até a próxima tentativa. */}
        {syncError !== null && (
          <ErrorBanner message={syncError} onRetry={() => void handleSync()} retrying={isSyncing} />
        )}

        {/* Altura NATURAL: a tabela cresce com as linhas e quem rola é o `<main>`. */}
        <div aria-busy={detailQuery.isFetching}>
          {detailQuery.isLoading ? (
            <AccountsSkeleton />
          ) : detailQuery.isError ? (
            <ErrorBanner
              message={
                detailQuery.error instanceof ApiError
                  ? detailQuery.error.userMessage
                  : 'Não foi possível carregar as contas.'
              }
              onRetry={() => void detailQuery.refetch()}
              retrying={detailQuery.isFetching}
            />
          ) : total === 0 ? (
            <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center">
              <p className="text-muted-foreground text-sm">
                {originCode === null
                  ? 'Nenhuma conta bancária sincronizada. Clique em "Extrair contas do Omie" para buscá-las.'
                  : 'Nenhuma conta bancária sincronizada — este cliente ainda não tem uma origem de onde buscá-las.'}
              </p>
              {originCode === null && (
                <Button type="button" onClick={() => void handleSync()} disabled={isSyncing}>
                  {isSyncing ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  )}
                  {isSyncing ? 'Extraindo…' : 'Extrair contas do Omie'}
                </Button>
              )}
            </div>
          ) : (
            <TableCard pageScroll>
              <Table stickyHeader="page" scrollRegionLabel="Contas bancárias (rolável)">
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
            </TableCard>
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
      </div>
    </section>
  );
}

/**
 * Falha que fica na tela (falha do detalhe OU da última extração), com o
 * caminho de saída ao lado. `role="alert"` para o leitor anunciar na hora.
 */
function ErrorBanner({
  message,
  onRetry,
  retrying,
}: {
  message: string;
  onRetry: () => void;
  retrying: boolean;
}) {
  return (
    <div
      role="alert"
      // Fundo OPACO (`destructive-muted`), nunca `bg-destructive/N`: texto sobre
      // fundo com alfa compõe com o que está embaixo e não tem par testado
      // (CLAUDE.md v1.48, skill front-gate §4).
      className="bg-destructive-muted text-destructive ring-destructive/30 flex flex-col items-start gap-3 rounded-lg p-4 text-sm ring-1 ring-inset sm:flex-row sm:items-center sm:justify-between"
    >
      <span>{message}</span>
      <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
        Tentar novamente
      </Button>
    </div>
  );
}

function AccountsSkeleton() {
  return (
    <div
      role="status"
      className="space-y-2 rounded-lg border p-4"
      aria-label="Carregando contas bancárias"
    >
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
