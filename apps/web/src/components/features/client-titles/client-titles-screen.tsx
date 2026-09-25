'use client';

/**
 * Tela "Carteira" do cliente — Sprint 11 / R3 · R4 · R5 (FRONT 11.7).
 *
 * A posição de títulos em aberto do cliente: todos os títulos a pagar e a
 * receber não liquidados, de **todas** as contas correntes e sem recorte de
 * competência — que é o que faz um título vencido há quatro meses aparecer
 * aqui e nunca na conciliação do mês.
 *
 * **O que esta tela NÃO faz, e por quê:**
 *
 *   - **não soma nada.** Totais, vencido e os quatro baldes vêm da rota
 *     `/summary`, calculados no servidor sobre a carteira INTEIRA. Somar as
 *     linhas da página daria um aging que muda ao paginar e ao filtrar — e
 *     aging errado é pior que aging nenhum, exatamente o problema do relatório
 *     manual que motivou a sprint;
 *   - **não recalcula o atraso.** `overdueDays` e `bucket` vêm prontos, com a
 *     `referenceDate` do SERVIDOR. Recalcular com o relógio do navegador poria
 *     o mesmo título em baldes diferentes para pessoas em fusos diferentes;
 *   - **não busca por nome.** O nome do devedor não é persistido (§4.5): é
 *     resolvido em runtime pelo cache de clientes, e pode nem vir. Oferecer
 *     busca por nome exigiria persistir o nome;
 *   - **não mostra zeros quando a carteira nunca foi sincronizada.** `neverSynced`
 *     é campo explícito do contrato justamente porque "este cliente não deve
 *     nada" e "ninguém nunca consultou a origem" são estados diferentes (R3).
 *
 * **Gating (matriz do R5 + BACK 11.5).** LER pede `view_client_receivables` —
 * ✅ nos cinco papéis, o operador inclusive. SINCRONIZAR pede
 * `sync_client_receivables`, que é ❌ só para o `client_operator`. A ação fica
 * OCULTA para quem não a tem (nunca desabilitada: botão desabilitado ainda
 * anuncia "existe algo aqui que você não pode fazer"), e a autoridade continua
 * sendo o backend — forçar a rota segue negado lá.
 *
 * **Estado na URL** (`page`, `pageSize`, `type`, `situation`, `bucket`,
 * `sortBy`, `sortOrder`): a view fica linkável e sobrevive ao F5. Todos os
 * filtros e a ordenação são aplicados **no servidor** — o request carrega os
 * parâmetros, e nenhuma linha é recortada no navegador.
 *
 * **Sem virtualização, de propósito:** `pageSize` tem teto de 100 no servidor,
 * então a tabela nunca renderiza mais que 100 linhas. Virtualizar aqui seria a
 * primeira exceção, não o primeiro uso.
 */

