'use client';

/**
 * Lista de Conciliações do cliente — Sprint 4 / R1.
 *
 * É a TELA PRINCIPAL do cliente (substitui a seção "Histórico de Conciliações"
 * que vivia empilhada no detalhe). Padrão de listas do design-system:
 *
 *   - filtros no topo (conta · mês · status), combinados com E, limpáveis, com
 *     **estado na URL** — a view fica linkável e sobrevive ao refresh;
 *   - **paginação fixa no rodapé** (`x–y de N` · página atual/total · itens por
 *     página, padrão 20), sempre visível;
 *   - linha clicável levando ao detalhe, com ações internas usando
 *     `stopPropagation`;
 *   - estados loading / vazio / erro tratados.
 *
 * Filtrar reseta a página para 1 no MESMO `replace` da URL: sem isso, quem
 * estava na página 4 e aplica um filtro que devolve 2 páginas cairia numa
 * página vazia e acharia que o filtro não encontrou nada.
 *
 * Nenhum contador é recalculado aqui — os números vêm materializados do
 * backend (fonte única, `reconciliations.totals`), então lista e detalhe não
 * divergem (learning "valor derivado calculado em 2 lugares diverge").
 */

import { Plus } from 'lucide-react';
import { useEffect, useMemo } from 'react';

