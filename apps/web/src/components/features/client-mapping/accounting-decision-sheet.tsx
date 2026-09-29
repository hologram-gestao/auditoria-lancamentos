'use client';

/**
 * Gaveta "Decisão do de-para" no destino `conta_contabil` (Sprint 16 — FRONT
 * 16.6 / R2 · R4).
 *
 * Componente PRÓPRIO, e não um ramo dentro de `MappingDecisionSheet`: nos outros
 * destinos a gaveta tem de ficar idêntica à da S12 (regressão), e cada `if` de
 * destino espalhado lá dentro é um lugar a mais para ela deixar de ser. A lista
 * escolhe qual gaveta montar por `isAccountingDestination`.
 *
 * O que muda aqui:
 *   - o alvo é uma conta ANALÍTICA e ATIVA do plano contábil DO CLIENTE
 *     (`AccountingAccountCombobox`, busca por código no servidor), nunca o
 *     catálogo da organização; cliente sem plano vê o estado que orienta a
 *     importar, com o link para "Plano contábil" (sem botão de importar aqui —
 *     importar é da outra tela e pede outra permissão);
 *   - o histórico padrão, com contador até o teto DOCUMENTADO no contrato
 *     (`MAPPING_HISTORY_MAX_CHARS`), validado no cliente e com o 400 do
 *     servidor tratado no próprio campo;
 *   - a gaveta diz que trocar a conta OU só o histórico cria vigência nova;
 *   - decisão LEGADA (catálogo, `requiresRedo`) aparece só-leitura no topo, e
 *     gravar cria a vigência nova a partir dela.
 * A vigência (padrão, retroativa, materializada) é a mesma peça da S12.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import {
  AccountingAccountCombobox,
  accountingAccountLabel,
} from '@/components/features/accounting-chart/accounting-account-combobox';
import { accountingChartPath } from '@/components/features/navigation/nav-items';
import { Button } from '@/components/ui/button';
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
import { Textarea } from '@/components/ui/textarea';
import { useAccountingChartList } from '@/hooks/use-client-accounting-chart';
import { useWriteMappingDecision } from '@/hooks/use-client-mapping';
import { readNotPostableMessage } from '@/lib/accounting-chart-errors';
import { ApiError } from '@/lib/api/client';
import type { MappingDestination, MappingListItem } from '@/lib/contracts';
import { formatReferenceMonth } from '@/lib/format';
import { cn } from '@/lib/utils';
import {
  MAPPING_HISTORY_MAX_CHARS,
  mappingAccountingDecisionFormSchema,
  type MappingAccountingDecisionFormValues,
} from '@/lib/validation/client-mapping';

import { LegacyRedoBadge } from './accounting-destination';
import { MappingSituationBadge } from './client-mapping-badges';
import {
  readVigenciaConflict,
  VigenciaConflictNotice,
  VigenciaExplanation,
  type VigenciaConflict,
} from './vigencia';

const DECISION_LABELS: Record<MappingAccountingDecisionFormValues['decision'], string> = {
  alvo: 'Enviar para uma conta do plano do cliente',
  nao_mapear: 'Não mapear (decisão: fica fora deste destino)',
};

interface AccountingDecisionSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  destination: MappingDestination;
  item: MappingListItem | null;
  /** Competência corrente do SERVIDOR — o padrão do início da vigência. */
  serverCompetence: string;
  /**
   * `manage_client_accounting_chart`. A gaveta abre com `manage_client_mapping`
   * (o `client_manager` a tem), mas importar o plano é de outra permissão: sem
   * ela, o estado "sem plano" não manda importar (ADR-053-FE).
   */
  canManageChart: boolean;
}

