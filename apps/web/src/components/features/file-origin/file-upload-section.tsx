'use client';

/**
 * Seção "Enviar arquivo" da aba Origem por arquivo (Sprint 14 — FRONT 14.6 /
 * R1 · R2 · R5).
 *
 * A rotina do segundo mês em diante: competência, total opcional e o arquivo —
 * **um passo só** quando há mapeamento salvo (o card "Será aplicado" mostra o
 * que vai ser lido; "Enviar arquivo" processa). Sem mapeamento, o envio
 * CONDUZ ao editor (FRONT 14.5) já com o arquivo em mãos, e volta para enviar.
 *
 * Toda recusa do servidor vira estado PRÓPRIO dentro da seção
 * (`FileRefusalNotice`: coluna divergente nomeada, linha × motivo, totais lado
 * a lado…) — só código desconhecido cai no toast. Sucesso mostra as contagens
 * e o link para a prévia do de-para daquela competência, no toast E no bloco
 * (o toast some; o bloco fica até o próximo envio).
 *
 * Gating (§4.9): `upload_client_file` é dos 5 papéis — o operador envia, e não
 * vê botão de configurar; cliente encerrado e conexão `arquivo` fora do ar
 * escondem a ação, com o estado explicado.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { ArrowRight, CheckCircle2, Loader2, Settings2, Upload } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { mappingPreviewPath } from '@/components/features/navigation/nav-items';
import { OriginStateBlock } from '@/components/shared/origin-state-notice';
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
import { useProcessFile } from '@/hooks/use-client-file-origin';
import { ApiError } from '@/lib/api/client';
import type { FileProcessResult, InputMapping } from '@/lib/contracts';
import { isFileRefusal } from '@/lib/file-origin-errors';
import { formatReferenceMonth } from '@/lib/format';
import { centsFromTyped, centsToDecimalString, formatCentsForInput } from '@/lib/money-input';
import { isOriginError, type OriginErrorCode } from '@/lib/origin-state';
import { fileUploadFormSchema, type FileUploadFormValues } from '@/lib/validation/file-origin';

import { FILE_ACCEPT, FileRefusalNotice } from './file-refusal-notice';
import { MappingSummary } from './mapping-summary';

type Outcome =
  | { kind: 'success'; result: FileProcessResult }
  | { kind: 'refused'; error: unknown }
  | null;

interface FileUploadSectionProps {
  clientId: string;
  /** `null` = sem mapeamento salvo; `undefined` = não sabemos (carregando ou GET falhou). */
  mapping: InputMapping | null | undefined;
  /** O GET do mapeamento falhou: erro com "Tentar novamente", nunca "sem mapeamento". */
  mappingFailed: boolean;
  onRetryMapping: () => void;
  /** `upload_client_file` E cliente aberto. */
  canUpload: boolean;
  /** `manage_input_mapping` E cliente aberto — decide o botão de configurar. */
  canManage: boolean;
  isClosed: boolean;
  /** A conexão `arquivo` está fora do ar (`inativa`/`erro`)? O servidor responderia 409. */
  originCode: OriginErrorCode | null;
  competence: string;
  onCompetenceChange: (competence: string) => void;
  /**
   * Abre o editor já com o arquivo escolhido (sem mapeamento, ou "Revisar
   * mapeamento" de uma recusa); `afterSave` processa esse arquivo assim que o
   * mapeamento for salvo — o envio prossegue sem pedir o arquivo de novo.
   */
  onConfigureMapping: (file: File | null, afterSave: () => void) => void;
  /** Âncora da lista de processados, para a recusa `ARQUIVO_JA_PROCESSADO`. */
  importsHref: string;
}

