'use client';

/**
 * Confirmação de SUSPENDER ou REATIVAR uma organização (86e36ecwa).
 *
 * Diálogo separado do de nome de propósito: suspender não é editar um campo, é
 * tirar N pessoas do ar. O corpo diz exatamente o que acontece, com as duas
 * contagens da própria linha — a decisão é de quem lê, e ela precisa do número
 * antes de confirmar, não depois.
 *
 * O efeito é do SERVIDOR: `active=false` faz `get_current_user` recusar o staff
 * daquela organização no request seguinte e o login passa a ser negado com a
 * mensagem genérica. Nada é apagado; reativar desfaz.
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
import { useUpdateOrganization } from '@/hooks/use-organizations';
import { ApiError } from '@/lib/api/client';
import type { OrganizationItem } from '@/lib/api/organizations';

interface OrganizationStatusConfirmProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  organization: OrganizationItem | null;
}

function pluralize(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function OrganizationStatusConfirm({
  open,
  onOpenChange,
  organization,
}: OrganizationStatusConfirmProps) {
  const mutation = useUpdateOrganization(organization?.id ?? '');
  const isPending = mutation.isPending;
  const isSuspending = organization?.active === true;

  async function handleConfirm() {
    if (!organization) return;
    try {
      await mutation.mutateAsync({ active: !organization.active });
      toast.success(isSuspending ? 'Organização suspensa.' : 'Organização reativada.');
      onOpenChange(false);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.userMessage
          : 'Não foi possível alterar a situação da organização.';
      toast.error(msg);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isSuspending ? 'Suspender organização' : 'Reativar organização'}
          </DialogTitle>
          <DialogDescription>
            {isSuspending ? (
              <>
                Deseja suspender{' '}
                <span className="text-foreground font-medium">{organization?.name}</span>?{' '}
                {pluralize(organization?.users_count ?? 0, 'usuário perde', 'usuários perdem')}{' '}
                acesso na próxima requisição e a organização deixa de receber clientes novos. Os
                dados de {pluralize(organization?.clients_count ?? 0, 'cliente', 'clientes')}{' '}
                continuam salvos.
              </>
            ) : (
              <>
                Deseja reativar{' '}
                <span className="text-foreground font-medium">{organization?.name}</span>? O staff
                dela volta a entrar e a organização passa a receber clientes novos.
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
            variant={isSuspending ? 'destructive' : 'default'}
            onClick={handleConfirm}
            disabled={isPending}
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {isSuspending ? 'Suspender' : 'Reativar'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
