'use client';

/**
 * Aba 1 — Movimentações (FRONT 9.12, Doc §14.2).
 *
 * Pivôs de design:
 *   - Filtros (situation, type, search) ficam em state local; cada um
 *     entra na query key da TanStack — paginação se reseta para 1 via
 *     `useEffect` quando filtros mudam.
 *   - `useDebouncedValue` 300ms no campo de busca evita disparar request
 *     a cada keystroke.
 *   - Lookup batched de supplier/category via `useOmieLancamentos` recebe
 *     SÓ os IDs presentes na página atual (linhas com `omie_lancamento_id`).
 *     Dict por ID renderizado nas colunas.
 *   - Ações por situação: `confirm`/`flag`/`ignore` mandam `user_action`.
 *     Mudança de situação (ignorar / restaurar) manda `situation`. Para
 *     trocar Omie, abre modal; o PATCH é feito por lá.
 *   - "Anotar" inline: textarea expandido abaixo da linha — state local
 *     `noteFor` segura o entry id editável.
 *   - "Registrar anomalia": abre modal com `file_entry_id` pré-preenchido.
 */

import { ChevronLeft, ChevronRight, MoreHorizontal } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
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
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import {
  useAllSessionAnomalies,
  useFileEntries,
  useOmieLancamentos,
  usePatchFileEntry,
} from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { AnomalyItem, FileEntryItem } from '@/lib/api/reconciliations';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

import { QualificationCell, isQualificationAnomaly } from './qualification-cell';
import { QualificationOverrideDialog } from './qualification-override-dialog';
import { RegistrarAnomaliaModal } from './registrar-anomalia-modal';
import { SituationBadge } from './situation-badge';
import { TrocarLancamentoModal } from './trocar-lancamento-modal';

interface MovementsTabProps {
  sessionId: string;
  /** FRONT 1.8: cartão → filtro de tipo vira Compras/Estornos. */
  isCard: boolean;
}

type SituationFilter = 'all' | 'conciliado' | 'sem_omie' | 'ignorado';
type TypeFilter = 'all' | 'credit' | 'debit';

const PAGE_SIZES = [10, 20, 50] as const;
const DEFAULT_PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;

