'use client';

/**
 * Modal "Novo Usuário" — Doc §8.3.
 *
 * Erros tratados:
 *   - 409 CONFLICT (email duplicado) → mensagem inline no campo e-mail.
 *   - Demais erros → toast destrutivo com `userMessage` do backend.
 *
 * Submit é via react-hook-form + zod; o botão fica disabled enquanto a mutation
 * está in-flight (controlado por `useMutation`).
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Eye, EyeOff, Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
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
import { useCreateUser } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import { createUserSchema, type CreateUserFormValues } from '@/lib/validation/users';

interface CreateUserModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateUserModal({ open, onOpenChange }: CreateUserModalProps) {
  const [showPassword, setShowPassword] = useState(false);
  const createMutation = useCreateUser();

  const form = useForm<CreateUserFormValues>({
    resolver: zodResolver(createUserSchema),
    defaultValues: { name: '', email: '', password: '', role: 'manager' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (!open) {
      form.reset();
      setShowPassword(false);
      createMutation.reset();
    }
    // form/createMutation são estáveis; rodar só quando o modal abrir/fechar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function onSubmit(values: CreateUserFormValues) {
    try {
      await createMutation.mutateAsync(values);
      toast.success('Usuário criado com sucesso.');
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        form.setError('email', { type: 'server', message: err.userMessage });
        return;
      }
      const msg = err instanceof ApiError ? err.userMessage : 'Não foi possível criar o usuário.';
      toast.error(msg);
    }
  }

  const isSubmitting = createMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Novo Usuário</DialogTitle>
          <DialogDescription>
            O usuário será criado já ativo. A senha pode ser trocada em uma futura tela de
            redefinição (não disponível no MVP).
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
                    <Input autoComplete="name" autoFocus disabled={isSubmitting} {...field} />
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
                    <Input
                      type="email"
                      autoComplete="off"
                      disabled={isSubmitting}
                      placeholder="usuario@hologram.com.br"
                      {...field}
                    />
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
                    disabled={isSubmitting}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Selecione o perfil" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="manager">Gerente</SelectItem>
                      <SelectItem value="admin">Admin</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Senha inicial</FormLabel>
                  <FormControl>
                    <div className="relative">
                      <Input
                        type={showPassword ? 'text' : 'password'}
                        autoComplete="new-password"
                        disabled={isSubmitting}
                        className="pr-10"
                        {...field}
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword((v) => !v)}
                        aria-label={showPassword ? 'Ocultar senha' : 'Mostrar senha'}
                        aria-pressed={showPassword}
                        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring absolute inset-y-0 right-0 flex items-center rounded-md pr-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                      >
                        {showPassword ? (
                          <EyeOff className="h-4 w-4" aria-hidden="true" />
                        ) : (
                          <Eye className="h-4 w-4" aria-hidden="true" />
                        )}
                      </button>
                    </div>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

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
                Criar usuário
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
