'use client';

/**
 * Gaveta "Importar plano contábil" (Sprint 16 — FRONT 16.5 / R1).
 *
 * O modelo da planilha documentado no topo (colunas e exemplo), o arquivo, e
 * três desfechos que NÃO são toast:
 *
 *   - **reimportação** (o cliente já tem plano): a confirmação vem ANTES de
 *     enviar, dentro da própria gaveta — um segundo passo no rodapé, e não um
 *     `AlertDialog` empilhado sobre a gaveta (dois modais do Radix marcam o
 *     fundo com `aria-hidden` e o de cima sai do teclado; front-gate §4). O
 *     aviso diz o que muda: conta que não vier na planilha fica INATIVA;
 *   - **sucesso**: as três contagens DA RESPOSTA (contas, novas, inativadas),
 *     no toast e na tela (o toast some); a lista se atualiza pela invalidação;
 *   - **recusa 422**: estado na gaveta ramificado por `code`
 *     (`AccountingChartRefusalNotice`). Só código desconhecido cai no toast
 *     (ADR-047-FE).
 *
 * Remontada por `key` a cada abertura: estado limpo, sem arquivo nem recusa
 * da vez anterior.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { AlertTriangle, Loader2, Upload } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { FILE_ACCEPT } from '@/components/features/file-origin/file-refusal-notice';
import { Button } from '@/components/ui/button';
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
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useImportAccountingChart } from '@/hooks/use-client-accounting-chart';
import { readAccountingChartRefusal } from '@/lib/accounting-chart-errors';
import { ApiError } from '@/lib/api/client';
import type { AccountingChartImportResult } from '@/lib/contracts';
import {
  accountingChartImportSchema,
  type AccountingChartImportValues,
} from '@/lib/validation/accounting-chart';

import { AccountingChartModel } from './accounting-chart-model';
import { AccountingChartRefusalNotice } from './accounting-chart-refusal-notice';

interface AccountingChartImportSheetProps {
  clientId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** O cliente já tem plano? Decide a confirmação de reimportação. */
  hasPlan: boolean;
  /** Sucesso: a tela mostra as contagens fora da gaveta, que fecha. */
  onImported: (result: AccountingChartImportResult) => void;
}

export function importSuccessMessage(result: AccountingChartImportResult): string {
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  return `Plano contábil importado: ${plural(result.contas, 'conta', 'contas')}, ${plural(
    result.contasNovas,
    'nova',
    'novas',
  )}, ${plural(result.contasInativadas, 'inativada', 'inativadas')}.`;
}

export function AccountingChartImportSheet({
  clientId,
  open,
  onOpenChange,
  hasPlan,
  onImported,
}: AccountingChartImportSheetProps) {
  const importMutation = useImportAccountingChart(clientId);
  const [refusal, setRefusal] = useState<unknown>(null);
  // Segundo passo da reimportação: o arquivo já foi validado e a pessoa
  // precisa confirmar que as contas ausentes ficarão inativas.
  const [confirming, setConfirming] = useState(false);

  const form = useForm<AccountingChartImportValues>({
    resolver: zodResolver(accountingChartImportSchema),
    defaultValues: { file: null },
    mode: 'onSubmit',
  });

  const isPending = importMutation.isPending;

  function onSubmit(values: AccountingChartImportValues) {
    if (!(values.file instanceof File)) return;
    if (hasPlan && !confirming) {
      setConfirming(true);
      return;
    }
    void send(values.file);
  }

  async function send(file: File) {
    setRefusal(null);
    try {
      const result = await importMutation.mutateAsync(file);
      toast.success(importSuccessMessage(result));
      onImported(result);
      onOpenChange(false);
    } catch (err) {
      setConfirming(false);
      if (readAccountingChartRefusal(err) !== null) {
        setRefusal(err);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível importar o plano contábil.',
      );
    }
  }

  return (
    <Sheet open={open} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <SheetContent side="right" className="flex flex-col p-0">
        <SheetHeader>
          <SheetTitle>
            {hasPlan ? 'Reimportar plano contábil' : 'Importar plano contábil'}
          </SheetTitle>
          <SheetDescription>
            O plano do sistema contábil onde o escritório lança — é dele que o de-para escolhe a
            conta de cada categoria. Ou a planilha entra inteira, ou nada entra.
          </SheetDescription>
        </SheetHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col"
            noValidate
          >
            <SheetBody className="space-y-5">
              <AccountingChartModel headingId="accounting-chart-import-model" />

              <FormField
                control={form.control}
                name="file"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Planilha (.csv ou .xlsx)</FormLabel>
                    <FormControl>
                      <Input
                        type="file"
                        accept={FILE_ACCEPT}
                        disabled={isPending}
                        name={field.name}
                        ref={field.ref}
                        onBlur={field.onBlur}
                        onChange={(e) => {
                          field.onChange(e.target.files?.[0] ?? null);
                          setRefusal(null);
                          setConfirming(false);
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {hasPlan && (
                <div
                  role={confirming ? 'alert' : 'note'}
                  data-testid="accounting-chart-reimport-warning"
                  className="bg-warning-muted text-warning ring-warning/30 space-y-1 rounded-lg p-3 text-sm ring-1 ring-inset"
                >
                  <p className="flex items-start gap-2 font-medium">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    <span>Este cliente já tem plano contábil</span>
                  </p>
                  <p>
                    A reimportação casa as contas pelo código reduzido: as novas entram, as
                    existentes são atualizadas e as contas que NÃO estiverem na planilha ficarão
                    inativas. Conta inativa não é apagada, mas deixa de receber decisão nova e de
                    ser conta do banco.
                  </p>
                </div>
              )}

              {refusal !== null && <AccountingChartRefusalNotice error={refusal} />}
            </SheetBody>

            <SheetFooter>
              {confirming ? (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setConfirming(false)}
                    disabled={isPending}
                  >
                    Voltar
                  </Button>
                  <Button type="submit" disabled={isPending}>
                    {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                    {isPending ? 'Importando…' : 'Confirmar reimportação'}
                  </Button>
                </>
              ) : (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => onOpenChange(false)}
                    disabled={isPending}
                  >
                    Cancelar
                  </Button>
                  <Button type="submit" disabled={isPending}>
                    {isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Upload className="h-4 w-4" aria-hidden="true" />
                    )}
                    {isPending ? 'Importando…' : hasPlan ? 'Reimportar' : 'Importar'}
                  </Button>
                </>
              )}
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}
