'use client';

/**
 * Transferir um staff (admin ou gerente) para OUTRA organização (86e3bvbfx).
 *
 * Só a plataforma vê esta ação: mover gente entre organizações é escrita
 * cross-org por definição, e o admin de uma organização não alcança a outra.
 * O backend recusa com 403 para qualquer outro papel — isto aqui é para a
 * ação não aparecer para quem o servidor negaria (§4.9).
 *
 * Diálogo SEPARADO do "Editar Usuário" de propósito: transferir não é editar
 * um campo. Tem pré-condição (a pessoa não pode ser responsável de cliente
 * aberto) e efeitos que o formulário de edição esconderia — a carteira em
 * clientes abertos e os favoritos fora da organização nova SOMEM. O corpo diz
 * isso ANTES de confirmar, como a confirmação de suspensão de organização.
 *
 * O 409 do responsável volta como erro INLINE, não toast: é a mensagem que diz
 * o que fazer antes (definir outro responsável), e ela precisa ficar na tela.
 *
 * As opções são só organizações ATIVAS, menos a atual: o backend responderia
 * 409 para as duas, e oferecer a opção seria mostrar ação que o servidor nega.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect } from 'react';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useTransferUser } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import type { User } from '@/lib/api/users';
import { USER_ROLE_LABELS } from '@/lib/authz';
import { transferUserSchema, type TransferUserFormValues } from '@/lib/validation/users';

interface TransferUserDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: User | null;
}

export function TransferUserDialog({ open, onOpenChange, user }: TransferUserDialogProps) {
  const mutation = useTransferUser(user?.id ?? '');
  const { organizations, isLoading, isError } = useOrganizationOptions({
    enabled: open,
    activeOnly: true,
  });
  // A organização ATUAL fica fora: o backend responde 409 para ela.
  const destinations = organizations.filter((o) => o.id !== user?.organization_id);

  const form = useForm<TransferUserFormValues>({
    resolver: zodResolver(transferUserSchema),
    defaultValues: { organization_id: '' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (open) {
      form.reset({ organization_id: '' });
      mutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, user]);

  async function onSubmit(values: TransferUserFormValues) {
    if (!user) return;
    try {
      const moved = await mutation.mutateAsync({ organization_id: values.organization_id });
      toast.success(`${moved.name} agora é da organização ${moved.organization_name ?? ''}.`);
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        // 409: responsável de cliente aberto, organização suspensa ou a mesma.
        // Todos dizem o que fazer, então ficam no campo, não num toast que some.
        form.setError('organization_id', { type: 'server', message: err.userMessage });
        return;
      }
      if (err instanceof ApiError && err.code === 'NOT_FOUND') {
        // 404 aqui é do ALVO (a pessoa deixou de ser staff entre abrir e
        // confirmar), não do destino — o seletor só lista organizações que
        // existem. Não fica no campo: fecha, e a lista recarrega sem ela.
        toast.error(err.userMessage);
        onOpenChange(false);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível transferir o usuário.',
      );
    }
  }

  const isSubmitting = mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Transferir de organização</DialogTitle>
          <DialogDescription>
            <span className="text-foreground font-medium">{user?.name}</span> (
            {user ? USER_ROLE_LABELS[user.role] : ''}) sai de{' '}
            <span className="text-foreground font-medium">{user?.organization_name}</span> e passa a
            operar só os clientes da organização escolhida. A carteira em clientes abertos e os
            favoritos fora dela são removidos; o papel não muda. O acesso muda na próxima
            requisição; o nome da organização no cabeçalho atualiza quando a pessoa entrar de novo.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="organization_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Organização de destino</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    disabled={isSubmitting || isLoading || isError}
                  >
                    <FormControl>
                      <SelectTrigger aria-label="Organização de destino">
                        <SelectValue
                          placeholder={isLoading ? 'Carregando...' : 'Escolha a organização'}
                        />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {destinations.map((o) => (
                        <SelectItem key={o.id} value={o.id}>
                          {organizationOptionLabel(o)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {isError ? <OrganizationLoadError /> : null}
                  {!isLoading && !isError && destinations.length === 0 ? (
                    <p className="text-muted-foreground text-xs">
                      Não há outra organização ativa para onde transferir.
                    </p>
                  ) : null}
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
              <Button type="submit" disabled={isSubmitting || destinations.length === 0}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Transferir
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
