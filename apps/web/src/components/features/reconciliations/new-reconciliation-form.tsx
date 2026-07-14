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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useClientDetail } from '@/hooks/use-clients';
import {
  useCheckDuplicate,
  useCreateReconciliation,
  useParseStatement,
} from '@/hooks/use-reconciliations';
import { ApiError } from '@/lib/api/client';
import type { BankAccount } from '@/lib/api/clients';
import type { ChecksumResult, ParsedStatement } from '@/lib/api/reconciliations';
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
import { ParsePreview } from './parse-preview';

const FILE_ACCEPT = ALLOWED_EXTENSIONS.map((ext) => `.${ext}`).join(',');

/**
 * Código do erro de truncamento da IA (BACK 02.1). Quando o `POST /parse`
 * responde 422 com este code, a extração foi cortada no meio (`stop_reason ==
 * "max_tokens"`) e NADA foi importado — surfaçamos como erro persistente.
 */
const PARSE_TRUNCATED_CODE = 'ADL-PARSE-TRUNCADO';

/**
 * Código do 409 de duplicata (BACK 02.6). O `POST /parse` agora barra o
 * arquivo já importado ANTES de chamar a IA. Chega aqui quando o pré-check V3
 * foi pulado ou houve corrida entre o `/check-duplicate` e o `/parse`.
 */