export function FileUploadSection({
  clientId,
  mapping,
  mappingFailed,
  onRetryMapping,
  canUpload,
  canManage,
  isClosed,
  originCode,
  competence,
  onCompetenceChange,
  onConfigureMapping,
  importsHref,
}: FileUploadSectionProps) {
  const router = useRouter();
  const processMutation = useProcessFile(clientId);
  const [outcome, setOutcome] = useState<Outcome>(null);

  const form = useForm<FileUploadFormValues>({
    resolver: zodResolver(fileUploadFormSchema),
    defaultValues: { competence, declaredTotalCents: '', file: null },
    mode: 'onSubmit',
  });

  const hasMapping = mapping !== null && mapping !== undefined;
  const isPending = processMutation.isPending;
  const canAct = canUpload && originCode === null;

  // Processa com o formulário ATUAL, revalidado — é o que roda depois que o
  // editor salva o mapeamento (a competência e o arquivo seguem os escolhidos).
  const processCurrent = () => void form.handleSubmit(processFile)();

  function onSubmit(values: FileUploadFormValues) {
    if (!(values.file instanceof File)) return;
    // Sem saber se há mapeamento, nada sai daqui (o botão nem aparece).
    if (mapping === undefined) return;
    if (!hasMapping) {
      // Sem mapeamento não há o que aplicar: o editor abre com o arquivo em
      // mãos e, salvo o mapeamento, o envio prossegue sozinho.
      onConfigureMapping(values.file, processCurrent);
      return;
    }
    void processFile(values);
  }

  async function processFile(values: FileUploadFormValues) {
    if (!(values.file instanceof File)) return;
    setOutcome(null);
    try {
      const result = await processMutation.mutateAsync({
        file: values.file,
        competence: values.competence,
        declaredTotal: centsToDecimalString(values.declaredTotalCents),
      });
      setOutcome({ kind: 'success', result });
      const target = mappingPreviewPath(clientId, result.competence);
      toast.success(successMessage(result), {
        action: {
          label: `Ver de-para de ${formatReferenceMonth(result.competence)}`,
          onClick: () => router.push(target),
        },
      });
    } catch (err) {
      if (isFileRefusal(err) || isOriginError(err)) {
        setOutcome({ kind: 'refused', error: err });
        return;
      }
      // Código desconhecido: o único caso em que o toast genérico é aceitável.
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível enviar o arquivo.');
    }
  }

  return (
    <section
      aria-labelledby="file-upload-heading"
      className="bg-card space-y-4 rounded-lg border p-4"
      data-testid="file-upload-section"
    >
      <div className="space-y-1">
        <h3 id="file-upload-heading" className="text-base font-semibold">
          Enviar arquivo
        </h3>
        <p className="text-muted-foreground text-sm">
          A planilha ou o extrato do mês. Ou o arquivo entra inteiro, ou nada entra: qualquer linha
          inválida recusa o envio com o motivo.
        </p>
      </div>

      {isClosed ? (
        <p className="text-muted-foreground text-sm" role="status">
          Cliente encerrado: o envio de arquivos está indisponível.
        </p>
      ) : originCode !== null ? (
        <OriginStateBlock code={originCode} clientId={clientId} />
      ) : !canUpload ? (
        <p className="text-muted-foreground text-sm" role="status">
          Você não tem acesso para enviar arquivos deste cliente.
        </p>
      ) : (
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <FormField
                control={form.control}
                name="competence"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Competência</FormLabel>
                    <FormControl>
                      <Input
                        type="month"
                        lang="pt-BR"
                        disabled={isPending}
                        {...field}
                        onChange={(e) => {
                          field.onChange(e);
                          onCompetenceChange(e.target.value);
                        }}
                      />
                    </FormControl>
                    <FormDescription>O mês a que o arquivo se refere.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="declaredTotalCents"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      Total do arquivo{' '}
                      <span className="text-muted-foreground font-normal">(opcional)</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        inputMode="decimal"
                        autoComplete="off"
                        placeholder="0,00"
                        disabled={isPending}
                        name={field.name}
                        ref={field.ref}
                        onBlur={field.onBlur}
                        // RAW (centavos) no estado, formatação só aqui na exibição.
                        value={formatCentsForInput(field.value)}
                        onChange={(e) => field.onChange(centsFromTyped(e.target.value))}
                      />
                    </FormControl>
                    <FormDescription>
                      Soma dos valores com sinal. Se não bater com o arquivo, o envio é recusado.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="file"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Arquivo (.csv ou .xlsx)</FormLabel>
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
                          setOutcome(null);
                        }}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            {mapping === undefined && mappingFailed ? (
              <div
                role="alert"
                data-testid="upload-mapping-error"
                className="bg-destructive-muted text-destructive space-y-2 rounded-lg p-3 text-sm"
              >
                <p>
                  Não foi possível carregar o mapeamento de colunas, e sem ele não dá para saber
                  como ler o arquivo. O envio fica indisponível até o mapeamento carregar.
                </p>
                <Button type="button" variant="outline" size="sm" onClick={onRetryMapping}>
                  Tentar novamente
                </Button>
              </div>
            ) : mapping === undefined ? (
              <div className="bg-muted h-16 animate-pulse rounded-lg" aria-hidden="true" />
            ) : hasMapping ? (
              <div
                className="bg-muted/50 space-y-2 rounded-lg border p-3"
                data-testid="mapping-will-apply"
              >
                <p className="text-sm font-medium">Será aplicado</p>
                <MappingSummary mapping={mapping} compact />
              </div>
            ) : (
              <div
                role="status"
                data-testid="upload-needs-mapping"
                className="bg-info-muted text-info ring-info/30 space-y-1 rounded-lg p-3 text-sm ring-1 ring-inset"
              >
                <p className="font-medium">Este cliente ainda não tem mapeamento de colunas</p>
                <p>
                  {canManage
                    ? 'Escolha o arquivo e clique em "Configurar mapeamento e enviar": as colunas dele aparecem no editor, e o envio segue depois de salvar.'
                    : 'Sem o mapeamento o arquivo não processa. Peça a alguém da equipe com acesso de configuração para criá-lo.'}
                </p>
              </div>
            )}

            {mapping !== undefined && (hasMapping || canManage) && (
              <Button type="submit" disabled={isPending || !canAct}>
                {isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : hasMapping ? (
                  <Upload className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <Settings2 className="h-4 w-4" aria-hidden="true" />
                )}
                {isPending
                  ? 'Enviando…'
                  : hasMapping
                    ? 'Enviar arquivo'
                    : 'Configurar mapeamento e enviar'}
              </Button>
            )}
          </form>
        </Form>
      )}

      {outcome?.kind === 'success' && <SuccessBlock clientId={clientId} result={outcome.result} />}
      {outcome?.kind === 'refused' && (
        <FileRefusalNotice
          error={outcome.error}
          clientId={clientId}
          onReviewMapping={
            canManage && mapping !== undefined
              ? () => onConfigureMapping(form.getValues('file'), processCurrent)
              : undefined
          }
          importsHref={importsHref}
        />
      )}
    </section>
  );
}

function successMessage(result: FileProcessResult): string {
  const rows = `${result.rows} ${result.rows === 1 ? 'linha' : 'linhas'}`;
  const categories = `${result.categoriesCreated} ${
    result.categoriesCreated === 1 ? 'categoria nova' : 'categorias novas'
  }`;
  return `Arquivo de ${formatReferenceMonth(result.competence)} processado: ${rows}, ${categories}.`;
}

function SuccessBlock({ clientId, result }: { clientId: string; result: FileProcessResult }) {
  return (
    <div
      role="status"
      data-testid="upload-success"
      className="bg-success-muted text-success ring-success/30 space-y-3 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <p className="flex items-start gap-2 font-medium">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{successMessage(result)}</span>
      </p>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Linhas" value={result.rows} />
        <Stat label="Colunas reconhecidas" value={result.columnsRecognized} />
        <Stat label="Categorias novas" value={result.categoriesCreated} />
        <Stat label="Ausentes no reenvio" value={result.absent} />
      </dl>
      <Button asChild variant="outline" size="sm">
        <Link href={mappingPreviewPath(clientId, result.competence)}>
          Ver de-para de {formatReferenceMonth(result.competence)}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </Button>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
