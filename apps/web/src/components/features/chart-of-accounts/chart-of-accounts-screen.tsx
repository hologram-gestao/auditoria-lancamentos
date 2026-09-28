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
 * **Estado na URL** (`page`, `pageSize`, `status`, `parentCode`, `code`,
 * `hasDreCode`, `hasAccountingCode`): a view fica linkável e sobrevive ao F5.
 * Os dois campos de texto guardam o valor digitado em estado local e só
 * escrevem na URL depois do debounce — sem isso, cada tecla viraria um
 * `router.replace` e um request. Desde a 86e3f55bc os cinco cards de cobertura
 * filtram (tabela em `coverageFilterParams`), a situação é um grupo de botões e
 * cada filtro ativo vira etiqueta removível; link antigo com `status`,
 * `parentCode` e `code` continua abrindo o mesmo recorte, com as etiquetas.
 *
 * **A página rola, a tabela não** (86e3f55bc, o desenho da carteira na
 * 86e3eq9uy): a seção tem altura natural, quem rola é o `<main>` do shell e o
 * cabeçalho da tabela gruda no topo dele (`<Table stickyHeader="page">`, de
 * `xl` para cima). A paginação vem depois da última linha, no fluxo.
 *
 * **Sem virtualização, de propósito:** `pageSize` tem teto de 100 no servidor,
 * então a tabela nunca renderiza mais que 100 linhas. Virtualizar aqui seria a
 * primeira exceção, não o primeiro uso.
 */

