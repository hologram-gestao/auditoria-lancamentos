'use client';

/**
 * Formulário "Nova Conciliação" — `[FRONT 5.1]` + `[FRONT 6.1]` + `[FRONT 7.2]`,
 * Doc §11.1–11.3 e §12.3.
 *
 * Pipeline de submit (sequencial — para na primeira falha):
 *   V1. Campos obrigatórios + formato + tamanho ≤ 20 MB → coberto pelo zod
 *       resolver do RHF. Se há erro, o submit handler nem é chamado.
 *   V2. SHA-256 do arquivo via `lib/crypto/hash.ts` (Web Crypto API,
 *       client-side, arquivo NÃO trafega).
 *   V3. `GET /reconciliations/check-duplicate` — se duplicata, bloqueia
 *       sem opção de continuar (Doc §11.3 explícito).
 *   V4. `POST /reconciliations/parse` — manda arquivo + client_id, espera o
 *       `ExtractedStatement` da IA, troca `view` para `'preview'` (Doc §12).
 *
 * Decisões herdadas de `[FRONT 5.1]`:
 *   - Reusa `useClientDetail` (S7) para o select de contas.
 *   - Sem filtro por `account_type` — back já garante CC/CR/CA/CP/CX no cache (Doc §6.2).
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
 *
 * Decisões de `[FRONT 7.2]`:
 *   - Preview vive no MESMO componente, alternado por `view: 'form' | 'preview'`.
 *     Render condicional (não desmontagem) preserva o `useForm` state — se o
 *     usuário cancelar, todos os values voltam intactos (RHF não é resetado).
 *     Persistência cross-route (sessionStorage / Zustand) seria cara à toa.
 *
 * Decisões de `[FRONT 8.7]` (S10):
 *   - "Confirmar e processar" agora chama `POST /api/v1/reconciliations`
 *     com o `ParsedStatement` + a meta do form + o `file_hash` calculado
 *     no V2. O hash NÃO é recalculado: guardamos em `submittedHash`
 *     quando o parse devolveu OK (mesmo arquivo + mesmo file_hash que o
 *     backend acabou de aceitar para checagem de duplicata).
 *   - Em sucesso → `router.push` para `/clientes/{id}/conciliacao/processando/{sessionId}`
 *     (a tela de polling de S10 cuida do resto).
 *   - Em erro do POST → toast com `userMessage` e usuário fica no preview
 *     pra tentar de novo (o `parsed` continua válido em memória).
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
import { useClientDetail } from '@/hooks/use-clients';
import {
  useCheckDuplicate,
  useCreateReconciliation,
  useParseStatement,
} from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import { isCreditCardAccount, type BankAccount } from '@/lib/api/clients';
import type { ChecksumResult, ParsedStatement } from '@/lib/api/reconciliations';
import { sha256Hex } from '@/lib/crypto/hash';
import {
  ALLOWED_EXTENSIONS,
  MAX_FILE_SIZE_LABEL,
  currentMonth,
  newReconciliationSchema,
  type NewReconciliationFormValues,
} from '@/lib/validation/reconciliations';

import { FileInputField } from './file-input-field';
import { ParsePreview } from './parse-preview';

const FILE_ACCEPT = ALLOWED_EXTENSIONS.map((ext) => `.${ext}`).join(',');

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
  // `router` local para a navegação em `handlePreviewConfirm` (S10) — o
  // hook do Next só pode ser chamado em client component, e `FormReady`
  // já é client (vive sob a diretiva 'use client' do arquivo).
  const router = useRouter();

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
      reference_month: '',
    } as Partial<NewReconciliationFormValues> as NewReconciliationFormValues,
    mode: 'onSubmit',
  });

  const watchedAccountId = useWatch({ control: form.control, name: 'omie_conta_id' });
  const selectedAccount = useMemo(() => {
    if (watchedAccountId === undefined || watchedAccountId === null) return null;
    return sortedAccounts.find((a) => a.omie_conta_id === Number(watchedAccountId)) ?? null;
  }, [sortedAccounts, watchedAccountId]);
  // FRONT 1.4: a tela muda dinamicamente p/ fatura de cartão quando a conta
  // selecionada é cartão (account_type === 'CR'; ⚠️ nunca 'CA' — bug M-1).
  const isCardSelected = selectedAccount
    ? isCreditCardAccount(selectedAccount.account_type)
    : false;

  const [step, setStep] = useState<SubmitStep>('idle');
  const [view, setView] = useState<View>('form');
  const [parsed, setParsed] = useState<ParsedStatement | null>(null);
  const [checksum, setChecksum] = useState<ChecksumResult | null>(null);
  // Hash do arquivo aceito pelo /parse — preservado para o POST de criação
  // da sessão (S10). Resetado junto com `parsed` no cancel/select-other-file.
  const [submittedHash, setSubmittedHash] = useState<string | null>(null);
  const checkDuplicate = useCheckDuplicate();
  const parseStatement = useParseStatement();
  const createReconciliation = useCreateReconciliation();

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

    // V4 — parse via Claude (Doc §12). Endpoint stateless: nada persiste no
    // back até o usuário confirmar em S10. Erros 4xx/5xx do back já vêm com
    // `userMessage` em PT-BR (PARSE_ERROR, FILE_TOO_LARGE, INVALID_FILE,
    // timeout 504, auth fault 502 — ver `lib/api/reconciliations.ts`).
    setStep('parsing');
    try {
      const { statement, checksum } = await parseStatement.mutateAsync({
        client_id: clientId,
        file: values.file,
      });
      setParsed(statement);
      // BACK 02.3 — o checksum acompanha a prévia: quando não fecha, a
      // confirmação é bloqueada e o motivo aparece na tela.
      setChecksum(checksum);
      // Hash que o backend acabou de aceitar no /check-duplicate — reusamos
      // no POST /reconciliations sem precisar recalcular o SHA-256.
      setSubmittedHash(hash);
      setView('preview');
      setStep('idle');
    } catch (err) {
      setStep('idle');
      const userMessage =
        err instanceof ApiError
          ? err.userMessage
          : 'Não foi possível processar o arquivo. Tente novamente.';
      toast.error(userMessage);
    }
  }

  function handlePreviewCancel() {
    // Volta ao form sem resetar o RHF: o `useForm` continua montado por causa
    // do render condicional logo abaixo, então todos os values estão intactos.
    setParsed(null);
    setSubmittedHash(null);
    setView('form');
  }

  async function handlePreviewConfirm() {
    if (parsed === null || submittedHash === null) {
      // Defensive — não deve acontecer (botão só aparece com `parsed` setado),
      // mas evita rodar a mutation com payload incompleto.
      toast.error('Não foi possível recuperar o arquivo processado. Reenvie e tente novamente.');
      return;
    }

    const values = form.getValues();
    try {
      const result = await createReconciliation.mutateAsync({
        client_id: clientId,
        omie_conta_id: values.omie_conta_id,
        // Backend exige `YYYY-MM-DD` (`date`); normaliza pra dia 1 do mês.
        reference_month: `${values.reference_month}-01`,
        file_hash: submittedHash,
        statement: parsed,
      });
      // Sai do preview antes de navegar — caso o `router.push` resolva async,
      // a UI já some o botão "Confirmar" e o usuário não dispara duas vezes.
      router.push(`/clientes/${clientId}/conciliacao/processando/${result.session_id}`);
    } catch (err) {
      // Mantém usuário no preview pra ele tentar de novo (parsed continua válido).
      // 409 DUPLICATE_FILE pode chegar aqui se outra conciliação for criada
      // entre o /check-duplicate (V3) e este POST — a userMessage do back já
      // explica isso em PT-BR.
      const userMessage =
        err instanceof ApiError
          ? err.userMessage
          : 'Não foi possível iniciar o processamento. Tente novamente.';
      toast.error(userMessage);
    }
  }

  function handleSelectOtherFile() {
    // O cast é necessário porque o schema exige `File`; defaultValues também
    // usa o mesmo padrão (cast em `Partial<...>` mais acima). Resetar o
    // estado do passo é o que devolve o botão "Processar" para a UI.
    form.setValue('file', undefined as unknown as File, { shouldValidate: false });
    form.clearErrors('file');
    setStep('idle');
    setSubmittedHash(null);
  }

  const isPipelineRunning =
    step === 'hashing' || step === 'checking-duplicate' || step === 'parsing';
  const isDuplicateBlocked = step === 'duplicate-blocked';
  const isSubmitting = form.formState.isSubmitting || isPipelineRunning;
  const inputsDisabled = isSubmitting || isDuplicateBlocked;
  const showPreview = view === 'preview' && parsed !== null;

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <header className="space-y-3">
        <Breadcrumb clientId={clientId} clientName={clientName} />
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">Nova Conciliação</h1>
          {isCardSelected && (
            <span
              className="inline-flex items-center rounded-md bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700 ring-1 ring-inset ring-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:ring-blue-900"
              aria-live="polite"
            >
              Cartão de Crédito
            </span>
          )}
        </div>
      </header>

      {showPreview && parsed && (
        <ParsePreview
          parsed={parsed}
          checksum={checksum}
          isCard={isCardSelected}
          accountName={selectedAccount?.name ?? ''}
          onCancel={handlePreviewCancel}
          onConfirm={() => void handlePreviewConfirm()}
          isConfirming={createReconciliation.isPending}
        />
      )}

      {/* RHF state-preservation: o `<Form>` permanece montado mesmo durante o
          preview (escondido via `hidden`). Cancelar volta com todos os values
          do formulário intactos. Desmontar (render condicional `?:`) zeraria
          o `useForm`. */}
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-6"
          noValidate
          hidden={showPreview}
          aria-hidden={showPreview}
        >
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
            name="file"
            render={({ field, fieldState }) => (
              <FormItem>
                <FormLabel>{isCardSelected ? 'Arquivo da Fatura' : 'Arquivo do Extrato'}</FormLabel>
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
                  {isCardSelected
                    ? `PDF ou XLS da fatura do cartão. Formatos aceitos: PDF, XLS, XLSX. Máx ${MAX_FILE_SIZE_LABEL}.`
                    : `Formatos aceitos: ${ALLOWED_EXTENSIONS.join(', ').toUpperCase()} · Tamanho máximo: ${MAX_FILE_SIZE_LABEL}.`}
                </FormDescription>
                {isCardSelected && <CardInvoiceNote />}
                <FormMessage />
              </FormItem>
            )}
          />

          {isDuplicateBlocked && <DuplicateBlockAlert />}

          {step === 'parsing' && <ParsingHint />}

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

