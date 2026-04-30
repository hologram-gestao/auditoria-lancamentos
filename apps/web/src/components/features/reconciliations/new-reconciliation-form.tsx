'use client';

/**
 * Formulário "Nova Conciliação" — `[FRONT 5.1]`, Doc §11.1.
 *
 * Esta sessão é APENAS o formulário. Não calcula hash SHA-256 nem chama
 * `/check-duplicate` (`[FRONT 6.1]`) e não dispara o pipeline de parsing (S9).
 * O submit válido apenas mostra um toast informativo — o handler real é
 * conectado nas próximas tarefas.
 *
 * Decisões:
 *   - Reusa `useClientDetail` (S7) para alimentar o select de contas. Não
 *     existe um endpoint só de "contas" no back; o detalhe já vem com elas.
 *   - Contas são ordenadas por `name` (pt-BR) — UX previsível e estável,
 *     independente da ordem que o Omie devolveu.
 *   - Tipos `'CC'` (corrente) e `'CA'` (cartão) são os conciliáveis (Doc §6.2).
 *     O cache geralmente já vem só com esses, mas filtramos defensivamente.
 *   - Cliente sem contas no cache → select desabilitado + mensagem com link
 *     pra forçar a sincronização na tela de detalhe (Pedro pediu UX clara).
 *   - Erros de detalhe (404 / falha) reaproveitam o `<ErrorState>` do detalhe
 *     reescrito inline aqui para não refatorar S7.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { ChevronRight, Info, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo } from 'react';
import { useForm, useWatch } from 'react-hook-form';
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useClientDetail } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import type { BankAccount } from '@/lib/api/clients';
import {
  ALLOWED_EXTENSIONS,
  DEFAULT_TOLERANCE,
  TOLERANCE_OPTIONS,
  currentMonth,
  newReconciliationSchema,
  type NewReconciliationFormValues,
} from '@/lib/validation/reconciliations';

import { FileInputField } from './file-input-field';

const FILE_ACCEPT = ALLOWED_EXTENSIONS.map((ext) => `.${ext}`).join(',');

const TOLERANCE_TOOLTIP =
  'Margem de dias entre a data do lançamento no banco e no Omie. Um lançamento do dia 31 pode estar registrado no Omie no dia 2 do mês seguinte; a tolerância evita falsos positivos. Padrão é 3 dias.';

interface NewReconciliationFormProps {
  clientId: string;
}

export function NewReconciliationForm({ clientId }: NewReconciliationFormProps) {
  const router = useRouter();
  const detailQuery = useClientDetail(clientId);

  if (detailQuery.isLoading) {
    return <FormSkeleton />;
  }

  if (detailQuery.isError) {
    const err = detailQuery.error;
    const isNotFound = err instanceof ApiError && err.status === 404;
    return (
      <ErrorState
        clientId={clientId}
        title={isNotFound ? 'Cliente não encontrado' : 'Não foi possível carregar o cliente'}
        message={
          err instanceof ApiError ? err.userMessage : 'Ocorreu um erro inesperado. Tente novamente.'
        }
        onRetry={() => void detailQuery.refetch()}
        showRetry={!isNotFound}
      />
    );
  }

  const client = detailQuery.data;
  if (!client) return null;

  return (
    <FormReady
      clientId={clientId}
      clientName={client.name}
      accounts={client.accounts}
      onCancel={() => router.push(`/clientes/${clientId}`)}
    />
  );
}

interface FormReadyProps {
  clientId: string;
  clientName: string;
  accounts: BankAccount[];
  onCancel: () => void;
}

function FormReady({ clientId, clientName, accounts, onCancel }: FormReadyProps) {
  // Não filtramos por `account_type` aqui: o backend já garante que apenas
  // contas conciliáveis (CC/CA) entram no cache (Doc §6.2). Um filtro case/
  // whitespace-sensitive no front zera a lista quando o Omie devolve o tipo
  // com espaços ou variação de caixa.
  const sortedAccounts = useMemo(() => {
    return [...accounts].sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'));
  }, [accounts]);

  const hasAccounts = sortedAccounts.length > 0;
  const maxMonth = currentMonth();

  const form = useForm<NewReconciliationFormValues>({
    resolver: zodResolver(newReconciliationSchema),
    // `omie_conta_id` e `file` ficam como `undefined` até o usuário selecionar;
    // o resolver Zod cuida de exigir os campos. Tipagem do RHF aceita
    // `Partial<>` em defaultValues, mas para manter strict-friendly usamos cast.
    defaultValues: {
      tolerance_days: DEFAULT_TOLERANCE,
      reference_month: '',
    } as Partial<NewReconciliationFormValues> as NewReconciliationFormValues,
    mode: 'onSubmit',
  });

  const watchedAccountId = useWatch({ control: form.control, name: 'omie_conta_id' });
  const selectedAccount = useMemo(() => {
    if (watchedAccountId === undefined || watchedAccountId === null) return null;
    return sortedAccounts.find((a) => a.omie_conta_id === Number(watchedAccountId)) ?? null;
  }, [sortedAccounts, watchedAccountId]);

  async function onSubmit(_values: NewReconciliationFormValues) {
    // TODO(FRONT 6.1): calcular hash SHA-256 e chamar /check-duplicate.
    // TODO(S9): após check, enviar arquivo ao endpoint de parsing.
    toast.info('Formulário pronto. Processamento será habilitado nas próximas tarefas.');
  }

  const isSubmitting = form.formState.isSubmitting;

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <header className="space-y-3">
        <Breadcrumb clientId={clientId} clientName={clientName} />
        <h1 className="text-2xl font-semibold">Nova Conciliação</h1>
      </header>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6" noValidate>
          <FormField
            control={form.control}
            name="omie_conta_id"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Conta Bancária</FormLabel>
                <Select
                  onValueChange={(v) => field.onChange(Number(v))}
                  value={field.value !== undefined ? String(field.value) : undefined}
                  disabled={!hasAccounts || isSubmitting}
                >
                  <FormControl>
                    <SelectTrigger aria-label="Conta bancária">
                      <SelectValue
                        placeholder={
                          hasAccounts ? 'Selecione uma conta' : 'Nenhuma conta disponível'
                        }
                      />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {sortedAccounts.map((account) => (
                      <SelectItem key={account.id} value={String(account.omie_conta_id)}>
                        {formatAccountLabel(account)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                {selectedAccount && selectedAccount.bank_name !== '—' && (
                  <span
                    className="bg-muted text-muted-foreground inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium"
                    aria-live="polite"
                  >
                    Banco: {selectedAccount.bank_name}
                  </span>
                )}

                {!hasAccounts && (
                  <FormDescription>
                    Nenhuma conta sincronizada para este cliente.{' '}
                    <Link
                      href={`/clientes/${clientId}`}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      Sincronize as contas na tela de detalhe
                    </Link>{' '}
                    antes de criar uma conciliação.
                  </FormDescription>
                )}

                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="reference_month"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Mês de Referência</FormLabel>
                <FormControl>
                  <input
                    type="month"
                    max={maxMonth}
                    disabled={isSubmitting || !hasAccounts}
                    aria-label="Mês de referência"
                    className="border-input bg-background ring-offset-background placeholder:text-muted-foreground focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    {...field}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="tolerance_days"
            render={({ field }) => (
              <FormItem>
                <div className="flex items-center gap-1.5">
                  <FormLabel>Tolerância de Data</FormLabel>
                  <TooltipProvider delayDuration={150}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          aria-label="Ajuda sobre tolerância de data"
                          className="text-muted-foreground hover:text-foreground focus-visible:ring-ring rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                        >
                          <Info className="h-4 w-4" aria-hidden="true" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="text-xs leading-snug">
                        {TOLERANCE_TOOLTIP}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
                <Select
                  onValueChange={(v) => field.onChange(Number(v))}
                  value={
                    field.value !== undefined ? String(field.value) : String(DEFAULT_TOLERANCE)
                  }
                  disabled={isSubmitting || !hasAccounts}
                >
                  <FormControl>
                    <SelectTrigger aria-label="Tolerância de data">
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {TOLERANCE_OPTIONS.map((opt) => (
                      <SelectItem key={opt} value={String(opt)}>
                        {opt === 1 ? '1 dia' : `${opt} dias`}
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
            name="file"
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>Arquivo</FormLabel>
                <FormControl>
                  <FileInputField
                    accept={FILE_ACCEPT}
                    value={field.value ?? null}
                    onChange={(file) => field.onChange(file ?? undefined)}
                    disabled={isSubmitting || !hasAccounts}
                    aria-invalid={!!fieldState.error}
                  />
                </FormControl>
                <FormDescription>
                  Formatos aceitos: {ALLOWED_EXTENSIONS.join(', ').toUpperCase()}.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
              Cancelar
            </Button>
            <Button type="submit" disabled={isSubmitting || !hasAccounts}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              Processar
            </Button>
          </div>
        </form>
      </Form>
    </div>
  );
}

function formatAccountLabel(account: BankAccount): string {
  const base =
    account.bank_name && account.bank_name !== '—'
      ? `${account.name} — ${account.bank_name}`
      : account.name;
  // Normaliza para tolerar variações como ' CA ', 'ca' que o Omie pode devolver.
  const normalizedType = account.account_type.trim().toUpperCase();
  return normalizedType === 'CA' ? `${base} (Cartão)` : base;
}

function Breadcrumb({ clientId, clientName }: { clientId: string; clientName: string }) {
  return (
    <nav aria-label="Breadcrumb" className="text-muted-foreground text-sm">
      <ol className="flex flex-wrap items-center gap-1.5">
        <li>
          <Link href="/clientes" className="hover:text-foreground hover:underline">
            Clientes
          </Link>
        </li>
        <li aria-hidden="true">
          <ChevronRight className="h-3.5 w-3.5" />
        </li>
        <li>
          <Link href={`/clientes/${clientId}`} className="hover:text-foreground hover:underline">
            {clientName}
          </Link>
        </li>
        <li aria-hidden="true">
          <ChevronRight className="h-3.5 w-3.5" />
        </li>
        <li className="text-foreground font-medium" aria-current="page">
          Nova Conciliação
        </li>
      </ol>
    </nav>
  );
}

function FormSkeleton() {
  return (
    <div
      className="mx-auto max-w-2xl space-y-6"
      aria-busy="true"
      aria-label="Carregando formulário"
    >
      <div className="space-y-3">
        <div className="bg-muted h-3 w-48 animate-pulse rounded" />
        <div className="bg-muted h-7 w-64 animate-pulse rounded" />
      </div>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="space-y-2">
          <div className="bg-muted h-4 w-32 animate-pulse rounded" />
          <div className="bg-muted h-10 w-full animate-pulse rounded-md" />
        </div>
      ))}
    </div>
  );
}

interface ErrorStateProps {
  clientId: string;
  title: string;
  message: string;
  onRetry: () => void;
  showRetry: boolean;
}

function ErrorState({ clientId, title, message, onRetry, showRetry }: ErrorStateProps) {
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <nav aria-label="Breadcrumb" className="text-muted-foreground text-sm">
        <Link href={`/clientes/${clientId}`} className="hover:text-foreground hover:underline">
          ← Voltar para o cliente
        </Link>
      </nav>
      <div className="bg-destructive/5 border-destructive/30 text-destructive space-y-3 rounded-lg border p-6">
        <h1 className="text-lg font-semibold">{title}</h1>
        <p className="text-sm">{message}</p>
        {showRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            Tentar novamente
          </Button>
        )}
      </div>
    </div>
  );
}
