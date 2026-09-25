'use client';

/**
 * Gaveta "Contexto do título" (Sprint 15 — FRONT 15.1 / R1 · R2).
 *
 * Uma gaveta só para as duas metades do R2: o HISTÓRICO completo (mais recente
 * primeiro, vindo pronto do servidor) e, para quem tem `manage_title_context`,
 * o FORMULÁRIO para acrescentar uma entrada nova — append-only, então registrar
 * não fecha a gaveta nem substitui nada, só invalida o histórico e a linha.
 *
 * `title` é o `ClientTitle` inteiro (não só o `id`): o cabeçalho da gaveta
 * mostra o título que a pessoa estava olhando na tabela, sem um 2º request.
 *
 * **Sem manage_title_context**: R2 pede leitura + ação OCULTA, não desabilitada
 * — aqui isso é o formulário inteiro sumindo, e o rodapé com um único botão
 * "Fechar" no lugar de Cancelar/Registrar.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';

import { AuthorLabel } from '@/components/features/reconciliations/author-label';
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
import { useRegisterTitleContext, useTitleContextHistory } from '@/hooks/use-client-titles';
import { ApiError } from '@/lib/api/client';
import type { ClientTitle, TitleContext } from '@/lib/contracts';
import { formatBRDate, formatBRL, formatCreatedAt } from '@/lib/format';
import {
  TITLE_CONTEXT_MAX_TEXT_CHARS,
  TITLE_CONTEXT_TYPE_LABELS,
  titleContextFormSchema,
  type TitleContextFormValues,
} from '@/lib/validation/title-context';

import { TitleTypeBadge } from './client-titles-badges';

const TYPE_OPTIONS = Object.entries(TITLE_CONTEXT_TYPE_LABELS) as [
  TitleContextFormValues['type'],
  string,
][];

interface TitleContextSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  title: ClientTitle | null;
  canManage: boolean;
}

export function TitleContextSheet({
  open,
  onOpenChange,
  clientId,
  title,
  canManage,
}: TitleContextSheetProps) {
  const titleId = title?.id ?? '';
  const historyQuery = useTitleContextHistory(clientId, titleId, {
    enabled: open && title !== null,
  });
  const registerMutation = useRegisterTitleContext(clientId, titleId);

  const form = useForm<TitleContextFormValues>({
    resolver: zodResolver(titleContextFormSchema),
    defaultValues: { type: 'acordo_de_pagamento', text: '' },
    mode: 'onSubmit',
  });

  // Remonta o formulário a cada título aberto — nunca herdar o rascunho do
  // título anterior (mesmo padrão de `key` das outras gavetas de criação).
  useEffect(() => {
    if (open) form.reset({ type: 'acordo_de_pagamento', text: '' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, titleId]);

  async function onSubmit(values: TitleContextFormValues) {
    try {
      await registerMutation.mutateAsync(values);
      toast.success('Contexto registrado.');
      form.reset({ type: values.type, text: '' });
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível registrar o contexto.',
      );
    }
  }

  const isSubmitting = registerMutation.isPending;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex flex-col p-0">
        <SheetHeader>
          <SheetTitle>Contexto do título</SheetTitle>
          <SheetDescription>
            {title ? (
              <span className="flex flex-wrap items-center gap-2">
                <TitleTypeBadge titleType={title.titleType} />
                <span>Vencimento {formatBRDate(title.dueDate)}</span>
                <span className="tabular-nums">{formatBRL(title.amount)}</span>
              </span>
            ) : (
              'O histórico e o registro de contexto deste título.'
            )}
          </SheetDescription>
        </SheetHeader>

        {canManage ? (
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit)}
              className="flex min-h-0 flex-1 flex-col"
              noValidate
            >
              <SheetBody className="space-y-6">
                <TitleContextHistory query={historyQuery} />

                <div className="space-y-4 border-t pt-4">
                  <h3 className="text-sm font-semibold">Registrar novo contexto</h3>
                  <FormField
                    control={form.control}
                    name="type"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Tipo</FormLabel>
                        <Select
                          value={field.value}
                          onValueChange={field.onChange}
                          disabled={isSubmitting}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {TYPE_OPTIONS.map(([value, label]) => (
                              <SelectItem key={value} value={value}>
                                {label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="text"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Descrição</FormLabel>
                        <FormControl>
                          <Textarea
                            rows={4}
                            disabled={isSubmitting}
                            maxLength={TITLE_CONTEXT_MAX_TEXT_CHARS}
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
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
                <Button type="submit" disabled={isSubmitting}>
                  {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                  Registrar contexto
                </Button>
              </SheetFooter>
            </form>
          </Form>
        ) : (
          <>
            <SheetBody>
              <TitleContextHistory query={historyQuery} />
            </SheetBody>
            <SheetFooter className="justify-end">
              {/* `aria-label` distingue do botão-X do header (Radix `Dialog.Close`,
                  acessível também como "Fechar") — dois "Fechar" na mesma gaveta
                  quebrariam `getByRole('button', { name: 'Fechar' })` no strict mode. */}
              <Button
                type="button"
                variant="outline"
                aria-label="Fechar gaveta de contexto"
                onClick={() => onOpenChange(false)}
              >
                Fechar
              </Button>
            </SheetFooter>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

/**
 * O histórico é sempre a primeira coisa na gaveta — inclusive para quem tem
 * `manage_title_context`, que vê o que já foi registrado antes de acrescentar.
 * Ordem (mais recente primeiro) vem PRONTA do servidor; nada é reordenado aqui.
 */
function TitleContextHistory({
  query,
}: {
  query: { data?: TitleContext[]; isLoading: boolean; isError: boolean };
}) {
  if (query.isLoading) {
    return (
      <div className="space-y-2" aria-hidden="true">
        {Array.from({ length: 2 }).map((_, index) => (
          <div key={index} className="bg-muted h-16 w-full animate-pulse rounded-lg" />
        ))}
      </div>
    );
  }

  if (query.isError) {
    return (
      <p role="alert" className="text-destructive text-sm">
        Não foi possível carregar o histórico de contexto.
      </p>
    );
  }

  const entries = query.data ?? [];
  if (entries.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">Nenhum contexto registrado para este título.</p>
    );
  }

  return (
    <ul className="space-y-3">
      {entries.map((entry) => (
        <li key={entry.id} className="space-y-1 rounded-lg border p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="bg-muted text-muted-foreground inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium">
              {TITLE_CONTEXT_TYPE_LABELS[entry.type]}
            </span>
            <span className="text-muted-foreground text-xs">
              {formatCreatedAt(entry.createdAt)}
            </span>
          </div>
          {entry.decryptFailed ? (
            <p className="bg-destructive-muted text-destructive ring-destructive/30 rounded px-2 py-1 text-sm ring-1 ring-inset">
              Indecifrável
            </p>
          ) : (
            <p className="text-sm">{entry.text}</p>
          )}
          <p className="text-muted-foreground text-xs">
            Registrado por <AuthorLabel author={entry.author} />
          </p>
        </li>
      ))}
    </ul>
  );
}
