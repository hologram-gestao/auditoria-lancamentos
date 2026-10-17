'use client';

/**
 * Gaveta "Importar plano contábil" (Sprint 16 — FRONT 16.5 / R1; prévia desde a
 * 86e3qhhrm).
 *
 * O modelo da planilha documentado no topo (colunas e exemplo), o arquivo, e
 * dois passos, sempre, na primeira importação e na reimportação:
 *
 *   1. **Revisar**: o arquivo vai à PRÉVIA (`POST …/import/preview`, não grava) e
 *      a gaveta mostra o que muda (`AccountingChartImportPreviewPanel`): novas,
 *      alteradas, sem mudança e as que não vieram, com quantas delas estão em uso.
 *      O segundo passo mora no rodapé da própria gaveta, e não num `AlertDialog`
 *      empilhado (dois modais do Radix marcam o fundo com `aria-hidden` e o de
 *      cima sai do teclado; front-gate §4);
 *   2. **Confirmar**: o import de verdade, com o MESMO arquivo. A reimportação
 *      não inativa: a conta que não veio fica como está, com o selo.
 *
 * Desfechos que NÃO são toast: as contagens da importação (no toast e na tela,
 * porque o toast some) e a recusa 422, da prévia ou da importação, ramificada por
 * `code` (`AccountingChartRefusalNotice`). Só código desconhecido cai no toast
 * (ADR-047-FE). Planilha que não muda nada não oferece importar.
 *
 * Remontada por `key` a cada abertura: estado limpo, sem arquivo, prévia nem
 * recusa da vez anterior.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Eye, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { FILE_ACCEPT } from '@/components/features/file-origin/file-refusal-notice';
import { FileInputField } from '@/components/shared/file-input-field';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import {
  useImportAccountingChart,
  usePreviewAccountingChartImport,
} from '@/hooks/use-client-accounting-chart';
import { readAccountingChartRefusal } from '@/lib/accounting-chart-errors';
import { ApiError } from '@/lib/api/client';
import type { AccountingChartImportPreview, AccountingChartImportResult } from '@/lib/contracts';
import {
  accountingChartImportSchema,
  type AccountingChartImportValues,
} from '@/lib/validation/accounting-chart';

import { AccountingChartImportPreviewPanel } from './accounting-chart-import-preview';
import { AccountingChartModel } from './accounting-chart-model';
import { AccountingChartRefusalNotice } from './accounting-chart-refusal-notice';

interface AccountingChartImportSheetProps {
  clientId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** O cliente já tem plano? Decide o título e os rótulos (a prévia vale para os dois). */
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
  )}, ${plural(result.contasAlteradas, 'alterada', 'alteradas')}, ${plural(
    result.contasAusentes,
    'não veio',
    'não vieram',
  )}.`;
}

export function AccountingChartImportSheet({
  clientId,
  open,
  onOpenChange,
  hasPlan,
  onImported,
}: AccountingChartImportSheetProps) {
  const importMutation = useImportAccountingChart(clientId);
  const previewMutation = usePreviewAccountingChartImport(clientId);
  const [refusal, setRefusal] = useState<unknown>(null);
  // Segundo passo: a prévia do arquivo escolhido, que a pessoa confirma.
  const [preview, setPreview] = useState<AccountingChartImportPreview | null>(null);

  const form = useForm<AccountingChartImportValues>({
    resolver: zodResolver(accountingChartImportSchema),
    defaultValues: { file: null },
    mode: 'onSubmit',
  });

  const isPending = importMutation.isPending || previewMutation.isPending;

  function onSubmit(values: AccountingChartImportValues) {
    if (!(values.file instanceof File)) return;
    if (preview === null) {
      void review(values.file);
      return;
    }
    void send(values.file);
  }

  function handleFailure(err: unknown, fallback: string) {
    if (readAccountingChartRefusal(err) !== null) {
      setRefusal(err);
      return;
    }
    toast.error(err instanceof ApiError ? err.userMessage : fallback);
  }

  async function review(file: File) {
    setRefusal(null);
    try {
      setPreview(await previewMutation.mutateAsync(file));
    } catch (err) {
      handleFailure(err, 'Não foi possível ler a planilha do plano contábil.');
    }
  }

  async function send(file: File) {
    setRefusal(null);
    try {
      const result = await importMutation.mutateAsync(file);
      toast.success(importSuccessMessage(result));
      onImported(result);
      onOpenChange(false);
    } catch (err) {
      setPreview(null);
      handleFailure(err, 'Não foi possível importar o plano contábil.');
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
                    <FormLabel>Planilha (.csv, .xlsx ou .xls)</FormLabel>
                    <FormControl>
                      <FileInputField
                        accept={FILE_ACCEPT}
                        disabled={isPending}
                        name={field.name}
                        ref={field.ref}
                        onBlur={field.onBlur}
                        value={field.value}
                        onChange={(file) => {
                          field.onChange(file);
                          setRefusal(null);
                          setPreview(null);
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {hasPlan && preview === null && (
                <p
                  role="note"
                  data-testid="accounting-chart-reimport-note"
                  className="bg-muted text-foreground rounded-lg p-3 text-sm"
                >
                  Este cliente já tem plano contábil. A reimportação casa as contas pelo código
                  reduzido e só adiciona e atualiza: as contas que não estiverem na planilha
                  continuam como estão, com o selo &quot;não veio na última importação&quot;. Antes
                  de gravar, você revisa o que muda.
                </p>
              )}

              {preview !== null && <AccountingChartImportPreviewPanel preview={preview} />}

              {refusal !== null && <AccountingChartRefusalNotice error={refusal} />}
            </SheetBody>

            <SheetFooter>
              {preview !== null ? (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setPreview(null)}
                    disabled={isPending}
                  >
                    Voltar
                  </Button>
                  {preview.temMudancas ? (
                    <Button type="submit" disabled={isPending}>
                      {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                      {isPending
                        ? 'Importando…'
                        : hasPlan
                          ? 'Confirmar reimportação'
                          : 'Confirmar importação'}
                    </Button>
                  ) : (
                    <Button type="button" onClick={() => onOpenChange(false)} disabled={isPending}>
                      Fechar sem importar
                    </Button>
                  )}
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
                      <Eye className="h-4 w-4" aria-hidden="true" />
                    )}
                    {isPending ? 'Lendo a planilha…' : 'Revisar mudanças'}
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
