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
 *      alteradas, sem mudança e as que não vieram, com quantas delas estão em uso;
 *      ela entra NO LUGAR do modelo da planilha e rola para a vista (em 390px, sem
 *      isso, ela ficava abaixo da dobra, atrás do "Confirmar").
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
 * **Etapa de mapeamento** (86e3ydjz4): quando a prévia responde 422
 * `MAPEAMENTO_NECESSARIO`, a gaveta abre "Diga o que é cada coluna"
 * (`AccountingChartMappingStep`) no lugar do modelo; "Continuar" pede a prévia de novo
 * com o `mapeamento`, e "Confirmar" manda o MESMO mapeamento para a importação. O
 * mapeamento vive só nesta gaveta (não persiste).
 *
 * Remontada por `key` a cada abertura: estado limpo, sem arquivo, prévia nem
 * recusa da vez anterior.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Eye, Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
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
import { Switch } from '@/components/ui/switch';
import {
  useImportAccountingChart,
  usePreviewAccountingChartImport,
} from '@/hooks/use-client-accounting-chart';
import {
  readAccountingChartRefusal,
  readChartMappingRequired,
  type ChartMappingRequired,
} from '@/lib/accounting-chart-errors';
import { ApiError } from '@/lib/api/client';
import type { AccountingChartImportPreview, AccountingChartImportResult } from '@/lib/contracts';
import {
  accountingChartImportSchema,
  type AccountingChartImportValues,
} from '@/lib/validation/accounting-chart';
import {
  accountingChartMappingSchema,
  suggestedRole,
  toMappingPayload,
  type AccountingChartMappingPayload,
  type AccountingChartMappingValues,
} from '@/lib/validation/accounting-chart-mapping';

import { AccountingChartImportPreviewPanel } from './accounting-chart-import-preview';
import { AccountingChartMappingStep } from './accounting-chart-mapping-step';
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
  const excluded = result.contasExcluidas ?? 0;
  return `Plano contábil importado: ${plural(result.contas, 'conta', 'contas')}, ${plural(
    result.contasNovas,
    'nova',
    'novas',
  )}, ${plural(result.contasAlteradas, 'alterada', 'alteradas')}, ${plural(
    result.contasAusentes,
    'não veio',
    'não vieram',
  )}${excluded > 0 ? `; ${plural(excluded, 'linha ficou', 'linhas ficaram')} de fora` : ''}.`;
}

/**
 * O vínculo pela coluna de CNPJ/CPF da própria planilha (86e3ydjzy): desligado por
 * padrão; ligado, o tipo do LOTE é escolhido aqui (nunca inferido da posição da conta).
 */
function DocumentLinkChoice({
  found,
  enabled,
  onEnabledChange,
  partyType,
  onPartyTypeChange,
  disabled,
}: {
  found: number;
  enabled: boolean;
  onEnabledChange: (next: boolean) => void;
  partyType: 'cliente' | 'fornecedor';
  onPartyTypeChange: (next: 'cliente' | 'fornecedor') => void;
  disabled: boolean;
}) {
  const label =
    found === 1
      ? 'Vincular a conta ao CNPJ/CPF que veio no arquivo'
      : `Vincular as ${found} contas ao CNPJ/CPF que vieram no arquivo`;
  return (
    <section
      aria-label="Vínculo pelos documentos da planilha"
      data-testid="accounting-chart-link-documents"
      className="bg-card space-y-3 rounded-lg border p-3 text-sm"
    >
      <div className="flex items-start gap-3">
        <Switch
          id="accounting-chart-link-documents-switch"
          checked={enabled}
          onCheckedChange={onEnabledChange}
          disabled={disabled}
          aria-describedby="accounting-chart-link-documents-help"
        />
        <div className="space-y-0.5">
          <label
            htmlFor="accounting-chart-link-documents-switch"
            className="cursor-pointer font-medium"
          >
            {label}
          </label>
          <p id="accounting-chart-link-documents-help" className="text-muted-foreground text-xs">
            A planilha tem uma coluna de CNPJ/CPF. Ligado, cada conta fica vinculada à pessoa (como
            na importação da relação do Domínio); documento inválido ou repetido fica sem vínculo e
            aparece no resultado.
          </p>
        </div>
      </div>
      {enabled && (
        <fieldset className="space-y-1" disabled={disabled}>
          <legend className="text-xs font-medium">As contas são de</legend>
          <div className="flex flex-wrap gap-4">
            {(['fornecedor', 'cliente'] as const).map((option) => (
              <label key={option} className="flex cursor-pointer items-center gap-2">
                <input
                  type="radio"
                  name="accounting-chart-link-type"
                  value={option}
                  checked={partyType === option}
                  onChange={() => onPartyTypeChange(option)}
                  className="accent-primary h-4 w-4"
                />
                {option === 'fornecedor' ? 'Fornecedores' : 'Clientes'}
              </label>
            ))}
          </div>
        </fieldset>
      )}
    </section>
  );
}

