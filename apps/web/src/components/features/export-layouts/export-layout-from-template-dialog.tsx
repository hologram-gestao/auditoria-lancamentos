'use client';

/**
 * "Criar a partir do modelo Domínio" — Sprint 13 (FRONT 13.5 / R1).
 *
 * Uma ação só: o layout da organização nasce com EXATAMENTE os parâmetros do
 * modelo declarado no código (`GET /export-layout-templates`). Sem editor
 * visual (fora de escopo): quem precisar de outro formato cria versão nova pela
 * API.
 *
 * Erros:
 *   - 409 `LAYOUT_NOME_DUPLICADO` → erro no campo Nome (é onde se corrige).
 *   - Demais erros tipados (409 de organização suspensa, 404 de modelo, 403,
 *     422) → caixa de erro DENTRO do diálogo com o `userMessage`: é erro que
 *     orienta, e toast some antes de ser lido.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import {
  OrganizationLoadError,
  organizationOptionLabel,
  useOrganizationOptions,
} from '@/components/features/organizations/organization-select';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useCreateExportLayoutFromTemplate,
  useExportLayoutTemplates,
} from '@/hooks/use-export-layouts';
import { ApiError, NetworkError } from '@/lib/api/client';
import {
  makeExportLayoutFromTemplateSchema,
  type ExportLayoutFromTemplateFormValues,
} from '@/lib/validation/export-layouts';

/** Chave do modelo Domínio no backend (`DOMINIO_TEMPLATE.key`). */
export const DOMINIO_TEMPLATE_KEY = 'dominio_lancamentos_csv';

interface ExportLayoutFromTemplateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** A plataforma escolhe a organização de destino (obrigatório). */
  requireOrganization: boolean;
}

export function ExportLayoutFromTemplateDialog({
  open,
  onOpenChange,
  requireOrganization,
}: ExportLayoutFromTemplateDialogProps) {
  const createMutation = useCreateExportLayoutFromTemplate();
  const templatesQuery = useExportLayoutTemplates({ enabled: open });
  const template = templatesQuery.data?.find((t) => t.key === DOMINIO_TEMPLATE_KEY) ?? null;
  const {
    organizations,
    isLoading: organizationsLoading,
    isError: organizationsError,
  } = useOrganizationOptions({ enabled: requireOrganization && open, activeOnly: true });
  const [serverError, setServerError] = useState<string | null>(null);

  const schema = useMemo(
    () => makeExportLayoutFromTemplateSchema({ requireOrganization }),
    [requireOrganization],
  );
  const form = useForm<ExportLayoutFromTemplateFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', organization_id: '' },
    mode: 'onSubmit',
  });

  useEffect(() => {
    if (open) {
      form.reset({ name: '', organization_id: '' });
      setServerError(null);
      createMutation.reset();
    }
    // form/mutation são estáveis; rodar só quando abrir.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function onSubmit(values: ExportLayoutFromTemplateFormValues) {
    setServerError(null);
    try {
      const created = await createMutation.mutateAsync({
        templateKey: DOMINIO_TEMPLATE_KEY,
        // Vazio = o nome do modelo (o backend decide; `null` é o "omitido").
        name: values.name ? values.name : null,
        ...(values.organization_id ? { organizationId: values.organization_id } : {}),
      });
      toast.success(`Layout "${created.name}" criado.`);
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'LAYOUT_NOME_DUPLICADO') {
        form.setError('name', { type: 'server', message: err.userMessage });
        return;
      }
      setServerError(
        err instanceof ApiError || err instanceof NetworkError
          ? err.userMessage
          : 'Não foi possível criar o layout. Tente novamente.',
      );
    }
  }

  const isSubmitting = createMutation.isPending;
  const templateUnavailable = templatesQuery.isError || (templatesQuery.isSuccess && !template);

  return (
    // Com o POST em andamento, Esc e clique fora não fecham: o diálogo sumiria com
    // a criação ainda sem resposta, e o erro dela não teria onde aparecer.
    <Dialog open={open} onOpenChange={(next) => !isSubmitting && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Criar a partir do modelo Domínio</DialogTitle>
          <DialogDescription>
            O layout nasce com os parâmetros do importador de lançamentos contábeis do Domínio e
            fica disponível para gerar o arquivo de todos os clientes da organização.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            {templatesQuery.isLoading ? (
              <div className="bg-muted h-16 w-full animate-pulse rounded-md" aria-hidden="true" />
            ) : template ? (
              <div className="bg-muted/40 space-y-1 rounded-md border p-3 text-sm">
                <p className="font-medium">{template.name}</p>
                <p className="text-muted-foreground">{template.description}</p>
              </div>
            ) : null}

            {templateUnavailable && (
              <p role="alert" className="text-destructive text-sm">
                Não foi possível carregar o modelo Domínio. Feche e tente de novo em instantes.
              </p>
            )}

            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome do layout (opcional)</FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      autoComplete="off"
                      disabled={isSubmitting}
                      placeholder={template?.name ?? 'Domínio: lançamentos contábeis (CSV)'}
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>Em branco, o layout recebe o nome do modelo.</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {requireOrganization && (
              <FormField
                control={form.control}
                name="organization_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Organização</FormLabel>
                    <Select
                      value={field.value ?? ''}
                      onValueChange={field.onChange}
                      disabled={isSubmitting || organizationsLoading}
                    >
                      <FormControl>
                        <SelectTrigger aria-label="Organização do layout">
                          <SelectValue placeholder="Selecione a organização" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {organizations.map((o) => (
                          <SelectItem key={o.id} value={o.id}>
                            {organizationOptionLabel(o)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {organizationsError && <OrganizationLoadError />}
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            {serverError && (
              <div
                role="alert"
                className="bg-destructive-muted text-destructive ring-destructive/30 rounded-md p-3 text-sm ring-1 ring-inset"
              >
                {serverError}
              </div>
            )}

            <DialogFooter className="gap-2 sm:gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={isSubmitting || !template}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Criar layout
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
