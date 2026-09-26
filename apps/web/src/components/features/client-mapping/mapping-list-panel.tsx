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
 * **Gating.** Ler é de quem alcança o cliente (o operador inclusive). Editar,
 * confirmar herdadas em lote e "Iniciar de-para" pedem `manage_client_mapping`
 * — as ações ficam OCULTAS para quem não a tem (nunca desabilitadas), e cliente
 * encerrado também as esconde, com o motivo dito na tela (§4.12).
 */

import { CheckCheck, Loader2, Pencil, Search, Sprout } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { toast } from 'sonner';

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
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
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

import {
  MAPPING_SITUATION_FILTER_LABELS,
  MappingDivergenceIndicator,
  MappingSituationBadge,
} from './client-mapping-badges';
import { MappingDecisionSheet } from './mapping-decision-sheet';
import { VigenciaActionDialog } from './vigencia-action-dialog';

/** O Radix não aceita `''` como valor de item — `all` é o "sem filtro". */
const ALL = 'all';
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
  onCodeInputChange: (value: string) => void;
  onSituationChange: (value: MappingSituation | null) => void;
  onClearFilters: () => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  page: number;
  pageSize: number;
  hasFilters: boolean;
  serverCompetence: string;
  canManage: boolean;
  isClosed: boolean;
}

