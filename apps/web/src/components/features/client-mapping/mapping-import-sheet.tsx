'use client';

/**
 * Gaveta "Importar de-para" (Sprint 12 — FRONT 12.7 / R6, portabilidade).
 *
 * Dois passos, e o segundo só existe depois do primeiro:
 *
 *   1. **Prévia obrigatória** — a planilha (a mesma que "Exportar" gera) e a
 *      competência de início vão ao servidor, que NÃO grava nada e devolve
 *      criadas · alteradas · ignoradas e as linhas RECUSADAS com o motivo. A
 *      linha recusada não derruba o lote: ela é listada e o resto segue.
 *   2. **Aplicar** — com a mesma planilha e o mesmo início. Se a importação muda
 *      decisões CONFIRMADAS por pessoa, a gaveta exige confirmação explícita e
 *      diz que isso cria vigência nova (nunca sobrescreve a vigente). Os 409 de
 *      vigência viram estado aqui dentro, como na decisão individual.
 *
 * Trocar o arquivo ou o início descarta a prévia: aplicar precisa ser
 * exatamente o que a pessoa viu.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

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
import { Label } from '@/components/ui/label';
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
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useApplyMappingImport, usePreviewMappingImport } from '@/hooks/use-client-mapping';
import { ApiError } from '@/lib/api/client';
import type { MappingDestination, MappingImportPreview } from '@/lib/contracts';
import { formatReferenceMonth } from '@/lib/format';
import {
  IMPORT_REJECT_REASON_LABELS,
  mappingImportFormSchema,
  type MappingImportFormValues,
} from '@/lib/validation/client-mapping';

import {
  readVigenciaConflict,
  VigenciaConflictNotice,
  VigenciaExplanation,
  type VigenciaConflict,
} from './vigencia';

interface MappingImportSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  destination: MappingDestination;
  serverCompetence: string;
}

export function MappingImportSheet({
  open,
  onOpenChange,
  clientId,
  destination,
  serverCompetence,
}: MappingImportSheetProps) {
  const previewMutation = usePreviewMappingImport(clientId, destination.type);
  const applyMutation = useApplyMappingImport(clientId, destination.type);
  const [preview, setPreview] = useState<MappingImportPreview | null>(null);
  const [acceptAlterConfirmed, setAcceptAlterConfirmed] = useState(false);
  const [conflict, setConflict] = useState<VigenciaConflict | null>(null);

  const form = useForm<MappingImportFormValues>({
    resolver: zodResolver(mappingImportFormSchema),
    defaultValues: { file: null, effectiveFrom: serverCompetence },
  });

  function resetAll() {
    form.reset({ file: null, effectiveFrom: serverCompetence });
    setPreview(null);
    setAcceptAlterConfirmed(false);
    setConflict(null);
    previewMutation.reset();
    applyMutation.reset();
  }

  useEffect(() => {
    if (open) resetAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, serverCompetence]);

  const file = form.watch('file');
  const effectiveFrom = form.watch('effectiveFrom');
  // A prévia vale para ESTE arquivo e ESTE início — trocar qualquer um descarta.
  useEffect(() => {
    setPreview(null);
    setAcceptAlterConfirmed(false);
    setConflict(null);
  }, [file, effectiveFrom]);

  const isBusy = previewMutation.isPending || applyMutation.isPending;
  const retroactivePending = conflict?.kind === 'retroactive';
  const writes = preview ? preview.created + preview.altered : 0;
  const needsAlterConfirmation = (preview?.altersConfirmed ?? 0) > 0;

  async function onPreview(values: MappingImportFormValues) {
    if (!(values.file instanceof File)) return;
    try {
      const result = await previewMutation.mutateAsync({
        file: values.file,
        effectiveFrom: values.effectiveFrom,
      });
      setPreview(result);
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.userMessage
          : 'Não foi possível gerar a prévia da importação.',
      );
    }
  }

  async function onApply() {
    const values = form.getValues();
    if (!(values.file instanceof File) || !preview) return;
    try {
      const result = await applyMutation.mutateAsync({
        file: values.file,
        effectiveFrom: values.effectiveFrom,
        confirmRetroactive: retroactivePending,
      });
      const applied = result.result;
      toast.success(
        applied
          ? `Importação aplicada: ${applied.created} ${applied.created === 1 ? 'vigência nova' : 'vigências novas'} a partir de ${formatReferenceMonth(applied.effectiveFrom)}.`
          : 'Importação aplicada.',
      );
      onOpenChange(false);
    } catch (err) {
      const vigencia = readVigenciaConflict(err);
      if (vigencia) {
        setConflict(vigencia);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível aplicar a importação.',
      );
    }
  }

  return (
    <Sheet open={open} onOpenChange={(next) => !isBusy && onOpenChange(next)}>
      <SheetContent side="right" className="flex flex-col p-0">
        <SheetHeader>
          <SheetTitle>Importar de-para</SheetTitle>
          <SheetDescription>
            Planilha exportada de {destination.name}. As linhas casam pelo CÓDIGO da categoria e do
            alvo — a coluna de nome é ignorada.
          </SheetDescription>
        </SheetHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onPreview)}
            className="flex min-h-0 flex-1 flex-col"
            noValidate
          >
            <SheetBody className="space-y-5">
              <FormField
                control={form.control}
                name="file"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Planilha (.xlsx)</FormLabel>
                    <FormControl>
                      <Input
                        type="file"
                        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        disabled={isBusy}
                        name={field.name}
                        ref={field.ref}
                        onBlur={field.onBlur}
                        onChange={(e) => field.onChange(e.target.files?.[0] ?? null)}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="effectiveFrom"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Competência de início</FormLabel>
                    <FormControl>
                      <Input type="month" lang="pt-BR" disabled={isBusy} {...field} />
                    </FormControl>
                    <FormDescription>
                      Padrão: a competência corrente ({formatReferenceMonth(serverCompetence)}).
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {preview && <ImportPreviewBlock preview={preview} />}

              {preview && writes > 0 && (
                <VigenciaExplanation
                  effectiveFrom={effectiveFrom}
                  serverCompetence={serverCompetence}
                  hasCurrentDecision={preview.altered > 0}
                />
              )}

              {preview && needsAlterConfirmation && (
                <div className="bg-warning-muted text-warning ring-warning/30 space-y-3 rounded-lg p-3 text-sm ring-1 ring-inset">
                  <p className="font-medium">
                    {preview.altersConfirmed}{' '}
                    {preview.altersConfirmed === 1
                      ? 'decisão confirmada por pessoa muda'
                      : 'decisões confirmadas por pessoa mudam'}{' '}
                    com esta importação.
                  </p>
                  <div className="flex items-center gap-2">
                    <Switch
                      id="mapping-import-accept-altered"
                      checked={acceptAlterConfirmed}
                      onCheckedChange={setAcceptAlterConfirmed}
                      disabled={isBusy}
                    />
                    <Label htmlFor="mapping-import-accept-altered" className="cursor-pointer">
                      Alterar as confirmadas, com vigência nova a partir de{' '}
                      {formatReferenceMonth(effectiveFrom)}
                    </Label>
                  </div>
                </div>
              )}

              {conflict && <VigenciaConflictNotice conflict={conflict} />}
            </SheetBody>

            <SheetFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isBusy}
              >
                Cancelar
              </Button>
              {preview === null ? (
                <Button type="submit" disabled={isBusy}>
                  {previewMutation.isPending && (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  )}
                  Gerar prévia
                </Button>
              ) : (
                <Button
                  type="button"
                  onClick={() => void onApply()}
                  disabled={
                    isBusy ||
                    writes === 0 ||
                    (needsAlterConfirmation && !acceptAlterConfirmed) ||
                    conflict?.kind === 'materialized'
                  }
                >
                  {applyMutation.isPending && (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  )}
                  {retroactivePending ? 'Confirmar importação retroativa' : 'Aplicar importação'}
                </Button>
              )}
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}

function ImportPreviewBlock({ preview }: { preview: MappingImportPreview }) {
  const rejected = preview.rejected;
  return (
    <section aria-labelledby="mapping-import-preview-heading" className="space-y-3">
      <h3 id="mapping-import-preview-heading" className="text-sm font-semibold">
        Prévia da importação
      </h3>
      <dl className="grid grid-cols-3 gap-2 text-center" data-testid="mapping-import-preview">
        <div className="bg-muted rounded-lg p-2">
          <dt className="text-muted-foreground text-xs">Criadas</dt>
          <dd className="text-lg font-semibold tabular-nums">{preview.created}</dd>
        </div>
        <div className="bg-muted rounded-lg p-2">
          <dt className="text-muted-foreground text-xs">Alteradas</dt>
          <dd className="text-lg font-semibold tabular-nums">{preview.altered}</dd>
        </div>
        <div className="bg-muted rounded-lg p-2">
          <dt className="text-muted-foreground text-xs">Ignoradas</dt>
          <dd className="text-lg font-semibold tabular-nums">{preview.ignored}</dd>
        </div>
      </dl>
      {preview.created + preview.altered === 0 && (
        <p className="text-muted-foreground text-sm">
          Nada a gravar: a planilha não traz decisão nova nem alterada.
        </p>
      )}
      {rejected.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium">
            {rejected.length} {rejected.length === 1 ? 'linha recusada' : 'linhas recusadas'} (as
            demais seguem)
          </p>
          <TableCard className="max-h-64">
            <Table fill scrollRegionLabel="Lista de linhas recusadas (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead>Linha</TableHead>
                  <TableHead>Categoria</TableHead>
                  <TableHead>Alvo</TableHead>
                  <TableHead>Motivo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rejected.map((line) => (
                  <TableRow key={`${line.line}:${line.categoryCode}`}>
                    <TableCell className="tabular-nums">{line.line}</TableCell>
                    <TableCell className="tabular-nums">{line.categoryCode}</TableCell>
                    <TableCell className="tabular-nums">{line.targetCode ?? '—'}</TableCell>
                    <TableCell className="min-w-40">
                      {IMPORT_REJECT_REASON_LABELS[line.reason] ?? line.reason}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
        </div>
      )}
    </section>
  );
}
