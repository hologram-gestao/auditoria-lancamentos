'use client';

/**
 * Modal "Registrar anomalia" (FRONT 9.14, Doc §14.5).
 *
 * Source XOR (pitfall §9 do briefing):
 *   - O front sabe qual aba abriu o modal e manda APENAS um entre
 *     `file_entry_id` e `omie_entry_id`. Discriminated union em `source`.
 *
 * Validação:
 *   - `anomaly_type_id`: obrigatório (vem do catálogo `useAnomalyTypes`).
 *   - `context`: opcional, ≤ 2000 chars.
 *
 * Reset ao fechar (pitfall §7): `useEffect` zera o form.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useAnomalyTypes, useCreateAnomaly } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import { formatBRDate, formatBRL } from '@/lib/format';

import { SeverityBadge } from './severity-badge';

export type AnomalySource =
  | {
      kind: 'file_entry';
      id: string;
      date: string;
      description: string;
      amount: string;
    }
  | {
      kind: 'omie_entry';
      id: string;
      date: string;
      /** Texto da linha — fornecedor ou ID Omie quando ausente. */
      description: string;
      amount: string | null;
    };

interface RegistrarAnomaliaModalProps {
  sessionId: string;
  source: AnomalySource;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const schema = z.object({
  anomaly_type_id: z
    .string()
    .uuid({ message: 'Selecione um tipo de anomalia.' })
    .min(1, 'Selecione um tipo de anomalia.'),
  context: z.string().max(2000, 'O contexto deve ter no máximo 2000 caracteres.').optional(),
});

type FormValues = z.infer<typeof schema>;

export function RegistrarAnomaliaModal({
  sessionId,
  source,
  open,
  onOpenChange,
}: RegistrarAnomaliaModalProps) {
  const typesQuery = useAnomalyTypes();
  const createMutation = useCreateAnomaly(sessionId);

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { anomaly_type_id: '', context: '' },
  });

  // Reset ao fechar (pitfall §7).
  useEffect(() => {
    if (!open) {
      form.reset({ anomaly_type_id: '', context: '' });
    }
  }, [open, form]);

  async function onSubmit(values: FormValues) {
    try {
      const payload =
        source.kind === 'file_entry'
          ? {
              anomaly_type_id: values.anomaly_type_id,
              file_entry_id: source.id,
              context: values.context?.trim() || undefined,
            }
          : {
              anomaly_type_id: values.anomaly_type_id,
              omie_entry_id: source.id,
              context: values.context?.trim() || undefined,
            };
      await createMutation.mutateAsync(payload);
      toast.success('Anomalia registrada.');
      onOpenChange(false);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível registrar a anomalia.';
      toast.error(message);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Registrar anomalia</DialogTitle>
          <DialogDescription>
            Use anomalias para sinalizar situações que precisam de atenção (duplicidades, juros
            inesperados, conciliações duvidosas, etc).
          </DialogDescription>
        </DialogHeader>

        <section className="bg-muted/40 space-y-1 rounded-md border p-3 text-sm">
          <p className="text-muted-foreground text-xs uppercase tracking-wide">Linha relacionada</p>
          <p>
            <span className="font-medium">{formatBRDate(source.date)}</span> ·{' '}
            <span>{source.description}</span>
            {source.amount !== null && (
              <>
                {' '}
                ·{' '}
                <span className="font-medium tabular-nums">
                  {formatBRL(source.amount, { signed: true })}
                </span>
              </>
            )}
          </p>
        </section>

        <form
          onSubmit={(e) => {
            void form.handleSubmit(onSubmit)(e);
          }}
          className="space-y-4"
        >
          <div className="space-y-1">
            <Label htmlFor="anomaly-type">Tipo de anomalia</Label>
            <Select
              value={form.watch('anomaly_type_id')}
              onValueChange={(v) => form.setValue('anomaly_type_id', v, { shouldValidate: true })}
            >
              <SelectTrigger id="anomaly-type">
                <SelectValue placeholder="Selecione um tipo" />
              </SelectTrigger>
              <SelectContent>
                {typesQuery.isLoading && (
                  <SelectItem value="__loading" disabled>
                    Carregando…
                  </SelectItem>
                )}
                {!typesQuery.isLoading &&
                  (typesQuery.data ?? []).map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      <span className="flex items-center gap-2">
                        <SeverityBadge severity={t.severity} />
                        <span>{t.name}</span>
                      </span>
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            {form.formState.errors.anomaly_type_id?.message !== undefined && (
              <p className="text-destructive text-xs">
                {form.formState.errors.anomaly_type_id.message}
              </p>
            )}
          </div>

          <div className="space-y-1">
            <Label htmlFor="anomaly-context">Contexto (opcional)</Label>
            <Textarea
              id="anomaly-context"
              {...form.register('context')}
              maxLength={2000}
              rows={4}
              placeholder="Descreva o que motivou o registro da anomalia"
            />
            {form.formState.errors.context?.message !== undefined && (
              <p className="text-destructive text-xs">{form.formState.errors.context.message}</p>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Registrando…' : 'Registrar'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