function ParsingHint() {
  // Doc §12.1 — parsing pode levar até 60 s. Avisar o usuário evita
  // a sensação de travamento (V2/V3 são quase instantâneos; o salto
  // para o V4 sem aviso parece bug).
  return (
    <p
      role="status"
      aria-live="polite"
      className="text-muted-foreground flex items-center gap-2 text-sm"
    >
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      Extraindo movimentações com IA. Isso pode levar até 60 segundos.
    </p>
  );
}

/**
 * Estados do pipeline de submit (Doc §11.3 + §12):
 *   - `idle`              : botão "Processar" habilitado, nenhum loading.
 *   - `hashing`           : V2 — calculando SHA-256 client-side.
 *   - `checking-duplicate`: V3 — request `/check-duplicate` em voo.
 *   - `duplicate-blocked` : terminal; UI substitui "Processar" por
 *                           "Selecionar outro arquivo" e mostra alert.
 *   - `parsing`           : V4 — `/reconciliations/parse` em voo (até 60 s).
 */
type SubmitStep = 'idle' | 'hashing' | 'checking-duplicate' | 'duplicate-blocked' | 'parsing';

/** Visualização atual do componente — alterna entre form e preview do parse. */
type View = 'form' | 'preview';

function processButtonLabel(step: SubmitStep): string {
  switch (step) {
    case 'hashing':
      return 'Gerando hash…';
    case 'checking-duplicate':
      return 'Verificando duplicata…';
    case 'parsing':
      return 'Processando arquivo…';
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

/**
 * Nota informativa exibida abaixo do file input quando a conta é cartão
 * (FRONT 1.4): o pagamento da fatura vive no extrato da conta corrente, não
 * na fatura — evita o usuário misturar os dois arquivos.
 */
function CardInvoiceNote() {
  return (
    <p role="note" className="text-muted-foreground flex items-start gap-2 text-xs leading-snug">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>
        Inclua somente o arquivo da fatura do cartão. O pagamento da fatura aparecerá no extrato da
        conta corrente — não inclua aqui.
      </span>
    </p>
  );
}

function formatAccountLabel(account: BankAccount): string {
  const base =
    account.bank_name && account.bank_name !== '—'
      ? `${account.name} — ${account.bank_name}`
      : account.name;
  // Cartão (CR) ganha sufixo "(Cartão)". `isCreditCardAccount` cuida do M-1
  // (`CA` é aplicação, não cartão) e da normalização de espaço/caixa do Omie.
  return isCreditCardAccount(account.account_type) ? `${base} (Cartão)` : base;
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
