'use client';

/**
 * Confirmação de exclusão de categoria (86e34jd8m).
 *
 * O backend recusa (409) categoria com clientes vinculados — a FK é RESTRICT.
 * A tabela já sabe a contagem, então aqui a exclusão nem é oferecida nesse
 * caso: o diálogo explica e o botão fica desabilitado. Se ainda assim vier
 * 409 (corrida: alguém vinculou um cliente enquanto o diálogo estava aberto),
 * o `userMessage` do servidor vai direto para o toast.
 */

import { Loader2 } from 'lucide-react';
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
import { useDeleteClientCategory } from '@/hooks/use-client-categories';
import { ApiError } from '@/lib/api/client';
import type { ClientCategoryItem } from '@/lib/api/client-categories';

interface ClientCategoryDeleteConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  category: ClientCategoryItem | null;
}

export function ClientCategoryDeleteConfirm({
  open,
  onOpenChange,
  category,
}: ClientCategoryDeleteConfirmProps) {
  const deleteMutation = useDeleteClientCategory();
  const isPending = deleteMutation.isPending;
  const inUse = (category?.clients_count ?? 0) > 0;

  async function handleConfirm() {
    if (!category) return;
    try {
      await deleteMutation.mutateAsync(category.id);
      toast.success('Categoria excluída.');
      onOpenChange(false);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível excluir a categoria.';
      toast.error(msg);
      if (err instanceof ApiError && err.code === 'CONFLICT') onOpenChange(false);
    }
  }

  const count = category?.clients_count ?? 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Excluir categoria</DialogTitle>
          <DialogDescription>
            {inUse ? (
              <>
                <span className="text-foreground font-medium">{category?.name}</span> está em uso
                por {count} cliente{count === 1 ? '' : 's'}. Mova{' '}
                {count === 1 ? 'esse cliente' : 'esses clientes'} para outra categoria antes de
                excluir.
              </>
            ) : (
              <>
                Excluir <span className="text-foreground font-medium">{category?.name}</span>? Esta
                ação é permanente. Nenhum cliente usa esta categoria hoje.
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            {inUse ? 'Fechar' : 'Cancelar'}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={handleConfirm}
            disabled={isPending || inUse}
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Excluir
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
