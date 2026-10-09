'use client';

/**
 * Gaveta "Nova conta" / "Editar conta" do plano contábil (86e3nb816).
 *
 * O escritório criou uma conta no sistema contábil e quer usá-la já, sem
 * exportar e reimportar a planilha inteira. Uma gaveta só para os dois modos
 * (design-system: formulário em Drawer única, create+edit), com Cancelar à
 * esquerda; quem abre remonta por `key` para o estado nascer limpo.
 *
 * Decisões que valem comentário:
 *   - **O código não se edita.** Ele é a chave da reimportação e o que já foi
 *     para as materializações e o arquivo contábil: na edição o campo aparece
 *     desabilitado, com o motivo.
 *   - **A edição manda só o que mudou** (`PATCH` parcial do servidor). A
 *     classificação vazia vira `null`, que LIMPA no servidor.
 *   - **Recusa do servidor cai no campo**: código repetido no código, conta em
 *     uso no tipo ou na situação, com a mensagem do servidor (que traz as
 *     contagens e nunca nomeia categoria). O resto vira toast.
 *   - A conta nova nasce ativa, então o switch de situação só existe na edição.
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
import { Switch } from '@/components/ui/switch';
import {
  useCreateAccountingAccount,
  useUpdateAccountingAccount,
} from '@/hooks/use-client-accounting-chart';
import { readAccountFormRefusal } from '@/lib/accounting-chart-errors';
import { ApiError } from '@/lib/api/client';
import type {
  AccountingAccount,
  AccountingAccountType,
  AccountingAccountUpdateRequest,
} from '@/lib/contracts';
import {
  ACCOUNT_MAX_CLASSIFICATION_CHARS,
  ACCOUNT_MAX_CODE_CHARS,
  ACCOUNT_MAX_NAME_CHARS,
  accountingAccountSchema,
  type AccountingAccountFormValues,
} from '@/lib/validation/accounting-account';

const TYPE_OPTIONS: { value: AccountingAccountType; label: string; hint: string }[] = [
  { value: 'analitica', label: 'Analítica', hint: 'Recebe lançamento.' },
  { value: 'sintetica', label: 'Sintética', hint: 'Só agrupa outras contas.' },
];

interface AccountingAccountFormSheetProps {
  clientId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `null` = nova conta; conta preenchida = edição. */
  account: AccountingAccount | null;
}

function emptyToNull(value: string): string | null {
  return value === '' ? null : value;
}

/** Só o que mudou em relação à conta — o PATCH do servidor é parcial. */
export function accountChanges(
  account: AccountingAccount,
  values: AccountingAccountFormValues,
): AccountingAccountUpdateRequest {
  const changes: AccountingAccountUpdateRequest = {};
  if (!account.nameResolved || values.name !== account.name) changes.name = values.name;
  if (values.type !== account.type) changes.type = values.type;
  const classification = emptyToNull(values.classification);
  if (classification !== (account.classification ?? null)) {
    changes.classification = classification;
  }
  if (values.active !== account.active) changes.active = values.active;
  return changes;
}

export function AccountingAccountFormSheet({
  clientId,
  open,
  onOpenChange,
  account,
}: AccountingAccountFormSheetProps) {
  const isEdit = account !== null;
  const createMutation = useCreateAccountingAccount(clientId);
  const updateMutation = useUpdateAccountingAccount(clientId);
  const isSubmitting = createMutation.isPending || updateMutation.isPending;

  const form = useForm<AccountingAccountFormValues>({
    resolver: zodResolver(accountingAccountSchema),
    defaultValues: {
      code: account?.code ?? '',
      name: account?.nameResolved ? account.name : '',
      type: account?.type ?? 'analitica',
      classification: account?.classification ?? '',
      active: account?.active ?? true,
    },
    mode: 'onSubmit',
  });

  async function onSubmit(values: AccountingAccountFormValues) {
    try {
      if (account !== null) {
        const payload = accountChanges(account, values);
        if (Object.keys(payload).length > 0) {
          await updateMutation.mutateAsync({ accountId: account.id, payload });
          toast.success(`Conta ${account.code} atualizada.`);
        }
      } else {
        const created = await createMutation.mutateAsync({
          code: values.code,
          name: values.name,
          type: values.type,
          classification: emptyToNull(values.classification),
        });
        toast.success(`Conta ${created.code} incluída no plano.`);
      }
      onOpenChange(false);
    } catch (err) {
      const refusal = readAccountFormRefusal(err);
      if (refusal !== null) {
        form.setError(refusal.field, { type: 'server', message: refusal.message });
        form.setFocus(refusal.field);
        return;
      }
      const fallback = isEdit
        ? 'Não foi possível salvar a conta.'
        : 'Não foi possível incluir a conta.';
      toast.error(err instanceof ApiError ? err.userMessage : fallback);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="p-0">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="flex h-full flex-col" noValidate>
            <SheetHeader>
              <SheetTitle>{isEdit ? `Editar conta ${account.code}` : 'Nova conta'}</SheetTitle>
              <SheetDescription>
                {isEdit
                  ? 'As alterações valem para as próximas decisões do de-para. O que já foi materializado não muda.'
                  : 'Inclua a conta que acabou de ser criada no sistema contábil, sem reimportar a planilha. Ela aparece na lista no lugar da classificação.'}
              </SheetDescription>
            </SheetHeader>

            <SheetBody className="space-y-4">
              <FormField
                control={form.control}
                name="code"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Código reduzido</FormLabel>
                    <FormControl>
                      <Input
                        autoComplete="off"
                        inputMode="text"
                        maxLength={ACCOUNT_MAX_CODE_CHARS}
                        disabled={isSubmitting || isEdit}
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>
                      {isEdit
                        ? 'O código não se edita: é ele que vai no arquivo contábil.'
                        : 'O mesmo código do sistema contábil. Letras, números, ponto e hífen.'}
                    </FormDescription>
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
                      <Input
                        autoComplete="off"
                        maxLength={ACCOUNT_MAX_NAME_CHARS}
                        disabled={isSubmitting}
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

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
                        <SelectTrigger ref={field.ref}>
                          <SelectValue placeholder="Selecione o tipo" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {TYPE_OPTIONS.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      {TYPE_OPTIONS.find((option) => option.value === field.value)?.hint}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="classification"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Classificação (opcional)</FormLabel>
                    <FormControl>
                      <Input
                        autoComplete="off"
                        placeholder="1.1.1.02.001"
                        maxLength={ACCOUNT_MAX_CLASSIFICATION_CHARS}
                        disabled={isSubmitting}
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>Decide a posição da conta na lista.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {isEdit && (
                <FormField
                  control={form.control}
                  name="active"
                  render={({ field }) => (
                    <FormItem>
                      <div className="flex items-center justify-between gap-4">
                        <FormLabel>Conta ativa</FormLabel>
                        <FormControl>
                          <Switch
                            ref={field.ref}
                            checked={field.value}
                            onCheckedChange={field.onChange}
                            disabled={isSubmitting}
                          />
                        </FormControl>
                      </div>
                      <FormDescription>
                        Conta inativa continua no plano, mas não recebe decisão nova no de-para.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
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
                {isEdit ? 'Salvar alterações' : 'Incluir conta'}
              </Button>
            </SheetFooter>
          </form>
        </Form>
      </SheetContent>
    </Sheet>
  );
}
