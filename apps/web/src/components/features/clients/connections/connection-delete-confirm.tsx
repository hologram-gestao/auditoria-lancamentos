'use client';

/**
 * Confirmação de remoção de uma origem do cliente (Sprint 9 / R1 · R5).
 *
 * `AlertDialog` (ADR-006-FE), nunca um `<div fixed inset-0>` manual: não fecha
 * por clique fora e o foco inicial fica no Cancelar.
 *
 * A cópia diz a CONSEQUÊNCIA antes de o usuário confirmar, e ela é específica:
 * a remoção é **definitiva** (a linha some, não é remoção lógica) e, se esta
 * for a última origem ativa, as telas que dependem dela param de funcionar até
 * alguém reconectar. Reconectar depois é possível — inclusive com o mesmo
 * rótulo, que é justamente o motivo de a remoção ser definitiva.
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
import { useDeleteConnection } from '@/hooks/use-client-connections';
import { ApiError } from '@/lib/api/client';
import type { ClientConnection } from '@/lib/contracts';

interface ConnectionDeleteConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  connection: ClientConnection | null;
  /** Quantas origens ATIVAS o cliente tem hoje — decide o aviso do "vai parar". */
  activeCount: number;
}

export function ConnectionDeleteConfirm({
  open,
  onOpenChange,
  clientId,
  connection,
  activeCount,
}: ConnectionDeleteConfirmProps) {
  const mutation = useDeleteConnection(clientId);
  const isPending = mutation.isPending;

  // Só avisa quando a remoção realmente deixa o cliente sem origem ativa —
  // um aviso que aparece sempre deixa de ser lido.
  const losesLastActive = connection?.status === 'ativa' && activeCount <= 1;

  async function handleConfirm() {
    if (!connection) return;
    try {
      await mutation.mutateAsync(connection.id);
      toast.success('Origem removida.');
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível remover a origem.');
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remover origem</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="text-foreground font-medium">{connection?.label}</span> sai deste
            cliente em definitivo, junto com a credencial guardada. Para voltar, será preciso
            conectá-la de novo — o mesmo rótulo fica livre.
            {losesLastActive && (
              <>
                {' '}
                Como esta é a única origem ativa, o cliente fica{' '}
                <span className="text-foreground font-medium">sem origem conectada</span>: contas,
                conciliações e exportação param até alguém reconectar.
              </>
            )}
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