export function MappingListPanel({
  clientId,
  destination,
  listQuery,
  situation,
  codeInput,
  onCodeInputChange,
  onSituationChange,
  onClearFilters,
  onPageChange,
  onPageSizeChange,
  page,
  pageSize,
  hasFilters,
  serverCompetence,
  canManage,
  isClosed,
}: MappingListPanelProps) {
  const [editing, setEditing] = useState<MappingListItem | null>(null);

  const rows = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;
  // §4.9 + §4.12: sem a permissão, ou com o cliente encerrado, as ações de
  // escrita não existem na tela.
  const showWriteActions = canManage && !isClosed;
  const inherits = destination.type === INHERITING_DESTINATION_TYPE;
  const columnCount = showWriteActions ? 5 : 4;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      {destination.targetsCount === 0 && (
        <div
          role="status"
          className="bg-warning-muted text-warning ring-warning/30 space-y-1 rounded-lg p-4 text-sm ring-1 ring-inset"
        >
          <p className="font-medium">O catálogo de alvos deste destino está vazio</p>
          <p>
            Sem alvos cadastrados, a única decisão possível é &quot;Não mapear&quot;. Peça ao
            administrador da organização para cadastrar os alvos (código e nome) do destino{' '}
            {destination.name}.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
        <div className="min-w-0 flex-1 space-y-1.5">
          <Label htmlFor="mapping-code-search">Buscar por código da categoria</Label>
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
              placeholder="Ex.: 2.01"
              className="pl-9"
              aria-describedby="mapping-code-search-help"
            />
          </div>
          <p id="mapping-code-search-help" className="text-muted-foreground text-xs">
            A busca é só pelo código (começando por). O nome vem da origem na hora e não é
            pesquisável.
          </p>
        </div>

        <div className="space-y-1.5 lg:w-56">
          <Label htmlFor="mapping-situation-filter">Situação</Label>
          <Select
            value={situation ?? ALL}
            onValueChange={(value) =>
              onSituationChange(value === ALL ? null : (value as MappingSituation))
            }
          >
            <SelectTrigger id="mapping-situation-filter" className="w-full">
              <SelectValue placeholder="Todas as situações" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Todas as situações</SelectItem>
              {SITUATION_FILTERS.map((value) => (
                <SelectItem key={value} value={value}>
                  {MAPPING_SITUATION_FILTER_LABELS[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {hasFilters && (
          <Button type="button" variant="outline" onClick={onClearFilters}>
            Limpar filtros
          </Button>
        )}

        {showWriteActions && (
          <div className="flex flex-wrap gap-2 lg:ml-auto">
            {inherits && (
              <InheritAction
                clientId={clientId}
                destination={destination}
                serverCompetence={serverCompetence}
              />
            )}
            <ConfirmInheritedAction
              clientId={clientId}
              destination={destination}
              serverCompetence={serverCompetence}
              codeFilterActive={
                codeInput.trim() !== '' || (situation !== undefined && situation !== 'herdada')
              }
            />
          </div>
        )}
      </div>

      {/* Mesmo piso da Carteira (S11/S15): abaixo de `lg` a tabela tem altura
          natural e quem rola é o `<main>`; de `lg` para cima ela rola dentro da
          própria área. Sem o piso, filtros empilhados colapsam a tabela a 0px. */}
      <div className="min-h-[24rem] flex-1 lg:min-h-[8rem]" aria-busy={listQuery.isFetching}>
        {listQuery.isError ? (
          <ListErrorState error={listQuery.error} onRetry={() => void listQuery.refetch()} />
        ) : (
          <TableCard>
            <Table fill scrollRegionLabel="Categorias do de-para (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead>Categoria</TableHead>
                  <TableHead>Situação</TableHead>
                  <TableHead>Decisão</TableHead>
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

      {showWriteActions && (
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

function MappingRow({
  item,
  showEdit,
  onEdit,
}: {
  item: MappingListItem;
  showEdit: boolean;
  onEdit: () => void;
}) {
  return (
    <TableRow>
      <TableCell className="min-w-48">
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
      <TableCell className="min-w-40">
        <DecisionCell item={item} />
      </TableCell>
      <TableCell className="whitespace-nowrap">
        {item.effectiveFrom ? formatReferenceMonth(item.effectiveFrom) : '—'}
      </TableCell>
      {showEdit && (
        <TableCell>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onEdit}
            aria-label={`${item.decision ? 'Alterar' : 'Decidir'} a categoria ${item.categoryCode}`}
          >
            <Pencil className="h-4 w-4" aria-hidden="true" />
            {item.decision ? 'Alterar' : 'Decidir'}
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
 * Universo vazio (R7): o cliente não tem plano de contas sincronizado nem
 * movimento com categoria — não há o que classificar ainda. A saída é o plano
 * de contas, e o estado diz isso em vez de mostrar uma tabela muda.
 */
function EmptyUniverseState({ clientId }: { clientId: string }) {
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <div className="space-y-1">
        <p className="text-sm font-medium">Ainda não há categorias para classificar</p>
        <p className="text-muted-foreground text-sm">
          O de-para lista as categorias do plano de contas sincronizado e as que aparecem nos
          movimentos do cliente. Sincronize o plano de contas para começar.
        </p>
      </div>
      <Button asChild variant="outline" size="sm">
        <Link href={`/clientes/${clientId}/plano-de-contas`}>Ir para o plano de contas</Link>
      </Button>
    </div>
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
 * ⚠️ A rota confirma TODAS as herdadas vigentes do destino: o servidor não
 * recebe o filtro da tela. Quando a lista está recortada por outra coisa, o
 * diálogo diz isso em vez de deixar a pessoa achar que confirmou só o que via.
 */
function ConfirmInheritedAction({
  clientId,
  destination,
  serverCompetence,
  codeFilterActive,
}: {
  clientId: string;
  destination: MappingDestination;
  serverCompetence: string;
  codeFilterActive: boolean;
}) {
  const [open, setOpen] = useState(false);
  const countMutation = useConfirmInheritedDecisions(clientId, destination.type);
  const applyMutation = useConfirmInheritedDecisions(clientId, destination.type);
  const count: ConfirmInheritedResult | undefined = countMutation.data;

  // A contagem sai no CLIQUE (evento), nunca num efeito: o diálogo abre já
  // perguntando ao servidor quantas herdadas a confirmação atingiria.
  function handleOpen() {
    setOpen(true);
    countMutation.mutate(
      { confirm: false, confirmRetroactive: false },
      {
        onError: (err) =>
          toast.error(
            err instanceof ApiError ? err.userMessage : 'Não foi possível contar as herdadas.',
          ),
      },
    );
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
          ? 'Não há decisões herdadas para confirmar neste destino.'
          : `${affected} ${affected === 1 ? 'decisão herdada será confirmada' : 'decisões herdadas serão confirmadas'}.`}
      </p>
      {affected > 0 && codeFilterActive && (
        <p className="text-muted-foreground">
          Vale para todas as herdadas do destino, não só as que o filtro atual mostra.
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
        serverCompetence={serverCompetence}
        errorFallback="Não foi possível confirmar as herdadas."
        onConfirm={async ({ effectiveFrom, confirmRetroactive }) => {
          const result = await applyMutation.mutateAsync({
            confirm: true,
            effectiveFrom,
            confirmRetroactive,
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
        description={`Pré-preenche "${destination.name}" com a conta de demonstrativo que o plano de contas da origem já traz. Cada decisão entra como herdada, para você confirmar ou alterar. Decisões existentes não são tocadas.`}
        confirmLabel="Iniciar de-para"
        serverCompetence={serverCompetence}
        errorFallback="Não foi possível iniciar o de-para."
        onConfirm={async ({ effectiveFrom, confirmRetroactive }) => {
          const result = await inheritMutation.mutateAsync({ effectiveFrom, confirmRetroactive });
          if (result.state === 'sem_plano_de_contas') {
            toast.info(
              'O cliente não tem plano de contas sincronizado: não há o que herdar. Sincronize o plano de contas e tente de novo.',
            );
          } else if (result.state === 'destino_sem_heranca') {
            toast.info('Este destino não herda do plano de contas: ele começa sem decisão.');
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
