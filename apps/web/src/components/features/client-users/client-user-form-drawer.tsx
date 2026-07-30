'use client';

/**
 * Gaveta de criação/edição de usuário DO CLIENTE (Sprint 5 / R5).
 *
 * Uma gaveta só para os dois modos (design-system: "formulário em Drawer única,
 * create+edit"), com o shell `SheetHeader/SheetBody/SheetFooter` — header e
 * rodapé fixos, miolo rolando, **Cancelar à esquerda** e ação primária à
 * direita. Quem abre remonta por `key` para o estado nascer limpo.
 *
 * Decisões que valem comentário:
 *   - O select de papel oferece **apenas** `client_manager`/`client_operator` —
 *     é a mesma whitelist do backend (`ClientUserRole`), que devolve 422 para
 *     qualquer outro valor. Papel de sistema não é opção nem por engano.
 *   - **`client_id` não vai no body.** O servidor o fixa a partir do tenant da
 *     rota e o request é `extra="forbid"`: mandar seria 422. É também o que
 *     impede o gerente de criar usuário em outro tenant.
 *   - A senha inicial só existe na criação (não há reset por autoatendimento no
 *     MVP) e nunca é logada — nem em `console`, nem em toast, nem em telemetria.
 *   - 409 (e-mail em uso) vira erro INLINE no campo; qualquer outro erro do
 *     servidor vira toast destrutivo com o `userMessage` em PT-BR. O front nunca
 *     mostra `err.message` (que carrega detalhe interno).
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormDescription,
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
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useCreateClientUser, useUpdateClientUser } from '@/hooks/use-client-users';
import { ApiError } from '@/lib/api/client';
import type { ClientUserResponse } from '@/lib/contracts';
import {
  CLIENT_USER_MIN_PASSWORD_LENGTH,
  CLIENT_USER_ROLE_LABELS,
  createClientUserSchema,
  updateClientUserSchema,
  type ClientUserRoleFormValue,
} from '@/lib/validation/client-users';

interface ClientUserFormValues {
  name: string;
  email: string;
  role: ClientUserRoleFormValue;
  /** Só usado na criação; na edição fica vazio e não é enviado. */
  password: string;
}

interface ClientUserFormDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  /** `null` = criação; usuário preenchido = edição. */
  user: ClientUserResponse | null;
}

const ROLE_OPTIONS = Object.entries(CLIENT_USER_ROLE_LABELS) as [
  ClientUserRoleFormValue,
  string,
][];

export function ClientUserFormDrawer({
  open,
  onOpenChange,
  clientId,
  user,
}: ClientUserFormDrawerProps) {
  const isEdit = user !== null;

  // Na edição o campo de senha não é renderizado; `z.string()` mantém o tipo do
  // formulário único (um resolver por modo, um único shape de valores).
  const schema: z.ZodType<ClientUserFormValues> = isEdit
    ? updateClientUserSchema.extend({ password: z.string() })
    : createClientUserSchema;

  const createMutation = useCreateClientUser(clientId);
  const updateMutation = useUpdateClientUser(clientId, user?.id ?? '');

  const form = useForm<ClientUserFormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: user?.name ?? '',
      email: user?.email ?? '',
      role: readRole(user?.role),
      password: '',
    },
    mode: 'onSubmit',
  });

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  async function onSubmit(values: ClientUserFormValues) {
    try {
      if (isEdit) {
        await updateMutation.mutateAsync({
          name: values.name,
          email: values.email,
          role: values.role,
        });
        toast.success('Usuário atualizado.');
      } else {
        await createMutation.mutateAsync({
          name: values.name,
          email: values.email,
          role: values.role,
          password: values.password,
        });
        toast.success('Usuário criado com sucesso.');
      }
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        form.setError('email', { type: 'server', message: err.userMessage });
        return;
      }
      const fallback = isEdit
        ? 'Não foi possível atualizar o usuário.'
        : 'Não foi possível criar o usuário.';
      toast.error(err instanceof ApiError ? err.userMessage : fallback);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="p-0">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex h-full flex-col" noValidate>
            <SheetHeader>
              <SheetTitle>{isEdit ? 'Editar usuário' : 'Novo usuário'}</SheetTitle>
              <SheetDescription>
                {isEdit
                  ? 'Altere nome, e-mail ou papel. A senha não é redefinida por aqui.'
                  : 'O usuário será criado já ativo e enxergará apenas este cliente.'}
              </SheetDescription>
            </SheetHeader>

            <SheetBody className="space-y-4">
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
                      <Input
                        type="email"
                        autoComplete="off"
                        disabled={isSubmitting}
                        placeholder="pessoa@empresa.com.br"
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
                    <FormLabel>Papel</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={field.onChange}
                      disabled={isSubmitting}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Selecione o papel" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {ROLE_OPTIONS.map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      O gerente do cliente também administra os usuários deste cliente; o operador,
                      não.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {!isEdit && (
                <FormField
                  control={form.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Senha inicial</FormLabel>
                      {/* Filho DIRETO do `<FormControl>` — com uma `<div>` no
                          meio, o Slot do Radix daria o `id` do `FormItem` à div
                          e o rótulo apontaria para o nada. */}
                      <FormControl>
                        <PasswordInput
                          autoComplete="new-password"
                          disabled={isSubmitting}
                          {...field}
                        />
                      </FormControl>
                      <FormDescription>
                        Mínimo de {CLIENT_USER_MIN_PASSWORD_LENGTH} caracteres. Combine-a com a
                        pessoa por um canal seguro — não há redefinição por autoatendimento.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
            </SheetBody>

            {/* Cancelar à ESQUERDA (`justify-between` do SheetFooter). */}
            <SheetFooter>
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
                {isEdit ? 'Salvar alterações' : 'Criar usuário'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}

/**
 * O `role` vem do contrato como `string` ("lenient out"). Fora da whitelist,
 * o select cairia num estado sem opção correspondente — o default de operador
 * (o papel de menor privilégio) é a degradação segura.
 */
function readRole(role: string | undefined): ClientUserRoleFormValue {
  return role === 'client_manager' ? 'client_manager' : 'client_operator';
}
