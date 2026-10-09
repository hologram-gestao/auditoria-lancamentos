'use client';

/**
 * "Editar alvo" (86e3n70pn): só o NOME. O código é a chave da importação e do
 * snapshot da materialização e não muda (o `PATCH` nem aceita o campo); a situação
 * muda pela ação "Inativar"/"Reativar" da linha.
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
import { useUpdateMappingTarget } from '@/hooks/use-client-mapping';
import { ApiError } from '@/lib/api/client';
import type { MappingTarget } from '@/lib/contracts';
import {
  MAX_TARGET_NAME_CHARS,
  targetEditFormSchema,
  type TargetEditFormValues,
} from '@/lib/validation/mapping-catalog';

interface EditTargetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  destinationId: string;
  target: MappingTarget | null;
}

export function EditTargetDialog({
  open,
  onOpenChange,
  destinationId,
  target,
}: EditTargetDialogProps) {
  const mutation = useUpdateMappingTarget(destinationId);
  const form = useForm<TargetEditFormValues>({
    resolver: zodResolver(targetEditFormSchema),
    defaultValues: { name: '' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (open && target) {
      form.reset({ name: target.name });
      mutation.reset();
    }
    // form/mutation são estáveis; rodar quando abrir ou o alvo mudar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, target]);

  async function onSubmit(values: TargetEditFormValues) {
    if (!target) return;
    try {
      await mutation.mutateAsync({ targetId: target.id, patch: { name: values.name } });
      toast.success(`Alvo ${target.code} atualizado.`);
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível salvar o alvo.');
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Editar alvo {target?.code}</DialogTitle>
          <DialogDescription>
            O código não muda: é por ele que as decisões e as planilhas do de-para apontam o alvo.
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
                      maxLength={MAX_TARGET_NAME_CHARS}
                      disabled={mutation.isPending}
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
                disabled={mutation.isPending}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                )}
                Salvar
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