import { Loader2, MessageSquareText, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { OriginStateBlock } from '@/components/shared/origin-state-notice';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import {
  useClientTitlesList,
  useClientTitlesSummary,
  useSyncClientTitles,
} from '@/hooks/use-client-titles';
import { useClientDetail } from '@/hooks/use-clients';
import { readEnum, readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import type { ListClientTitlesParams } from '@/lib/api/client-titles';
import { hasPermission } from '@/lib/authz';
import type { AgingBucket, ClientTitle, TitleType } from '@/lib/contracts';
import { formatBRDate, formatBRL, formatCreatedAt } from '@/lib/format';
import { isOriginError, originErrorCode } from '@/lib/origin-state';
import { useAuthStore } from '@/stores/auth';

import {
  BUCKET_LABELS,
  TitleBucketBadge,
  TitleStatusBadge,
  TitleTypeBadge,
} from './client-titles-badges';
import { ClientTitlesSummaryBlock, ClientTitlesSummarySkeleton } from './client-titles-summary';
import { ReceivablesReportScreen } from './receivables-report-screen';
import { TitleContextSheet } from './title-context-sheet';

const PARAM = {
  page: 'page',
  pageSize: 'pageSize',
  type: 'type',
  situation: 'situation',
  bucket: 'bucket',
  sortBy: 'sortBy',
  sortOrder: 'sortOrder',
  // Sprint 15: `hasNoContext` é o filtro "vencidos sem contexto" (a fila de
  // trabalho de quem registra); `view` é a aba (`carteira` | `relatorio`),
  // estado na URL como o resto da tela.
  hasNoContext: 'hasNoContext',
  view: 'view',
} as const;

const DEFAULT_VIEW = 'carteira';
const VIEW_VALUES = ['carteira', 'relatorio'] as const;

const DEFAULT_PAGE_SIZE = 20;
/** O Radix não aceita `''` como valor de item — `all` é o "sem filtro". */
const ALL = 'all';
const COLUMN_COUNT = 6;

/**
 * Os vocabulários FECHADOS do servidor (`Literal` no Pydantic). Valor fora
 * deles é **400 `VALIDATION_ERROR`**, e não uma lista vazia — por isso a URL
 * passa por `readEnum` antes de virar request: URL editada à mão degrada para
 * "sem filtro" em vez de derrubar a carga inicial.
 */
const TYPE_FILTERS: readonly TitleType[] = ['a_receber', 'a_pagar'];
const SITUATION_FILTERS = ['em_aberto', 'vencido'] as const;
const BUCKET_FILTERS: readonly AgingBucket[] = ['a_vencer', '1_30', '31_60', '61_90', '90_mais'];
const SORT_FIELDS = ['due_date', 'amount'] as const;
const SORT_ORDERS = ['asc', 'desc'] as const;

const TYPE_FILTER_LABELS: Record<TitleType, string> = {
  a_receber: 'A receber',
  a_pagar: 'A pagar',
};

const SITUATION_FILTER_LABELS: Record<(typeof SITUATION_FILTERS)[number], string> = {
  em_aberto: 'Em aberto',
  vencido: 'Vencido',
};

/**
 * As quatro combinações de ordenação num controle só.
 *
 * Um `Select` por campo e outro por direção somariam dois controles a uma
 * barra que já tem três filtros — em 390px isso empurra a tabela para fora da
 * primeira dobra. A URL continua guardando os DOIS parâmetros do contrato
 * (`sortBy` e `sortOrder`): o que é único é o controle, não o estado.
 */
const SORT_OPTIONS = [
  {
    value: 'due_date:asc',
    sortBy: 'due_date',
    sortOrder: 'asc',
    label: 'Vencimento (mais antigo)',
  },
  {
    value: 'due_date:desc',
    sortBy: 'due_date',
    sortOrder: 'desc',
    label: 'Vencimento (mais recente)',
  },
  { value: 'amount:desc', sortBy: 'amount', sortOrder: 'desc', label: 'Valor (maior)' },
  { value: 'amount:asc', sortBy: 'amount', sortOrder: 'asc', label: 'Valor (menor)' },
] as const;

export function ClientTitlesScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const { get, setMany } = useUrlState();

  const page = readPositiveInt(get(PARAM.page), 1);
  const pageSize = readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE);
  const titleType = readEnum(get(PARAM.type), TYPE_FILTERS);
  const situation = readEnum(get(PARAM.situation), SITUATION_FILTERS);
  const bucket = readEnum(get(PARAM.bucket), BUCKET_FILTERS);
  const sortBy = readEnum(get(PARAM.sortBy), SORT_FIELDS) ?? 'due_date';
  const sortOrder = readEnum(get(PARAM.sortOrder), SORT_ORDERS) ?? 'asc';
  // Sprint 15: só `true` é um valor com significado — qualquer outra coisa na
  // URL (ausente, `false`, lixo) é "sem este filtro", igual aos outros.
  const hasNoContext = get(PARAM.hasNoContext) === 'true';
  const view = readEnum(get(PARAM.view), VIEW_VALUES) ?? DEFAULT_VIEW;

  // Sem `useMemo`: o objeto é a query key do TanStack Query, que compara por
  // VALOR (hash estrutural). Uma referência nova a cada render não refaz
  // request nenhum, e a memoização só acrescentaria uma lista de dependências
  // a manter em dia.
  const queryParams: ListClientTitlesParams = {
    page,
    pageSize,
    type: titleType ?? null,
    situation: situation ?? null,
    bucket: bucket ?? null,
    sortBy,
    sortOrder,
    hasNoContext: hasNoContext || null,
  };

  const listQuery = useClientTitlesList(clientId, queryParams);
  const summaryQuery = useClientTitlesSummary(clientId);
  const syncMutation = useSyncClientTitles(clientId);
  // Cliente ENCERRADO: o servidor recusa a sincronização com 409 e a LEITURA
  // continua 200 (§4.12). O shell já carregou este detalhe — vem do cache.
  const clientDetail = useClientDetail(clientId);

  // Os três códigos da taxonomia de origem viram ESTADO explicativo com caminho
  // de saída, nunca um toast genérico (S9 / R7). Guardar o erro é o que permite
  // renderizá-lo; qualquer outro erro segue no toast.
  const [originError, setOriginError] = useState<unknown>(null);

  // Sprint 15 (FRONT 15.1): a gaveta de contexto abre sobre UM título por vez —
  // guardamos o `ClientTitle` inteiro (não só o id) para o cabeçalho da gaveta
  // não precisar de um 2º request só para mostrar o que a pessoa já via na
  // linha (vencimento, tipo, valor).
  const [contextTitle, setContextTitle] = useState<ClientTitle | null>(null);

  if (currentUser === null) return null;

  const summary = summaryQuery.data;
  const rows = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;

  const isClosed = clientDetail.data?.closed_at != null;
  const originStatus = clientDetail.data?.origin_status ?? 'ativa';
  const originReady = originStatus === 'ativa';
  // O código vem do `origin_status` enquanto não houve clique, e do erro real
  // do sync depois dele — que pode ser `CAPACIDADE_AUSENTE`, um caso que o
  // `origin_status` sozinho não distingue (o provedor está ativo e simplesmente
  // não lista títulos em aberto).
  const originCode = originErrorCode(originError) ?? (originReady ? null : 'SEM_CONEXAO');
  const hasOriginBlock = !isClosed && originCode !== null;

  // §4.9: mostrar ação que o servidor nega é defeito. Três motivos diferentes
  // para a ação sumir, e cada um tem a sua explicação na tela.
  const canSync = hasPermission(currentUser, 'sync_client_receivables');
  const showSyncAction = canSync && !isClosed && originCode === null;
  const isSyncing = syncMutation.isPending;

  const hasFilters =
    titleType !== undefined || situation !== undefined || bucket !== undefined || hasNoContext;
  const canViewTitleContext = hasPermission(currentUser, 'view_title_context');
  const canManageTitleContext = hasPermission(currentUser, 'manage_title_context');
  // "Nunca sincronizou" é o estado VAZIO da tela (R3) — e é o único caso em que
  // os agregados NÃO aparecem: zeros ali seriam lidos como "não deve nada".
  const neverSynced = summary?.neverSynced === true;

  async function handleSync() {
    setOriginError(null);
    try {
      await syncMutation.mutateAsync();
      toast.success('Carteira sincronizada.');
    } catch (err) {
      if (isOriginError(err)) {
        setOriginError(err);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível sincronizar a carteira.',
      );
    }
  }

  function clearFilters() {
    setMany({
      [PARAM.type]: null,
      [PARAM.situation]: null,
      [PARAM.bucket]: null,
      [PARAM.hasNoContext]: null,
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

  const showCarteiraHeaderActions = view === DEFAULT_VIEW;

  return (
    <section aria-labelledby="client-titles-heading" className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="client-titles-heading" className="text-lg font-semibold">
            Carteira
          </h2>
          <p className="text-muted-foreground text-sm">
            Os títulos a pagar e a receber em aberto deste cliente, de todas as contas correntes e
            sem recorte de mês.
          </p>
        </div>
        {showCarteiraHeaderActions && showSyncAction && syncButton}
        {/* Encerrado é só-leitura: a ação some COM o motivo, em vez de sumir em
            silêncio e deixar a pessoa procurando o botão (§4.12). */}
        {showCarteiraHeaderActions && canSync && isClosed && (
          <p className="text-muted-foreground max-w-xs text-sm">
            Cliente encerrado: a sincronização está indisponível. A carteira já sincronizada
            continua disponível para leitura.
          </p>
        )}
      </div>

      {/* Sprint 15 (FRONT 15.2): "Relatório de recebíveis" é aba DENTRO desta
          tela, não rota-irmã — mesma permissão de leitura
          (`view_client_receivables`) que já guarda a tela inteira, então não
          precisa de gate próprio aqui. Estado na aba vive na URL (`?view=`),
          como todo o resto desta tela. */}
      <Tabs
        value={view}
        onValueChange={(value) => setMany({ [PARAM.view]: value === DEFAULT_VIEW ? null : value })}
        // `min-h-0` nos DOIS níveis (Tabs e TabsContent): item flex tem
        // `min-height: auto`, então sem ele a cadeia de altura para aqui, o
        // `min-h-0 flex-1` da área da tabela recebe o conteúdo inteiro e a
        // tabela deixa de rolar dentro da própria área — o defeito 86e2uca1d de
        // volta, pego pelo gate de a11y na validação humana da Sprint 15.
        className="flex min-h-0 flex-1 flex-col gap-4"
      >
        <TabsList>
          <TabsTrigger value="carteira">Carteira</TabsTrigger>
          <TabsTrigger value="relatorio">Relatório de recebíveis</TabsTrigger>
        </TabsList>

        <TabsContent value="relatorio" className="mt-0">
          <ReceivablesReportScreen clientId={clientId} />
        </TabsContent>

        <TabsContent value="carteira" className="mt-0 flex min-h-0 flex-1 flex-col gap-4">
          {/* Agregados ANTES da lista: o aging é a pergunta que a reunião faz, e
          quem opera precisa dele antes de percorrer as linhas. */}
          {summaryQuery.isLoading ? (
            <ClientTitlesSummarySkeleton />
          ) : summary !== undefined && !neverSynced ? (
            <ClientTitlesSummaryBlock summary={summary} />
          ) : null}

          {/* R3: "a última tentativa falhou" mostra os agregados da última ÍNTEGRA
          com a data dela — sem isso, a pessoa não sabe se está olhando a
          posição de ontem ou a de três meses atrás. */}
          {summary?.syncFailedAt != null && (
            <div
              role="status"
              className="bg-warning-muted text-warning ring-warning/30 space-y-1 rounded-lg p-4 text-sm ring-1 ring-inset"
            >
              <p className="font-medium">A última tentativa de sincronização falhou</p>
              <p>
                A carteira abaixo é a da última sincronização bem-sucedida
                {summary.syncedAt != null
                  ? `, de ${formatCreatedAt(summary.syncedAt)}.`
                  : ' — que ainda não aconteceu.'}
              </p>
            </div>
          )}

          {hasOriginBlock && originCode !== null && (
            <OriginStateBlock code={originCode} clientId={clientId} />
          )}

          <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
            <div className="space-y-1.5 lg:w-48">
              <Label htmlFor="titles-type-filter">Tipo</Label>
              <Select
                value={titleType ?? ALL}
                onValueChange={(value) =>
                  setMany({
                    [PARAM.type]: value === ALL ? null : value,
                    [PARAM.page]: null,
                  })
                }
              >
                <SelectTrigger id="titles-type-filter" className="w-full">
                  <SelectValue placeholder="Todos os tipos" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Todos os tipos</SelectItem>
                  {TYPE_FILTERS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {TYPE_FILTER_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5 lg:w-48">
              <Label htmlFor="titles-situation-filter">Situação</Label>
              <Select
                value={situation ?? ALL}
                onValueChange={(value) =>
                  setMany({
                    [PARAM.situation]: value === ALL ? null : value,
                    [PARAM.page]: null,
                  })
                }
              >
                <SelectTrigger id="titles-situation-filter" className="w-full">
                  <SelectValue placeholder="Todas as situações" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Todas as situações</SelectItem>
                  {SITUATION_FILTERS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {SITUATION_FILTER_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5 lg:w-48">
              <Label htmlFor="titles-bucket-filter">Balde de atraso</Label>
              <Select
                value={bucket ?? ALL}
                onValueChange={(value) =>
                  setMany({
                    [PARAM.bucket]: value === ALL ? null : value,
                    [PARAM.page]: null,
                  })
                }
              >
                <SelectTrigger id="titles-bucket-filter" className="w-full">
                  <SelectValue placeholder="Todos os baldes" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>Todos os baldes</SelectItem>
                  {BUCKET_FILTERS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {BUCKET_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5 lg:w-56">
              <Label htmlFor="titles-sort">Ordenar por</Label>
              <Select
                value={`${sortBy}:${sortOrder}`}
                onValueChange={(value) => {
                  const option = SORT_OPTIONS.find((item) => item.value === value);
                  if (!option) return;
                  setMany({
                    [PARAM.sortBy]: option.sortBy,
                    [PARAM.sortOrder]: option.sortOrder,
                    [PARAM.page]: null,
                  });
                }}
              >
                <SelectTrigger id="titles-sort" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORT_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Sprint 15 (FRONT 15.1): a fila de trabalho de quem registra contexto —
            aplicado NO SERVIDOR (`hasNoContext=true`), nunca filtro client-side. */}
            {canViewTitleContext && (
              <div className="flex items-center gap-2 lg:pb-2">
                <Switch
                  id="titles-has-no-context-filter"
                  checked={hasNoContext}
                  onCheckedChange={(checked) =>
                    setMany({
                      [PARAM.hasNoContext]: checked ? 'true' : null,
                      [PARAM.page]: null,
                    })
                  }
                  aria-label="Mostrar só vencidos sem contexto registrado"
                />
                <Label htmlFor="titles-has-no-context-filter" className="cursor-pointer text-sm">
                  Vencidos sem contexto
                </Label>
              </div>
            )}

            {hasFilters && (
              <Button type="button" variant="outline" onClick={clearFilters}>
                Limpar filtros
              </Button>
            )}
          </div>

          {/* `min-h-0 flex-1` sozinho COLAPSAVA a tabela a 0px abaixo de `lg`: a
          seção é `h-full`, e com os quatro filtros empilhados (a barra só vira
          linha em `lg`) o que está acima já consumia o viewport — o `flex-1`
          recebia 0 e o `min-h-0` autorizava encolher. Não sobrava cabeçalho,
          nem linha, nem estado vazio: só os filtros, um fio e "0–0 de 0".
          O piso de 24rem abaixo de `lg` faz o conteúdo transbordar a seção e
          quem rola passa a ser o `<main>` (o comportamento mobile previsto no
          `<TableCard>`). De `lg` para cima o piso é MENOR, 8rem: a altura é a do
          shell e a tabela rola dentro da própria área, mas a Sprint 15 pôs a
          faixa de abas acima da carteira e, no estado "última tentativa falhou"
          (aviso + agregados + filtros), sobravam 75px para a tabela a 900px de
          altura. O piso de 8rem fica abaixo do que sobra no estado normal (~167px),
          então ali nada muda e a tabela continua rolando por dentro; só no estado
          mais cheio ele transborda e quem rola é o `<main>`.
          ⚠️ `toBeVisible()` do Playwright NÃO pega esse defeito: ele não enxerga
          clipping por ancestral com `overflow`. A guarda é medir `boundingBox()`
          da região rolável (e2e `a11y-mocked.spec.ts`). */}
          <div className="min-h-[24rem] flex-1 lg:min-h-[8rem]" aria-busy={listQuery.isFetching}>
            {listQuery.isError ? (
              <ErrorState
                message={
                  listQuery.error instanceof ApiError
                    ? listQuery.error.userMessage
                    : 'Não foi possível carregar a carteira.'
                }
                onRetry={() => void listQuery.refetch()}
              />
            ) : (
              // Sem `overflow-x-auto` por fora: o `<Table>` já embrulha num
              // `ScrollRegion` focável, e um segundo scroller aninhado espreme as
              // colunas em 390px em vez de rolar (ADR-007-FE). O rótulo da região é
              // DIFERENTE do `<h2>` da seção de propósito: dois nomes iguais
              // aninhados quebram o `getByRole` no strict mode.
              <TableCard>
                <Table fill scrollRegionLabel="Títulos da carteira (rolável)">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="whitespace-nowrap">Vencimento</TableHead>
                      <TableHead>Tipo</TableHead>
                      <TableHead>Devedor / credor</TableHead>
                      <TableHead className="whitespace-nowrap">Valor</TableHead>
                      <TableHead>Situação</TableHead>
                      {canViewTitleContext && (
                        <TableHead>
                          <span className="sr-only">Contexto</span>
                        </TableHead>
                      )}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {listQuery.isLoading ? (
                      <TableSkeletonRows
                        columnCount={canViewTitleContext ? COLUMN_COUNT : COLUMN_COUNT - 1}
                      />
                    ) : (
                      rows.map((title) => (
                        <TitleRow
                          key={title.externalId}
                          title={title}
                          canViewTitleContext={canViewTitleContext}
                          onOpenContext={() => setContextTitle(title)}
                        />
                      ))
                    )}
                  </TableBody>
                </Table>
                {/* Fora do `<Table>` de propósito: a tabela rola na horizontal em 390px e
                uma célula `colSpan` cortaria o texto à direita (ver `TableEmpty`). */}
                {!listQuery.isLoading && rows.length === 0 && (
                  <TableEmpty>
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
                        Nenhum título nesta carteira.
                      </p>
                    )}
                  </TableEmpty>
                )}
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
              itemLabel="títulos"
            />
          )}
        </TabsContent>
      </Tabs>

      {/* Sprint 15 (FRONT 15.1): mesma gaveta serve o REGISTRO (para quem tem
          `manage_title_context`) e o HISTÓRICO em leitura (para quem só tem
          `view_title_context`) — a permissão que rege as duas é a mesma que
          esconde/mostra a coluna de ação na tabela, nunca um `role === `. */}
      <TitleContextSheet
        open={contextTitle !== null}
        onOpenChange={(next) => {
          if (!next) setContextTitle(null);
        }}
        clientId={clientId}
        title={contextTitle}
        canManage={canManageTitleContext}
      />
    </section>
  );
}

function TitleRow({
  title,
  canViewTitleContext,
  onOpenContext,
}: {
  title: ClientTitle;
  canViewTitleContext: boolean;
  onOpenContext: () => void;
}) {
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap font-medium tabular-nums">
        {formatBRDate(title.dueDate)}
        {title.overdueDays > 0 && (
          <span className="text-muted-foreground block text-xs">
            {title.overdueDays} {title.overdueDays === 1 ? 'dia' : 'dias'} de atraso
          </span>
        )}
      </TableCell>
      <TableCell>
        <TitleTypeBadge titleType={title.titleType} />
      </TableCell>
      <TableCell className="min-w-48">
        <SupplierCell title={title} />
        {title.documentNumber != null && (
          <span className="text-muted-foreground block text-xs">Doc. {title.documentNumber}</span>
        )}
      </TableCell>
      {/* `whitespace-nowrap`: valor monetário que quebra depois do hífen vira
          outro número para quem lê (defeito da S7). */}
      <TableCell className="whitespace-nowrap tabular-nums">{formatBRL(title.amount)}</TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1.5">
          <TitleStatusBadge status={title.status} />
          <TitleBucketBadge bucket={title.bucket ?? null} />
        </div>
      </TableCell>
      {canViewTitleContext && (
        <TableCell>
          {/* R2: a LINHA diz se o título já tem contexto. Sem isto o ícone era o
              mesmo em toda linha e quem registra não sabia onde já havia
              registro sem abrir a gaveta de cada título (validação humana da
              Sprint 15). A contagem vai no nome acessível também: quem usa leitor
              de tela ouve "2 registrados", não só vê o número. */}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Contexto do título ${title.externalId} (${
              title.contextCount === 0
                ? 'nenhum registrado'
                : `${title.contextCount} ${title.contextCount === 1 ? 'registrado' : 'registrados'}`
            })`}
            onClick={onOpenContext}
            className={
              title.contextCount > 0 ? 'text-primary gap-1.5' : 'text-muted-foreground gap-1.5'
            }
          >
            <MessageSquareText className="h-4 w-4" aria-hidden="true" />
            {title.contextCount > 0 && (
              <span className="text-xs font-semibold tabular-nums" aria-hidden="true">
                {title.contextCount}
              </span>
            )}
          </Button>
        </TableCell>
      )}
    </TableRow>
  );
}

/**
 * O devedor/credor da linha — e os TRÊS casos que o contrato distingue.
 *
 * `supplierNameResolved=false` significa "há código e o nome não pôde ser
 * resolvido": a célula mostra o CÓDIGO com a marcação explícita, nunca em
 * branco e nunca o código posando de nome (§4.5 + R4). É diferente de "este
 * título não tem devedor", em que o próprio `supplierCode` é nulo.
 *
 * A dica é `Tooltip` com `role="img"` + `aria-label`, nunca `title` nativo —
 * que não aparece no toque, não alcança o teclado e o leitor ignora.
 */
function SupplierCell({ title }: { title: ClientTitle }) {
  if (title.supplierName != null) {
    return <span>{title.supplierName}</span>;
  }
  if (title.supplierCode == null) {
    return <span className="text-muted-foreground">Sem devedor informado</span>;
  }
  const dica =
    'A origem não respondeu o nome deste cadastro agora. O código é o que a origem usa para identificá-lo.';
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={`Código ${title.supplierCode} — nome não resolvido. ${dica}`}
            className="focus-visible:ring-ring inline-flex flex-col focus-visible:outline-none focus-visible:ring-2"
          >
            <span className="tabular-nums">{title.supplierCode}</span>
            <span className="text-muted-foreground text-xs">Nome não resolvido</span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs leading-snug">
          {dica}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function TableSkeletonRows({ columnCount }: { columnCount: number }) {
  return (
    <>
      {Array.from({ length: 5 }).map((_, index) => (
        <TableRow key={index} aria-hidden="true">
          {Array.from({ length: columnCount }).map((__, cell) => (
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
 * Carteira que NUNCA foi sincronizada. Três textos porque são três situações
 * com saídas diferentes — e oferecer "Sincronizar agora" a quem o servidor nega
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
        <p className="text-sm font-medium">A carteira deste cliente ainda não foi sincronizada</p>
        <p className="text-muted-foreground text-sm">
          {isClosed
            ? 'O cliente foi encerrado antes de sincronizar, e a sincronização não fica disponível para clientes encerrados.'
            : canSync
              ? 'Traga os títulos em aberto da origem para ver o total devido e o aging.'
              : 'Quando alguém da equipe sincronizar, os títulos em aberto da origem aparecem aqui.'}
        </p>
      </div>
      {action}
    </div>
  );
}

function FilteredEmptyState({ onClear }: { onClear: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <p className="text-muted-foreground text-sm">Nenhum título encontrado para este recorte.</p>
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
      <p className="text-sm font-medium">Não foi possível carregar a carteira</p>
      <p className="text-sm">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