export function MovementsTab({ sessionId, isCard }: MovementsTabProps) {
  const [situation, setSituation] = useState<SituationFilter>('all');
  const [type, setType] = useState<TypeFilter>('all');
  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, SEARCH_DEBOUNCE_MS);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZES)[number]>(DEFAULT_PAGE_SIZE);

  // Reset para página 1 sempre que filtros mudam (paginação ficaria
  // pendurada num resultado vazio se trocássemos só o filtro).
  useEffect(() => {
    setPage(1);
  }, [situation, type, debouncedSearch, pageSize]);

  const listQuery = useFileEntries(sessionId, {
    page,
    pageSize,
    situation,
    type,
    search: debouncedSearch.trim() || undefined,
  });

  // Coleta IDs Omie das linhas conciliadas da página atual.
  const omieIdsInPage = useMemo(() => {
    if (listQuery.data === undefined) return [];
    return listQuery.data.data
      .map((row) => row.omie_lancamento_id)
      .filter((id): id is number => id !== null);
  }, [listQuery.data]);

  const omieLookupQuery = useOmieLancamentos(sessionId, omieIdsInPage);
  const omieById = useMemo(() => {
    const map = new Map<
      number,
      { supplier: string | null; category: string | null; date: string }
    >();
    omieLookupQuery.data?.forEach((item) => {
      map.set(item.omie_id, {
        supplier: item.supplier,
        category: item.category,
        date: item.transaction_date,
      });
    });
    return map;
  }, [omieLookupQuery.data]);

  // Estado para modais e anotação inline.
  const [noteEditingId, setNoteEditingId] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState('');
  const [trocarFor, setTrocarFor] = useState<FileEntryItem | null>(null);
  const [anomalyFor, setAnomalyFor] = useState<FileEntryItem | null>(null);
  const [overrideFor, setOverrideFor] = useState<FileEntryItem | null>(null);
  const [onlySuspect, setOnlySuspect] = useState(false);

  // S19 — Lookup `file_entry_id` → anomalias de qualificação pendentes.
  // Usa hook que pagina internamente; key tem prefixo `['review', sid, 'anomalies']`
  // pra ser invalidada por `usePatchAnomaly` / `useCreateAnomaly`.
  const anomaliesQuery = useAllSessionAnomalies(sessionId);
  const qualificationByEntry = useMemo(() => {
    const map = new Map<string, AnomalyItem[]>();
    anomaliesQuery.data?.forEach((a) => {
      if (a.resolved) return;
      if (!isQualificationAnomaly(a)) return;
      const feId = a.related_file_entry?.id;
      if (feId === undefined || feId === null) return;
      const bucket = map.get(feId);
      if (bucket === undefined) {
        map.set(feId, [a]);
      } else {
        bucket.push(a);
      }
    });
    return map;
  }, [anomaliesQuery.data]);

  const patchMutation = usePatchFileEntry(sessionId);

  async function applyPatch(
    entry: FileEntryItem,
    payload: Parameters<typeof patchMutation.mutateAsync>[0]['payload'],
    successMessage: string,
  ) {
    try {
      await patchMutation.mutateAsync({ entryId: entry.id, payload });
      toast.success(successMessage);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível atualizar a movimentação.';
      toast.error(message);
    }
  }

  function openNoteEditor(entry: FileEntryItem) {
    setNoteEditingId(entry.id);
    setNoteDraft(entry.user_note ?? '');
  }

  async function saveNote(entry: FileEntryItem) {
    const trimmed = noteDraft.trim();
    await applyPatch(entry, { user_note: trimmed || null }, 'Anotação salva.');
    setNoteEditingId(null);
    setNoteDraft('');
  }

  const isLoading = listQuery.isLoading;
  // O Switch "Apenas qualificação suspeita" filtra client-side; pode esvaziar a
  // página atual mesmo com `total > 0` no back. Pediríamos um filtro server-side
  // pra ficar consistente, mas isso exige endpoint novo (fora do escopo S19).
  const items = useMemo(() => {
    const raw = listQuery.data?.data ?? [];
    return onlySuspect ? raw.filter((e) => qualificationByEntry.has(e.id)) : raw;
  }, [listQuery.data?.data, onlySuspect, qualificationByEntry]);
  const pagination = listQuery.data?.pagination;
  const totalPages = pagination?.totalPages ?? 0;
  const total = pagination?.total ?? 0;
  const fromIndex = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const toIndex = Math.min(page * pageSize, total);

  const hasAnyFilter =
    situation !== 'all' || type !== 'all' || debouncedSearch.trim() !== '' || onlySuspect;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label htmlFor="filter-situation" className="text-muted-foreground text-xs">
            Situação
          </label>
          <Select value={situation} onValueChange={(v) => setSituation(v as SituationFilter)}>
            <SelectTrigger id="filter-situation" className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas</SelectItem>
              <SelectItem value="conciliado">Conciliadas</SelectItem>
              <SelectItem value="sem_omie">Sem Omie</SelectItem>
              <SelectItem value="ignorado">Ignoradas</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-type" className="text-muted-foreground text-xs">
            Tipo
          </label>
          <Select value={type} onValueChange={(v) => setType(v as TypeFilter)}>
            <SelectTrigger id="filter-type" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todos</SelectItem>
              <SelectItem value="credit">{isCard ? 'Estornos' : 'Créditos'}</SelectItem>
              <SelectItem value="debit">{isCard ? 'Compras' : 'Débitos'}</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="min-w-[220px] flex-1 space-y-1">
          <label htmlFor="filter-search" className="text-muted-foreground text-xs">
            Buscar
          </label>
          <Input
            id="filter-search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Filtrar por descrição"
            maxLength={200}
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="filter-page-size" className="text-muted-foreground text-xs">
            Itens por página
          </label>
          <Select
            value={String(pageSize)}
            onValueChange={(v) => setPageSize(Number(v) as (typeof PAGE_SIZES)[number])}
          >
            <SelectTrigger id="filter-page-size" className="w-24">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center gap-2 pb-1">
          <Switch
            id="filter-only-suspect"
            checked={onlySuspect}
            onCheckedChange={setOnlySuspect}
            aria-label="Apenas qualificação suspeita"
          />
          <label htmlFor="filter-only-suspect" className="cursor-pointer text-sm">
            Apenas qualificação suspeita
          </label>
        </div>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">Data</TableHead>
              <TableHead>Descrição</TableHead>
              <TableHead className="w-36 text-right">Valor</TableHead>
              <TableHead className="w-48">Fornecedor Omie</TableHead>
              <TableHead className="w-48">Categoria Omie</TableHead>
              <TableHead className="w-32">Situação</TableHead>
              <TableHead className="w-20 text-center">Análise</TableHead>
              <TableHead className="w-12" aria-label="Ações" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading && <SkeletonRows pageSize={pageSize} />}

            {!isLoading && items.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-muted-foreground py-10 text-center text-sm">
                  {hasAnyFilter
                    ? 'Nenhuma movimentação encontrada com os filtros selecionados.'
                    : 'Nenhuma movimentação cadastrada.'}
                </TableCell>
              </TableRow>
            )}

            {!isLoading &&
              items.map((entry) => {
                const isEditingNote = noteEditingId === entry.id;
                const omieData =
                  entry.omie_lancamento_id !== null ? omieById.get(entry.omie_lancamento_id) : null;
                const amountNum = Number(entry.amount);
                const qualificationAnomalies = qualificationByEntry.get(entry.id) ?? [];
                // FRONT 1.8: linha conciliada com data divergente → tooltip
                // "Data no arquivo: X · Data no Omie: Y" (o lançamento Omie já
                // foi buscado no lookup batched desta página).
                const divergenceTitle =
                  entry.situation === 'conciliado_data_divergente' && omieData
                    ? `Data no arquivo: ${formatBRDate(entry.transaction_date)} · Data no Omie: ${formatBRDate(omieData.date)}`
                    : undefined;
                return (
                  <RowFragment
                    key={entry.id}
                    entry={entry}
                    amountNum={amountNum}
                    supplier={omieData?.supplier ?? null}
                    category={omieData?.category ?? null}
                    divergenceTitle={divergenceTitle}
                    qualificationAnomalies={qualificationAnomalies}
                    onOpenOverride={() => setOverrideFor(entry)}
                    onConfirm={() =>
                      applyPatch(entry, { user_action: 'confirm' }, 'Movimentação confirmada.')
                    }
                    onTrocar={() => setTrocarFor(entry)}
                    onAnotar={() => openNoteEditor(entry)}
                    onIgnorar={() =>
                      applyPatch(entry, { situation: 'ignorado' }, 'Movimentação ignorada.')
                    }
                    onFlag={() =>
                      applyPatch(entry, { user_action: 'flag' }, 'Marcada para verificação.')
                    }
                    onRestaurar={() => {
                      const next: 'conciliado' | 'sem_omie' =
                        entry.omie_lancamento_id !== null ? 'conciliado' : 'sem_omie';
                      void applyPatch(entry, { situation: next }, 'Movimentação restaurada.');
                    }}
                    onCreateAnomaly={() => setAnomalyFor(entry)}
                    isEditingNote={isEditingNote}
                    noteDraft={noteDraft}
                    onNoteDraftChange={setNoteDraft}
                    onSaveNote={() => saveNote(entry)}
                    onCancelNote={() => {
                      setNoteEditingId(null);
                      setNoteDraft('');
                    }}
                  />
                );
              })}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col items-center justify-between gap-2 sm:flex-row">
        <p className="text-muted-foreground text-sm">
          Mostrando {fromIndex}–{toIndex} de {total}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            aria-label="Página anterior"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          </Button>
          <span className="text-sm">
            {page} / {Math.max(1, totalPages)}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            aria-label="Próxima página"
          >
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>

      {trocarFor !== null && (
        <TrocarLancamentoModal
          sessionId={sessionId}
          entry={trocarFor}
          open={trocarFor !== null}
          onOpenChange={(open) => {
            if (!open) setTrocarFor(null);
          }}
        />
      )}

      {anomalyFor !== null && (
        <RegistrarAnomaliaModal
          sessionId={sessionId}
          source={{
            kind: 'file_entry',
            id: anomalyFor.id,
            date: anomalyFor.transaction_date,
            description: anomalyFor.description,
            amount: anomalyFor.amount,
          }}
          open={anomalyFor !== null}
          onOpenChange={(open) => {
            if (!open) setAnomalyFor(null);
          }}
        />
      )}

      {overrideFor !== null && (
        <QualificationOverrideDialog
          sessionId={sessionId}
          anomalies={qualificationByEntry.get(overrideFor.id) ?? []}
          entry={{
            transaction_date: overrideFor.transaction_date,
            description: overrideFor.description,
            amount: overrideFor.amount,
          }}
          open={overrideFor !== null}
          onOpenChange={(open) => {
            if (!open) setOverrideFor(null);
          }}
        />
      )}
    </div>
  );
}

