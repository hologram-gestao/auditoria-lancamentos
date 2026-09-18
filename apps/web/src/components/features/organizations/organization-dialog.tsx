'use client';

/**
 * Diálogo de criar/editar organização (86e36ecwa) — um só componente para os
 * dois modos: `organization === null` cria, senão renomeia.
 *
 * Só o NOME é editável aqui. Suspender/reativar tem diálogo próprio
 * (`OrganizationStatusConfirm`) porque a consequência é outra: mexer no
 * `active` derruba gente no request seguinte, e isso não pode acontecer como
 * efeito colateral de "salvar" um formulário de nome.
 *
 * Erros:
 *   - 409 `CONFLICT` → nome já existe (sem distinção de caixa) → erro inline no
 *     campo, sem toast redundante.
 *   - Demais → toast com o `userMessage` do backend.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
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
import { useCreateOrganization, useUpdateOrganization } from '@/hooks/use-organizations';
import { ApiError } from '@/lib/api/client';
import type { OrganizationItem } from '@/lib/api/organizations';
import { organizationSchema, type OrganizationFormValues } from '@/lib/validation/organizations';

interface OrganizationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = criar. */
  organization: OrganizationItem | null;
}

export function OrganizationDialog({ open, onOpenChange, organization }: OrganizationDialogProps) {
  const isEdit = organization !== null;
  const createMutation = useCreateOrganization();
  const updateMutation = useUpdateOrganization(organization?.id ?? '');

  const form = useForm<OrganizationFormValues>({
    resolver: zodResolver(organizationSchema),
    defaultValues: { name: '' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (open) {
      form.reset({ name: organization?.name ?? '' });
      createMutation.reset();
      updateMutation.reset();
    }
    // form/mutations são estáveis; rodar quando abrir ou o alvo mudar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, organization]);

  async function onSubmit(values: OrganizationFormValues) {
    try {
      if (isEdit) {
        await updateMutation.mutateAsync({ name: values.name });
        toast.success('Organização atualizada.');
      } else {
        await createMutation.mutateAsync({ name: values.name });
        toast.success('Organização criada.');
      }
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        form.setError('name', {
          type: 'server',
          message: 'Já existe uma organização com este nome.',
        });
        return;
      }
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar a organização.';
      toast.error(msg);
    }
  }

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Editar organização' : 'Nova organização'}</DialogTitle>
          <DialogDescription>
            A organização é o escritório dono dos clientes e das pessoas que os atendem. Os usuários
            e o catálogo de categorias nascem dentro dela.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome</FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      autoComplete="off"
                      disabled={isSubmitting}
                      placeholder="ex.: Hologram, Prospecta"
                      {...field}
                    />
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
                {isEdit ? 'Salvar' : 'Criar organização'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
