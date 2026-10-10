'use client';

/**
 * Confirmação de EXCLUIR um layout de exportação (86e3nuuub).
 *
 * Só o layout que nunca gerou arquivo contábil sai: cada arquivo gerado aponta
 * para a versão do layout que o produziu, e o download refaz o arquivo a partir
 * dela. A regra é do SERVIDOR (409 `LAYOUT_EM_USO`, com a contagem); a tela diz
 * a regra antes de confirmar e, quando o servidor recusa, mostra a mensagem dele
 * DENTRO do diálogo, no lugar da ação, porque tentar de novo não muda nada.
 *
 * `AlertDialog` (não fecha por clique fora, foco inicial no Cancelar) e o
 * `AlertDialogAction` da casa não fecha sozinho: o diálogo só fecha no sucesso.
 */

import { Loader2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { useDeleteExportLayout } from '@/hooks/use-export-layouts';
import { ApiError } from '@/lib/api/client';
import type { ExportLayoutItem } from '@/lib/contracts';

interface ExportLayoutDeleteConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  layout: ExportLayoutItem | null;
}

export function ExportLayoutDeleteConfirm({
  open,
  onOpenChange,
  layout,
}: ExportLayoutDeleteConfirmProps) {
  const mutation = useDeleteExportLayout();
  const isPending = mutation.isPending;
  const [refusal, setRefusal] = useState<{ message: string; inUse: boolean } | null>(null);

  function handleOpenChange(next: boolean) {
    if (isPending) return;
    if (!next) setRefusal(null);
    onOpenChange(next);
  }

  async function handleConfirm() {
    if (!layout) return;
    setRefusal(null);
    try {
      await mutation.mutateAsync(layout.id);
      toast.success('Layout excluído.');
      onOpenChange(false);
    } catch (err) {
      setRefusal({
        message: err instanceof ApiError ? err.userMessage : 'Não foi possível excluir o layout.',
        inUse: err instanceof ApiError && err.code === 'LAYOUT_EM_USO',
      });
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Excluir layout de exportação</AlertDialogTitle>
          <AlertDialogDescription>
            Deseja excluir <span className="text-foreground font-medium">{layout?.name}</span> e
            todas as versões dele? A exclusão é definitiva e só vale para layout que nunca gerou
            arquivo contábil.
          </AlertDialogDescription>
        </AlertDialogHeader>

        {refusal !== null && (
          <div
            role="alert"
            className="bg-destructive-muted text-destructive ring-destructive/30 rounded-lg p-3 text-sm ring-1 ring-inset"
          >
            {refusal.message}
          </div>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>
            {refusal?.inUse ? 'Fechar' : 'Cancelar'}
          </AlertDialogCancel>
          {!refusal?.inUse && (
            <AlertDialogAction variant="destructive" onClick={handleConfirm} disabled={isPending}>
              {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              Excluir
            </AlertDialogAction>
          )}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
