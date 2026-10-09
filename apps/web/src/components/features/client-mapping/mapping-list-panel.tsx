'use client';

/**
 * Aba "Decisões" do de-para (Sprint 12 — FRONT 12.7 / R2 · R6 · R7).
 *
 * A lista é do SERVIDOR: uma linha por categoria do universo do cliente (plano
 * de contas + categorias vistas nos movimentos + as que já têm decisão), com a
 * decisão VIGENTE na competência corrente. Filtro por situação e busca por
 * CÓDIGO vão como query params — nada é recortado no navegador.
 *
 * **A busca é só por código, e a copy do campo diz isso.** O nome da categoria
 * é resolvido em runtime e nunca persistido (§4.5, decidido na Sprint 10):
 * oferecer busca por nome exigiria gravá-lo.
 *
 * **Contadores que filtram e barra compacta (86e3f55bd).** O recorte por
 * situação vem dos contadores do topo (`counts` do envelope, universo inteiro);
 * o `Select` de situação saiu. Cada filtro ativo vira etiqueta removível, e link
 * antigo com `situation`/`code` continua abrindo o mesmo recorte. A página rola
 * e a tabela não (`stickyHeader="page"`), e clicar na linha abre a gaveta de
 * decisão para quem edita.
 *
 * **Gating.** Ler é de quem alcança o cliente (o operador inclusive). Editar,
 * confirmar herdadas em lote e "Iniciar de-para" pedem `manage_client_mapping`
 * — as ações ficam OCULTAS para quem não a tem (nunca desabilitadas), e cliente
 * encerrado também as esconde, com o motivo dito na tela (§4.12). As duas ações
 * do LOTE só existem no destino que herda (R7).
 *
 * **Destino `conta_contabil` (S16 — FRONT 16.6).** A decisão aponta uma conta do
 * plano contábil DO CLIENTE e leva um histórico padrão: a coluna Decisão mostra
 * código e nome da conta, entra a coluna "Histórico padrão" (truncado, dica
 * acessível), a decisão LEGADA do catálogo ganha o selo "Refazer no plano do
 * cliente" e a ação "Refazer", e a gaveta é a `AccountingDecisionSheet`. Nos
 * outros destinos nada disso existe — `isAccountingDestination` é o único corte.
 */

import { CheckCheck, Loader2, Pencil, Search, Sprout, X } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { toast } from 'sonner';

import {
  accountingChartPath,
  chartOfAccountsPath,
  mappingCatalogPath,
} from '@/components/features/navigation/nav-items';
import { EmptyState } from '@/components/shared/empty-state';
import { MappingVignette } from '@/components/shared/vignettes';
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
import { useAccountingChartList } from '@/hooks/use-client-accounting-chart';
import { useConfirmInheritedDecisions, useInheritMapping } from '@/hooks/use-client-mapping';
import { ApiError } from '@/lib/api/client';
import type {
  ConfirmInheritedResult,
  MappingDestination,
  MappingListItem,
  MappingListResponse,
  MappingSituation,
} from '@/lib/contracts';
import { formatReferenceMonth } from '@/lib/format';
import { cn } from '@/lib/utils';

import { AccountingDecisionSheet } from './accounting-decision-sheet';
import {
  isAccountingDestination,
  LegacyRedoBadge,
  TruncatedHistory,
} from './accounting-destination';
import {
  MAPPING_SITUATION_FILTER_LABELS,
  MappingDivergenceIndicator,
  MappingSituationBadge,
} from './client-mapping-badges';
import { MappingDecisionSheet } from './mapping-decision-sheet';
import {
  isMappingCountActive,
  mappingCountFilterSituation,
  MappingSituationCountsBlock,
  MappingSituationCountsSkeleton,
  type MappingCountKey,
} from './mapping-situation-counts';
import { OriginTargetsAction } from './origin-targets-dialog';
import { VigenciaActionDialog } from './vigencia-action-dialog';

