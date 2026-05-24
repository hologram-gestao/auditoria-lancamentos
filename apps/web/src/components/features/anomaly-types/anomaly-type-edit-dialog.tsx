'use client';

/**
 * Modal "Editar tipo de anomalia" — S15 FRONT 11.2.
 *
 * `code` é IMUTÁVEL no backend → renderizado readonly. Submit dispara PATCH
 * parcial (somente os campos editáveis).
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
import { useUpdateAnomalyType } from '@/hooks/use-anomaly-types';
import type { AnomalyType, AnomalySeverity } from '@/lib/api/anomaly-types';
import { ApiError } from '@/lib/api/client';
import {
  updateAnomalyTypeSchema,
  type UpdateAnomalyTypeFormValues,
} from '@/lib/validation/anomaly-types';

const VALID_SEVERITIES: ReadonlyArray<AnomalySeverity> = ['critical', 'moderate', 'info'];

function coerceSeverity(value: string): AnomalySeverity {
  return (VALID_SEVERITIES as ReadonlyArray<string>).includes(value)
    ? (value as AnomalySeverity)
    : 'info';
}

interface AnomalyTypeEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  anomalyType: AnomalyType | null;
}

export function AnomalyTypeEditDialog({
  open,
  onOpenChange,
  anomalyType,
}: AnomalyTypeEditDialogProps) {
  const updateMutation = useUpdateAnomalyType(anomalyType?.id ?? '');

  const form = useForm<UpdateAnomalyTypeFormValues>({
    resolver: zodResolver(updateAnomalyTypeSchema),
    defaultValues: { name: '', description: '', severity: 'info' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (open && anomalyType) {
      form.reset({
        name: anomalyType.name,
        description: anomalyType.description,
        severity: coerceSeverity(anomalyType.severity),
      });
      updateMutation.reset();
    }
    // form/mutation são estáveis; rodar só quando o modal abrir/fechar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, anomalyType]);

  async function onSubmit(values: UpdateAnomalyTypeFormValues) {
    if (!anomalyType) return;
    try {
      await updateMutation.mutateAsync(values);
      toast.success('Tipo de anomalia atualizado.');
      onOpenChange(false);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar as alterações.';
      toast.error(msg);
    }
  }

  const isSubmitting = updateMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Editar tipo de anomalia</DialogTitle>
          <DialogDescription>
            O código é fixo após a criação — anomalias antigas o referenciam por chave.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormItem>
              <FormLabel>Código</FormLabel>
              <FormControl>
                <Input
                  value={anomalyType?.code ?? ''}
                  readOnly
                  disabled
                  className="font-mono text-sm"
                />
              </FormControl>
            </FormItem>

            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome</FormLabel>
                  <FormControl>
                    <Input autoFocus disabled={isSubmitting} {...field} />
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
                Salvar alterações
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
