'use client';

/**
 * Formulário "Nova Conciliação" — `[FRONT 5.1]` + `[FRONT 6.1]`, Doc §11.1–11.3.
 *
 * Pipeline de submit (Doc §11.3, sequencial — para na primeira falha):
 *   V1. Campos obrigatórios + formato + tamanho ≤ 20 MB → coberto pelo zod
 *       resolver do RHF. Se há erro, o submit handler nem é chamado.
 *   V2. SHA-256 do arquivo via `lib/crypto/hash.ts` (Web Crypto API,
 *       client-side, arquivo NÃO trafega).
 *   V3. `GET /reconciliations/check-duplicate` — se duplicata, bloqueia
 *       sem opção de continuar (Doc §11.3 explícito).
 *
 * Decisões herdadas de `[FRONT 5.1]`:
 *   - Reusa `useClientDetail` (S7) para o select de contas.
 *   - Sem filtro por `account_type` — back já garante CC/CA no cache (Doc §6.2).
 *   - Sem contas → select desabilitado + link pra sincronização.
 *
 * Decisões de `[FRONT 6.1]`:
 *   - Hash NÃO é pré-calculado no `onChange` do file: arquivos de 20 MB no
 *     limite custam ~100ms de CPU + alocação. Só rodamos no submit, e o
 *     `step` reseta para `'idle'` quando o usuário muda de arquivo no
 *     bloqueio de duplicata — recálculo feito sob demanda.
 *   - Bloqueio de duplicata exige interação explícita pra sair: o botão
 *     "Selecionar outro arquivo" limpa o `file` do form e volta o step.
 *     Submit "Processar" some enquanto o bloqueio está ativo — sem rota
 *     "continuar mesmo assim".
 *   - Pipeline de upload + parsing ainda é S9. No caminho feliz, mostramos
 *     toast informativo e devolvemos o botão para `idle` — placeholder
 *     intencional até S9.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { AlertTriangle, ChevronRight, Info, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
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
import { useCheckDuplicate } from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { BankAccount } from '@/lib/api/clients';
import { sha256Hex } from '@/lib/crypto/hash';
import {
  ALLOWED_EXTENSIONS,
  DEFAULT_TOLERANCE,
  MAX_FILE_SIZE_LABEL,
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

  const [step, setStep] = useState<SubmitStep>('idle');
  const checkDuplicate = useCheckDuplicate();

  async function onSubmit(values: NewReconciliationFormValues) {
    // V1 (zod resolver): se chegou aqui, campos obrigatórios + formato +
    // tamanho ≤ 20 MB já passaram. Não precisa repetir.

    // V2 — hash SHA-256 client-side.
    setStep('hashing');
    let hash: string;
    try {
      hash = await sha256Hex(values.file);
    } catch (err) {
      setStep('idle');
      const message =
        err instanceof Error ? err.message : 'Não foi possível calcular a assinatura do arquivo.';
      toast.error(message);
      return;
    }

    // V3 — checagem de duplicata no backend.
    setStep('checking-duplicate');
    let duplicate: boolean;
    try {
      const res = await checkDuplicate.mutateAsync({
        client_id: clientId,
        omie_conta_id: values.omie_conta_id,
        month: values.reference_month,
        hash,
      });
      duplicate = res.duplicate;
    } catch (err) {
      setStep('idle');
      const userMessage =
        err instanceof ApiError
          ? err.userMessage
          : 'Não foi possível verificar a duplicata. Tente novamente.';
      toast.error(userMessage);
      return;
    }

    if (duplicate) {
      // Bloqueio terminal: usuário precisa trocar arquivo ou cancelar.
      // Não voltamos para `idle` automaticamente — Doc §11.3 V3 é explícito.
      setStep('duplicate-blocked');
      return;
    }

    // Caminho feliz: aqui em S9 dispara o upload + parsing real.
    toast.info('Validações OK. Processamento será habilitado em S9.');
    setStep('idle');
  }

  function handleSelectOtherFile() {
    // O cast é necessário porque o schema exige `File`; defaultValues também
    // usa o mesmo padrão (cast em `Partial<...>` mais acima). Resetar o
    // estado do passo é o que devolve o botão "Processar" para a UI.
    form.setValue('file', undefined as unknown as File, { shouldValidate: false });
    form.clearErrors('file');
    setStep('idle');
  }

  const isPipelineRunning = step === 'hashing' || step === 'checking-duplicate';
  const isDuplicateBlocked = step === 'duplicate-blocked';
  const isSubmitting = form.formState.isSubmitting || isPipelineRunning;
  const inputsDisabled = isSubmitting || isDuplicateBlocked;

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
                  disabled={!hasAccounts || inputsDisabled}
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
                    disabled={inputsDisabled || !hasAccounts}
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
                  disabled={inputsDisabled || !hasAccounts}
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
                    disabled={inputsDisabled || !hasAccounts}
                    aria-invalid={!!fieldState.error}
                  />
                </FormControl>
                <FormDescription>
                  Formatos aceitos: {ALLOWED_EXTENSIONS.join(', ').toUpperCase()} · Tamanho máximo:{' '}
                  {MAX_FILE_SIZE_LABEL}.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          {isDuplicateBlocked && <DuplicateBlockAlert />}

          <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onCancel} disabled={isPipelineRunning}>
              Cancelar
            </Button>
            {isDuplicateBlocked ? (
              <Button type="button" onClick={handleSelectOtherFile}>
                Selecionar outro arquivo
              </Button>
            ) : (
              <Button type="submit" disabled={isSubmitting || !hasAccounts}>
                {isPipelineRunning && (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                )}
                {processButtonLabel(step)}
              </Button>
            )}
          </div>
        </form>
      </Form>
    </div>
  );
}

/**
 * Estados do pipeline de submit (Doc §11.3):
 *   - `idle`              : botão "Processar" habilitado, nenhum loading.
 *   - `hashing`           : V2 — calculando SHA-256 client-side.
 *   - `checking-duplicate`: V3 — request `/check-duplicate` em voo.
 *   - `duplicate-blocked` : terminal; UI substitui "Processar" por
 *                           "Selecionar outro arquivo" e mostra alert.
 */
type SubmitStep = 'idle' | 'hashing' | 'checking-duplicate' | 'duplicate-blocked';

function processButtonLabel(step: SubmitStep): string {
  switch (step) {
    case 'hashing':
      return 'Gerando hash…';
    case 'checking-duplicate':
      return 'Verificando duplicata…';
    default:
      return 'Processar';
  }
}

function DuplicateBlockAlert() {
  return (
    <div
      role="alert"
      className="bg-destructive/5 border-destructive/30 text-destructive flex items-start gap-3 rounded-lg border p-4 text-sm"
    >
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-semibold">Arquivo duplicado</p>
        <p className="leading-snug">
          Já existe uma conciliação para esta conta, mês e arquivo. Não é possível criar outra.
          Selecione um arquivo diferente para continuar.
        </p>
      </div>
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
