'use client';

/**
 * Confirmação de remoção de entrada do glossário (Sprint 6 / R2).
 *
 * Remover é destrutivo do ponto de vista de quem usa: a entrada some da
 * listagem e **deixa de ser considerada** na próxima análise de classificação
 * do cliente (a remoção incrementa a versão do glossário no servidor). Por isso
 * passa por `AlertDialog` (ADR-006-FE) — não fecha por clique fora, foco
 * inicial no Cancelar —, e nunca por um `<div fixed inset-0>` manual.
 *
 * A cópia diz o EFEITO, não o mecanismo: "deixa de valer na próxima análise" é
 * o que a pessoa precisa decidir; "soft delete com `deleted_at`" é detalhe do
 * servidor e não ajuda ninguém a decidir.
 */

import { Loader2 } from 'lucide-react';
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
import { useDeleteGlossaryEntry } from '@/hooks/use-glossary';
import { ApiError } from '@/lib/api/client';
import type { GlossaryEntry } from '@/lib/contracts';
import { GLOSSARY_KIND_LABELS, type GlossaryKindFormValue } from '@/lib/validation/glossary';

interface GlossaryDeleteConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  entry: GlossaryEntry | null;
}

export function GlossaryDeleteConfirm({
  open,
  onOpenChange,
  clientId,
  entry,
}: GlossaryDeleteConfirmProps) {
  const mutation = useDeleteGlossaryEntry(clientId);
  const isPending = mutation.isPending;

  const kindLabel = entry
    ? (GLOSSARY_KIND_LABELS[entry.kind as GlossaryKindFormValue] ?? entry.kind)
    : '';

  async function handleConfirm() {
    if (!entry) return;
    try {
      await mutation.mutateAsync({ entryId: entry.id });
      toast.success('Entrada removida do glossário.');
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível remover a entrada.',
      );
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remover entrada do glossário</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="text-foreground font-medium">{entry?.name}</span> ({kindLabel}) sai da
            lista e deixa de ser considerada na próxima análise de classificação deste cliente. Para
            voltar a valer, será preciso cadastrá-la de novo.
          </AlertDialogDescription>
        </AlertDialogHeader>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancelar</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={handleConfirm} disabled={isPending}>
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Remover
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
