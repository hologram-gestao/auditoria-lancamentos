'use client';

/**
 * Diálogo de resolução de anomalia (FRONT 9.16, Doc §17.3).
 *
 * Validação:
 *   - `resolution_note` ≥ 10 chars (espelha o que o back valida em
 *     `service.resolve_anomaly` — pitfall §10 do briefing).
 *
 * Reset ao fechar (pitfall §7).
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

interface ResolveAnomalyDialogProps {
  sessionId: string;
  anomalyId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const schema = z.object({
  resolution_note: z
    .string()
    .trim()
    .min(10, 'A nota de resolução deve ter pelo menos 10 caracteres.')
    .max(2000, 'A nota deve ter no máximo 2000 caracteres.'),
});

type FormValues = z.infer<typeof schema>;

export function ResolveAnomalyDialog({
  sessionId,
  anomalyId,
  open,
  onOpenChange,
}: ResolveAnomalyDialogProps) {
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
    try {
      await patchMutation.mutateAsync({
        anomalyId,
        payload: { resolved: true, resolution_note: values.resolution_note },
      });
      toast.success('Anomalia marcada como resolvida.');
      onOpenChange(false);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível resolver a anomalia.';
      toast.error(message);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resolver anomalia</DialogTitle>
          <DialogDescription>
            Descreva como a anomalia foi resolvida (mínimo 10 caracteres). A nota fica registrada
            para auditoria.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={(e) => {
            void form.handleSubmit(onSubmit)(e);
          }}
          className="space-y-4"
        >
          <div className="space-y-1">
            <Label htmlFor="resolution-note">Nota de resolução</Label>
            <Textarea
              id="resolution-note"
              {...form.register('resolution_note')}
              maxLength={2000}
              rows={4}
              placeholder="Ex: Lançamento foi registrado em duplicidade; o segundo foi cancelado no Omie."
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
            <Button type="submit" disabled={patchMutation.isPending}>
              {patchMutation.isPending ? 'Salvando…' : 'Marcar como resolvida'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
