'use client';

/**
 * "Guia para configurar no Omie" (86e3qhht4).
 *
 * Depois de importar o plano de contas na Omie, o contador diz à Omie, na TELA de
 * categorias da integração contábil, qual conta cada categoria recebe e como ela se
 * comporta. A Omie não importa esse vínculo por arquivo nem por API. O guia é a
 * planilha que ele abre ao lado da Omie e copia de cima para baixo, gerada do
 * de-para no destino `conta_contabil`.
 *
 * A plataforma guarda só a conta: o comportamento é um padrão escolhido aqui e
 * repetido em toda linha de "Configurar" (a exceção se ajusta na própria Omie).
 *
 * Quem chama mostra o botão só no destino `conta_contabil` e para cliente com
 * conexão Omie (`hasOmieConnection`). Sem permissão especial, como "Exportar".
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Download, FileSpreadsheet, Loader2 } from 'lucide-react';
import { useState } from 'react';
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
import { Form, FormField, FormItem, FormMessage } from '@/components/ui/form';
import { useExportOmieCategoriesGuide } from '@/hooks/use-client-mapping';
import { ApiError, NetworkError } from '@/lib/api/client';
import { triggerBrowserDownload } from '@/lib/download';
import { cn } from '@/lib/utils';
import {
  DEFAULT_OMIE_CATEGORY_BEHAVIOR,
  OMIE_CATEGORY_BEHAVIORS,
  OMIE_CATEGORY_BEHAVIOR_LABELS,
  omieCategoriesGuideSchema,
  type OmieCategoriesGuideFormValues,
} from '@/lib/validation/omie-categories-guide';

export function OmieCategoriesGuideAction({ clientId }: { clientId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button type="button" variant="outline" onClick={() => setOpen(true)}>
        <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />
        Guia para configurar no Omie
      </Button>
      {open && <OmieCategoriesGuideDialog open={open} onOpenChange={setOpen} clientId={clientId} />}
    </>
  );
}

function OmieCategoriesGuideDialog({
  open,
  onOpenChange,
  clientId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
}) {
  const exportMutation = useExportOmieCategoriesGuide(clientId);
  const form = useForm<OmieCategoriesGuideFormValues>({
    resolver: zodResolver(omieCategoriesGuideSchema),
    defaultValues: { behavior: DEFAULT_OMIE_CATEGORY_BEHAVIOR },
  });
  const isPending = exportMutation.isPending;

  async function onSubmit(values: OmieCategoriesGuideFormValues) {
    try {
      const { blob, filename } = await exportMutation.mutateAsync(values.behavior);
      triggerBrowserDownload(blob, filename ?? 'guia-omie.xlsx');
      toast.success('Guia baixado. Abra ao lado da tela de categorias da Omie.');
      onOpenChange(false);
    } catch (err) {
      toast.error(
        err instanceof ApiError || err instanceof NetworkError
          ? err.userMessage
          : 'Não foi possível gerar o guia.',
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Guia para configurar no Omie</DialogTitle>
          <DialogDescription>
            Uma planilha com a conta contábil de cada categoria, na ordem da tela de categorias da
            integração contábil da Omie, para copiar de cima para baixo.
          </DialogDescription>
        </DialogHeader>

        <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-sm">
          <li>
            <span className="text-foreground font-medium">Configurar:</span> as categorias com conta
            decidida, com a conta e o comportamento escolhido abaixo.
          </li>
          <li>
            <span className="text-foreground font-medium">Não gerar lançamento:</span> as categorias
            marcadas como &ldquo;não mapear&rdquo; no de-para.
          </li>
          <li>
            <span className="text-foreground font-medium">Sem decisão:</span> as que ainda precisam
            de uma conta aqui, antes de configurar na Omie.
          </li>
        </ul>

        <Form {...form}>
          <form
            id="omie-categories-guide-form"
            onSubmit={(event) => void form.handleSubmit(onSubmit)(event)}
            noValidate
          >
            <FormField
              control={form.control}
              name="behavior"
              render={({ field }) => (
                <FormItem>
                  <fieldset className="space-y-2" disabled={isPending}>
                    <legend className="text-sm font-medium leading-none">
                      Comportamento padrão na Omie
                    </legend>
                    <p className="text-muted-foreground text-sm">
                      Vai em todas as linhas de &ldquo;Configurar&rdquo;. A exceção você ajusta na
                      própria Omie.
                    </p>
                    <div className="flex flex-col gap-2">
                      {OMIE_CATEGORY_BEHAVIORS.map((value) => {
                        const checked = field.value === value;
                        return (
                          <label
                            key={value}
                            className={cn(
                              'has-[:focus-visible]:ring-ring flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm has-[:focus-visible]:ring-2',
                              checked
                                ? 'border-primary bg-accent text-accent-foreground'
                                : 'bg-background',
                            )}
                          >
                            <input
                              type="radio"
                              name={field.name}
                              value={value}
                              checked={checked}
                              onChange={() => field.onChange(value)}
                              onBlur={field.onBlur}
                              className="accent-primary h-4 w-4 shrink-0"
                            />
                            {OMIE_CATEGORY_BEHAVIOR_LABELS[value]}
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                  <FormMessage />
                </FormItem>
              )}
            />
          </form>
        </Form>

        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            Cancelar
          </Button>
          <Button type="submit" form="omie-categories-guide-form" disabled={isPending}>
            {isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="h-4 w-4" aria-hidden="true" />
            )}
            {isPending ? 'Gerando…' : 'Baixar guia'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
