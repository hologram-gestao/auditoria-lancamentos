'use client';

/**
 * Gaveta "Decisão do de-para" (Sprint 12 — FRONT 12.7 / R2 · R4 · R6).
 *
 * Uma categoria, um destino: escolher um ALVO do catálogo ou "Não mapear" — e
 * "Não mapear" é rotulado como DECISÃO (deixar a categoria fora deste destino),
 * distinto de "sem decisão", que é a ausência dela e não é uma opção aqui.
 *
 * Toda gravação é append-only (R4): a gaveta diz, antes de gravar, que a
 * alteração cria vigência nova e a partir de quando, e pede a competência de
 * início com padrão = a corrente do SERVIDOR. Os dois 409 de vigência viram
 * estado dentro da própria gaveta (`vigencia.tsx`): retroativa lista as
 * competências afetadas e troca a ação para "Confirmar alteração retroativa";
 * materializada é recusa com as competências nomeadas.
 *
 * Só é montada para quem tem `manage_client_mapping` — quem não tem não vê o
 * botão que a abre (§4.9).
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Combobox } from '@/components/ui/combobox';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useMappingTargets, useWriteMappingDecision } from '@/hooks/use-client-mapping';
import { ApiError } from '@/lib/api/client';
import type { MappingDestination, MappingListItem } from '@/lib/contracts';
import { formatReferenceMonth } from '@/lib/format';
import {
  mappingDecisionFormSchema,
  type MappingDecisionFormValues,
} from '@/lib/validation/client-mapping';

import { MappingSituationBadge } from './client-mapping-badges';
import {
  readVigenciaConflict,
  VigenciaConflictNotice,
  VigenciaExplanation,
  type VigenciaConflict,
} from './vigencia';

const DECISION_LABELS: Record<MappingDecisionFormValues['decision'], string> = {
  alvo: 'Enviar para um alvo do catálogo',
  nao_mapear: 'Não mapear (decisão: fica fora deste destino)',
};

interface MappingDecisionSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  destination: MappingDestination;
  item: MappingListItem | null;
  /** Competência corrente do SERVIDOR — o padrão do início da vigência. */
  serverCompetence: string;
}

function defaultsFor(item: MappingListItem | null, serverCompetence: string) {
  return {
    decision: item?.decision ?? 'alvo',
    targetCode: item?.targetCode ?? '',
    effectiveFrom: serverCompetence,
  } satisfies MappingDecisionFormValues;
}

