'use client';

/**
 * Modal "Trocar Lançamento Omie" (FRONT 9.13, Doc §14.3).
 *
 * Fluxo:
 *   - Card de contexto (Data · Descrição · Valor) da linha de origem.
 *   - Busca por valor/descrição com debounce 300ms; back já retorna
 *     candidatos no período expandido subtraindo IDs já vinculados em
 *     outras linhas (BACK 9.4).
 *   - Click na linha = seleção (radio implícito) → highlight.
 *   - Confirmar → `PATCH /file-entries/{id}` com `omie_lancamento_id`.
 *
 * Reset de estado ao fechar (pitfall §7 do briefing): `useEffect` no `open`
 * limpa search e selection. Não dá pra confiar em destructuring por
 * componente — o pai mantém o modal montado para evitar flash.
 */

import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useAvailableOmieEntries, usePatchFileEntry } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { FileEntryItem } from '@/lib/api/reconciliations';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

import { OmieStatusBadge } from './omie-status-badge';

interface TrocarLancamentoModalProps {
  sessionId: string;
  entry: FileEntryItem;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const SEARCH_DEBOUNCE_MS = 300;

export function TrocarLancamentoModal({
  sessionId,
  entry,
  open,
  onOpenChange,
}: TrocarLancamentoModalProps) {
  const [search, setSearch] = useState('');
  const debouncedSearch = useDebouncedValue(search, SEARCH_DEBOUNCE_MS);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  // Reset ao abrir/fechar.
  useEffect(() => {
    if (!open) {
      setSearch('');
      setSelectedId(null);
    }
  }, [open]);

  const candidatesQuery = useAvailableOmieEntries(sessionId, debouncedSearch.trim(), {
    enabled: open,
  });
  const candidates = candidatesQuery.data ?? [];
  const patchMutation = usePatchFileEntry(sessionId);

  async function handleConfirm() {
    if (selectedId === null) return;
    try {
      await patchMutation.mutateAsync({
        entryId: entry.id,
        payload: { omie_lancamento_id: selectedId },
      });
      toast.success('Lançamento Omie atualizado.');
      onOpenChange(false);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível trocar o lançamento.';
      toast.error(message);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Selecionar lançamento Omie correto</DialogTitle>
          <DialogDescription>
            Os candidatos abaixo já estão dentro do período da sessão e ainda não vinculados a outra
            movimentação.
          </DialogDescription>
        </DialogHeader>

        <section className="bg-muted/40 space-y-1 rounded-md border p-3 text-sm">
          <p className="text-muted-foreground text-xs uppercase tracking-wide">Linha selecionada</p>
          <p>
            <span className="font-medium">{formatBRDate(entry.transaction_date)}</span> ·{' '}
            <span>{entry.description}</span> ·{' '}
            <span className="font-medium tabular-nums">
              {formatBRL(entry.amount, { signed: true })}
            </span>
          </p>
        </section>

        <div className="space-y-2">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar lançamento por valor ou descrição"
            maxLength={200}
            aria-label="Buscar lançamento Omie"
          />
        </div>

        <div className="max-h-80 overflow-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-28">Data</TableHead>
                <TableHead>Descrição / Fornecedor</TableHead>
                <TableHead className="w-40">Categoria</TableHead>
                <TableHead className="w-32 text-right">Valor</TableHead>
                <TableHead className="w-28">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidatesQuery.isLoading && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground py-6 text-center text-sm">
                    Carregando candidatos…
                  </TableCell>
                </TableRow>
              )}
              {!candidatesQuery.isLoading && candidates.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground py-6 text-center text-sm">
                    Nenhum lançamento disponível.
                  </TableCell>
                </TableRow>
              )}
              {!candidatesQuery.isLoading &&
                candidates.map((item) => {
                  const isSelected = selectedId === item.omie_id;
                  return (
                    <TableRow
                      key={item.omie_id}
                      onClick={() => setSelectedId(item.omie_id)}
                      className={cn(
                        'cursor-pointer',
                        isSelected && 'bg-primary/10 hover:bg-primary/15',
                      )}
                      aria-selected={isSelected}
                    >
                      <TableCell className="text-sm">
                        {formatBRDate(item.transaction_date)}
                      </TableCell>
                      <TableCell className="text-sm">
                        <div className="flex flex-col">
                          <span>{item.description}</span>
                          {item.supplier !== null && item.supplier !== item.description && (
                            <span className="text-muted-foreground text-xs">{item.supplier}</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {item.category ?? '—'}
                      </TableCell>
                      <TableCell className="text-right text-sm tabular-nums">
                        {formatBRL(item.amount, { signed: true })}
                      </TableCell>
                      <TableCell>
                        <OmieStatusBadge status={item.status} />
                      </TableCell>
                    </TableRow>
                  );
                })}
            </TableBody>
          </Table>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button onClick={handleConfirm} disabled={selectedId === null || patchMutation.isPending}>
            {patchMutation.isPending ? 'Salvando…' : 'Confirmar'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