const DUPLICATE_FILE_CODE = 'DUPLICATE_FILE';

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
  const [view, setView] = useState<View>('form');
  const [parsed, setParsed] = useState<ParsedStatement | null>(null);
  // Resultado do checksum de saldos (BACK 02.3) que acompanha o parse. Quando
  // `ok=false`, a prévia bloqueia a confirmação e exibe `reason`.
  const [checksum, setChecksum] = useState<ChecksumResult | null>(null);
  // Mensagem de parse TRUNCADO (BACK 02.1 / ADL-PARSE-TRUNCADO): erro
  // persistente e amigável no form — a sessão falhou, nada foi importado.
  const [parseError, setParseError] = useState<string | null>(null);
  // Mensagem do 409 de duplicata vindo do próprio `/parse` (BACK 02.6). Guarda
  // a `userMessage` do back (com a data de importação) para um alerta acionável
  // — `null` no caminho de duplicata do pré-check V3 (que não tem essa data).
  const [duplicateInfo, setDuplicateInfo] = useState<string | null>(null);
  // Hash do arquivo aceito pelo /parse — preservado para o POST de criação
  // da sessão (S10). Resetado junto com `parsed` no cancel/select-other-file.
  const [submittedHash, setSubmittedHash] = useState<string | null>(null);
  const checkDuplicate = useCheckDuplicate();
  const parseStatement = useParseStatement();
  const createReconciliation = useCreateReconciliation();

  async function onSubmit(values: NewReconciliationFormValues) {
    // V1 (zod resolver): se chegou aqui, campos obrigatórios + formato +
    // tamanho ≤ 20 MB já passaram. Não precisa repetir.

    // Nova tentativa: limpa erros persistentes de uma tentativa anterior.
    setParseError(null);
    setDuplicateInfo(null);

    // V2 — hash SHA-256 client-side. OPCIONAL (BACK 02.6): o servidor recalcula
    // o hash do conteúdo no /parse e é a autoridade da dedup. Se o cálculo local
    // falhar (ex: crypto.subtle indisponível), NÃO bloqueamos — seguimos para o
    // /parse, que revalida no servidor. O hash, quando disponível, é reusado no
    // pré-check V3 e no POST de criação (S10).
    setStep('hashing');
    let hash: string | null = null;
    try {
      hash = await sha256Hex(values.file);
    } catch {
      hash = null;
    }

    // V3 — pré-check advisory de duplicata (só quando temos o hash local). O
    // /parse (V4) faz a dedup definitiva no servidor, então sem hash aqui
    // apenas pulamos direto para o parse.
    if (hash !== null) {
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
    }

    // V4 — parse via Claude (Doc §12). Endpoint stateless: nada persiste no
    // back até o usuário confirmar em S10. Erros 4xx/5xx do back já vêm com
    // `userMessage` em PT-BR (PARSE_ERROR, FILE_TOO_LARGE, INVALID_FILE,
    // timeout 504, auth fault 502 — ver `lib/api/reconciliations.ts`).
    setStep('parsing');
    try {
      const result = await parseStatement.mutateAsync({
        client_id: clientId,
        file: values.file,
      });
      setParsed(result.statement);
      setChecksum(result.checksum);
      // Hash local (quando calculado) — reusado no POST /reconciliations sem
      // recalcular. Pode ser `null` se o cálculo client-side falhou; nesse caso
      // o botão de confirmar já orienta reenviar (o create exige o hash).
      setSubmittedHash(hash);
      setView('preview');
      setStep('idle');
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      const userMessage =
        apiErr?.userMessage ?? 'Não foi possível processar o arquivo. Tente novamente.';
      if (apiErr?.code === PARSE_TRUNCATED_CODE) {
        // Truncamento (BACK 02.1): erro persistente e prominente no form — a
        // extração perdeu linhas e NADA foi importado. Não é uma tela que
        // "parece certa"; o operador precisa dividir o arquivo e reenviar.
        setStep('idle');
        setParseError(userMessage);
      } else if (apiErr?.code === DUPLICATE_FILE_CODE) {
        // Duplicata barrada DENTRO do /parse (BACK 02.6), sem gastar IA. Bloqueio
        // terminal com mensagem acionável (quando importado + para onde ir) —
        // não um erro genérico de framework. Reusa o step `duplicate-blocked`
        // para trocar "Processar" por "Selecionar outro arquivo".
        setStep('duplicate-blocked');
        setDuplicateInfo(userMessage);
      } else {
        setStep('idle');
        toast.error(userMessage);
      }
    }
  }

  function handlePreviewCancel() {
    // Volta ao form sem resetar o RHF: o `useForm` continua montado por causa
    // do render condicional logo abaixo, então todos os values estão intactos.
    setParsed(null);
    setChecksum(null);
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
        date_tolerance_days: values.tolerance_days,
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
    setParseError(null);
    setDuplicateInfo(null);
  }

  const isPipelineRunning =
    step === 'hashing' || step === 'checking-duplicate' || step === 'parsing';
  const isDuplicateBlocked = step === 'duplicate-blocked';
  const isSubmitting = form.formState.isSubmitting || isPipelineRunning;
  const inputsDisabled = isSubmitting || isDuplicateBlocked;
  const showPreview = view === 'preview' && parsed !== null && checksum !== null;

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <header className="space-y-3">
        <Breadcrumb clientId={clientId} clientName={clientName} />
        <h1 className="text-2xl font-semibold">Nova Conciliação</h1>
      </header>

      {showPreview && parsed && checksum && (
        <ParsePreview
          parsed={parsed}
          checksum={checksum}
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
                    onChange={(file) => {
                      field.onChange(file ?? undefined);
                      // Trocar o arquivo dissolve o erro de truncamento anterior.
                      setParseError(null);
                    }}
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

          {isDuplicateBlocked &&
            (duplicateInfo !== null ? (
              <DuplicateParseAlert clientId={clientId} message={duplicateInfo} />
            ) : (
              <DuplicateBlockAlert />
            ))}

          {parseError !== null && <TruncatedParseAlert message={parseError} />}

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
 * Alerta acionável do 409 de duplicata vindo do `POST /parse` (BACK 02.6).
 * Diferente do `DuplicateBlockAlert` do pré-check V3, este traz a `userMessage`
 * do backend (com a DATA em que o extrato foi importado) e um caminho claro
 * PARA ONDE ir: o histórico de conciliações do cliente, onde a conciliação
 * existente pode ser aberta. O `session_id` da duplicata não é exposto no
 * corpo do erro (só em logs/Sentry), então navegamos para a lista do cliente
 * em vez de um deep-link à sessão específica.
 */
function DuplicateParseAlert({ clientId, message }: { clientId: string; message: string }) {
  return (
    <div
      role="alert"
      className="bg-destructive/5 border-destructive/30 text-destructive flex items-start gap-3 rounded-lg border p-4 text-sm"
    >
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div className="space-y-2">
        <div className="space-y-1">
          <p className="font-semibold">Este extrato já foi importado</p>
          <p className="leading-snug">{message}</p>
        </div>
        <Button asChild variant="outline" size="sm">
          <Link href={`/clientes/${clientId}`}>Ver conciliações do cliente</Link>
        </Button>
      </div>
    </div>
  );
}

/**
 * Alerta persistente de parse TRUNCADO (BACK 02.1 / ADL-PARSE-TRUNCADO).
 * A extração foi cortada no meio e NADA foi importado — o operador precisa
 * dividir o arquivo em períodos menores e reenviar. `message` é a `userMessage`
 * em PT-BR vinda do backend (já pronta para exibição). Token destrutivo — este
 * é um erro de dado, não um aviso de sucesso.
 */
function TruncatedParseAlert({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="bg-destructive/5 border-destructive/30 text-destructive flex items-start gap-3 rounded-lg border p-4 text-sm"
    >
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-semibold">Arquivo grande demais — extração interrompida</p>
        <p className="leading-snug">{message}</p>
      </div>
    </div>
  );
}

function formatAccountLabel(account: BankAccount): string {
  const base =
    account.bank_name && account.bank_name !== '—'
      ? `${account.name} — ${account.bank_name}`
      : account.name;
  // Marca cartão de crédito (CR) — auditoria M-1: `CA` na Omie é Conta
  // Aplicação, não cartão. Normalização tolera espaço/case do Omie.
  const normalizedType = account.account_type.trim().toUpperCase();
  return normalizedType === 'CR' ? `${base} (Cartão)` : base;
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