export function MappingDecisionSheet({
  open,
  onOpenChange,
  clientId,
  destination,
  item,
  serverCompetence,
}: MappingDecisionSheetProps) {
  const targetsQuery = useMappingTargets(destination.id, { enabled: open });
  const writeMutation = useWriteMappingDecision(clientId, destination.type);
  const [conflict, setConflict] = useState<VigenciaConflict | null>(null);

  const form = useForm<MappingDecisionFormValues>({
    resolver: zodResolver(mappingDecisionFormSchema),
    defaultValues: defaultsFor(item, serverCompetence),
    mode: 'onSubmit',
  });

  // Estado limpo a cada categoria aberta — nunca herdar o rascunho (nem o
  // conflito de vigência) da categoria anterior.
  useEffect(() => {
    if (open) {
      form.reset(defaultsFor(item, serverCompetence));
      setConflict(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, item?.categoryCode, item?.sourceType, serverCompetence]);

  const decision = form.watch('decision');
  const effectiveFrom = form.watch('effectiveFrom');

  // A confirmação retroativa vale para AQUELE início: mudou o mês, o servidor
  // precisa dizer de novo quais competências são afetadas.
  useEffect(() => {
    setConflict(null);
  }, [effectiveFrom, decision]);

  const isSubmitting = writeMutation.isPending;
  const retroactivePending = conflict?.kind === 'retroactive';
  const targets = targetsQuery.data ?? [];
  const targetOptions = targets.map((target) => ({
    value: target.code,
    label: `${target.code} — ${target.name}`,
  }));

  async function onSubmit(values: MappingDecisionFormValues) {
    if (!item) return;
    try {
      const result = await writeMutation.mutateAsync({
        categoryCode: item.categoryCode,
        sourceType: item.sourceType,
        decision: values.decision,
        targetCode: values.decision === 'alvo' ? values.targetCode : null,
        effectiveFrom: values.effectiveFrom,
        confirmRetroactive: retroactivePending,
      });
      if (result.created === 0 && result.resolved === 0 && result.unchanged > 0) {
        toast.info('Nada mudou: esta já é a decisão confirmada vigente.');
      } else {
        toast.success(
          `Decisão gravada, vigente a partir de ${formatReferenceMonth(result.effectiveFrom)}.`,
        );
      }
      onOpenChange(false);
    } catch (err) {
      const vigencia = readVigenciaConflict(err);
      if (vigencia) {
        setConflict(vigencia);
        return;
      }
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível gravar a decisão.');
    }
  }

  const categoryLabel = item
    ? item.categoryNameResolved && item.categoryName
      ? `${item.categoryCode} — ${item.categoryName}`
      : item.categoryCode
    : '';

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex flex-col p-0">
        <SheetHeader>
          <SheetTitle>Decisão do de-para</SheetTitle>
          <SheetDescription>
            {item ? (
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-foreground font-medium">{categoryLabel}</span>
                <MappingSituationBadge situation={item.situation} />
                <span>em {destination.name}</span>
              </span>
            ) : (
              'Escolha o alvo desta categoria no destino.'
            )}
          </SheetDescription>
        </SheetHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col"
            noValidate
          >
            <SheetBody className="space-y-5">
              <FormField
                control={form.control}
                name="decision"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Decisão</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={(value) => {
                        field.onChange(value);
                        if (value === 'nao_mapear') form.setValue('targetCode', '');
                      }}
                      disabled={isSubmitting}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {(
                          Object.keys(DECISION_LABELS) as MappingDecisionFormValues['decision'][]
                        ).map((value) => (
                          <SelectItem key={value} value={value}>
                            {DECISION_LABELS[value]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      &quot;Não mapear&quot; é uma decisão registrada: a categoria fica fora deste
                      destino de propósito — diferente de deixá-la sem decisão.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {decision === 'alvo' && (
                <FormField
                  control={form.control}
                  name="targetCode"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Alvo</FormLabel>
                      <FormControl>
                        <Combobox
                          options={targetOptions}
                          value={field.value === '' ? null : field.value}
                          onValueChange={field.onChange}
                          label="Alvo do catálogo"
                          placeholder="Escolher alvo…"
                          searchPlaceholder="Buscar por código ou nome…"
                          emptyMessage={
                            targetsQuery.isError
                              ? 'Não foi possível carregar o catálogo.'
                              : 'Nenhum alvo encontrado.'
                          }
                          loading={targetsQuery.isLoading}
                          disabled={isSubmitting}
                        />
                      </FormControl>
                      {!targetsQuery.isLoading && targets.length === 0 && (
                        <FormDescription>
                          O catálogo deste destino não tem alvos ativos. Peça ao administrador da
                          organização para cadastrá-los — enquanto isso, só &quot;Não mapear&quot; é
                          possível.
                        </FormDescription>
                      )}
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              <FormField
                control={form.control}
                name="effectiveFrom"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Competência de início</FormLabel>
                    <FormControl>
                      <Input type="month" lang="pt-BR" disabled={isSubmitting} {...field} />
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
                hasCurrentDecision={item?.decision != null}
              />

              {conflict && <VigenciaConflictNotice conflict={conflict} />}
            </SheetBody>

            <SheetFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={isSubmitting || conflict?.kind === 'materialized'}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {retroactivePending ? 'Confirmar alteração retroativa' : 'Gravar decisão'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}
