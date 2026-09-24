'use client';

/**
 * Tela "Plano de Contas" do cliente — Sprint 10 / R3 (FRONT 10.5).
 *
 * Mostra a classificação que a ORIGEM já tem, dentro do produto: código,
 * hierarquia, situação e — o insumo do de-para da Sprint 12 — a conta de
 * demonstrativo que a origem vincula a cada categoria.
 *
 * **O que esta tela NÃO faz, e por quê:**
 *
 *   - **não soma nada.** As cinco contagens vêm da rota `/coverage`, calculadas
 *     no servidor sobre o conjunto INTEIRO do cliente. Somar as linhas da
 *     página daria um número que muda ao paginar e ao filtrar;
 *   - **não busca por nome.** O campo diz "Buscar por código" porque o servidor
 *     só aceita `code`: nome de categoria não é persistido (§4.5), é resolvido
 *     em runtime pelo mesmo cache de 6 h da tela de revisão. Oferecer busca por
 *     nome exigiria persistir o nome — exatamente o que a sprint decidiu não
 *     fazer;
 *   - **não infere destino.** `dreCode` nulo é "sem destino declarado", e isso é
 *     INFORMAÇÃO: na amostra real são as transferências e as totalizadoras, que
 *     por definição contábil não têm conta de demonstrativo própria. Por isso as
 *     marcações da origem aparecem ao lado da situação — elas explicam a coluna
 *     vazia em vez de deixá-la parecendo pendência.
 *
 * **Gating (matriz do R4 + BACK 10.3).** LER é de todo papel com acesso ao
 * cliente, o operador inclusive. SINCRONIZAR pede
 * `sync_client_chart_of_accounts` — todos menos o `client_operator`. A ação
 * fica OCULTA para quem não a tem (nunca desabilitada: botão desabilitado ainda
 * anuncia "existe algo aqui que você não pode fazer"), e a autoridade continua
 * sendo o backend. A rota NÃO é bloqueada por papel, porque ler é de todos.
 *
 * **Estado na URL** (`page`, `pageSize`, `status`, `parentCode`, `code`): a view
 * fica linkável e sobrevive ao F5. Os dois campos de texto guardam o valor
 * digitado em estado local e só escrevem na URL depois do debounce — sem isso,
 * cada tecla viraria um `router.replace` e um request.
 *
 * **Sem virtualização, de propósito:** `pageSize` tem teto de 100 no servidor,
 * então a tabela nunca renderiza mais que 100 linhas. Virtualizar aqui seria a
 * primeira exceção, não o primeiro uso.
 */

