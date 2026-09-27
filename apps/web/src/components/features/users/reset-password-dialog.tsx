'use client';

/**
 * A plataforma redefine a senha de QUALQUER usuário (86e3ewukz): suporte e
 * emergência. Só quem tem `reset_user_password` vê a ação que abre isto (a
 * célula é só da plataforma; o backend recusa o resto com 403).
 *
 * `AlertDialog`, e não `Dialog`: é ação sensível com efeito imediato sobre
 * outra pessoa — a senha antiga deixa de valer e TODOS os acessos abertos dela
 * são encerrados pelo servidor (`users.password_changed_at`). O corpo diz isso
 * antes de confirmar. Cancelar à esquerda, ação à direita, `disabled` +
 * spinner no envio (design-system).
 *
 * A senha é digitada pela plataforma (decisão 2 da task: como na criação de
 * usuário), com confirmação e a regra de tamanho do TIPO do alvo (8 staff, 10
 * usuário de cliente), as mesmas da criação. A senha vai UMA vez ao servidor,
 * em request; nunca volta em resposta, toast ou log.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
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
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { PasswordInput } from '@/components/ui/password-input';
import { useResetUserPassword } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import { makeResetPasswordSchema, type ResetPasswordFormValues } from '@/lib/validation/users';

/** O que o diálogo precisa saber do alvo — o suficiente para nomear e para a regra de tamanho. */
export interface ResetPasswordTarget {
  id: string;
  name: string;
  email: string;
  /** `client` = usuário de cliente (mínimo 10); o resto é staff (mínimo 8). */
  scope: 'platform' | 'system' | 'client';
}

/** Os mesmos mínimos de `apps/api/app/modules/users/schemas.py` (criação e redefinição). */
export const STAFF_MIN_PASSWORD_LENGTH = 8;
export const CLIENT_USER_MIN_PASSWORD_LENGTH = 10;

export function minPasswordLengthFor(scope: ResetPasswordTarget['scope']): number {
  return scope === 'client' ? CLIENT_USER_MIN_PASSWORD_LENGTH : STAFF_MIN_PASSWORD_LENGTH;
}

interface ResetPasswordDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  target: ResetPasswordTarget | null;
}

export function ResetPasswordDialog({ open, onOpenChange, target }: ResetPasswordDialogProps) {
  const mutation = useResetUserPassword(target?.id ?? '');
  const minLength = minPasswordLengthFor(target?.scope ?? 'system');
  const form = useForm<ResetPasswordFormValues>({
    resolver: zodResolver(makeResetPasswordSchema(minLength)),
    defaultValues: { password: '', confirm: '' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (open) {
      form.reset({ password: '', confirm: '' });
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, target?.id]);

  const isPending = mutation.isPending;

  function handleOpenChange(next: boolean) {
    if (isPending) return;
    onOpenChange(next);
  }

  async function onSubmit(values: ResetPasswordFormValues) {
    if (!target) return;
    try {
      await mutation.mutateAsync({ password: values.password });
      toast.success('Senha redefinida. Os acessos abertos dessa pessoa foram encerrados.');
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível redefinir a senha.',
      );
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Redefinir senha</AlertDialogTitle>
          <AlertDialogDescription>
            {target ? (
              <>
                <span className="text-foreground font-medium">{target.name}</span> ({target.email}).
                A pessoa será desconectada de todos os acessos e vai entrar com esta senha. Passe a
                senha por um canal seguro.
              </>
            ) : null}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="space-y-4"
            noValidate
            data-testid="reset-password-form"
          >
            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Senha nova</FormLabel>
                  {/* Filho DIRETO do `<FormControl>`: com uma `<div>` no meio, o Slot
                      do Radix daria o `id` do `FormItem` à div e o rótulo apontaria
                      para o nada (mesma armadilha do modal de criação). */}
                  <FormControl>
                    <PasswordInput autoComplete="new-password" disabled={isPending} {...field} />
                  </FormControl>
                  <FormDescription>
                    Pelo menos {minLength} caracteres
                    {target?.scope === 'client' ? ' (usuário de cliente).' : ' (staff).'}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="confirm"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Confirmar senha nova</FormLabel>
                  <FormControl>
                    <PasswordInput autoComplete="new-password" disabled={isPending} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <AlertDialogFooter className="gap-2 sm:justify-between">
              <AlertDialogCancel disabled={isPending}>Cancelar</AlertDialogCancel>
              <AlertDialogAction type="submit" variant="default" disabled={isPending}>
                {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Redefinir senha
              </AlertDialogAction>
            </AlertDialogFooter>
          </form>
        </Form>
      </AlertDialogContent>
    </AlertDialog>
  );
}
