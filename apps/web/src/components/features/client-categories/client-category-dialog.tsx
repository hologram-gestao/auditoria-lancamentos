'use client';

/**
 * Diálogo de criar/editar categoria de cliente (86e34jd8m) — um só componente
 * para os dois modos: `category === null` cria, senão edita.
 *
 * Erros:
 *   - 409 `CONFLICT` → nome já existe (sem distinção de caixa) → erro inline no
 *     campo, sem toast redundante.
 *   - Demais → toast com o `userMessage` do backend.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { useForm, useWatch } from 'react-hook-form';
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
import { useCreateClientCategory, useUpdateClientCategory } from '@/hooks/use-client-categories';
import { ApiError } from '@/lib/api/client';
import type { ClientCategoryItem } from '@/lib/api/client-categories';
import { isPlatformScoped } from '@/lib/authz';
import {
  makeClientCategorySchema,
  type ClientCategoryFormValues,
} from '@/lib/validation/client-categories';
import { useAuthStore } from '@/stores/auth';

import {
  CLIENT_CATEGORY_TONE_LABELS,
  CLIENT_CATEGORY_TONES,
  CategoryBadge,
} from './category-badge';

interface ClientCategoryDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = criar. */
  category: ClientCategoryItem | null;
}

export function ClientCategoryDialog({ open, onOpenChange, category }: ClientCategoryDialogProps) {
  const isEdit = category !== null;
  const createMutation = useCreateClientCategory();
  const updateMutation = useUpdateClientCategory(category?.id ?? '');

  // O catálogo é POR organização (86e36ecqz): a plataforma escolhe em qual a
  // categoria nasce. Na EDIÇÃO o campo não aparece — categoria não muda de
  // organização, e o `PATCH` do backend nem aceita o campo.
  const isPlatform = isPlatformScoped(useAuthStore((st) => st.user));
  const needsOrganization = isPlatform && !isEdit;
  const {
    organizations,
    isLoading: organizationsLoading,
    isError: organizationsError,
  } = useOrganizationOptions({
    enabled: needsOrganization && open,
    activeOnly: true,
  });
  const schema = useMemo(
    () => makeClientCategorySchema({ requireOrganization: needsOrganization }),
    [needsOrganization],
  );

  const form = useForm<ClientCategoryFormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '', tone: 'neutral', organization_id: '' },
    mode: 'onSubmit',
  });
  const watchedName = useWatch({ control: form.control, name: 'name' });
  const watchedTone = useWatch({ control: form.control, name: 'tone' });

  useEffect(() => {
    if (open) {
      form.reset(
        category
          ? { name: category.name, tone: toneOrNeutral(category.tone), organization_id: '' }
          : { name: '', tone: 'neutral', organization_id: '' },
      );
      createMutation.reset();
      updateMutation.reset();
    }
    // form/mutations são estáveis; rodar quando abrir ou o alvo mudar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, category]);

  async function onSubmit(values: ClientCategoryFormValues) {
    try {
      const { organization_id: organizationId, ...fields } = values;
      if (isEdit) {
        // O PATCH não tem `organization_id` (ClientCategoryUpdate): a categoria
        // não troca de organização, e mandar o campo seria pedir por algo que o
        // contrato não oferece.
        await updateMutation.mutateAsync(fields);
        toast.success('Categoria atualizada.');
      } else {
        await createMutation.mutateAsync({
          ...fields,
          ...(organizationId ? { organization_id: organizationId } : {}),
        });
        toast.success('Categoria criada.');
      }
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONFLICT') {
        form.setError('name', {
          type: 'server',
          message: 'Já existe uma categoria com este nome.',
        });
        return;
      }
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar a categoria.';
      toast.error(msg);
    }
  }

  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Editar categoria' : 'Nova categoria'}</DialogTitle>
          <DialogDescription>
            Categorias agrupam os clientes por nicho ou segmento. O nome aparece como chip na lista
            de clientes; o tom define a cor no tema.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome</FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      autoComplete="off"
                      disabled={isSubmitting}
                      placeholder="ex.: Fintech, Varejo, Saúde"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Só na CRIAÇÃO e só para a plataforma (86e36ed1d). */}
            {needsOrganization && (
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
                        <SelectTrigger aria-label="Organização da categoria">
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

            <FormField
              control={form.control}
              name="tone"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Tom</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    disabled={isSubmitting}
                  >
                    <FormControl>
                      <SelectTrigger aria-label="Tom da categoria">
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {CLIENT_CATEGORY_TONES.map((tone) => (
                        <SelectItem key={tone} value={tone}>
                          {CLIENT_CATEGORY_TONE_LABELS[tone]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Prévia:</span>
              <CategoryBadge name={watchedName?.trim() || 'Categoria'} tone={watchedTone} />
            </div>

            <DialogFooter className="gap-2 sm:gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {isEdit ? 'Salvar' : 'Criar categoria'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}

function toneOrNeutral(tone: string): ClientCategoryFormValues['tone'] {
  return (CLIENT_CATEGORY_TONES as readonly string[]).includes(tone)
    ? (tone as ClientCategoryFormValues['tone'])
    : 'neutral';
}
