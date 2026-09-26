'use client';

/**
 * Diálogo de uma ação do de-para que grava EM LOTE com vigência (Sprint 12 /
 * R4 · R6 · R7): a confirmação das herdadas e o "Iniciar de-para".
 *
 * O que ele garante, igual à gaveta da decisão individual: a competência de
 * início é pedida (padrão = corrente do servidor), a vigência nova é dita antes
 * de gravar, e os 409 de vigência viram estado dentro do diálogo. O RESUMO do
 * que será afetado (a contagem do lote) vem de quem chama, porque cada ação tem
 * o seu.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
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
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { ApiError } from '@/lib/api/client';
import { formatReferenceMonth } from '@/lib/format';
import {
  mappingVigenciaFormSchema,
  type MappingVigenciaFormValues,
} from '@/lib/validation/client-mapping';

import {
  readVigenciaConflict,
  VigenciaConflictNotice,
  VigenciaExplanation,
  type VigenciaConflict,
} from './vigencia';

export interface VigenciaActionValues {
  effectiveFrom: string;
  confirmRetroactive: boolean;
}

interface VigenciaActionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: React.ReactNode;
  /** O que a ação atinge (a contagem do lote), já no texto de quem chama. */
  summary?: React.ReactNode;
  confirmLabel: string;
  /** Ação indisponível (contando, ou nada a fazer): o botão de confirmar fica desabilitado. */
  confirmDisabled?: boolean;
  serverCompetence: string;
  /** Mensagem de erro genérica, quando o servidor não mandou `userMessage`. */
  errorFallback: string;
  /** Lança o erro da API — o diálogo decide se é conflito de vigência ou toast. */
  onConfirm: (values: VigenciaActionValues) => Promise<void>;
}

export function VigenciaActionDialog({
  open,
  onOpenChange,
  title,
  description,
  summary,
  confirmLabel,
  confirmDisabled = false,
  serverCompetence,
  errorFallback,
  onConfirm,
}: VigenciaActionDialogProps) {
  const [conflict, setConflict] = useState<VigenciaConflict | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const form = useForm<MappingVigenciaFormValues>({
    resolver: zodResolver(mappingVigenciaFormSchema),
    defaultValues: { effectiveFrom: serverCompetence },
  });

  useEffect(() => {
    if (open) {
      form.reset({ effectiveFrom: serverCompetence });
      setConflict(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, serverCompetence]);

  const effectiveFrom = form.watch('effectiveFrom');
  useEffect(() => {
    setConflict(null);
  }, [effectiveFrom]);

  const retroactivePending = conflict?.kind === 'retroactive';

  async function onSubmit(values: MappingVigenciaFormValues) {
    setSubmitting(true);
    try {
      await onConfirm({
        effectiveFrom: values.effectiveFrom,
        confirmRetroactive: retroactivePending,
      });
    } catch (err) {
      const vigencia = readVigenciaConflict(err);
      if (vigencia) {
        setConflict(vigencia);
      } else {
        toast.error(err instanceof ApiError ? err.userMessage : errorFallback);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !submitting && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {summary}
            <FormField
              control={form.control}
              name="effectiveFrom"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Competência de início</FormLabel>
                  <FormControl>
                    <Input type="month" lang="pt-BR" disabled={submitting} {...field} />
                  </FormControl>
                  <FormDescription>
                    Padrão: a competência corrente ({formatReferenceMonth(serverCompetence)}).
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <VigenciaExplanation
              effectiveFrom={effectiveFrom}
              serverCompetence={serverCompetence}
              hasCurrentDecision
            />
            {conflict && <VigenciaConflictNotice conflict={conflict} />}
            <DialogFooter className="gap-2 sm:justify-between">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={submitting}
              >
                Cancelar
              </Button>
              <Button
                type="submit"
                disabled={submitting || confirmDisabled || conflict?.kind === 'materialized'}
              >
                {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {retroactivePending ? 'Confirmar alteração retroativa' : confirmLabel}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
