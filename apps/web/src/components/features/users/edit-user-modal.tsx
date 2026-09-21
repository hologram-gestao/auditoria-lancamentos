'use client';

/**
 * Modal "Editar Usuário" — Doc §8.4.
 *
 * Regras:
 *   - Sem campo de senha (não está no escopo do MVP).
 *   - Admin não pode rebaixar o próprio perfil para Gerente — select desabilitado
 *     quando `target.id === currentUserId` (defesa em profundidade: backend
 *     também bloqueia, retornando 403).
 *   - Email duplicado → erro inline; demais erros → toast.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { useForm } from 'react-hook-form';
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
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useUpdateUser } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import type { User } from '@/lib/api/users';
import { USER_ROLE_LABELS } from '@/lib/authz';
import {
  SYSTEM_USER_ROLES,
  updateUserSchema,
  type UpdateUserFormValues,
} from '@/lib/validation/users';

interface EditUserModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: User | null;
  currentUserId: string;
  /**
   * Só a PLATAFORMA passa isto (86e3bvbfx): mover gente entre organizações é
   * escrita cross-org, e o servidor recusa para qualquer outro papel. Sem a
   * prop, a seção nem é montada — ação que o servidor nega não aparece (§4.9).
   * Quem abre o diálogo de transferência é a PÁGINA, depois de fechar este:
   * dois diálogos empilhados do Radix marcam o fundo com `aria-hidden` e o
   * segundo fica inacessível por teclado.
   */
  onTransfer?: (user: User) => void;
}

export function EditUserModal({
  open,
  onOpenChange,
  user,
  currentUserId,
  onTransfer,
}: EditUserModalProps) {
  const updateMutation = useUpdateUser(user?.id ?? '');

  const form = useForm<UpdateUserFormValues>({
    resolver: zodResolver(updateUserSchema),
    defaultValues: { name: '', email: '', role: 'manager' },
    mode: 'onSubmit',
  });

  // Sincroniza o form quando o usuário-alvo muda ou o modal abre.
  useEffect(() => {
    if (open && user) {
      form.reset({ name: user.name, email: user.email, role: user.role });
      updateMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, user]);

  async function onSubmit(values: UpdateUserFormValues) {
    if (!user) return;
    try {
      await updateMutation.mutateAsync(values);
      toast.success('Usuário atualizado.');
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        form.setError('email', { type: 'server', message: err.userMessage });
        return;
      }
      if (err instanceof ApiError && err.code === 'FORBIDDEN') {
        // Tentou rebaixar a si mesmo — mensagem específica do backend
        form.setError('role', { type: 'server', message: err.userMessage });
        return;
      }
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar as alterações.';
      toast.error(msg);
    }
  }

  const isSubmitting = updateMutation.isPending;
  const isSelf = user?.id === currentUserId;
  const roleSelectDisabled = isSubmitting || (isSelf && user?.role === 'admin');
  // Transferência pendente (86e3bvbfx): o alvo fica guardado até este diálogo
  // TERMINAR de fechar. Abrir o outro no mesmo tick deixaria o FocusScope
  // daqui devolver o foco ao botão da tabela, por baixo do modal novo, quando
  // o Radix desmontasse este ~200ms depois.
  const pendingTransfer = useRef<User | null>(null);
  const isDirty = form.formState.isDirty;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-md"
        onCloseAutoFocus={(event) => {
          const target = pendingTransfer.current;
          if (!target || !onTransfer) return;
          // O foco não volta ao gatilho: quem o recebe é o diálogo de
          // transferência, que abre agora, com este já fora da árvore.
          event.preventDefault();
          pendingTransfer.current = null;
          onTransfer(target);
        }}
      >
        <DialogHeader>
          <DialogTitle>Editar Usuário</DialogTitle>
          <DialogDescription>
            Alterações refletem na próxima requisição autenticada do usuário.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome completo</FormLabel>
                  <FormControl>
                    <Input autoComplete="name" disabled={isSubmitting} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>E-mail</FormLabel>
                  <FormControl>
                    <Input type="email" autoComplete="off" disabled={isSubmitting} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="role"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Perfil</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    disabled={roleSelectDisabled}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Selecione o perfil" />
                      </SelectTrigger>
                    </FormControl>
                    {/* Mesmas opções e mesmos rótulos do "Novo Usuário"
                        (86e36ed1d): a whitelist vem do CONTRATO e o rótulo de
                        `USER_ROLE_LABELS`. Digitados à mão, os dois diálogos da
                        MESMA tela diziam "Admin" aqui e "Administrador" lá. */}
                    <SelectContent>
                      {SYSTEM_USER_ROLES.map((role) => (
                        <SelectItem key={role} value={role}>
                          {USER_ROLE_LABELS[role]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {isSelf && user?.role === 'admin' ? (
                    <p className="text-muted-foreground text-xs">
                      Você não pode rebaixar seu próprio perfil de administrador.
                    </p>
                  ) : null}
                  <FormMessage />
                </FormItem>
              )}
            />

            {onTransfer && user ? (
              <>
                <div className="border-border flex flex-col gap-2 rounded-md border px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-muted-foreground text-xs">
                    Organização atual:{' '}
                    <span className="text-foreground font-medium">{user.organization_name}</span>
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={isSubmitting || isDirty}
                    onClick={() => {
                      pendingTransfer.current = user;
                      onOpenChange(false);
                    }}
                  >
                    Transferir de organização
                  </Button>
                </div>
                {isDirty ? (
                  <p className="text-muted-foreground -mt-2 text-xs">
                    Salve ou descarte as alterações antes de transferir.
                  </p>
                ) : null}
              </>
            ) : null}

            <DialogFooter className="gap-2 sm:gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Salvar alterações
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
