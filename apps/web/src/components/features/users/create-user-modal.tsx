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
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import {
  OrganizationLoadError,
  organizationOptionLabel,
  useOrganizationOptions,
} from '@/components/features/organizations/organization-select';
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
import { PasswordInput } from '@/components/ui/password-input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useCreateUser } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import { isPlatformScoped, USER_ROLE_LABELS } from '@/lib/authz';
import {
  makeCreateUserSchema,
  SYSTEM_USER_ROLES,
  type CreateUserFormValues,
} from '@/lib/validation/users';
import { useAuthStore } from '@/stores/auth';

interface CreateUserModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateUserModal({ open, onOpenChange }: CreateUserModalProps) {
  const createMutation = useCreateUser();

  // Onde o usuário NASCE (86e36ed1d): a plataforma escolhe (e é obrigada a);
  // o admin de organização não vê o campo — o backend usa a organização da
  // LINHA dele e recusa payload divergente com 403.
  const isPlatform = isPlatformScoped(useAuthStore((s) => s.user));
  const {
    organizations,
    isLoading: organizationsLoading,
    isError: organizationsError,
  } = useOrganizationOptions({
    enabled: isPlatform && open,
    activeOnly: true,
  });
  const schema = useMemo(
    () => makeCreateUserSchema({ requireOrganization: isPlatform }),
    [isPlatform],
  );

  const form = useForm<CreateUserFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', email: '', password: '', role: 'manager', organization_id: '' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (!open) {
      form.reset();
      createMutation.reset();
    }
    // form/createMutation são estáveis; rodar só quando o modal abrir/fechar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function onSubmit(values: CreateUserFormValues) {
    try {
      const { organization_id: organizationId, ...rest } = values;
      await createMutation.mutateAsync({
        ...rest,
        // Só a plataforma manda o campo: o admin omitindo é o que faz o
        // backend usar a organização da própria linha.
        ...(organizationId ? { organization_id: organizationId } : {}),
      });
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

            {/* Só a plataforma escolhe onde o usuário nasce (86e36ed1d). */}
            {isPlatform && (
              <FormField
                control={form.control}
                name="organization_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Organização</FormLabel>
                    <Select
                      value={field.value ?? ''}
                      onValueChange={field.onChange}
                      disabled={isSubmitting || organizationsLoading}
                    >
                      <FormControl>
                        <SelectTrigger aria-label="Organização do usuário">
                          <SelectValue placeholder="Selecione a organização" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {organizations.map((o) => (
                          <SelectItem key={o.id} value={o.id}>
                            {organizationOptionLabel(o)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {organizationsError && <OrganizationLoadError />}
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

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
                    {/* Opções da whitelist do CONTRATO (86e36ed1d), não dois
                        `<SelectItem>` digitados: papel novo aceito pela API
                        aparece aqui sozinho, e um que saia da whitelist some. */}
                    <SelectContent>
                      {SYSTEM_USER_ROLES.map((role) => (
                        <SelectItem key={role} value={role}>
                          {USER_ROLE_LABELS[role]}
                        </SelectItem>
                      ))}
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
                  {/* Filho DIRETO do `<FormControl>`: com uma `<div>` no meio, o
                      Slot do Radix daria o `id` do `FormItem` à div e o rótulo
                      "Senha inicial" apontaria para o nada. */}
                  <FormControl>
                    <PasswordInput autoComplete="new-password" disabled={isSubmitting} {...field} />
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