import { Button } from '@/components/ui/button';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useReconciliationsList } from '@/hooks/use-clients';
import { readEnum, readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import {
  RECONCILIATIONS_DEFAULT_PAGE_SIZE,
  type BankAccount,
  type ReconciliationStatusFilterValue,
  type ReconciliationsListParams,
} from '@/lib/api/clients';
import { currentMonth } from '@/lib/validation/reconciliations';
import { usePendingCreations } from '@/stores/pending-creations';

import { ReconciliationListItem } from './reconciliation-list-item';

/** Chaves de querystring — centralizadas para o `clear` não esquecer nenhuma. */
const PARAM = {
  account: 'conta',
  month: 'mes',
  status: 'status',
  page: 'page',
  pageSize: 'pageSize',
} as const;

const ALL_VALUE = '__all__';

/** Valores aceitos pelo backend no filtro de status (contrato BACK 04.3). */
const STATUS_VALUES = ['processing', 'processed', 'error'] as const;

const STATUS_LABELS: Record<ReconciliationStatusFilterValue, string> = {
  processing: 'Em processamento',
  processed: 'Processada',
  error: 'Erro',
};

interface ReconciliationsListProps {
  clientId: string;
  accounts: BankAccount[];
  /** Abre a gaveta de criação (FRONT 04.6). */
  onCreateClick: () => void;
}

export function ReconciliationsList({
  clientId,
  accounts,
  onCreateClick,
}: ReconciliationsListProps) {
  const url = useUrlState();

  const rawAccount = url.get(PARAM.account);
  const accountFilter = rawAccount !== null && /^\d+$/.test(rawAccount) ? Number(rawAccount) : null;
  const monthFilter = readMonth(url.get(PARAM.month));
  const statusFilter = readEnum(url.get(PARAM.status), STATUS_VALUES);
  const page = readPositiveInt(url.get(PARAM.page), 1);
  const pageSize = readPositiveInt(url.get(PARAM.pageSize), RECONCILIATIONS_DEFAULT_PAGE_SIZE);

  const queryParams = useMemo<ReconciliationsListParams>(() => {
    const params: ReconciliationsListParams = { page, pageSize };
    if (accountFilter !== null) params.omie_conta_id = accountFilter;
    if (monthFilter !== null) params.month = monthFilter;
    if (statusFilter !== undefined) params.status = statusFilter;
    return params;
  }, [page, pageSize, accountFilter, monthFilter, statusFilter]);

  const { data, isLoading, isFetching, isError, error, refetch } = useReconciliationsList(
    clientId,
    queryParams,
  );

  const accountLookup = useMemo(() => {
    const map = new Map<number, string>();
    for (const account of accounts) map.set(account.omie_conta_id, account.name);
    return map;
  }, [accounts]);

  // `useMemo` para o array manter identidade estável entre renders — sem isso,
  // o `?? []` cria um array novo a cada render e o efeito de baixa abaixo
  // rodaria em loop.
  const sessions = useMemo(() => data?.data ?? [], [data]);
  const pagination = data?.pagination;
  const hasFilters = accountFilter !== null || monthFilter !== null || statusFilter !== undefined;

  // A pessoa criou e FICOU na lista até terminar: ela esperou olhando, então
  // `autor_navegou_fora` não se aplica. Dar baixa aqui é o que impede o evento
  // de ser emitido depois, quando ela finalmente sair — o que contaria uma
  // história falsa sobre o outcome da sprint.
  const settleCreation = usePendingCreations((s) => s.settle);
  useEffect(() => {
    for (const session of sessions) {
      if (session.status !== 'processing') settleCreation(session.id);
    }
  }, [sessions, settleCreation]);

  /** Todo filtro volta para a página 1 no mesmo replace (ver docstring). */
  function applyFilter(patch: Record<string, string | null>) {
    url.setMany({ ...patch, [PARAM.page]: null });
  }

  return (
    <section aria-labelledby="reconciliations-heading" className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="reconciliations-heading" className="text-lg font-semibold">
          Conciliações
        </h2>
        <Button type="button" onClick={onCreateClick}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          Criar conciliação
        </Button>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:gap-4">
        <div className="min-w-[12rem] flex-1 space-y-1">
          <label htmlFor="filter-account" className="text-muted-foreground text-xs font-medium">
            Conta bancária
          </label>
          <Select
            value={accountFilter === null ? ALL_VALUE : String(accountFilter)}
            onValueChange={(value) =>
              applyFilter({ [PARAM.account]: value === ALL_VALUE ? null : value })
            }
          >
            <SelectTrigger id="filter-account" aria-label="Filtrar por conta bancária">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_VALUE}>Todas as contas</SelectItem>
              {accounts.map((account) => (
                <SelectItem key={account.id} value={String(account.omie_conta_id)}>
                  {account.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="min-w-[10rem] flex-1 space-y-1">
          <label htmlFor="filter-month" className="text-muted-foreground text-xs font-medium">
            Mês de referência
          </label>
          {/* `<input type="month">` é o month picker nativo, já localizado
              pt-BR pelo idioma do navegador. */}
          <input
            id="filter-month"
            type="month"
            lang="pt-BR"
            max={currentMonth()}
            value={monthFilter ?? ''}
            onChange={(e) => applyFilter({ [PARAM.month]: e.target.value || null })}
            aria-label="Filtrar por mês de referência"
            className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full cursor-pointer rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        <div className="min-w-[10rem] flex-1 space-y-1">
          <label htmlFor="filter-status" className="text-muted-foreground text-xs font-medium">
            Status
          </label>
          <Select
            value={statusFilter ?? ALL_VALUE}
            onValueChange={(value) =>
              applyFilter({ [PARAM.status]: value === ALL_VALUE ? null : value })
            }
          >
            <SelectTrigger id="filter-status" aria-label="Filtrar por status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_VALUE}>Todos os status</SelectItem>
              {STATUS_VALUES.map((value) => (
                <SelectItem key={value} value={value}>
                  {STATUS_LABELS[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {hasFilters && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => url.clear([PARAM.account, PARAM.month, PARAM.status, PARAM.page])}
            className="sm:mb-0.5"
          >
            Limpar filtros
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-3" aria-busy={isFetching}>
        {isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ErrorState
            message={
              error instanceof ApiError
                ? error.userMessage
                : 'Não foi possível carregar as conciliações.'
            }
            onRetry={() => void refetch()}
          />
        ) : sessions.length === 0 ? (
          <EmptyState hasFilters={hasFilters} onCreateClick={onCreateClick} />
        ) : (
          sessions.map((session) => (
            <ReconciliationListItem
              key={session.id}
              clientId={clientId}
              session={session}
              accountName={
                accountLookup.get(session.omie_conta_id) ?? `Conta #${session.omie_conta_id}`
              }
            />
          ))
        )}
      </div>

      {!isError && (
        <PaginationBar
          page={pagination?.page ?? page}
          pageSize={pagination?.pageSize ?? pageSize}
          total={pagination?.total ?? 0}
          totalPages={pagination?.totalPages ?? 0}
          onPageChange={(next) => url.setMany({ [PARAM.page]: String(next) })}
          onPageSizeChange={(next) =>
            url.setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
          }
          disabled={isLoading}
          itemLabel="conciliações"
        />
      )}
    </section>
  );
}

/** Aceita só `YYYY-MM` — URL editada à mão não vira 422 no backend. */
function readMonth(raw: string | null): string | null {
  if (raw === null) return null;
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(raw) ? raw : null;
}

function ListSkeleton() {
  return (
    <div aria-label="Carregando conciliações" className="space-y-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="bg-card space-y-3 rounded-lg border p-4 shadow-sm">
          <div className="flex justify-between">
            <div className="bg-muted h-4 w-1/3 animate-pulse rounded" />
            <div className="bg-muted h-5 w-32 animate-pulse rounded-full" />
          </div>
          <div className="bg-muted h-3 w-2/3 animate-pulse rounded" />
          <div className="bg-muted h-3 w-1/2 animate-pulse rounded" />
        </div>
      ))}
    </div>
  );
}

function EmptyState({
  hasFilters,
  onCreateClick,
}: {
  hasFilters: boolean;
  onCreateClick: () => void;
}) {
  if (hasFilters) {
    return (
      <div className="text-muted-foreground rounded-lg border border-dashed p-8 text-center text-sm">
        Nenhuma conciliação encontrada com esses filtros.
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center">
      <p className="text-muted-foreground text-sm">
        Nenhuma conciliação. Clique em &quot;Criar conciliação&quot; para começar.
      </p>
      <Button type="button" onClick={onCreateClick}>
        <Plus className="h-4 w-4" aria-hidden="true" />
        Criar conciliação
      </Button>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="bg-destructive/5 border-destructive/30 text-destructive flex flex-col items-start gap-3 rounded-lg border p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
    >
      <span>{message}</span>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
