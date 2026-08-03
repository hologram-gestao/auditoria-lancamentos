'use client';

/**
 * Gaveta de criação/edição de entrada do glossário (Sprint 6 / R2).
 *
 * Uma gaveta só para os dois modos (design-system: "formulário em Drawer única,
 * create+edit"), com o shell `SheetHeader/SheetBody/SheetFooter` — header e
 * rodapé fixos, miolo rolando, **Cancelar à esquerda** e ação primária à
 * direita. Quem abre remonta por `key` para o estado nascer limpo.
 *
 * Decisões que valem comentário:
 *   - **PATCH substitui o registro inteiro** (o texto é cifrado no servidor:
 *     mudar só a descrição exigiria decifrar o resto). Por isso a edição envia
 *     os mesmos campos da criação, e não um "patch parcial".
 *   - **`client_id` não vai no body.** O servidor o fixa a partir do tenant da
 *     rota e o request é `extra="forbid"`: mandar seria 422. É também o que
 *     impede editar o glossário de outro tenant pelo corpo do request.
 *   - `code`/`description` viram `null` quando vazios (`emptyToNull`) — mandar
 *     `""` gravaria um código vazio em vez de nenhum.
 *   - `GLOSSARY_LIMIT_EXCEEDED` (teto de entradas) NÃO vira erro inline: não é
 *     defeito de um campo, é limite do glossário inteiro. Vai como toast com o
 *     `userMessage` do servidor, que já diz o que fazer ("remova alguma"). O
 *     front nunca mostra `err.message`, que carrega detalhe interno — e o 403
 *     do operador que forçar a escrita cai no mesmo caminho, legível.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
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
import { useCreateGlossaryEntry, useUpdateGlossaryEntry } from '@/hooks/use-glossary';
import { ApiError } from '@/lib/api/client';
import type { GlossaryEntry } from '@/lib/contracts';
import {
  emptyToNull,
  glossaryEntrySchema,
  GLOSSARY_KIND_HINTS,
  GLOSSARY_KIND_LABELS,
  GLOSSARY_MAX_DESCRIPTION_CHARS,
  type GlossaryFormValues,
  type GlossaryKindFormValue,
} from '@/lib/validation/glossary';

interface GlossaryFormDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  /** `null` = criação; entrada preenchida = edição. */
  entry: GlossaryEntry | null;
}

const KIND_OPTIONS = Object.entries(GLOSSARY_KIND_LABELS) as [GlossaryKindFormValue, string][];

export function GlossaryFormDrawer({
  open,
  onOpenChange,
  clientId,
  entry,
}: GlossaryFormDrawerProps) {
  const isEdit = entry !== null;

  const createMutation = useCreateGlossaryEntry(clientId);
  const updateMutation = useUpdateGlossaryEntry(clientId, entry?.id ?? '');

  const form = useForm<GlossaryFormValues>({
    resolver: zodResolver(glossaryEntrySchema),
    defaultValues: {
      kind: readKind(entry?.kind),
      name: entry?.name ?? '',
      code: entry?.code ?? '',
      description: entry?.description ?? '',
    },
    mode: 'onSubmit',
  });

  const isSubmitting = createMutation.isPending || updateMutation.isPending;
  const selectedKind = form.watch('kind');

  async function onSubmit(values: GlossaryFormValues) {
    const payload = {
      kind: values.kind,
      name: values.name,
      code: emptyToNull(values.code),
      description: emptyToNull(values.description),
    };
    try {
      if (isEdit) {
        await updateMutation.mutateAsync(payload);
        toast.success('Entrada atualizada.');
      } else {
        await createMutation.mutateAsync(payload);
        toast.success('Entrada adicionada ao glossário.');
      }
      onOpenChange(false);
    } catch (err) {
      const fallback = isEdit
        ? 'Não foi possível atualizar a entrada.'
        : 'Não foi possível adicionar a entrada.';
      toast.error(err instanceof ApiError ? err.userMessage : fallback);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="p-0">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex h-full flex-col" noValidate>
            <SheetHeader>
              <SheetTitle>{isEdit ? 'Editar entrada' : 'Nova entrada'}</SheetTitle>
              <SheetDescription>
                {isEdit
                  ? 'As alterações passam a valer na próxima análise de classificação deste cliente.'
                  : 'O que você cadastrar aqui é usado como referência na análise de classificação deste cliente.'}
              </SheetDescription>
            </SheetHeader>

            <SheetBody className="space-y-4">
              <FormField
                control={form.control}
                name="kind"
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
                          <SelectValue placeholder="Selecione o tipo" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {KIND_OPTIONS.map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormDescription>{GLOSSARY_KIND_HINTS[selectedKind]}</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Nome</FormLabel>
                    <FormControl>
                      <Input autoComplete="off" disabled={isSubmitting} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Código (opcional)</FormLabel>
                    <FormControl>
                      <Input autoComplete="off" disabled={isSubmitting} {...field} />
                    </FormControl>
                    <FormDescription>
                      Se a entrada tem um código no plano de contas do cliente, informe-o aqui.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Descrição de uso (opcional)</FormLabel>
                    <FormControl>
                      <Textarea rows={4} disabled={isSubmitting} {...field} />
                    </FormControl>
                    <FormDescription>
                      Quando esta entrada se aplica. Máximo de {GLOSSARY_MAX_DESCRIPTION_CHARS}{' '}
                      caracteres.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </SheetBody>

            {/* Cancelar à ESQUERDA (`justify-between` do SheetFooter). */}
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
                {isEdit ? 'Salvar alterações' : 'Adicionar entrada'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}

/**
 * O `kind` chega do contrato como enum fechado, mas em runtime é o servidor que
 * manda. Valor fora da whitelist cairia num select sem opção correspondente —
 * cair em "categoria" mantém a gaveta operável em vez de travar num estado sem
 * seleção.
 */
function readKind(kind: string | undefined): GlossaryKindFormValue {
  if (kind === 'fornecedor' || kind === 'regra') return kind;
  return 'categoria';
}
