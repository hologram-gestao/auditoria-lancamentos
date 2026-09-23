'use client';

/**
 * Modal "Editar Cliente" — Doc §9.3, revisto na Sprint 9 (R5).
 *
 * ⚠️ **A credencial saiu daqui.** Até a S8 este modal recriptografava as
 * credenciais Omie nas colunas de `clients`. Isso virou a SEGUNDA via de
 * escrita que o PRD manda fechar: a leitura passou a vir de
 * `client_connections`, e gravar na coluna antiga faria o sistema autenticar
 * com a credencial velha **em silêncio**. O backend agora responde **422** só
 * pela presença de `omieAppKey`/`omieAppSecret` no corpo do PATCH — então os
 * campos sumiram da tela, e no lugar deles há o caminho para a seção de
 * origens, na tela do cliente.
 *
 * Comportamento:
 *   - Nome pré-preenchido editável; status (Ativo/Inativo) também.
 *   - Credencial → tela do cliente → seção "Origens de dado".
 *   - Admin vê a seção "Gerentes com acesso" (86e390m4c —
 *     `client-managers-section.tsx`): quem tem acesso, quem é o responsável,
 *     adicionar, remover (com aviso nomeando quem perde o acesso) e tornar
 *     responsável (sem remover ninguém). As ações da carteira são IMEDIATAS,
 *     cada uma com a própria confirmação — o "Salvar" só grava os campos.
 *   - Manager (não-admin) não vê a seção; só nome/status/categoria.
 *   - O corpo do formulário rola dentro do modal (`ScrollRegion`), com header e
 *     rodapé fixos: em 390px a seção de gerentes empurraria o "Salvar" para fora
 *     da viewport — o defeito que o gate de a11y NÃO mede (CLAUDE.md §7).
 *
 * Erros tratados: toast destrutivo com o `userMessage` do backend.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useEffect } from 'react';
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
import { ScrollRegion } from '@/components/ui/scroll-region';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useClientCategories } from '@/hooks/use-client-categories';
import { useUpdateClient } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import type { Client, UpdateClientPayload } from '@/lib/api/clients';
import { hasPermission } from '@/lib/authz';
import { originFixPath } from '@/lib/origin-state';
import { updateClientSchema, type UpdateClientFormValues } from '@/lib/validation/clients';
import { useAuthStore } from '@/stores/auth';

import { ClientManagersSection } from './client-managers-section';

interface EditClientModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client: Client | null;
}

export function EditClientModal({ open, onOpenChange, client }: EditClientModalProps) {
  // Sprint 5 (R4): quem pode editar dados do cliente sai da MATRIZ
  // (`lib/authz`), não de um `role === 'admin'` local. Antes o papel vinha por
  // prop tipada `'admin' | 'manager'` — com os papéis de cliente no contrato,
  // isso deixaria de compilar e, pior, um `client_manager` cairia no ramo
  // "não-admin" por acidente em vez de por regra.
  const currentUser = useAuthStore((s) => s.user);
  const isAdmin = hasPermission(currentUser, 'edit_client');
  // R5: quem NÃO gere conexões não recebe nem o caminho para elas.
  const canManageConnections = hasPermission(currentUser, 'manage_client_connections');

  const updateMutation = useUpdateClient(client?.id ?? '');
  // Catálogo de categorias (86e34jd8m) — só busca com o modal aberto.
  const categoriesQuery = useClientCategories({ enabled: open });
  const categories = categoriesQuery.data ?? [];

  const form = useForm<UpdateClientFormValues>({
    resolver: zodResolver(updateClientSchema),
    defaultValues: {
      name: '',
      active: 'active',
      category_id: 'none',
    },
    mode: 'onSubmit',
  });

  // Sincroniza o form sempre que o modal abre (ou o cliente-alvo muda).
  useEffect(() => {
    if (open && client) {
      form.reset({
        name: client.name,
        active: client.active ? 'active' : 'inactive',
        category_id: client.category?.id ?? 'none',
      });
      updateMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, client]);

  async function onSubmit(values: UpdateClientFormValues) {
    if (!client) return;

    const updatePayload: UpdateClientPayload = {
      name: values.name,
      active: values.active === 'active',
      // Sempre enviado: 'none' vira `null` (limpa), uuid troca — tri-estado do backend.
      category_id: values.category_id && values.category_id !== 'none' ? values.category_id : null,
    };

    try {
      await updateMutation.mutateAsync(updatePayload);
      toast.success('Cliente atualizado.');
      onOpenChange(false);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar as alterações.';
      toast.error(msg);
    }
  }

  const isSubmitting = updateMutation.isPending;
  const inputsDisabled = isSubmitting;

  const canSubmit = !isSubmitting && (form.getValues('name') ?? '').trim().length > 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* `flex` vence o `grid` do componente-base via twMerge: header e rodapé
          fixos, o miolo rola (`ScrollRegion`) quando o modal passa da viewport. */}
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Editar Cliente</DialogTitle>
          <DialogDescription>
            Nome, situação e categoria. As credenciais da origem são alteradas na seção
            &quot;Origens de dado&quot;, na tela do cliente.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col gap-4"
            noValidate
          >
            <ScrollRegion label="Dados do cliente" className="-mx-1 min-h-0 flex-1 space-y-4 px-1">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Nome do cliente</FormLabel>
                    <FormControl>
                      <Input autoComplete="off" autoFocus disabled={inputsDisabled} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* R5: o caminho para a credencial, não a credencial. Link e não
                  botão: o destino é uma rota, e clicar fecha o modal de
                  propósito — a seção de origens é onde a ação vive. */}
              {client && canManageConnections && (
                <p className="text-muted-foreground text-sm">
                  Para trocar a credencial da origem, use{' '}
                  <Link
                    href={originFixPath(client.id)}
                    onClick={() => onOpenChange(false)}
                    className="text-foreground underline underline-offset-4"
                  >
                    Origens de dado
                  </Link>{' '}
                  na tela do cliente.
                </p>
              )}

              <FormField
                control={form.control}
                name="active"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Status</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={field.onChange}
                      disabled={inputsDisabled}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="active">Ativo</SelectItem>
                        <SelectItem value="inactive">Inativo</SelectItem>
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="category_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Categoria</FormLabel>
                    <Select
                      value={field.value ?? 'none'}
                      onValueChange={field.onChange}
                      disabled={inputsDisabled || categoriesQuery.isLoading}
                    >
                      <FormControl>
                        <SelectTrigger aria-label="Categoria do cliente">
                          <SelectValue placeholder="Sem categoria" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="none">Sem categoria</SelectItem>
                        {categories.map((c) => (
                          <SelectItem key={c.id} value={c.id}>
                            {c.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {isAdmin && client && (
                <ClientManagersSection client={client} disabled={inputsDisabled} />
              )}
            </ScrollRegion>

            <DialogFooter className="gap-2 sm:gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={!canSubmit}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Salvar
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
