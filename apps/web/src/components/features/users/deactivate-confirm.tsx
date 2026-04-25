'use client';

/**
 * Modal de confirmação para desativar usuário — Doc §8.5.
 *
 * Após confirmar:
 *   - Backend grava `active=false`; `get_current_user` (a cada request) bloqueia
 *     na próxima chamada — o usuário-alvo perde acesso imediatamente.
 *   - Lista é invalidada via TanStack Query.
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
import { useDeactivateUser } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import type { User } from '@/lib/api/users';

interface DeactivateConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: User | null;
}

export function DeactivateConfirm({ open, onOpenChange, user }: DeactivateConfirmProps) {
  const deactivateMutation = useDeactivateUser();
  const isPending = deactivateMutation.isPending;

  async function handleConfirm() {
    if (!user) return;
    try {
      await deactivateMutation.mutateAsync(user.id);
      toast.success('Usuário desativado.');
      onOpenChange(false);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível desativar o usuário.';
      toast.error(msg);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Desativar usuário</DialogTitle>
          <DialogDescription>
            Deseja desativar <span className="text-foreground font-medium">{user?.name}</span>? Ele
            perderá acesso imediatamente.
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
            Desativar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