export const SITUATION_FILTERS: readonly MappingSituation[] = [
  'herdada',
  'confirmada',
  'nao_mapear',
  'sem_decisao',
];
/** Teto do termo, igual ao `MAX_MOVEMENT_CATEGORY_CODE_CHARS` do servidor. */
export const MAX_CODE_CHARS = 50;

/**
 * Só o `demonstrativo_contabil` HERDA do plano de contas (R7): é a regra do
 * servidor (`inherit` responde `destino_sem_heranca` nos outros quatro), e o
 * botão que não faria nada não aparece.
 */
export const INHERITING_DESTINATION_TYPE = 'demonstrativo_contabil';

interface MappingListPanelProps {
  clientId: string;
  destination: MappingDestination;
  listQuery: {
    data?: MappingListResponse;
    isLoading: boolean;
    isFetching: boolean;
    isError: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  situation: MappingSituation | undefined;
  codeInput: string;
  /** O recorte por código APLICADO (o da URL, que a lista já mostra) — não o que está sendo digitado. */
  codeFilter: string;
  onCodeInputChange: (value: string) => void;
  onSituationChange: (value: MappingSituation | null) => void;
  onClearFilters: () => void;
  /** Remove SÓ o recorte por código (etiqueta): limpa o campo e a URL juntos. */
  onClearCode: () => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  page: number;
  pageSize: number;
  hasFilters: boolean;
  serverCompetence: string;
  canManage: boolean;
  /** `manage_client_accounting_chart` — só quem a tem é mandado importar o plano. */
  canManageChart: boolean;
  /**
   * `manage_mapping_catalog` (86e3n70pn): só quem escreve no catálogo da organização
   * é mandado cadastrar alvos e vê a ação de criá-los a partir da origem.
   */
  canManageCatalog: boolean;
  isClosed: boolean;
}

export function MappingListPanel({
  clientId,
  destination,
  listQuery,
  situation,
  codeInput,
  codeFilter,
  onCodeInputChange,
  onSituationChange,
  onClearFilters,
  onClearCode,
  onPageChange,
  onPageSizeChange,
  page,
  pageSize,
  hasFilters,
  serverCompetence,
  canManage,
  canManageChart,
  canManageCatalog,
  isClosed,
}: MappingListPanelProps) {
  const [editing, setEditing] = useState<MappingListItem | null>(null);

  const rows = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;
  // §4.9 + §4.12: sem a permissão, ou com o cliente encerrado, as ações de
  // escrita não existem na tela.
  const showWriteActions = canManage && !isClosed;
  const inherits = destination.type === INHERITING_DESTINATION_TYPE;
  const accounting = isAccountingDestination(destination.type);
  const columnCount = (showWriteActions ? 5 : 4) + (accounting ? 1 : 0);

  const counts = listQuery.data?.counts;
  const activeSituation = situation ?? null;

  /**
   * Clique num contador: aplica a situação da tabela
   * `mappingCountFilterSituation`; clicar no que já está ativo desfaz. O código
   * não é do contador: fica.
   */
  function handleCountSelect(key: MappingCountKey) {
    onSituationChange(
      isMappingCountActive(activeSituation, key) ? null : mappingCountFilterSituation(key),
    );
  }

  // Etiquetas removíveis: uma por parâmetro, remover limpa SÓ aquele.
  const activeChips: { key: string; label: string; onRemove: () => void }[] = [];
  if (situation !== undefined) {
    activeChips.push({
      key: 'situation',
      label: MAPPING_SITUATION_FILTER_LABELS[situation],
      onRemove: () => onSituationChange(null),
    });
  }
  if (codeFilter !== '') {
    activeChips.push({ key: 'code', label: `Código ${codeFilter}`, onRemove: onClearCode });
  }

  return (
    <div className="flex flex-col gap-4">
      {/* No `conta_contabil` o alvo vem do plano do CLIENTE, não do catálogo:
          o aviso de catálogo vazio não se aplica — o que importa é ter plano. */}
      {accounting && (
        <AccountingPlanNotice
          clientId={clientId}
          canManageChart={canManageChart}
          isClosed={isClosed}
        />
      )}

      {!accounting && destination.targetsCount === 0 && (
        <EmptyCatalogNotice
          clientId={clientId}
          destination={destination}
          canManageCatalog={canManageCatalog}
          isClosed={isClosed}
        />
      )}

      {/* Contadores ANTES da barra: são o recorte por situação (Parte B). */}
      {listQuery.isLoading ? (
        <MappingSituationCountsSkeleton />
      ) : counts !== undefined ? (
        <MappingSituationCountsBlock
          counts={counts}
          active={activeSituation}
          onSelect={handleCountSelect}
        />
      ) : null}

      {/* Barra compacta (Parte C): numa linha de `xl` para cima, com as ações do
          lote à direita. O rótulo do campo é `sr-only` (o nome acessível segue o
          mesmo); a ajuda visível é curta e a descrição completa vai no leitor. */}
      <div className="flex flex-col gap-3 xl:flex-row xl:flex-wrap xl:items-center">
        <div className="xl:w-72">
          <Label htmlFor="mapping-code-search" className="sr-only">
            Buscar por código da categoria
          </Label>
          <div className="relative">
            <Search
              className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              id="mapping-code-search"
              value={codeInput}
              maxLength={MAX_CODE_CHARS}
              onChange={(e) => onCodeInputChange(e.target.value)}
              placeholder="Buscar por código"
              className="pl-9 pr-28"
              aria-describedby="mapping-code-search-help"
            />
            <span
              aria-hidden="true"
              className="text-muted-foreground pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs"
            >
              só pelo código
            </span>
          </div>
          <p id="mapping-code-search-help" className="sr-only">
            A busca é só pelo código (começando por). O nome vem da origem na hora e não é
            pesquisável.
          </p>
        </div>

        {/* Etiquetas e "Limpar filtros" andam juntos: quando não cabem na linha,
            descem como um bloco, e o botão nunca fica sozinho. */}
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
            <Button type="button" variant="outline" size="sm" onClick={onClearFilters}>
              Limpar filtros
            </Button>
          </div>
        )}

