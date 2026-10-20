'use client';

/**
 * Confirmação de "Remover vínculo" de UMA conta (86e3tttaf).
 *
 * `AlertDialog`, e não ação direta: o vínculo sai da conta e a planilha de importação do
 * plano de contas da Omie (86e3qhhrr) deixa de levar o documento dela, então a Omie passa
 * a tratar a conta como "diversos". O corpo diz isso ANTES de confirmar. Vale para o
 * vínculo importado da relação e para o manual. Cancelar à esquerda, ação à direita,
 * `disabled` + spinner no envio; quem fecha é o sucesso (`preventDefault` no clique, o
 * `AlertDialogAction` do Radix fecharia sozinho). O foco volta a quem abriu pelo
 * `OpenerCapture` do `AlertDialogContent`.
 */

import { Loader2 } from 'lucide-react';
import type { MouseEvent } from 'react';
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
import { useRemoveAccountingPartyLink } from '@/hooks/use-client-accounting-chart';
import { ApiError } from '@/lib/api/client';
import type { AccountingPartyLink } from '@/lib/contracts';

interface AccountingPartyUnlinkDialogProps {
  clientId: string;
  /** A linha cujo vínculo sai; `null` = diálogo fechado. */
  link: AccountingPartyLink | null;
  onOpenChange: (open: boolean) => void;
}

export function AccountingPartyUnlinkDialog({
  clientId,
  link,
  onOpenChange,
}: AccountingPartyUnlinkDialogProps) {
  const mutation = useRemoveAccountingPartyLink(clientId);
  const isPending = mutation.isPending;
  const who = link?.partyType === 'fornecedor' ? 'fornecedor' : 'cliente';

  async function handleConfirm(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    if (link === null || isPending) return;
    try {
      await mutation.mutateAsync(link.accountId);
      toast.success(`Vínculo da conta ${link.code} removido.`);
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível remover o vínculo.',
      );
    }
  }

  return (
    <AlertDialog open={link !== null} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remover vínculo</AlertDialogTitle>
          <AlertDialogDescription>
            {link !== null ? (
              <>
                A conta <span className="text-foreground font-medium">{link.code}</span> volta a
                ficar sem {who} vinculado, e a planilha da Omie deixa de levar o CNPJ/CPF dela. Para
                vincular de novo, use &quot;Vincular conta&quot; ou importe a relação do Domínio.
              </>
            ) : null}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="gap-2 sm:justify-between">
          <AlertDialogCancel disabled={isPending}>Cancelar</AlertDialogCancel>
          <AlertDialogAction disabled={isPending} onClick={handleConfirm}>
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Remover vínculo
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
