'use client';

/**
 * Aba 2 — Divergências Omie (FRONT 9.15, Doc §14.4).
 *
 * Lançamentos do Omie sem correspondente no arquivo enviado. Não tem
 * filtros (volume é tipicamente menor), só paginação. Ações:
 *   - "Marcar para verificação" → PATCH `user_action='flag'`.
 *   - "Ignorar" → PATCH `user_action='ignore'`.
 *   - "Anotar" inline.
 *   - "Registrar anomalia" → modal compartilhado (FRONT 9.14).
 *
 * Não invalida `status` no PATCH (BACK 9.6 mantém `omie_sem_arquivo_count`
 * estático). O hook já cuida disso.
 */

import { ChevronLeft, ChevronRight, MoreHorizontal } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useOmieEntries, usePatchOmieEntry } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { OmieEntryItem } from '@/lib/api/reconciliations';
import { formatBRDate, formatBRL } from '@/lib/format';

import { OmieStatusBadge } from './omie-status-badge';
import { RegistrarAnomaliaModal } from './registrar-anomalia-modal';

interface OmieDivergencesTabProps {
  sessionId: string;
}

const PAGE_SIZE = 20;

export function OmieDivergencesTab({ sessionId }: OmieDivergencesTabProps) {
  const [page, setPage] = useState(1);
  const listQuery = useOmieEntries(sessionId, { page, pageSize: PAGE_SIZE });
  const patchMutation = usePatchOmieEntry(sessionId);

  const [noteEditingId, setNoteEditingId] = useState<string | null>(null);
  const [noteDraft, setNoteDraft] = useState('');
  const [anomalyFor, setAnomalyFor] = useState<OmieEntryItem | null>(null);

  async function applyPatch(
    entry: OmieEntryItem,
    payload: Parameters<typeof patchMutation.mutateAsync>[0]['payload'],
    successMessage: string,
  ) {
    try {
      await patchMutation.mutateAsync({ entryId: entry.id, payload });
      toast.success(successMessage);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível atualizar a divergência.';
      toast.error(message);
    }
  }

  function openNoteEditor(entry: OmieEntryItem) {
    setNoteEditingId(entry.id);
    setNoteDraft(entry.user_note ?? '');
  }

  async function saveNote(entry: OmieEntryItem) {
    const trimmed = noteDraft.trim();
    await applyPatch(entry, { user_note: trimmed || null }, 'Anotação salva.');
    setNoteEditingId(null);
    setNoteDraft('');
  }

  const items = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;
  const totalPages = pagination?.totalPages ?? 0;
  const total = pagination?.total ?? 0;
  const fromIndex = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const toIndex = Math.min(page * PAGE_SIZE, total);

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        Lançamentos no Omie sem correspondente no arquivo enviado.
      </p>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-28">Data Omie</TableHead>
              <TableHead className="w-56">Fornecedor</TableHead>
              <TableHead>Categoria</TableHead>
              <TableHead className="w-36 text-right">Valor</TableHead>
              <TableHead className="w-28">Status</TableHead>
              <TableHead>Observação</TableHead>
              <TableHead className="w-12" aria-label="Ações" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {listQuery.isLoading && (
              <>
                {Array.from({ length: 6 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell colSpan={7}>
                      <div className="bg-muted h-6 animate-pulse rounded" />
                    </TableCell>
                  </TableRow>
                ))}
              </>
            )}
            {!listQuery.isLoading && items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="text-muted-foreground py-10 text-center text-sm">
                  Nenhuma divergência Omie.
                </TableCell>
              </TableRow>
            )}
            {!listQuery.isLoading &&
              items.map((entry) => {
                const isEditingNote = noteEditingId === entry.id;
                return (
                  <RowFragment
                    key={entry.id}
                    entry={entry}
                    onFlag={() =>
                      applyPatch(entry, { user_action: 'flag' }, 'Marcado para verificação.')
                    }
                    onIgnore={() =>
                      applyPatch(entry, { user_action: 'ignore' }, 'Divergência ignorada.')
                    }
                    onAnnotate={() => openNoteEditor(entry)}
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

      {anomalyFor !== null && (
        <RegistrarAnomaliaModal
          sessionId={sessionId}
          source={{
            kind: 'omie_entry',
            id: anomalyFor.id,
            date: anomalyFor.transaction_date,
            description: anomalyFor.supplier ?? `Omie #${anomalyFor.omie_lancamento_id}`,
            amount: anomalyFor.amount,
          }}
          open={anomalyFor !== null}
          onOpenChange={(open) => {
            if (!open) setAnomalyFor(null);
          }}
        />
      )}
    </div>
  );
}

interface RowFragmentProps {
  entry: OmieEntryItem;
  onFlag: () => void;
  onIgnore: () => void;
  onAnnotate: () => void;
  onCreateAnomaly: () => void;
  isEditingNote: boolean;
  noteDraft: string;
  onNoteDraftChange: (v: string) => void;
  onSaveNote: () => void;
  onCancelNote: () => void;
}

function RowFragment({
  entry,
  onFlag,
  onIgnore,
  onAnnotate,
  onCreateAnomaly,
  isEditingNote,
  noteDraft,
  onNoteDraftChange,
  onSaveNote,
  onCancelNote,
}: RowFragmentProps) {
  return (
    <>
      <TableRow>
        <TableCell className="text-sm">{formatBRDate(entry.transaction_date)}</TableCell>
        <TableCell className="text-sm">{entry.supplier ?? '—'}</TableCell>
        <TableCell className="text-muted-foreground text-sm">{entry.category ?? '—'}</TableCell>
        <TableCell className="text-right text-sm tabular-nums">
          {entry.amount === null ? '—' : formatBRL(entry.amount, { signed: true })}
        </TableCell>
        <TableCell>
          <OmieStatusBadge status={entry.omie_status} />
        </TableCell>
        <TableCell className="text-muted-foreground text-sm">{entry.user_note ?? '—'}</TableCell>
        <TableCell>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Abrir ações">
                <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onFlag}>Marcar para verificação</DropdownMenuItem>
              <DropdownMenuItem onSelect={onIgnore}>Ignorar</DropdownMenuItem>
              <DropdownMenuItem onSelect={onAnnotate}>Anotar</DropdownMenuItem>
              <DropdownMenuItem onSelect={onCreateAnomaly}>Registrar anomalia</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </TableCell>
      </TableRow>
      {isEditingNote && (
        <TableRow>
          <TableCell colSpan={7} className="bg-muted/30">
            <div className="space-y-2">
              <Textarea
                value={noteDraft}
                onChange={(e) => onNoteDraftChange(e.target.value)}
                maxLength={2000}
                placeholder="Anotação sobre esta divergência"
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