interface RowFragmentProps {
  entry: FileEntryItem;
  amountNum: number;
  supplier: string | null;
  category: string | null;
  /** Tooltip da linha divergente (FRONT 1.8): data arquivo · data Omie. */
  divergenceTitle?: string;
  qualificationAnomalies: AnomalyItem[];
  onOpenOverride: () => void;
  onConfirm: () => void;
  onTrocar: () => void;
  onAnotar: () => void;
  onIgnorar: () => void;
  onFlag: () => void;
  onRestaurar: () => void;
  onCreateAnomaly: () => void;
  isEditingNote: boolean;
  noteDraft: string;
  onNoteDraftChange: (v: string) => void;
  onSaveNote: () => void;
  onCancelNote: () => void;
}

function RowFragment({
  entry,
  amountNum,
  supplier,
  category,
  divergenceTitle,
  qualificationAnomalies,
  onOpenOverride,
  onConfirm,
  onTrocar,
  onAnotar,
  onIgnorar,
  onFlag,
  onRestaurar,
  onCreateAnomaly,
  isEditingNote,
  noteDraft,
  onNoteDraftChange,
  onSaveNote,
  onCancelNote,
}: RowFragmentProps) {
  const amountClass = cn(
    'tabular-nums',
    amountNum > 0 && 'text-emerald-700 dark:text-emerald-300',
    amountNum < 0 && 'text-red-700 dark:text-red-300',
  );
  return (
    <>
      <TableRow>
        <TableCell className="text-sm">{formatBRDate(entry.transaction_date)}</TableCell>
        <TableCell className="text-sm">
          <div className="flex flex-col">
            <span>{entry.description}</span>
            {entry.user_note !== null && entry.user_note.trim() !== '' && !isEditingNote && (
              <span className="text-muted-foreground mt-0.5 text-xs italic">
                Anotação: {entry.user_note}
              </span>
            )}
          </div>
        </TableCell>
        <TableCell className={cn('text-right text-sm', amountClass)}>
          {formatBRL(entry.amount, { signed: true })}
        </TableCell>
        <TableCell className="text-muted-foreground text-sm">
          {entry.omie_lancamento_id === null ? '—' : (supplier ?? '—')}
        </TableCell>
        <TableCell className="text-muted-foreground text-sm">
          {entry.omie_lancamento_id === null ? '—' : (category ?? '—')}
        </TableCell>
        <TableCell>
          <SituationBadge situation={entry.situation} title={divergenceTitle} />
        </TableCell>
        <TableCell className="text-center">
          <QualificationCell anomalies={qualificationAnomalies} onOpenOverride={onOpenOverride} />
        </TableCell>
        <TableCell>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Abrir ações">
                <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {entry.situation === 'conciliado' && (
                <>
                  <DropdownMenuItem onSelect={onConfirm}>Confirmar</DropdownMenuItem>
                  <DropdownMenuItem onSelect={onTrocar}>Trocar lançamento</DropdownMenuItem>
                  <DropdownMenuItem onSelect={onAnotar}>Anotar</DropdownMenuItem>
                  <DropdownMenuItem onSelect={onIgnorar}>Ignorar</DropdownMenuItem>
                </>
              )}
              {entry.situation === 'sem_omie' && (
                <>
                  <DropdownMenuItem onSelect={onFlag}>Marcar para verificação</DropdownMenuItem>
                  <DropdownMenuItem onSelect={onAnotar}>Anotar</DropdownMenuItem>
                  <DropdownMenuItem onSelect={onIgnorar}>Ignorar</DropdownMenuItem>
                </>
              )}
              {entry.situation === 'ignorado' && (
                <DropdownMenuItem onSelect={onRestaurar}>Restaurar</DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={onCreateAnomaly}>Registrar anomalia</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </TableCell>
      </TableRow>
      {isEditingNote && (
        <TableRow>
          <TableCell colSpan={8} className="bg-muted/30">
            <div className="space-y-2">
              <Textarea
                value={noteDraft}
                onChange={(e) => onNoteDraftChange(e.target.value)}
                maxLength={2000}
                placeholder="Anotação sobre esta movimentação"
                rows={3}
              />
              <div className="flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={onCancelNote}>
                  Cancelar
                </Button>
                <Button size="sm" onClick={onSaveNote}>
                  Salvar anotação
                </Button>
              </div>
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function SkeletonRows({ pageSize }: { pageSize: number }) {
  return (
    <>
      {Array.from({ length: Math.min(pageSize, 8) }).map((_, i) => (
        <TableRow key={i}>
          <TableCell colSpan={8}>
            <div className="bg-muted h-6 animate-pulse rounded" />
          </TableCell>
        </TableRow>
      ))}
    </>
  );
}