import { Loader2, RefreshCw, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { OriginStateBlock } from '@/components/shared/origin-state-notice';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  useChartOfAccountsCoverage,
  useChartOfAccountsList,
  useSyncChartOfAccounts,
} from '@/hooks/use-client-chart-of-accounts';
import { useClientDetail } from '@/hooks/use-clients';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { readEnum, readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import type { ListChartOfAccountsParams } from '@/lib/api/client-chart-of-accounts';
import { hasPermission } from '@/lib/authz';
import type { ChartOfAccountEntry, ChartOfAccountsStatus } from '@/lib/contracts';
import { formatCreatedAt } from '@/lib/format';
import { isOriginError, originErrorCode } from '@/lib/origin-state';
import { useAuthStore } from '@/stores/auth';

import { ChartFlagBadges, ChartStatusBadge } from './chart-of-accounts-badges';
import {
  ChartOfAccountsCoverageBlock,
  ChartOfAccountsCoverageSkeleton,
} from './chart-of-accounts-coverage';

const PARAM = {
  page: 'page',
  pageSize: 'pageSize',
  status: 'status',
  parentCode: 'parentCode',
  code: 'code',
} as const;

const DEFAULT_PAGE_SIZE = 20;
/** O Radix não aceita `''` como valor de item — `all` é o "sem filtro". */
const ALL_STATUS = 'all';
/** As três situações que o servidor aceita em `?status=` (`Literal` no Pydantic). */
const STATUS_FILTERS: readonly ChartOfAccountsStatus[] = ['ativa', 'inativa', 'ausente_na_origem'];
const STATUS_FILTER_LABELS: Record<ChartOfAccountsStatus, string> = {
  ativa: 'Ativas',
  inativa: 'Inativas',
  ausente_na_origem: 'Ausentes na origem',
};
/** Teto do termo, igual ao `MAX_CODE_SEARCH_CHARS` do servidor (422 acima dele). */
const MAX_CODE_CHARS = 50;
const COLUMN_COUNT = 5;

export function ChartOfAccountsScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const { get, setMany } = useUrlState();

  const page = readPositiveInt(get(PARAM.page), 1);
  const pageSize = readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE);
  const status = readEnum(get(PARAM.status), STATUS_FILTERS);
  const codeParam = get(PARAM.code) ?? '';
  const parentCodeParam = get(PARAM.parentCode) ?? '';

  // Estado local dos dois campos de texto + debounce → URL. Inicializados da
  // URL para o deep link já nascer com o campo preenchido.
  const [codeInput, setCodeInput] = useState(codeParam);
  const [parentInput, setParentInput] = useState(parentCodeParam);
  const debouncedCode = useDebouncedValue(codeInput, 300);
  const debouncedParent = useDebouncedValue(parentInput, 300);

  // O guard de igualdade é o que impede o laço: `setMany` só é chamado quando o
  // valor debounced difere do que já está na URL. Filtrar volta para a página 1
  // — senão a pessoa cai numa página que o novo recorte não tem.
  useEffect(() => {
    if (debouncedCode === codeParam) return;
    setMany({ [PARAM.code]: debouncedCode || null, [PARAM.page]: null });
  }, [debouncedCode, codeParam, setMany]);

  useEffect(() => {
    if (debouncedParent === parentCodeParam) return;
    setMany({ [PARAM.parentCode]: debouncedParent || null, [PARAM.page]: null });
  }, [debouncedParent, parentCodeParam, setMany]);

  const queryParams = useMemo<ListChartOfAccountsParams>(
    () => ({
      page,
      pageSize,
      status: status ?? null,
      parentCode: parentCodeParam || null,
      code: codeParam || null,
    }),
    [page, pageSize, status, parentCodeParam, codeParam],
  );

  const listQuery = useChartOfAccountsList(clientId, queryParams);
  const coverageQuery = useChartOfAccountsCoverage(clientId);
  const syncMutation = useSyncChartOfAccounts(clientId);
  // Cliente ENCERRADO: o servidor recusa a sincronização com 409 e a LEITURA
  // continua 200 (§4.12). O shell já carregou este detalhe — vem do cache.
  const clientDetail = useClientDetail(clientId);

  // Os três códigos da taxonomia de origem viram ESTADO explicativo com caminho
  // de saída, nunca um toast genérico (S9 / R7). Guardar o erro é o que permite
  // renderizá-lo; qualquer outro erro segue no toast.
  const [originError, setOriginError] = useState<unknown>(null);

  if (currentUser === null) return null;

  const coverage = coverageQuery.data;
  const rows = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;

  const isClosed = clientDetail.data?.closed_at != null;
  const originStatus = clientDetail.data?.origin_status ?? 'ativa';
  const originReady = originStatus === 'ativa';
  // O código vem do `origin_status` enquanto não houve clique, e do erro real do
  // sync depois dele — que pode ser `CAPACIDADE_AUSENTE`, um caso que o
  // `origin_status` sozinho não distingue.
  const originCode = originErrorCode(originError) ?? (originReady ? null : 'SEM_CONEXAO');
  const hasOriginBlock = !isClosed && originCode !== null;

  // §4.9: mostrar ação que o servidor nega é defeito. Três motivos diferentes
  // para a ação sumir, e cada um tem a sua explicação na tela.
  const canSync = hasPermission(currentUser, 'sync_client_chart_of_accounts');
  const showSyncAction = canSync && !isClosed && originCode === null;
  const isSyncing = syncMutation.isPending;

  const hasFilters = status !== undefined || codeParam !== '' || parentCodeParam !== '';
  // "Nunca sincronizou" é o estado VAZIO da tela (R3) — diferente de "o filtro
  // não achou nada", que tem outra saída (limpar os filtros).
  const neverSynced = coverage !== undefined && coverage.syncedAt == null && coverage.total === 0;

  async function handleSync() {
    setOriginError(null);
    try {
      await syncMutation.mutateAsync();
      toast.success('Plano de contas sincronizado.');
    } catch (err) {
      if (isOriginError(err)) {
        setOriginError(err);
        return;
      }
      toast.error(
        err instanceof ApiError
          ? err.userMessage
          : 'Não foi possível sincronizar o plano de contas.',
      );
    }
  }

  function clearFilters() {
    setCodeInput('');
    setParentInput('');
    setMany({
      [PARAM.code]: null,
      [PARAM.parentCode]: null,
      [PARAM.status]: null,
      [PARAM.page]: null,
    });
  }

  const syncButton = (
    <Button type="button" onClick={() => void handleSync()} disabled={isSyncing}>
      {isSyncing ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
      )}
      {isSyncing ? 'Sincronizando…' : 'Sincronizar agora'}
    </Button>
  );

  return (
    <section aria-labelledby="chart-of-accounts-heading" className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="chart-of-accounts-heading" className="text-lg font-semibold">
            Plano de Contas
          </h2>
          <p className="text-muted-foreground text-sm">
            A classificação que a origem deste cliente já tem. A conta de demonstrativo vinculada a
            cada categoria é o que o de-para aproveita pronto.
          </p>
        </div>
        {showSyncAction && syncButton}
        {/* Encerrado é só-leitura: a ação some COM o motivo, em vez de sumir em
            silêncio e deixar a pessoa procurando o botão (§4.12). */}
        {canSync && isClosed && (
          <p className="text-muted-foreground max-w-xs text-sm">
            Cliente encerrado: a sincronização está indisponível. O plano de contas já sincronizado
            continua disponível para leitura.
          </p>
        )}
      </div>

      {/* Cobertura ANTES da lista: é a pergunta "onde vai dar trabalho", e quem
          opera precisa dela antes de percorrer as linhas. */}
      {coverageQuery.isLoading ? (
        <ChartOfAccountsCoverageSkeleton />
      ) : coverage !== undefined ? (
        <ChartOfAccountsCoverageBlock coverage={coverage} />
      ) : null}

      {/* R3: "a última tentativa falhou" mostra a data da última BEM-SUCEDIDA
          junto do aviso — sem ela, a pessoa não sabe se está olhando dado de
          ontem ou de três meses atrás. */}
      {coverage?.syncFailedAt != null && (
        <div
          role="status"
          className="bg-warning-muted text-warning ring-warning/30 space-y-1 rounded-lg p-4 text-sm ring-1 ring-inset"
        >
          <p className="font-medium">A última tentativa de sincronização falhou</p>
          <p>
            O plano de contas abaixo é o da última sincronização bem-sucedida
            {coverage.syncedAt != null
              ? `, de ${formatCreatedAt(coverage.syncedAt)}.`
              : ' — que ainda não aconteceu.'}
          </p>
        </div>
      )}

      {hasOriginBlock && originCode !== null && (
        <OriginStateBlock code={originCode} clientId={clientId} />
      )}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
        <div className="min-w-0 flex-1 space-y-1.5">
          <Label htmlFor="chart-code-search">Buscar por código</Label>
          <div className="relative">
            <Search
              className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              id="chart-code-search"
              value={codeInput}
              maxLength={MAX_CODE_CHARS}
              onChange={(e) => setCodeInput(e.target.value)}
              placeholder="Ex.: 1.01"
              className="pl-9"
            />
          </div>
        </div>

        <div className="space-y-1.5 lg:w-56">
          <Label htmlFor="chart-status-filter">Situação</Label>
          <Select
            value={status ?? ALL_STATUS}
            onValueChange={(value) =>
              setMany({
                [PARAM.status]: value === ALL_STATUS ? null : value,
                [PARAM.page]: null,
              })
            }
          >
            <SelectTrigger id="chart-status-filter" className="w-full">
              <SelectValue placeholder="Todas as situações" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_STATUS}>Todas as situações</SelectItem>
              {STATUS_FILTERS.map((value) => (
                <SelectItem key={value} value={value}>
                  {STATUS_FILTER_LABELS[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5 lg:w-56">
          {/* Hierarquia: o servidor filtra pelas FILHAS DIRETAS de um código.
              "Grupo" não existe na resposta da origem — inventá-lo aqui seria
              criar um conceito que o dado não tem. */}
          <Label htmlFor="chart-parent-filter">Filhas do código</Label>
          <Input
            id="chart-parent-filter"
            value={parentInput}
            maxLength={MAX_CODE_CHARS}
            onChange={(e) => setParentInput(e.target.value)}
            placeholder="Ex.: 1.01"
          />
        </div>

        {hasFilters && (
          <Button type="button" variant="outline" onClick={clearFilters}>
            Limpar filtros
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1" aria-busy={listQuery.isFetching}>
        {listQuery.isError ? (
          <ErrorState
            message={
              listQuery.error instanceof ApiError
                ? listQuery.error.userMessage
                : 'Não foi possível carregar o plano de contas.'
            }
            onRetry={() => void listQuery.refetch()}
          />
        ) : (
          // Sem `overflow-x-auto` por fora: o `<Table>` já embrulha num
          // `ScrollRegion` focável, e um segundo scroller aninhado espreme as
          // colunas em 390px em vez de rolar (ADR-007-FE).
          <TableCard>
            <Table fill scrollRegionLabel="Categorias do plano de contas (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap">Código</TableHead>
                  <TableHead>Nome</TableHead>
                  <TableHead>Conta de demonstrativo</TableHead>
                  <TableHead className="whitespace-nowrap">Conta contábil</TableHead>
                  <TableHead>Situação</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {listQuery.isLoading ? (
                  <TableSkeletonRows />
                ) : rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={COLUMN_COUNT} className="py-12">
                      {neverSynced ? (
                        <NeverSyncedState
                          action={showSyncAction ? syncButton : null}
                          canSync={canSync}
                          isClosed={isClosed}
                        />
                      ) : hasFilters ? (
                        <FilteredEmptyState onClear={clearFilters} />
                      ) : (
                        <p className="text-muted-foreground text-center text-sm">
                          Nenhuma categoria neste plano de contas.
                        </p>
                      )}
                    </TableCell>
                  </TableRow>
                ) : (
                  rows.map((entry) => <ChartRow key={entry.categoryCode} entry={entry} />)
                )}
              </TableBody>
            </Table>
          </TableCard>
        )}
      </div>

      {!listQuery.isError && (
        <PaginationBar
          page={pagination?.page ?? page}
          pageSize={pagination?.pageSize ?? pageSize}
          total={pagination?.total ?? 0}
          totalPages={pagination?.totalPages ?? 0}
          onPageChange={(next) => setMany({ [PARAM.page]: String(next) })}
          onPageSizeChange={(next) =>
            setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
          }
          disabled={listQuery.isLoading}
          itemLabel="categorias"
        />
      )}
    </section>
  );
}

function ChartRow({ entry }: { entry: ChartOfAccountEntry }) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap font-medium tabular-nums">
        {entry.categoryCode}
        {entry.parentCode != null && (
          <span className="text-muted-foreground block text-xs">Filha de {entry.parentCode}</span>
        )}
      </TableCell>
      <TableCell className="min-w-48">
        {/* `null` = a origem não respondeu na hora de resolver os nomes
            (fail-soft do servidor). Repetir o código aqui faria o dado ausente
            parecer dado bom. */}
        {entry.name ?? <span className="text-muted-foreground">Nome não disponível</span>}
      </TableCell>
      <TableCell className="min-w-48">
        {entry.dreCode != null ? (
          <>
            <span className="tabular-nums">{entry.dreCode}</span>
            {entry.dreName != null && (
              <span className="text-muted-foreground block text-xs">{entry.dreName}</span>
            )}
          </>
        ) : (
          <span className="text-muted-foreground text-sm">Sem destino declarado</span>
        )}
      </TableCell>
      <TableCell className="text-muted-foreground whitespace-nowrap tabular-nums">
        {entry.contaContabilCode ?? '—'}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1.5">
          <ChartStatusBadge status={entry.status} />
          <ChartFlagBadges
            totalizadora={entry.totalizadora}
            transferencia={entry.transferencia}
            naoExibir={entry.naoExibir}
          />
        </div>
      </TableCell>
    </TableRow>
  );
}

function TableSkeletonRows() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, index) => (
        <TableRow key={index} aria-hidden="true">
          {Array.from({ length: COLUMN_COUNT }).map((__, cell) => (
            <TableCell key={cell}>
              <div className="bg-muted h-4 w-full animate-pulse rounded" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

/**
 * Cliente que NUNCA sincronizou. Três textos porque são três situações com
 * saídas diferentes — e oferecer "Sincronizar agora" a quem o servidor nega
 * seria o defeito que a §4.9 descreve.
 */
function NeverSyncedState({
  action,
  canSync,
  isClosed,
}: {
  action: React.ReactNode;
  canSync: boolean;
  isClosed: boolean;
}) {
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <div className="space-y-1">
        <p className="text-sm font-medium">Este cliente ainda não sincronizou o plano de contas</p>
        <p className="text-muted-foreground text-sm">
          {isClosed
            ? 'O cliente foi encerrado antes de sincronizar, e a sincronização não fica disponível para clientes encerrados.'
            : canSync
              ? 'Traga as categorias da origem para ver quanto da classificação já vem pronta.'
              : 'Quando alguém da equipe sincronizar, as categorias da origem aparecem aqui.'}
        </p>
      </div>
      {action}
    </div>
  );
}

function FilteredEmptyState({ onClear }: { onClear: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <p className="text-muted-foreground text-sm">
        Nenhuma categoria encontrada para este recorte.
      </p>
      <Button type="button" variant="outline" size="sm" onClick={onClear}>
        Limpar filtros
      </Button>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="border-destructive/30 bg-destructive/5 text-destructive space-y-3 rounded-lg border p-6"
    >
      <p className="text-sm font-medium">Não foi possível carregar o plano de contas</p>
      <p className="text-sm">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
