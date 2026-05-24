'use client';

/**
 * Confirmação de exclusão de tipo de anomalia — S15 FRONT 11.2.
 *
 * O backend só permite DELETE se nenhum anomalia referencia o tipo.
 * Caso contrário devolve 409 com `userMessage` orientando a DESATIVAR
 * (PATCH active=false). Aqui propagamos esse `userMessage` direto via toast
 * — é exatamente o texto que o usuário precisa ler.
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
import { useDeleteAnomalyType } from '@/hooks/use-anomaly-types';
import type { AnomalyType } from '@/lib/api/anomaly-types';
import { ApiError } from '@/lib/api/client';

interface AnomalyTypeDeleteConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  anomalyType: AnomalyType | null;
}

export function AnomalyTypeDeleteConfirm({
  open,
  onOpenChange,
  anomalyType,
}: AnomalyTypeDeleteConfirmProps) {
  const deleteMutation = useDeleteAnomalyType();
  const isPending = deleteMutation.isPending;

  async function handleConfirm() {
    if (!anomalyType) return;
    try {
      await deleteMutation.mutateAsync(anomalyType.id);
      toast.success('Tipo excluído.');
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        toast.error('Este tipo está em uso. Desative em vez de excluir.');
        onOpenChange(false);
        return;
      }
      const msg = err instanceof ApiError ? err.userMessage : 'Não foi possível excluir o tipo.';
      toast.error(msg);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Excluir tipo de anomalia</DialogTitle>
          <DialogDescription>
            Excluir <span className="text-foreground font-medium">{anomalyType?.name}</span>? Esta
            ação é permanente. Se o tipo já tiver sido usado em alguma anomalia, a exclusão será
            bloqueada — desative em vez de excluir.
          </DialogDescription>
        </DialogHeader>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            Cancelar
          </Button>
          <Button type="button" variant="destructive" onClick={handleConfirm} disabled={isPending}>
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Excluir
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
