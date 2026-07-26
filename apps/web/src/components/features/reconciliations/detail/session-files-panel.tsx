'use client';

/**
 * Partes (arquivos) da conciliação — Sprint 4 / R5.
 *
 * Uma conciliação pode ser composta por N partes (fatura de 12 páginas
 * quebrada em 3 PDFs, por exemplo). Este painel responde a duas perguntas que
 * o resumo consolidado sozinho não responde: **quantas partes** e **qual delas
 * falhou**.
 *
 * A parte que falhou aparece com o CÓDIGO do erro (S2/R9 — nunca a linguagem
 * interna) e pode ser removida; o backend re-consolida o restante. Remover a
 * ÚNICA parte com lançamentos é recusado com 409 — nesse caso o caminho é
 * excluir a conciliação inteira, e a mensagem do backend já diz isso.
 */

import { AlertCircle, CheckCircle2, FileText, Loader2, Trash2 } from 'lucide-react';
import { useState } from 'react';
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
import { useDeleteSessionFile, useSessionFiles } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { SessionFile } from '@/lib/api/reconciliations';
import { cn } from '@/lib/utils';

interface SessionFilesPanelProps {
  sessionId: string;
  /** Enquanto processa, o backend recusa remoção — escondemos a ação. */
  isProcessing: boolean;
}

export function SessionFilesPanel({ sessionId, isProcessing }: SessionFilesPanelProps) {
  const filesQuery = useSessionFiles(sessionId);
  const deleteFile = useDeleteSessionFile(sessionId);
  const [pendingRemoval, setPendingRemoval] = useState<SessionFile | null>(null);

  const files = filesQuery.data?.files ?? [];

  async function handleRemove() {
    if (pendingRemoval === null) return;
    try {
      const result = await deleteFile.mutateAsync(pendingRemoval.file_id);
      setPendingRemoval(null);
      toast.success(
        result.reprocessing
          ? 'Arquivo removido. A conciliação está sendo refeita com as partes restantes.'
          : 'Arquivo removido.',
      );
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível remover este arquivo.',
      );
    }
  }

  if (filesQuery.isLoading) {
    return (
      <div role="status" className="bg-card space-y-2 rounded-lg border p-4" aria-label="Carregando arquivos">
        <div className="bg-muted h-4 w-40 animate-pulse rounded" />
        <div className="bg-muted h-4 w-full animate-pulse rounded" />
      </div>
    );
  }

  if (filesQuery.isError || files.length === 0) {
    // Sem partes (sessão legada da Sprint 3) o painel não acrescenta nada —
    // o número de arquivos já aparece nos totalizadores.
    return null;
  }

  return (
    <section aria-labelledby="files-heading" className="bg-card rounded-lg border p-4 shadow-sm">
      <h2 id="files-heading" className="mb-3 text-sm font-semibold">
        Arquivos desta conciliação ({files.length})
      </h2>

      <ul className="space-y-2">
        {files.map((file, index) => (
          <li
            key={file.file_id}
            className={cn(
              'flex items-center gap-3 rounded-md border p-2 text-sm',
              file.status === 'error' && 'border-destructive/40 bg-destructive/5',
            )}
          >
            {file.status === 'error' ? (
              <AlertCircle className="text-destructive h-4 w-4 shrink-0" aria-hidden="true" />
            ) : (
              <CheckCircle2 className="text-success h-4 w-4 shrink-0" aria-hidden="true" />
            )}
            <div className="min-w-0 flex-1">
              {/* Partes migradas da Sprint 3 não têm nome guardado — "Arquivo N"
                  é melhor que uma célula vazia sem explicação. */}
              <p className="truncate font-medium">{file.filename ?? `Arquivo ${index + 1}`}</p>
              <p className="text-muted-foreground text-xs">
                {file.status === 'error'
                  ? `Não foi possível extrair esta parte${file.error_code != null ? ` (cód. ${file.error_code})` : ''}.`
                  : `${file.entry_count} movimentaç${file.entry_count === 1 ? 'ão' : 'ões'}`}
              </p>
            </div>
            {!isProcessing && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setPendingRemoval(file)}
                disabled={deleteFile.isPending}
                className="text-destructive hover:text-destructive hover:bg-destructive/10"
                aria-label={`Remover ${file.filename ?? `arquivo ${index + 1}`}`}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </Button>
            )}
          </li>
        ))}
      </ul>

      <Dialog
        open={pendingRemoval !== null}
        onOpenChange={(open) => !open && setPendingRemoval(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remover este arquivo da conciliação?</DialogTitle>
            <DialogDescription>
              As movimentações que vieram desta parte saem junto e a conciliação é refeita com as
              partes restantes. Esta ação não pode ser desfeita pela interface.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setPendingRemoval(null)}
              disabled={deleteFile.isPending}
            >
              Voltar
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void handleRemove()}
              disabled={deleteFile.isPending}
              aria-live="polite"
            >
              {deleteFile.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <FileText className="h-4 w-4" aria-hidden="true" />
              )}
              {deleteFile.isPending ? 'Removendo…' : 'Remover arquivo'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