/** O rótulo do "Confirmar": com linha de fora, diz quantas ficam de fora (86e3ydjze). */
function confirmLabel(
  preview: AccountingChartImportPreview,
  hasPlan: boolean,
  isPending: boolean,
): string {
  if (isPending) return 'Importando…';
  const excluded = preview.linhasComProblema.length;
  if (excluded > 0) {
    return excluded === 1
      ? 'Importar sem a linha com problema'
      : `Importar sem as ${excluded} linhas`;
  }
  return hasPlan ? 'Confirmar reimportação' : 'Confirmar importação';
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
  // A prévia é o que importa no segundo passo: ela entra no lugar do modelo e vem
  // para a vista (o rodapé com "Confirmar" fica fixo; sem isto, em 390px a prévia
  // ficava abaixo da dobra e a pessoa confirmaria sem vê-la).
  const previewRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (preview !== null) previewRef.current?.scrollIntoView?.({ block: 'start' });
  }, [preview]);

  // A etapa de mapeamento (86e3ydjz4): o perfil das colunas que o servidor mandou e o
  // mapeamento que valeu na prévia (o mesmo vai na importação).
  const [mappingStep, setMappingStep] = useState<ChartMappingRequired | null>(null);
  const [mapping, setMapping] = useState<AccountingChartMappingPayload | null>(null);
  // 86e3ydjzy: vincular as contas ao CNPJ/CPF da coluna de documento da planilha. Só
  // por escolha (desligado por padrão), com o tipo do lote pré-marcado "fornecedor".
  const [linkDocuments, setLinkDocuments] = useState(false);
  const [linkAs, setLinkAs] = useState<'cliente' | 'fornecedor'>('fornecedor');

  const form = useForm<AccountingChartImportValues>({
    resolver: zodResolver(accountingChartImportSchema),
    defaultValues: { file: null },
    mode: 'onSubmit',
  });
  const mappingForm = useForm<AccountingChartMappingValues>({
    resolver: zodResolver(accountingChartMappingSchema),
    defaultValues: { roles: [], tipoPelaHierarquia: false },
    mode: 'onSubmit',
  });

  const isPending = importMutation.isPending || previewMutation.isPending;

  function onSubmit(values: AccountingChartImportValues) {
    const file = values.file;
    if (!(file instanceof File)) return;
    if (preview !== null) {
      void send(file);
      return;
    }
    if (mappingStep !== null) {
      void mappingForm.handleSubmit((mapped) => review(file, toMappingPayload(mapped)))();
      return;
    }
    void review(file, null);
  }

  function resetSteps() {
    setRefusal(null);
    setPreview(null);
    setMappingStep(null);
    setMapping(null);
    setLinkDocuments(false);
  }

  function handleFailure(err: unknown, fallback: string) {
    if (readAccountingChartRefusal(err) !== null) {
      setRefusal(err);
      return;
    }
    toast.error(err instanceof ApiError ? err.userMessage : fallback);
  }

  async function review(file: File, nextMapping: AccountingChartMappingPayload | null) {
    setRefusal(null);
    try {
      const result = await previewMutation.mutateAsync({ file, mapping: nextMapping });
      setMapping(nextMapping);
      setPreview(result);
    } catch (err) {
      const required = readChartMappingRequired(err);
      if (required !== null) {
        const roles = Array.from({ length: required.columns.length }, () => suggestedRole(null));
        for (const column of required.columns)
          roles[column.indice] = suggestedRole(column.papelSugerido);
        mappingForm.reset({ roles, tipoPelaHierarquia: false });
        setMappingStep(required);
        return;
      }
      handleFailure(err, 'Não foi possível ler a planilha do plano contábil.');
    }
  }

  async function send(file: File) {
    setRefusal(null);
    try {
      // 86e3ydjze: as linhas com problema que a prévia mostrou e a pessoa confirmou deixar
      // de fora; o servidor só grava se forem EXATAMENTE as que ele calcula agora.
      const excluded = preview?.linhasComProblema.map((item) => item.linha) ?? [];
      const offersLink = (preview?.documentosEncontrados ?? 0) > 0;
      const result = await importMutation.mutateAsync({
        file,
        mapping,
        excludedLines: excluded.length > 0 ? excluded : null,
        linkDocumentsAs: offersLink && linkDocuments ? linkAs : null,
      });
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
            conta de cada categoria. Linha com problema só fica de fora se você confirmar na
            revisão.
          </SheetDescription>
        </SheetHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col"
            noValidate
          >
            <SheetBody className="space-y-5">
              {preview === null && mappingStep === null && (
                <AccountingChartModel headingId="accounting-chart-import-model" />
              )}

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
                          resetSteps();
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {preview === null && mappingStep !== null && (
                <AccountingChartMappingStep
                  columns={mappingStep.columns}
                  message={mappingStep.userMessage}
                  form={mappingForm}
                  disabled={isPending}
                />
              )}

              {hasPlan && preview === null && mappingStep === null && (
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

              {preview !== null && (
                <div ref={previewRef}>
                  <AccountingChartImportPreviewPanel preview={preview} />
                </div>
              )}

              {preview !== null && (preview.documentosEncontrados ?? 0) > 0 && (
                <DocumentLinkChoice
                  found={preview.documentosEncontrados ?? 0}
                  enabled={linkDocuments}
                  onEnabledChange={setLinkDocuments}
                  partyType={linkAs}
                  onPartyTypeChange={setLinkAs}
                  disabled={isPending}
                />
              )}

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
                      {confirmLabel(preview, hasPlan, isPending)}
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
                    {isPending
                      ? 'Lendo a planilha…'
                      : mappingStep !== null
                        ? 'Continuar'
                        : 'Revisar mudanças'}
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
