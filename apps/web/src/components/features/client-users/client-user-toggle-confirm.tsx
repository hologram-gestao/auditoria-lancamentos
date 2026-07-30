'use client';

/**
 * Confirmação de ativar/desativar usuário do cliente (Sprint 5 / R5).
 *
 * Desativar é **destrutivo de acesso**: o backend relê `active` a cada request,
 * então a pessoa perde o acesso no PRÓXIMO request — não na próxima expiração
 * de token. Por isso passa por `AlertDialog` (não fecha por clique fora, foco
 * inicial no Cancelar), e não por um diálogo comum.
 *
 * Reativar não é destrutivo, mas usa o mesmo componente com texto e variante
 * diferentes: um caminho só para a mesma decisão, sem duplicar markup.
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
import { useSetClientUserActive } from '@/hooks/use-client-users';
import { ApiError } from '@/lib/api/client';
import type { ClientUserResponse } from '@/lib/contracts';

interface ClientUserToggleConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  user: ClientUserResponse | null;
  /** `false` = desativar (destrutivo); `true` = reativar. */
  nextActive: boolean;
}

export function ClientUserToggleConfirm({
  open,
  onOpenChange,
  clientId,
  user,
  nextActive,
}: ClientUserToggleConfirmProps) {
  const mutation = useSetClientUserActive(clientId);
  const isPending = mutation.isPending;

  async function handleConfirm() {
    if (!user) return;
    try {
      await mutation.mutateAsync({ userId: user.id, active: nextActive });
      toast.success(nextActive ? 'Usuário reativado.' : 'Usuário desativado.');
      onOpenChange(false);
    } catch (err) {
      const fallback = nextActive
        ? 'Não foi possível reativar o usuário.'
        : 'Não foi possível desativar o usuário.';
      toast.error(err instanceof ApiError ? err.userMessage : fallback);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {nextActive ? 'Reativar usuário' : 'Desativar usuário'}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {nextActive ? (
              <>
                <span className="text-foreground font-medium">{user?.name}</span> volta a acessar
                este cliente na próxima tentativa de login.
              </>
            ) : (
              <>
                <span className="text-foreground font-medium">{user?.name}</span> perde o acesso a
                este cliente já na próxima requisição. Você pode reativá-lo depois.
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            variant={nextActive ? 'default' : 'destructive'}
            onClick={handleConfirm}
            disabled={isPending}
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {nextActive ? 'Reativar' : 'Desativar'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