function defaultsFor(
  item: MappingListItem | null,
  serverCompetence: string,
): MappingAccountingDecisionFormValues {
  // Legado: a decisão aponta o catálogo, não há conta do plano para herdar — a
  // pessoa escolhe a conta do zero. O histórico (se houver) segue como ponto de
  // partida.
  const legacy = item?.requiresRedo === true;
  return {
    decision: legacy ? 'alvo' : (item?.decision ?? 'alvo'),
    accountingAccountId: legacy ? '' : (item?.accountingAccountId ?? ''),
    history: item?.history ?? '',
    effectiveFrom: serverCompetence,
  };
}

export function AccountingDecisionSheet({
  open,
  onOpenChange,
  clientId,
  destination,
  item,
  serverCompetence,
  canManageChart,
}: AccountingDecisionSheetProps) {
  // "Tem plano?": sonda de 1 linha, sem filtro (o mesmo critério da tela do plano).
  const planProbe = useAccountingChartList(clientId, { page: 1, pageSize: 1 }, { enabled: open });
  const writeMutation = useWriteMappingDecision(clientId, destination.type);
  const [conflict, setConflict] = useState<VigenciaConflict | null>(null);

  const form = useForm<MappingAccountingDecisionFormValues>({
    resolver: zodResolver(mappingAccountingDecisionFormSchema),
    defaultValues: defaultsFor(item, serverCompetence),
    mode: 'onSubmit',
  });

  // Estado limpo a cada categoria aberta (mesma regra da gaveta da S12).
  useEffect(() => {
    if (open) {
      form.reset(defaultsFor(item, serverCompetence));
      setConflict(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, item?.categoryCode, item?.sourceType, serverCompetence]);

  const decision = form.watch('decision');
  const effectiveFrom = form.watch('effectiveFrom');
  const history = form.watch('history');
  const accountingAccountId = form.watch('accountingAccountId');

  // A confirmação retroativa vale para AQUELE início e AQUELA decisão.
  useEffect(() => {
    setConflict(null);
  }, [effectiveFrom, decision, accountingAccountId, history]);

  const isSubmitting = writeMutation.isPending;
  const retroactivePending = conflict?.kind === 'retroactive';
  const noPlan = planProbe.data?.pagination.total === 0;
  const legacy = item?.requiresRedo === true;
  const historyLength = history.trim().length;
  const historyOver = historyLength > MAPPING_HISTORY_MAX_CHARS;
  // A conta vigente, para o gatilho mostrar código e nome mesmo fora da página buscada.
  const currentAccountLabel =
    !legacy && item?.accountingAccountId && item.accountingAccountCode
      ? accountingAccountLabel({
          code: item.accountingAccountCode,
          name: item.accountingAccountName ?? '',
          nameResolved: Boolean(item.accountingAccountName),
        })
      : null;

  async function onSubmit(values: MappingAccountingDecisionFormValues) {
    if (!item) return;
    const isTarget = values.decision === 'alvo';
    try {
      const result = await writeMutation.mutateAsync({
        categoryCode: item.categoryCode,
        sourceType: item.sourceType,
        decision: values.decision,
        accountingAccountId: isTarget ? values.accountingAccountId : null,
        // Vazio = sem histórico (o servidor apara as pontas; `null` é o "sem").
        history: isTarget && values.history.trim() !== '' ? values.history.trim() : null,
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
      // Conta sintética/inativa (corrida) ou de fora do plano: erro do CAMPO.
      const notPostable = readNotPostableMessage(err);
      if (notPostable !== null) {
        form.setError('accountingAccountId', { message: notPostable });
        return;
      }
      if (err instanceof ApiError && err.status === 404) {
        form.setError('accountingAccountId', {
          message: 'Esta conta não está no plano contábil deste cliente. Escolha outra.',
        });
        return;
      }
      // 400 = forma inválida; neste formulário o único campo que o cliente não
      // trava 1:1 é o histórico (o servidor apara e mede depois).
      if (err instanceof ApiError && err.status === 400) {
        form.setError('history', {
          message: `O servidor recusou o histórico: no máximo ${MAPPING_HISTORY_MAX_CHARS} caracteres depois de aparar as pontas.`,
        });
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
              'Escolha a conta desta categoria no plano contábil do cliente.'
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
              {legacy && item && (
                <div
                  className="bg-warning-muted text-warning ring-warning/30 space-y-2 rounded-lg p-3 text-sm ring-1 ring-inset"
                  data-testid="accounting-legacy-decision"
                >
                  <LegacyRedoBadge />
                  <p>
                    A decisão vigente aponta o catálogo da organização
                    {item.targetCode
                      ? ` (${item.targetCode}${item.targetName ? ` — ${item.targetName}` : ''})`
                      : ''}
                    , anterior ao plano contábil do cliente. Ela continua valendo até ser refeita:
                    escolha a conta no plano do cliente para criar a vigência nova.
                  </p>
                </div>
              )}

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
                        if (value === 'nao_mapear') {
                          form.setValue('accountingAccountId', '');
                          form.clearErrors(['accountingAccountId', 'history']);
                        }
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
                          Object.keys(
                            DECISION_LABELS,
                          ) as MappingAccountingDecisionFormValues['decision'][]
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

              {decision === 'alvo' &&
                (noPlan ? (
                  <div
                    role="status"
                    data-testid="accounting-no-plan"
                    className="bg-info-muted text-info ring-info/30 space-y-2 rounded-lg p-3 text-sm ring-1 ring-inset"
                  >
                    <p className="font-medium">Este cliente ainda não tem plano contábil</p>
                    <p>
                      No destino {destination.name} a conta vem do plano contábil do cliente.{' '}
                      {canManageChart
                        ? 'Importe o plano em "Plano contábil" e volte para decidir'
                        : 'O plano é importado pelo escritório'}{' '}
                      — enquanto isso, só &quot;Não mapear&quot; é possível.
                    </p>
                    <Button asChild variant="outline" size="sm">
                      <Link href={accountingChartPath(clientId)}>
                        {canManageChart ? 'Ir para Plano contábil' : 'Ver Plano contábil'}
                      </Link>
                    </Button>
                  </div>
                ) : (
                  <>
                    <FormField
                      control={form.control}
                      name="accountingAccountId"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Conta contábil</FormLabel>
                          <FormControl>
                            <AccountingAccountCombobox
                              clientId={clientId}
                              value={field.value === '' ? null : field.value}
                              selectedLabel={currentAccountLabel}
                              onValueChange={(next) => {
                                field.onChange(next);
                                form.clearErrors('accountingAccountId');
                              }}
                              label="Conta do plano contábil do cliente"
                              disabled={isSubmitting}
                            />
                          </FormControl>
                          <FormDescription>
                            Só contas analíticas e ativas do plano contábil do cliente. Busca pelo
                            início do código.
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="history"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Histórico padrão</FormLabel>
                          <FormControl>
                            <Textarea
                              rows={3}
                              disabled={isSubmitting}
                              placeholder="Ex.: Pagamento de aluguel"
                              {...field}
                            />
                          </FormControl>
                          <div className="flex items-start justify-between gap-3">
                            <FormDescription>
                              O texto fixo da linha no arquivo contábil. Trocar a conta ou só o
                              histórico cria uma vigência nova.
                            </FormDescription>
                            <p
                              className={cn(
                                'shrink-0 text-xs tabular-nums',
                                historyOver
                                  ? 'text-destructive font-medium'
                                  : 'text-muted-foreground',
                              )}
                              data-testid="history-counter"
                              aria-live="polite"
                            >
                              {historyLength}/{MAPPING_HISTORY_MAX_CHARS}
                            </p>
                          </div>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </>
                ))}

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
              <Button
                type="submit"
                disabled={
                  isSubmitting ||
                  conflict?.kind === 'materialized' ||
                  (decision === 'alvo' && noPlan)
                }
              >
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {retroactivePending
                  ? 'Confirmar alteração retroativa'
                  : legacy
                    ? 'Refazer no plano do cliente'
                    : 'Gravar decisão'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}