        {/* As duas ações do lote só existem onde há herança (R7): nos outros
            destinos não há herdada para confirmar, e o botão abria um diálogo
            com "Confirmar" desabilitado (validação humana da S12). */}
        {showWriteActions && inherits && (
          <div className="flex flex-wrap gap-2 xl:ml-auto">
            {/* Catálogo já com alvos: a ação continua à mão para completar os que
                faltam (um cliente novo da organização declara contas novas). */}
            {canManageCatalog && destination.targetsCount > 0 && (
              <OriginTargetsAction clientId={clientId} destination={destination} />
            )}
            <InheritAction
              clientId={clientId}
              destination={destination}
              serverCompetence={serverCompetence}
            />
            <ConfirmInheritedAction
              clientId={clientId}
              destination={destination}
              serverCompetence={serverCompetence}
              codeFilter={codeFilter}
              situationFilter={situation}
            />
          </div>
        )}
      </div>

      {/* Altura NATURAL (86e3f55bd): sem piso nem `flex-1`. A tabela cresce com
          as linhas e quem rola é o `<main>`; o cabeçalho gruda no topo dele de
          `xl` para cima (`stickyHeader="page"`). */}
      <div aria-busy={listQuery.isFetching}>
        {listQuery.isError ? (
          <ListErrorState error={listQuery.error} onRetry={() => void listQuery.refetch()} />
        ) : (
          <TableCard pageScroll>
            <Table
              stickyHeader="page"
              scrollRegionLabel="Categorias do de-para (rolável)"
              // Linha mais baixa (Parte D), como na carteira.
              className="[&_td]:py-2 [&_th]:h-10"
            >
              <TableHeader>
                <TableRow>
                  <TableHead>Categoria</TableHead>
                  <TableHead>Situação</TableHead>
                  <TableHead>Decisão</TableHead>
                  {accounting && <TableHead>Histórico padrão</TableHead>}
                  <TableHead className="whitespace-nowrap">Vigente desde</TableHead>
                  {showWriteActions && (
                    <TableHead>
                      <span className="sr-only">Ações</span>
                    </TableHead>
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {listQuery.isLoading ? (
                  <SkeletonRows columnCount={columnCount} />
                ) : (
                  rows.map((item) => (
                    <MappingRow
                      key={`${item.sourceType}:${item.categoryCode}`}
                      item={item}
                      accounting={accounting}
                      showEdit={showWriteActions}
                      onEdit={() => setEditing(item)}
                    />
                  ))
                )}
              </TableBody>
            </Table>
            {/* Fora do `<Table>`: em 390px a tabela rola na horizontal e uma célula
                `colSpan` cortaria o texto à direita (ver `TableEmpty`). */}
            {!listQuery.isLoading && rows.length === 0 && (
              <TableEmpty>
                {hasFilters ? (
                  <div className="flex flex-col items-center gap-3 text-center">
                    <p className="text-muted-foreground text-sm">
                      Nenhuma categoria encontrada para este recorte.
                    </p>
                    <Button type="button" variant="outline" size="sm" onClick={onClearFilters}>
                      Limpar filtros
                    </Button>
                  </div>
                ) : (
                  <EmptyUniverseState clientId={clientId} />
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
          onPageChange={onPageChange}
          onPageSizeChange={onPageSizeChange}
          disabled={listQuery.isLoading}
          itemLabel="categorias"
        />
      )}

      {showWriteActions && accounting && (
        <AccountingDecisionSheet
          open={editing !== null}
          onOpenChange={(next) => {
            if (!next) setEditing(null);
          }}
          clientId={clientId}
          destination={destination}
          item={editing}
          serverCompetence={serverCompetence}
          canManageChart={canManageChart}
        />
      )}
      {showWriteActions && !accounting && (
        <MappingDecisionSheet
          open={editing !== null}
          onOpenChange={(next) => {
            if (!next) setEditing(null);
          }}
          clientId={clientId}
          destination={destination}
          item={editing}
          serverCompetence={serverCompetence}
        />
      )}
    </div>
  );
}

/**
 * Uma linha do de-para (Parte D da 86e3f55bd): mais baixa, e clicar em qualquer
 * ponto dela abre a gaveta de decisão para quem edita. O caminho de TECLADO
 * continua sendo o botão da coluna Ações: nada de `tabIndex`, `role` nem
 * `onKeyDown` no `<tr>` (interativo aninhado reprova no axe). O botão para a
 * propagação para não abrir a gaveta duas vezes. Para quem só lê, a linha não é
 * clicável.
 */
function MappingRow({
  item,
  accounting,
  showEdit,
  onEdit,
}: {
  item: MappingListItem;
  accounting: boolean;
  showEdit: boolean;
  onEdit: () => void;
}) {
  const legacy = accounting && item.requiresRedo;
  const actionLabel = legacy ? 'Refazer' : item.decision ? 'Alterar' : 'Decidir';
  return (
    <TableRow className={cn(showEdit && 'cursor-pointer')} onClick={showEdit ? onEdit : undefined}>
      {/* Categoria e Decisão quebram linha: em `xl`+ a tabela precisa caber na
          largura (o wrapper deixa de rolar). A vigência segue `nowrap`. */}
      <TableCell className="min-w-40 whitespace-normal">
        <span className="block font-medium tabular-nums">{item.categoryCode}</span>
        {item.categoryNameResolved && item.categoryName ? (
          <span className="text-muted-foreground block text-xs">{item.categoryName}</span>
        ) : (
          <span className="text-muted-foreground block text-xs">Nome indisponível agora</span>
        )}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1.5">
          <MappingSituationBadge situation={item.situation} />
          {item.divergent && (
            <MappingDivergenceIndicator
              categoryCode={item.categoryCode}
              originDreCode={item.originDreCode}
            />
          )}
        </div>
      </TableCell>
      <TableCell className="min-w-40 whitespace-normal">
        {accounting ? <AccountingDecisionCell item={item} /> : <DecisionCell item={item} />}
      </TableCell>
      {accounting && (
        <TableCell className="max-w-56">
          {item.decision === 'alvo' && !item.requiresRedo ? (
            <TruncatedHistory history={item.history} />
          ) : (
            <span className="text-muted-foreground text-sm">—</span>
          )}
        </TableCell>
      )}
      <TableCell className="whitespace-nowrap">
        {item.effectiveFrom ? formatReferenceMonth(item.effectiveFrom) : '—'}
      </TableCell>
      {showEdit && (
        <TableCell>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={(event) => {
              event.stopPropagation();
              onEdit();
            }}
            aria-label={`${actionLabel} a categoria ${item.categoryCode}`}
          >
            <Pencil className="h-4 w-4" aria-hidden="true" />
            {actionLabel}
          </Button>
        </TableCell>
      )}
    </TableRow>
  );
}

function DecisionCell({ item }: { item: MappingListItem }) {
  if (item.decision === 'nao_mapear') {
    return <span className="text-muted-foreground">Fica fora deste destino</span>;
  }
  if (item.decision === 'alvo' && item.targetCode) {
    return (
      <>
        <span className="block tabular-nums">{item.targetCode}</span>
        {item.targetName && (
          <span className="text-muted-foreground block text-xs">{item.targetName}</span>
        )}
      </>
    );
  }
  return <span className="text-muted-foreground">Aguardando decisão</span>;
}

/**
 * A decisão no `conta_contabil` (S16): a conta do plano do CLIENTE (código e
 * nome), ou a decisão LEGADA do catálogo — só-leitura, com o selo de refazer.
 */
function AccountingDecisionCell({ item }: { item: MappingListItem }) {
  if (item.decision === 'nao_mapear') {
    return <span className="text-muted-foreground">Fica fora deste destino</span>;
  }
  if (item.requiresRedo) {
    return (
      <div className="space-y-1">
        {item.targetCode && (
          <span className="text-muted-foreground block text-xs tabular-nums">
            Catálogo: {item.targetCode}
            {item.targetName ? ` — ${item.targetName}` : ''}
          </span>
        )}
        <LegacyRedoBadge />
      </div>
    );
  }
  if (item.decision === 'alvo' && item.accountingAccountCode) {
    return (
      <>
        <span className="block tabular-nums">{item.accountingAccountCode}</span>
        {item.accountingAccountName && (
          <span className="text-muted-foreground block text-xs">{item.accountingAccountName}</span>
        )}
      </>
    );
  }
  return <span className="text-muted-foreground">Aguardando decisão</span>;
}

/**
 * Destino com o catálogo de alvos VAZIO (86e3n70pn). Organização nova nasce assim, e
 * o texto antigo mandava "pedir ao administrador da organização" a quem era o
 * administrador. Agora o aviso se ramifica pela permissão do catálogo
 * (`manage_mapping_catalog`), a mesma que libera a tela de destino (§7 Frontend: a
 * ação oferecida existe no destino, pela MESMA pergunta dos dois lados):
 *   - quem pode cadastrar vê "Cadastrar alvos" (a tela do catálogo com o destino já
 *     aberto) e, no demonstrativo, a ação de criar os alvos a partir das contas que a
 *     origem deste cliente declara;
 *   - os demais leem o texto de sempre, sem verbo de ação que a matriz nega.
 */
function EmptyCatalogNotice({
  clientId,
  destination,
  canManageCatalog,
  isClosed,
}: {
  clientId: string;
  destination: MappingDestination;
  canManageCatalog: boolean;
  isClosed: boolean;
}) {
  const inherits = destination.type === INHERITING_DESTINATION_TYPE;
  return (
    <div
      role="status"
      data-testid="mapping-empty-catalog"
      className="bg-warning-muted text-warning ring-warning/30 space-y-2 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <p className="font-medium">O catálogo de alvos deste destino está vazio</p>
      {canManageCatalog ? (
        <>
          <p>
            Sem alvos, a única decisão possível é &quot;Não mapear&quot;.{' '}
            {inherits
              ? 'Cadastre os alvos (código e nome) do destino ou crie-os a partir das contas de demonstrativo que as categorias do Omie deste cliente já declaram; depois, "Iniciar de-para" herda as decisões.'
              : `Cadastre os alvos (código e nome) do destino ${destination.name}.`}{' '}
            O catálogo vale para todos os clientes da organização.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline" size="sm">
              <Link href={mappingCatalogPath(destination.id)}>Cadastrar alvos</Link>
            </Button>
            {inherits && !isClosed && (
              <OriginTargetsAction
                clientId={clientId}
                destination={destination}
                label="Criar alvos a partir das contas de demonstrativo deste cliente"
              />
            )}
          </div>
        </>
      ) : (
        <p>
          Sem alvos cadastrados, a única decisão possível é &quot;Não mapear&quot;. Peça ao
          administrador da organização para cadastrar os alvos (código e nome) do destino{' '}
          {destination.name}.
        </p>
      )}
    </div>
  );
}

/**
 * Cliente SEM plano contábil no destino `conta_contabil`: não há conta para
 * escolher. Quem pode importar (`manage_client_accounting_chart`, cliente
 * aberto) é orientado a importar em "Plano contábil"; os demais leem só o
 * porquê, com um link de LEITURA — nunca um verbo de ação que a matriz nega
 * (ADR-053-FE). Some com plano, e some enquanto a sonda carrega ou falha (sem
 * certeza, não se afirma "sem plano").
 */
function AccountingPlanNotice({
  clientId,
  canManageChart,
  isClosed,
}: {
  clientId: string;
  canManageChart: boolean;
  isClosed: boolean;
}) {
  const probe = useAccountingChartList(clientId, { page: 1, pageSize: 1 });
  if (probe.data?.pagination.total !== 0) return null;
  const canImport = canManageChart && !isClosed;
  return (
    <div
      role="status"
      data-testid="mapping-accounting-no-plan"
      className="bg-info-muted text-info ring-info/30 space-y-2 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <p className="font-medium">Este cliente ainda não tem plano contábil</p>
      <p>
        {canImport
          ? 'Neste destino a conta de cada categoria vem do plano contábil do cliente. Importe o plano para decidir; enquanto isso, só "Não mapear" é possível.'
          : isClosed
            ? 'Este destino depende do plano contábil do cliente, que não foi importado antes do encerramento.'
            : 'Este destino depende do plano contábil do cliente, que é importado pelo escritório. Enquanto isso, só "Não mapear" é possível.'}
      </p>
      <Button asChild variant="outline" size="sm">
        <Link href={accountingChartPath(clientId)}>
          {canImport ? 'Ir para Plano contábil' : 'Ver Plano contábil'}
        </Link>
      </Button>
    </div>
  );
}

function SkeletonRows({ columnCount }: { columnCount: number }) {
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
 * Universo vazio (R7): o cliente não tem categorias do Omie sincronizadas nem
 * movimento com categoria — não há o que classificar ainda. A saída é o plano
 * de contas, e o estado diz isso em vez de mostrar uma tabela muda.
 */
/** De-para sem categoria (86e3h57b5: vinheta do `EmptyState`, o `TableEmpty` é a moldura). */
function EmptyUniverseState({ clientId }: { clientId: string }) {
  return (
    <EmptyState
      framed={false}
      vignette={<MappingVignette />}
      title="Ainda não há categorias para classificar"
      description="O de-para lista as categorias do Omie sincronizadas e as que aparecem nos movimentos do cliente. Sincronize as categorias do Omie para começar."
      action={
        <Button asChild variant="outline" size="sm">
          <Link href={chartOfAccountsPath(clientId)}>Ir para Categorias do Omie</Link>
        </Button>
      }
    />
  );
}

function ListErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const notConfigured = error instanceof ApiError && error.code === 'DESTINO_NAO_CONFIGURADO';
  return (
    <div
      role="alert"
      className="border-destructive/30 bg-destructive/5 text-destructive space-y-3 rounded-lg border p-6"
    >
      <p className="text-sm font-medium">
        {notConfigured ? 'Destino não configurado' : 'Não foi possível carregar o de-para'}
      </p>
      <p className="text-sm">
        {error instanceof ApiError ? error.userMessage : 'Ocorreu um erro inesperado.'}
      </p>
      {!notConfigured && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Tentar novamente
        </Button>
      )}
    </div>
  );
}

/**
 * Confirmação em LOTE das herdadas (R6). A contagem vem do SERVIDOR antes de
 * confirmar (`confirm=false` não grava), e é ela que o diálogo mostra — nunca o
 * número de linhas da página.
 *
 * Duas coisas que o follow-up 86e3f0uxb fechou:
 *   - o recorte por CÓDIGO da lista vai ao servidor (`code`, começando por): a
 *     pessoa confirma "estas", não o destino inteiro, e `affected` respeita o
 *     recorte. O filtro de SITUAÇÃO não se aplica — o lote só toca herdadas — e
 *     o diálogo diz isso quando ele está ativo;
 *   - as herdadas VIGENTES dependem da competência de início: mudar o mês no
 *     diálogo reconta, senão o diálogo mostrava N e o servidor aplicava 0.
 */
function ConfirmInheritedAction({
  clientId,
  destination,
  serverCompetence,
  codeFilter,
  situationFilter,
}: {
  clientId: string;
  destination: MappingDestination;
  serverCompetence: string;
  codeFilter: string;
  situationFilter: MappingSituation | undefined;
}) {
  const [open, setOpen] = useState(false);
  const countMutation = useConfirmInheritedDecisions(clientId, destination.type);
  const applyMutation = useConfirmInheritedDecisions(clientId, destination.type);
  const count: ConfirmInheritedResult | undefined = countMutation.data;
  const code = codeFilter.trim();
  const situationFilterIgnored = situationFilter !== undefined && situationFilter !== 'herdada';

  // A contagem sai em EVENTO (o clique que abre, a troca do mês), nunca num
  // efeito: o diálogo abre já perguntando ao servidor quantas herdadas a
  // confirmação atingiria a partir da competência escolhida.
  function recount(effectiveFrom: string) {
    countMutation.mutate(
      { confirm: false, confirmRetroactive: false, effectiveFrom, ...(code ? { code } : {}) },
      {
        onError: (err) => {
          // Contagem velha não vale para o mês novo: sem número, sem confirmar.
          countMutation.reset();
          toast.error(
            err instanceof ApiError ? err.userMessage : 'Não foi possível contar as herdadas.',
          );
        },
      },
    );
  }

  function handleOpen() {
    setOpen(true);
    recount(serverCompetence);
  }

  function handleOpenChange(next: boolean) {
    if (next) return;
    setOpen(false);
    countMutation.reset();
  }

  const affected = count?.affected ?? 0;
  const summary = countMutation.isPending ? (
    <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      Contando as decisões herdadas…
    </p>
  ) : count !== undefined ? (
    <div className="bg-muted space-y-1 rounded-lg p-3 text-sm" role="status">
      <p className="font-medium" data-testid="confirm-inherited-count">
        {affected === 0
          ? code
            ? `Não há decisões herdadas com código começando por "${code}" para confirmar.`
            : 'Não há decisões herdadas para confirmar neste destino.'
          : `${affected} ${affected === 1 ? 'decisão herdada será confirmada' : 'decisões herdadas serão confirmadas'}${code ? ` (código começando por "${code}")` : ''}.`}
      </p>
      {affected > 0 && situationFilterIgnored && (
        <p className="text-muted-foreground">
          O filtro de situação não se aplica: o lote atinge só as herdadas
          {code ? ' dentro do recorte por código' : ' do destino'}.
        </p>
      )}
    </div>
  ) : null;

  return (
    <>
      <Button type="button" variant="outline" onClick={handleOpen}>
        <CheckCheck className="h-4 w-4" aria-hidden="true" />
        Confirmar herdadas
      </Button>
      <VigenciaActionDialog
        open={open}
        onOpenChange={handleOpenChange}
        title="Confirmar herdadas"
        description={`Cada decisão herdada da origem em "${destination.name}" vira decisão confirmada com o mesmo efeito.`}
        summary={summary}
        confirmLabel={affected > 0 ? `Confirmar ${affected}` : 'Confirmar'}
        confirmDisabled={count === undefined || affected === 0}
        onEffectiveFromChange={recount}
        serverCompetence={serverCompetence}
        errorFallback="Não foi possível confirmar as herdadas."
        onConfirm={async ({ effectiveFrom, confirmRetroactive }) => {
          const result = await applyMutation.mutateAsync({
            confirm: true,
            effectiveFrom,
            confirmRetroactive,
            ...(code ? { code } : {}),
          });
          toast.success(
            `${result.affected} ${result.affected === 1 ? 'decisão confirmada' : 'decisões confirmadas'}, vigentes a partir de ${formatReferenceMonth(effectiveFrom)}.`,
          );
          handleOpenChange(false);
        }}
      />
    </>
  );
}

/**
 * "Iniciar de-para" (R7) — só no destino que herda. Pré-preenche a partir do
 * plano de contas sincronizado, marcando cada decisão como HERDADA. Os dois
 * estados sem herança não são erro e viram aviso, não toast vermelho.
 */
function InheritAction({
  clientId,
  destination,
  serverCompetence,
}: {
  clientId: string;
  destination: MappingDestination;
  serverCompetence: string;
}) {
  const [open, setOpen] = useState(false);
  const inheritMutation = useInheritMapping(clientId, destination.type);
  const onOpenChange = setOpen;
  return (
    <>
      <Button type="button" variant="secondary" onClick={() => setOpen(true)}>
        <Sprout className="h-4 w-4" aria-hidden="true" />
        Iniciar de-para
      </Button>
      <VigenciaActionDialog
        open={open}
        onOpenChange={onOpenChange}
        title="Iniciar de-para"
        description={`Pré-preenche "${destination.name}" com a conta de demonstrativo que as categorias do Omie já trazem. Cada decisão entra como herdada, para você confirmar ou alterar. Decisões existentes não são tocadas.`}
        confirmLabel="Iniciar de-para"
        continuityNote="Só as categorias sem decisão recebem a herdada; quem já tem decisão não é tocado."
        serverCompetence={serverCompetence}
        errorFallback="Não foi possível iniciar o de-para."
        onConfirm={async ({ effectiveFrom, confirmRetroactive }) => {
          const result = await inheritMutation.mutateAsync({ effectiveFrom, confirmRetroactive });
          if (result.state === 'sem_plano_de_contas') {
            toast.info(
              'O cliente não tem as categorias do Omie sincronizadas: não há o que herdar. Sincronize em "Categorias do Omie" e tente de novo.',
            );
          } else if (result.state === 'destino_sem_heranca') {
            toast.info('Este destino não herda das categorias do Omie: ele começa sem decisão.');
          } else {
            toast.success(
              `${result.created} ${result.created === 1 ? 'decisão herdada' : 'decisões herdadas'} a partir de ${formatReferenceMonth(result.effectiveFrom)}.`,
            );
          }
          onOpenChange(false);
        }}
      />
    </>
  );
}
