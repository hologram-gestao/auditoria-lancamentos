'use client';

/**
 * Gaveta "Vincular conta" da aba Clientes e fornecedores (86e3tttaf).
 *
 * O escritório cria a conta e o cadastro no Domínio hoje e quer usá-los na hora, sem
 * exportar a relação de novo (o mesmo motivo da conta manual, 86e3nb816). A gaveta liga
 * UMA conta do plano a um CNPJ ou CPF, ou troca o vínculo que ela já tem (o servidor
 * responde ao `PUT` criando ou trocando).
 *
 * - **Conta**: o MESMO seletor do de-para e da conta do banco
 *   (`AccountingAccountCombobox`): só analíticas e ativas, busca pelo início do código,
 *   com o selo "não veio na última importação" no rótulo.
 * - **Tipo**: Cliente ou Fornecedor, pré-marcado com a sub-aba de onde a gaveta abriu.
 * - **Documento**: com ou sem máscara; o zod confere tamanho e dígito verificador
 *   (espelho de `cnpj.py`/`cpf.py`) e o servidor confere de novo.
 *
 * Recusa do servidor cai no CAMPO (`readPartyLinkRefusal`): o 409 do documento que outra
 * conta já tem, com o código dela, e o 422 da conta que deixou de ser lançável. Nunca
 * toast para recusa conhecida. O documento digitado não vai para log nem para o toast.
 * Remontada por `key` a cada abertura (estado limpo).
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
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useSetAccountingPartyLink } from '@/hooks/use-client-accounting-chart';
import { readPartyLinkRefusal } from '@/lib/accounting-parties-errors';
import { ApiError } from '@/lib/api/client';
import type { AccountingPartyType } from '@/lib/contracts';
import { cn } from '@/lib/utils';
import {
  PARTY_DOCUMENT_MAX_CHARS,
  partyLinkSchema,
  type PartyLinkValues,
} from '@/lib/validation/accounting-parties';

import { AccountingAccountCombobox } from './accounting-account-combobox';

const TYPE_OPTIONS: readonly { value: AccountingPartyType; label: string }[] = [
  { value: 'cliente', label: 'Cliente' },
  { value: 'fornecedor', label: 'Fornecedor' },
];

interface AccountingPartyLinkSheetProps {
  clientId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** O tipo pré-marcado: o da sub-aba de onde a gaveta abriu. */
  defaultPartyType: AccountingPartyType;
  /** Depois de gravar: a aba mostra a sub-aba do tipo gravado. */
  onLinked: (partyType: AccountingPartyType) => void;
}

export function AccountingPartyLinkSheet({
  clientId,
  open,
  onOpenChange,
  defaultPartyType,
  onLinked,
}: AccountingPartyLinkSheetProps) {
  const mutation = useSetAccountingPartyLink(clientId);
  const isPending = mutation.isPending;
  const form = useForm<PartyLinkValues>({
    resolver: zodResolver(partyLinkSchema),
    defaultValues: { accountId: '', partyType: defaultPartyType, document: '' },
    mode: 'onSubmit',
  });

  async function onSubmit(values: PartyLinkValues) {
    try {
      const link = await mutation.mutateAsync({
        accountId: values.accountId,
        payload: { partyType: values.partyType, document: values.document },
      });
      toast.success(
        `Conta ${link.code} vinculada a ${values.partyType === 'cliente' ? 'um cliente' : 'um fornecedor'}.`,
      );
      onLinked(values.partyType);
      onOpenChange(false);
    } catch (err) {
      const refusal = readPartyLinkRefusal(err);
      if (refusal !== null) {
        form.setError(refusal.field, { type: 'server', message: refusal.message });
        if (refusal.field === 'document') form.setFocus('document');
        return;
      }
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível vincular a conta.');
    }
  }

  return (
    <Sheet open={open} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <SheetContent side="right" className="flex flex-col p-0">
        <SheetHeader>
          <SheetTitle>Vincular conta</SheetTitle>
          <SheetDescription>
            Liga uma conta do plano contábil a um cliente ou fornecedor pelo CNPJ ou CPF, sem
            importar a relação do Domínio de novo. É por esse documento que a planilha da Omie liga
            a conta ao cadastro.
          </SheetDescription>
        </SheetHeader>

        <Form {...form}>
          <form
            className="flex min-h-0 flex-1 flex-col"
            noValidate
            onSubmit={form.handleSubmit(onSubmit)}
            data-testid="accounting-party-link-form"
          >
            <SheetBody className="space-y-5">
              <FormField
                control={form.control}
                name="accountId"
                render={({ field, fieldState }) => (
                  <FormItem>
                    <FormLabel>Conta do plano contábil</FormLabel>
                    <FormControl>
                      <AccountingAccountCombobox
                        clientId={clientId}
                        value={field.value === '' ? null : field.value}
                        onValueChange={(next) => {
                          field.onChange(next);
                          form.clearErrors('accountId');
                        }}
                        label="Conta do plano contábil"
                        disabled={isPending}
                        aria-invalid={fieldState.error !== undefined}
                      />
                    </FormControl>
                    <FormDescription>
                      Só contas analíticas e ativas. Se a conta já tiver um vínculo, ele é trocado
                      por este.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="partyType"
                render={({ field }) => (
                  <FormItem>
                    <fieldset className="space-y-2" disabled={isPending}>
                      <legend className="text-sm font-medium leading-none">Tipo do vínculo</legend>
                      <div className="flex flex-wrap gap-2">
                        {TYPE_OPTIONS.map((option) => {
                          const checked = field.value === option.value;
                          return (
                            <label
                              key={option.value}
                              className={cn(
                                'has-[:focus-visible]:ring-ring inline-flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm has-[:focus-visible]:ring-2',
                                checked
                                  ? 'border-primary bg-accent text-accent-foreground'
                                  : 'bg-background',
                              )}
                            >
                              <input
                                type="radio"
                                name={field.name}
                                value={option.value}
                                checked={checked}
                                onChange={() => field.onChange(option.value)}
                                onBlur={field.onBlur}
                                className="accent-primary h-4 w-4"
                              />
                              {option.label}
                            </label>
                          );
                        })}
                      </div>
                    </fieldset>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="document"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>CNPJ ou CPF</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        inputMode="numeric"
                        autoComplete="off"
                        maxLength={PARTY_DOCUMENT_MAX_CHARS}
                        placeholder="00.000.000/0000-00 ou 000.000.000-00"
                        disabled={isPending}
                        onChange={(event) => {
                          field.onChange(event);
                          if (form.formState.errors.document?.type === 'server') {
                            form.clearErrors('document');
                          }
                        }}
                      />
                    </FormControl>
                    <FormDescription>
                      Com ou sem pontuação. O documento fica cifrado com a chave do cliente e a
                      lista mostra só o fim dele.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </SheetBody>

            <SheetFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isPending}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={isPending}>
                {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {isPending ? 'Vinculando…' : 'Vincular'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}
