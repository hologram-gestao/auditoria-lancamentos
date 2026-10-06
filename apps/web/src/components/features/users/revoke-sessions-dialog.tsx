'use client';

/**
 * Encerrar as sessões de uma pessoa SEM desativar a conta nem trocar a senha
 * (86e3anx4u, parte 1). Quem vê a ação que abre isto é quem GERE o usuário, pela
 * matriz (`manage_org_users` na lista de staff e na de administradores da
 * plataforma, `manage_client_users` na lista do cliente) — a mesma pergunta de
 * "desativar", e por isso nenhuma permissão nova.
 *
 * `AlertDialog`, e não `Dialog`: é ação com efeito imediato sobre outra pessoa —
 * TODOS os acessos abertos dela são encerrados pelo servidor (o mesmo carimbo da
 * redefinição de senha, `users.password_changed_at`, sem tocar o hash). Não há
 * formulário: não existe senha a digitar. O corpo diz o que acontece ANTES de
 * confirmar: a conta continua ativa e a pessoa entra de novo com a senha atual.
 * Cancelar à esquerda, ação à direita, `disabled` + spinner no envio
 * (design-system). O foco volta a quem abriu pelo `OpenerCapture` do
 * `AlertDialogContent`.
 *
 * Quem chama entrega a mutation da ROTA certa (`useRevokeUserSessions` ou
 * `useRevokeClientUserSessions`): o diálogo é um só, as rotas são duas.
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
import { ApiError } from '@/lib/api/client';

/** O que o diálogo precisa saber do alvo — o suficiente para nomear quem sai. */
export interface RevokeSessionsTarget {
  id: string;
  name: string;
  email: string;
}

export const REVOKE_SESSIONS_SUCCESS_TOAST =
  'Sessões encerradas. A pessoa precisará entrar de novo com a senha atual.';

interface RevokeSessionsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  target: RevokeSessionsTarget | null;
  /** A mutation da rota certa; recebe o `id` do alvo. */
  revoke: (userId: string) => Promise<unknown>;
  isPending: boolean;
}

export function RevokeSessionsDialog({
  open,
  onOpenChange,
  target,
  revoke,
  isPending,
}: RevokeSessionsDialogProps) {
  function handleOpenChange(next: boolean) {
    if (isPending) return;
    onOpenChange(next);
  }

  async function handleConfirm(event: MouseEvent<HTMLButtonElement>) {
    // O `AlertDialogAction` do Radix fecha ao clicar; aqui quem fecha é o sucesso.
    event.preventDefault();
    if (!target || isPending) return;
    try {
      await revoke(target.id);
      // Sem nome nem e-mail: o toast fica na tela depois de a linha sumir do foco.
      toast.success(REVOKE_SESSIONS_SUCCESS_TOAST);
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível encerrar as sessões.',
      );
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Encerrar sessões</AlertDialogTitle>
          <AlertDialogDescription>
            {target ? (
              <>
                Todos os acessos abertos de{' '}
                <span className="text-foreground font-medium">{target.name}</span> ({target.email})
                serão encerrados agora, em todos os dispositivos. A conta continua ativa e a pessoa
                entra de novo com a senha atual.
              </>
            ) : null}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter className="gap-2 sm:justify-between">
          <AlertDialogCancel disabled={isPending}>Cancelar</AlertDialogCancel>
          <AlertDialogAction variant="default" disabled={isPending} onClick={handleConfirm}>
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Encerrar sessões
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