import { Loader2, RefreshCw, Search, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { OriginStateBlock } from '@/components/shared/origin-state-notice';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { PaginationBar } from '@/components/ui/pagination-bar';
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
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

import { ChartFlagBadges, ChartStatusBadge } from './chart-of-accounts-badges';
import {
  ChartOfAccountsCoverageBlock,
  ChartOfAccountsCoverageSkeleton,
  coverageFilterParams,
  isCoverageFilterActive,
  type CoverageFilterKey,
  type CoverageFilterParams,
} from './chart-of-accounts-coverage';

const PARAM = {
  page: 'page',
  pageSize: 'pageSize',
  status: 'status',
  parentCode: 'parentCode',
  code: 'code',
  hasDreCode: 'hasDreCode',
  hasAccountingCode: 'hasAccountingCode',
} as const;

/** Com a página rolando (86e3f55bc), 20 era pouco; o teto de 100 do servidor não muda. */
export const DEFAULT_PAGE_SIZE = 50;
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

/** Só `true` e `false` são recortes; qualquer outra coisa na URL é "sem filtro". */
function readBoolean(raw: string | null): boolean | null {
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  return null;
}

const DRE_CHIP_LABELS = { true: 'Com destino', false: 'Sem destino declarado' } as const;
const ACCOUNTING_CHIP_LABELS = { true: 'Com conta contábil', false: 'Sem conta contábil' } as const;

export function ChartOfAccountsScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const { get, setMany } = useUrlState();

  const page = readPositiveInt(get(PARAM.page), 1);
  const pageSize = readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE);
  const status = readEnum(get(PARAM.status), STATUS_FILTERS);
  const codeParam = get(PARAM.code) ?? '';
  const parentCodeParam = get(PARAM.parentCode) ?? '';
  const hasDreCode = readBoolean(get(PARAM.hasDreCode));
  const hasAccountingCode = readBoolean(get(PARAM.hasAccountingCode));

  // Estado local dos dois campos de texto + debounce → URL. Inicializados da
  // URL para o deep link já nascer com o campo preenchido.
  const [codeInput, setCodeInput] = useState(codeParam);
  const [parentInput, setParentInput] = useState(parentCodeParam);
  const debouncedCode = useDebouncedValue(codeInput, 300);
  const debouncedParent = useDebouncedValue(parentInput, 300);

  // O guard de igualdade é o que impede o laço: `setMany` só é chamado quando o
  // valor debounced difere do que já está na URL. Filtrar volta para a página 1
  // — senão a pessoa cai numa página que o novo recorte não tem. E só escreve
  // quando o debounce ASSENTOU (`debounced === input`): limpar o campo (etiqueta
  // removida, "Limpar filtros") zera a URL na hora, e sem esta guarda o valor
  // antigo, ainda no debounce, voltava para a URL por 300ms e disparava request.
  useEffect(() => {
    if (debouncedCode !== codeInput || debouncedCode === codeParam) return;
    setMany({ [PARAM.code]: debouncedCode || null, [PARAM.page]: null });
  }, [debouncedCode, codeInput, codeParam, setMany]);

  useEffect(() => {
    if (debouncedParent !== parentInput || debouncedParent === parentCodeParam) return;
    setMany({ [PARAM.parentCode]: debouncedParent || null, [PARAM.page]: null });
  }, [debouncedParent, parentInput, parentCodeParam, setMany]);

  const queryParams = useMemo<ListChartOfAccountsParams>(
    () => ({
      page,
      pageSize,
      status: status ?? null,
      parentCode: parentCodeParam || null,
      code: codeParam || null,
      hasDreCode,
      hasAccountingCode,
    }),
    [page, pageSize, status, parentCodeParam, codeParam, hasDreCode, hasAccountingCode],
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

  const hasFilters =
    status !== undefined ||
    codeParam !== '' ||
    parentCodeParam !== '' ||
    hasDreCode !== null ||
    hasAccountingCode !== null;
  const activeCoverage: CoverageFilterParams = {
    status: status ?? null,
    hasDreCode,
    hasAccountingCode,
  };
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
      [PARAM.hasDreCode]: null,
      [PARAM.hasAccountingCode]: null,
      [PARAM.page]: null,
    });
  }

  /**
   * Clique num card de cobertura: aplica o recorte da tabela
   * `coverageFilterParams`; clicar no que já está ativo desfaz os três
   * parâmetros. Código e hierarquia ficam como estão. Sempre com a página zerada.
   */
  function handleCoverageSelect(key: CoverageFilterKey) {
    const next = isCoverageFilterActive(activeCoverage, key)
      ? coverageFilterParams('total')
      : coverageFilterParams(key);
    setMany({
      [PARAM.status]: next.status,
      [PARAM.hasDreCode]: next.hasDreCode === null ? null : String(next.hasDreCode),
      [PARAM.hasAccountingCode]:
        next.hasAccountingCode === null ? null : String(next.hasAccountingCode),
      [PARAM.page]: null,
    });
  }

  // Parte C: cada filtro ativo vira etiqueta removível; remover limpa SÓ aquele
  // parâmetro (e zera a página). Os campos de texto limpam também o estado local,
  // senão o debounce devolveria o termo para a URL.
  const activeChips: { key: string; label: string; onRemove: () => void }[] = [];
  if (status !== undefined) {
    activeChips.push({
      key: 'status',
      label: STATUS_FILTER_LABELS[status],
      onRemove: () => setMany({ [PARAM.status]: null, [PARAM.page]: null }),
    });
  }
  if (hasDreCode !== null) {
    activeChips.push({
      key: 'hasDreCode',
      label: DRE_CHIP_LABELS[`${hasDreCode}`],
      onRemove: () => setMany({ [PARAM.hasDreCode]: null, [PARAM.page]: null }),
    });
  }
  if (hasAccountingCode !== null) {
    activeChips.push({
      key: 'hasAccountingCode',
      label: ACCOUNTING_CHIP_LABELS[`${hasAccountingCode}`],
      onRemove: () => setMany({ [PARAM.hasAccountingCode]: null, [PARAM.page]: null }),
    });
  }
  if (parentCodeParam !== '') {
    activeChips.push({
      key: 'parentCode',
      label: `Filhas de ${parentCodeParam}`,
      onRemove: () => {
        setParentInput('');
        setMany({ [PARAM.parentCode]: null, [PARAM.page]: null });
      },
    });
  }
  if (codeParam !== '') {
    activeChips.push({
      key: 'code',
      label: `Código ${codeParam}`,
      onRemove: () => {
        setCodeInput('');
        setMany({ [PARAM.code]: null, [PARAM.page]: null });
      },
    });
  }

  // Parte E: quando foi a última sincronização ÍNTEGRA, para todo leitor. Some
  // enquanto a cobertura carrega e quando nunca sincronizou (não há data). Com
  // falha depois, o aviso amarelo continua e esta linha segue com a íntegra.
  const syncedAtLabel =
    !coverageQuery.isLoading && coverage?.syncedAt != null
      ? `Atualizado em ${formatCreatedAt(coverage.syncedAt)}`
      : null;

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
    <section aria-labelledby="chart-of-accounts-heading" className="flex flex-col gap-4">
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
        {/* Parte E: a data da última sincronização íntegra ao lado da ação (ou
            do motivo de ela não existir), para todo leitor. */}
        <div className="flex flex-wrap items-center gap-3">
          {syncedAtLabel !== null && (
            <p className="text-muted-foreground text-sm" data-testid="chart-synced-at">
              {syncedAtLabel}
            </p>
          )}
          {showSyncAction && syncButton}
          {/* Encerrado é só-leitura: a ação some COM o motivo, em vez de sumir em
              silêncio e deixar a pessoa procurando o botão (§4.12). */}
          {canSync && isClosed && (
            <p className="text-muted-foreground max-w-xs text-sm">
              Cliente encerrado: a sincronização está indisponível. O plano de contas já
              sincronizado continua disponível para leitura.
            </p>
          )}
        </div>
      </div>

      {/* Cobertura ANTES da lista: é a pergunta "onde vai dar trabalho", e quem
          opera precisa dela antes de percorrer as linhas. Cada valor filtra a
          lista (Parte B). */}
      {coverageQuery.isLoading ? (
        <ChartOfAccountsCoverageSkeleton />
      ) : coverage !== undefined ? (
        <ChartOfAccountsCoverageBlock
          coverage={coverage}
          active={activeCoverage}
          onSelect={handleCoverageSelect}
        />
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

      {/* Parte C: barra compacta — numa linha só de `xl` para cima. Os rótulos
          dos dois campos são `sr-only` (o nome acessível segue o mesmo, e o e2e
          usa `getByLabel`); o que se lê na tela é o placeholder. */}
      <div className="flex flex-col gap-3 xl:flex-row xl:flex-wrap xl:items-center">
        <div className="xl:w-72">
          <Label htmlFor="chart-code-search" className="sr-only">
            Buscar por código
          </Label>
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
              placeholder="Buscar por código"
              aria-describedby="chart-code-search-hint"
              className="pl-9 pr-28"
            />
            {/* Não existe busca por nome (§4.5): dito na tela, não só no rótulo. */}
            <span
              id="chart-code-search-hint"
              className="text-muted-foreground pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs"
            >
              só pelo código
            </span>
          </div>
        </div>

        {/* Grupo de botões, não `Select`: quatro opções cabem numa linha e não
            escondem o estado atrás de um clique. Não há `ToggleGroup` em
            `components/ui/` e não se cria primitivo novo para isso. */}
        <div
          role="group"
          aria-label="Situação"
          className="bg-background inline-flex flex-wrap self-start rounded-md border p-0.5 xl:self-auto"
        >
          {(
            [
              { value: null, label: 'Todas' },
              ...STATUS_FILTERS.map((value) => ({ value, label: STATUS_FILTER_LABELS[value] })),
            ] as { value: ChartOfAccountsStatus | null; label: string }[]
          ).map((option) => {
            const pressed = (status ?? null) === option.value;
            return (
              <Button
                key={option.label}
                type="button"
                variant="ghost"
                size="sm"
                aria-pressed={pressed}
                className={cn('h-8', pressed && 'bg-accent text-accent-foreground')}
                onClick={() => setMany({ [PARAM.status]: option.value, [PARAM.page]: null })}
              >
                {option.label}
              </Button>
            );
          })}
        </div>

        {/* Hierarquia: o servidor filtra pelas FILHAS DIRETAS de um código.
            "Grupo" não existe na resposta da origem — inventá-lo aqui seria
            criar um conceito que o dado não tem. */}
        <div className="xl:w-48">
          <Label htmlFor="chart-parent-filter" className="sr-only">
            Filhas do código
          </Label>
          <Input
            id="chart-parent-filter"
            value={parentInput}
            maxLength={MAX_CODE_CHARS}
            onChange={(e) => setParentInput(e.target.value)}
            placeholder="Filhas do código"
          />
        </div>

        {/* Etiquetas e "Limpar filtros" andam juntos: quando não cabem na linha
            dos controles, descem como um bloco, e o botão nunca fica sozinho. */}
        {hasFilters && (
          <div className="flex flex-wrap items-center gap-2">
            {activeChips.length > 0 && (
              <ul aria-label="Filtros ativos" className="flex flex-wrap items-center gap-2">
                {activeChips.map((chip) => (
                  <li
                    key={chip.key}
                    className="bg-accent text-accent-foreground ring-border inline-flex items-center gap-1 rounded-full py-0.5 pl-2.5 pr-1 text-xs font-medium ring-1 ring-inset"
                  >
                    {chip.label}
                    <button
                      type="button"
                      aria-label={`Remover filtro ${chip.label}`}
                      onClick={chip.onRemove}
                      className="hover:bg-background/60 focus-visible:ring-ring inline-flex h-5 w-5 cursor-pointer items-center justify-center rounded-full focus-visible:outline-none focus-visible:ring-2"
                    >
                      <X className="h-3 w-3" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <Button type="button" variant="outline" size="sm" onClick={clearFilters}>
              Limpar filtros
            </Button>
          </div>
        )}
      </div>

      {/* Altura NATURAL (86e3f55bc): sem `min-h-0 flex-1`, sem piso. A tabela
          cresce com as linhas e quem rola é o `<main>`; o cabeçalho gruda no
          topo dele de `xl` para cima (`stickyHeader="page"`). */}
      <div aria-busy={listQuery.isFetching}>
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
          <TableCard pageScroll>
            <Table
              stickyHeader="page"
              scrollRegionLabel="Categorias do plano de contas (rolável)"
              // Linha mais baixa (Parte D): ~40px sem segunda linha de texto. O
              // `p-4` padrão da célula dava 72px.
              className="[&_td]:py-2 [&_th]:h-10"
            >
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
                ) : (
                  rows.map((entry) => <ChartRow key={entry.categoryCode} entry={entry} />)
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
                    Nenhuma categoria neste plano de contas.
                  </p>
                )}
              </TableEmpty>
            )}
          </TableCard>
        )}
      </div>

      {/* No fluxo, depois da última linha — nunca grudada no rodapé: barra
          grudada cobre linha durante a rolagem (86e2uca1d). */}
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
      {/* Nome e conta de demonstrativo quebram linha: em `xl`+ a tabela precisa
          caber na largura (o wrapper deixa de rolar). Códigos seguem `nowrap`. */}
      <TableCell className="min-w-40 whitespace-normal">
        {/* `null` = a origem não respondeu na hora de resolver os nomes
            (fail-soft do servidor). Repetir o código aqui faria o dado ausente
            parecer dado bom. */}
        {entry.name ?? <span className="text-muted-foreground">Nome não disponível</span>}
      </TableCell>
      <TableCell className="min-w-40 whitespace-normal">
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
