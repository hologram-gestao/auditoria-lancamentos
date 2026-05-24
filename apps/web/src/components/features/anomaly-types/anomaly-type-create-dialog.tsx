'use client';

/**
 * Modal "Novo tipo de anomalia" — S15 FRONT 11.2 (Fase 2).
 *
 * Por padrão a feature está atrás de `NEXT_PUBLIC_ANOMALY_TYPE_CREATE_ENABLED`
 * (default false). O botão que abre esse modal só fica habilitado quando a
 * flag é `true`; o componente em si funciona em qualquer cenário.
 *
 * Tratamento de erros:
 *   - 409 CONFLICT em POST → `code` já existe → erro inline no campo `code`.
 *   - Demais erros → toast destrutivo.
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useCreateAnomalyType } from '@/hooks/use-anomaly-types';
import { ApiError } from '@/lib/api/client';
import {
  createAnomalyTypeSchema,
  type CreateAnomalyTypeFormValues,
} from '@/lib/validation/anomaly-types';

interface AnomalyTypeCreateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AnomalyTypeCreateDialog({ open, onOpenChange }: AnomalyTypeCreateDialogProps) {
  const createMutation = useCreateAnomalyType();

  const form = useForm<CreateAnomalyTypeFormValues>({
    resolver: zodResolver(createAnomalyTypeSchema),
    defaultValues: { code: '', name: '', description: '', severity: 'info' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (!open) {
      form.reset();
      createMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function onSubmit(values: CreateAnomalyTypeFormValues) {
    try {
      await createMutation.mutateAsync(values);
      toast.success('Tipo de anomalia criado.');
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        form.setError('code', { type: 'server', message: 'Código já existe.' });
        toast.error('Código já existe');
        return;
      }
      const msg = err instanceof ApiError ? err.userMessage : 'Não foi possível criar o tipo.';
      toast.error(msg);
    }
  }

  const isSubmitting = createMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Novo tipo de anomalia</DialogTitle>
          <DialogDescription>
            O código é a chave usada por integradores e seeds — escolha com cuidado, ele não pode
            ser alterado depois.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="code"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Código</FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      disabled={isSubmitting}
                      placeholder="ex: extra_charge"
                      className="font-mono text-sm"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome</FormLabel>
                  <FormControl>
                    <Input disabled={isSubmitting} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="severity"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Severidade</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    disabled={isSubmitting}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Selecione a severidade" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="critical">Crítico</SelectItem>
                      <SelectItem value="moderate">Moderado</SelectItem>
                      <SelectItem value="info">Informativo</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Descrição</FormLabel>
                  <FormControl>
                    <Textarea rows={3} disabled={isSubmitting} {...field} />
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
                Criar tipo
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
