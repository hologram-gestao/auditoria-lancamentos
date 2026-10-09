'use client';

/**
 * "Adicionar alvos" (86e3n70pn): colar várias linhas `código;nome` e criar todas
 * de uma vez pelo LOTE do catálogo (`POST /mapping-destinations/{id}/targets`).
 *
 * O lote é ATÔMICO no servidor: um código que já existe no destino recusa tudo com
 * 409 `CONFLICT`, e a `userMessage` lista os códigos. Esse erro aparece NO CAMPO,
 * como o nome repetido da categoria de cliente, sem toast redundante: é a linha
 * colada que a pessoa precisa corrigir.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect } from 'react';
import { useForm, useWatch } from 'react-hook-form';
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
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Textarea } from '@/components/ui/textarea';
import { useCreateMappingTargets } from '@/hooks/use-client-mapping';
import { ApiError } from '@/lib/api/client';
import type { MappingDestination } from '@/lib/contracts';
import {
  parseTargetLines,
  targetLinesFormSchema,
  type TargetLinesFormValues,
} from '@/lib/validation/mapping-catalog';

interface AddTargetsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  destination: MappingDestination;
}

export function AddTargetsDialog({ open, onOpenChange, destination }: AddTargetsDialogProps) {
  const mutation = useCreateMappingTargets(destination.id);
  const form = useForm<TargetLinesFormValues>({
    resolver: zodResolver(targetLinesFormSchema),
    defaultValues: { lines: '' },
    mode: 'onSubmit',
  });
  const lines = useWatch({ control: form.control, name: 'lines' });
  const parsed = parseTargetLines(lines ?? '');

  useEffect(() => {
    if (open) {
      form.reset({ lines: '' });
      mutation.reset();
    }
    // form/mutation são estáveis; reabrir limpa o que ficou da vez anterior.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function onSubmit(values: TargetLinesFormValues) {
    const { targets } = parseTargetLines(values.lines);
    try {
      const created = await mutation.mutateAsync({ targets });
      toast.success(
        `${created.length} ${created.length === 1 ? 'alvo criado' : 'alvos criados'} em ${destination.name}.`,
      );
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        form.setError('lines', { type: 'server', message: err.userMessage });
        return;
      }
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível criar os alvos.');
    }
  }

  const count = parsed.targets.length;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Adicionar alvos</DialogTitle>
          <DialogDescription>
            Cole uma linha por alvo de &quot;{destination.name}&quot;, no formato código;nome.
            Copiar duas colunas de uma planilha também funciona.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="lines"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Alvos</FormLabel>
                  <FormControl>
                    <Textarea
                      autoFocus
                      rows={8}
                      spellCheck={false}
                      disabled={mutation.isPending}
                      placeholder={'3.01;Receita bruta\n3.02;Deduções da receita'}
                      className="font-mono text-xs"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    {count === 0
                      ? 'Nenhuma linha válida ainda.'
                      : `${count} ${count === 1 ? 'alvo pronto' : 'alvos prontos'} para criar.`}{' '}
                    O código não muda depois de criado.
                  </FormDescription>
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
                {count > 1 ? `Criar ${count} alvos` : 'Criar alvo'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
