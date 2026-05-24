'use client';

/**
 * Confirmação para ativar/desativar um tipo de anomalia — S15 FRONT 11.2.
 *
 * Comportamento (catálogo é histórico):
 *   - Desativar → backend grava `active=false`. Anomalias existentes
 *     continuam visíveis (FK `ondelete=RESTRICT`); processamento de novas
 *     sessões deixa de criar anomalias desse tipo.
 *   - Reativar → volta a entrar no pipeline.
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
import { useUpdateAnomalyType } from '@/hooks/use-anomaly-types';
import type { AnomalyType } from '@/lib/api/anomaly-types';
import { ApiError } from '@/lib/api/client';

interface AnomalyTypeToggleConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  anomalyType: AnomalyType | null;
}

export function AnomalyTypeToggleConfirm({
  open,
  onOpenChange,
  anomalyType,
}: AnomalyTypeToggleConfirmProps) {
  const updateMutation = useUpdateAnomalyType(anomalyType?.id ?? '');
  const isPending = updateMutation.isPending;
  const willDeactivate = anomalyType?.active === true;

  async function handleConfirm() {
    if (!anomalyType) return;
    try {
      await updateMutation.mutateAsync({ active: !anomalyType.active });
      toast.success(willDeactivate ? 'Tipo desativado.' : 'Tipo reativado.');
      onOpenChange(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.userMessage : 'Não foi possível alterar o status.';
      toast.error(msg);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{willDeactivate ? 'Desativar tipo' : 'Reativar tipo'}</DialogTitle>
          <DialogDescription>
            {willDeactivate ? (
              <>
                Desativar <span className="text-foreground font-medium">{anomalyType?.name}</span>?
                Anomalias existentes não são afetadas, mas o sistema deixará de criar novas
                anomalias desse tipo até reativar.
              </>
            ) : (
              <>
                Reativar <span className="text-foreground font-medium">{anomalyType?.name}</span>?
                Novas conciliações voltam a gerar anomalias desse tipo.
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
            Cancelar
          </Button>
          <Button
            type="button"
            variant={willDeactivate ? 'destructive' : 'default'}
            onClick={handleConfirm}
            disabled={isPending}
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {willDeactivate ? 'Desativar' : 'Reativar'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
