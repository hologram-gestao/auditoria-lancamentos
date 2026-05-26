'use client';

/**
 * Diálogo de override manual de qualificação (S19 FRONT 12.2).
 *
 * Mostra TODAS as anomalias de qualificação pendentes vinculadas a uma
 * movimentação. O usuário escreve uma única `resolution_note` (≥ 10 chars,
 * alinhada com o validator do back em `service.resolve_anomaly`) e clica
 * em "Marcar como ok manualmente" — disparamos um PATCH por anomalia em
 * paralelo via `Promise.allSettled`.
 *
 * Falhas parciais: toast detalha quantas resolveram; sucessos ficam
 * persistidos e o dialog continua aberto pra retry das que falharam (o
 * `useAllSessionAnomalies` se invalida via `usePatchAnomaly`, então a
 * lista shrinka até as pendentes restantes).
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
import { Textarea } from '@/components/ui/textarea';
import { usePatchAnomaly } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { AnomalyItem } from '@/lib/api/reconciliations';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

interface QualificationOverrideDialogProps {
  sessionId: string;
  /** Anomalias pendentes vinculadas à movimentação (já filtradas a qualificação). */
  anomalies: AnomalyItem[];
  entry: {
    transaction_date: string;
    description: string;
    amount: string;
  } | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const schema = z.object({
  resolution_note: z
    .string()
    .trim()
    .min(10, 'A justificativa deve ter pelo menos 10 caracteres.')
    .max(2000, 'A justificativa deve ter no máximo 2000 caracteres.'),
});

type FormValues = z.infer<typeof schema>;

export function QualificationOverrideDialog({
  sessionId,
  anomalies,
  entry,
  open,
  onOpenChange,
}: QualificationOverrideDialogProps) {
  const patchMutation = usePatchAnomaly(sessionId);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { resolution_note: '' },
  });

  useEffect(() => {
    if (!open) {
      form.reset({ resolution_note: '' });
    }
  }, [open, form]);

  async function onSubmit(values: FormValues) {
    const results = await Promise.allSettled(
      anomalies.map((a) =>
        patchMutation.mutateAsync({
          anomalyId: a.id,
          payload: { resolved: true, resolution_note: values.resolution_note },
        }),
      ),
    );
    const failed = results.filter((r) => r.status === 'rejected');
    const succeeded = results.length - failed.length;

    if (failed.length === 0) {
      toast.success(
        succeeded === 1
          ? 'Anomalia marcada como ok manualmente.'
          : `${succeeded} anomalias marcadas como ok manualmente.`,
      );
      onOpenChange(false);
      return;
    }

    const firstError = failed[0];
    const reason = firstError?.status === 'rejected' ? firstError.reason : undefined;
    const errorMessage =
      reason instanceof ApiError ? reason.userMessage : 'Não foi possível atualizar a anomalia.';

    if (succeeded === 0) {
      toast.error(errorMessage);
    } else {
      toast.error(`${succeeded} resolvida(s); ${failed.length} falharam: ${errorMessage}`);
    }
  }

  const isSubmitting = patchMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Marcar qualificação como ok</DialogTitle>
          <DialogDescription>
            Use quando a qualificação Omie está correta apesar do sinal automático. A justificativa
            fica registrada para auditoria.
          </DialogDescription>
        </DialogHeader>

        {entry !== null && (
          <div className="bg-muted/40 rounded-md border p-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground">{formatBRDate(entry.transaction_date)}</span>
              <span
                className={cn(
                  'font-medium tabular-nums',
                  Number(entry.amount) > 0 && 'text-emerald-700 dark:text-emerald-300',
                  Number(entry.amount) < 0 && 'text-red-700 dark:text-red-300',
                )}
              >
                {formatBRL(entry.amount, { signed: true })}
              </span>
            </div>
            <p className="text-foreground mt-1">{entry.description}</p>
          </div>
        )}

        <div className="space-y-2">
          <h3 className="text-sm font-medium">
            {anomalies.length === 1 ? 'Sinal detectado' : `${anomalies.length} sinais detectados`}
          </h3>
          <ul className="space-y-2">
            {anomalies.map((a) => (
              <li
                key={a.id}
                className={cn(
                  'rounded-md border px-3 py-2 text-sm',
                  a.anomaly_type.code === 'qualificacao_incoerente'
                    ? 'bg-red-50 dark:bg-red-950/30'
                    : 'bg-amber-50 dark:bg-amber-950/30',
                )}
              >
                <div className="font-medium">{a.anomaly_type.name}</div>
                {a.context !== null && a.context.trim() !== '' && (
                  <p className="text-muted-foreground mt-0.5 text-xs">{a.context}</p>
                )}
              </li>
            ))}
          </ul>
        </div>

        <form
          onSubmit={(e) => {
            void form.handleSubmit(onSubmit)(e);
          }}
          className="space-y-4"
        >
          <div className="space-y-1">
            <Label htmlFor="qualification-note">Justificativa</Label>
            <Textarea
              id="qualification-note"
              {...form.register('resolution_note')}
              maxLength={2000}
              rows={4}
              placeholder="Ex: Fornecedor mudou de razão social mas o lançamento é o correto."
            />
            {form.formState.errors.resolution_note?.message !== undefined && (
              <p className="text-destructive text-xs">
                {form.formState.errors.resolution_note.message}
              </p>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button type="submit" disabled={isSubmitting || anomalies.length === 0}>
              {isSubmitting ? 'Salvando…' : 'Marcar como ok manualmente'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
