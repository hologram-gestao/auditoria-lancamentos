'use client';

/**
 * Gaveta "Criar conciliação" — Sprint 4 / R2 (+ R5, multi-arquivo).
 *
 * Substitui a PÁGINA `/conciliacao/nova`, que tirava a pessoa da lista e, ao
 * processar, a prendia numa tela de progresso com polling. Aqui:
 *
 *   Step 1 — conta bancária + mês de referência;
 *   Step 2 — N arquivos (cada um com seu próprio hash/duplicata/extração) e a
 *            confirmação.
 *
 * Ao confirmar, o backend cria a sessão, devolve o `session_id` NA HORA e toca
 * o processamento em background: a gaveta fecha, sai um toast, e o item aparece
 * sozinho na lista com "Em processamento". **Ninguém fica preso em tela alguma**
 * — pode navegar, fechar a aba, o servidor conclui e notifica.
 *
 * **409 CONFLICT tem saída.** Uma conciliação é *uma conta + um mês*: se já
 * existe uma para essa combinação, criar outra é 409. Em vez de despejar o erro
 * e deixar a pessoa sem caminho, a gaveta localiza a conciliação existente e
 * oferece **anexar as partes a ela** (cenário S-3: "a parte 2 chegou no dia
 * seguinte"). Sem isso a nova unicidade seria um beco sem saída.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { AlertTriangle, ArrowLeft, ArrowRight, Info, Loader2, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';

import { Button, buttonVariants } from '@/components/ui/button';
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
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { ApiError } from '@/lib/api/client';
import { isCreditCardAccount, listReconciliations, type BankAccount } from '@/lib/api/clients';
import { attachSessionFiles, createReconciliation } from '@/lib/api/reconciliations';
import { formatReferenceMonth } from '@/lib/format';
import { cn } from '@/lib/utils';
import {
  ALLOWED_EXTENSIONS,
  MAX_FILE_SIZE_LABEL,
  currentMonth,
  reconciliationMetaSchema,
  type ReconciliationMetaValues,
} from '@/lib/validation/reconciliations';

import { UploadItemRow } from './upload-item-row';
import { useFilePipeline } from './use-file-pipeline';

const FILE_ACCEPT = ALLOWED_EXTENSIONS.map((ext) => `.${ext}`).join(',');
/** Teto de partes por conciliação — igual ao `maxItems` do contrato. */
const MAX_FILES = 20;

export interface CreatedReconciliation {
  sessionId: string;
  totalFiles: number;
}

interface CreateReconciliationDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  accounts: BankAccount[];
  /** Chamado após a criação/anexação — o pai fecha, dá o toast e refetcha. */
  onCreated: (created: CreatedReconciliation) => void;
}

export function CreateReconciliationDrawer(props: CreateReconciliationDrawerProps) {
  // Remount por `key`: cada abertura começa com estado limpo (form, arquivos,
  // step). Sem isso, reabrir mostraria os arquivos do envio anterior.
  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      {props.open && <DrawerContent {...props} />}
    </Sheet>
  );
}

function DrawerContent({
  onOpenChange,
  clientId,
  accounts,
  onCreated,
}: CreateReconciliationDrawerProps) {
  const [step, setStep] = useState<1 | 2>(1);
  const [submitting, setSubmitting] = useState(false);
  /** Sessão existente da mesma conta+mês, descoberta após um 409. */
  const [conflictSessionId, setConflictSessionId] = useState<string | null>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const pipeline = useFilePipeline();

  const form = useForm<ReconciliationMetaValues>({
    resolver: zodResolver(reconciliationMetaSchema),
    defaultValues: {
      reference_month: '',
    } as Partial<ReconciliationMetaValues> as ReconciliationMetaValues,
    mode: 'onSubmit',
  });

  const sortedAccounts = [...accounts].sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'));
  const hasAccounts = sortedAccounts.length > 0;

  // `useWatch` (e não `getValues`) para o Step 2 refletir uma troca feita depois
  // de "Voltar" — `getValues` não agenda re-render.
  const watchedAccountId = useWatch({ control: form.control, name: 'omie_conta_id' });
  const watchedMonth = useWatch({ control: form.control, name: 'reference_month' }) ?? '';
  const meta = { omie_conta_id: watchedAccountId, reference_month: watchedMonth };
  const selectedAccount = sortedAccounts.find((a) => a.omie_conta_id === Number(meta.omie_conta_id));
  const parsedCount = pipeline.items.filter((it) => it.status === 'parsed').length;
  const canConfirm = parsedCount > 0 && !pipeline.isProcessing && !submitting;

  async function handleAdvance(values: ReconciliationMetaValues) {
    // O Zod do step 1 já validou; guardar os values no RHF basta.
    void values;
    setStep(2);
  }

  function handleFilesChosen(event: React.ChangeEvent<HTMLInputElement>) {
    const chosen = Array.from(event.target.files ?? []);
    // Limpa o input para permitir reescolher o MESMO arquivo depois de removê-lo
    // (sem isso o `change` não dispara e a pessoa acha que travou).
    event.target.value = '';
    if (chosen.length === 0) return;

    const room = MAX_FILES - pipeline.items.length;
    if (room <= 0) {
      toast.error(`Uma conciliação aceita no máximo ${MAX_FILES} arquivos.`);
      return;
    }
    if (chosen.length > room) {
      toast.error(`Só cabem mais ${room} arquivo(s) nesta conciliação.`);
    }
    void pipeline.addFiles(chosen.slice(0, room), {
      clientId,
      omieContaId: Number(meta.omie_conta_id),
      month: meta.reference_month,
    });
  }

  /**
   * Descobre a conciliação já existente para esta conta+mês. O 409 do backend
   * traz a orientação em PT-BR mas NÃO o `session_id` (metadata não vai na
   * resposta), então a lista filtrada é o caminho para oferecer "anexar".
   */
  async function findExistingSession(): Promise<string | null> {
    try {
      const res = await listReconciliations(clientId, {
        page: 1,
        pageSize: 1,
        omie_conta_id: Number(meta.omie_conta_id),
        month: meta.reference_month,
      });
      return res.data[0]?.id ?? null;
    } catch {
      return null;
    }
  }

  async function handleConfirm() {
    const parts = pipeline.toParts();
    if (parts.filter((p) => p.statement != null).length === 0) {
      toast.error('Nenhum arquivo foi extraído com sucesso. Adicione ao menos um válido.');
      return;
    }

    setSubmitting(true);
    try {
      const result = await createReconciliation({
        client_id: clientId,
        omie_conta_id: Number(meta.omie_conta_id),
        // O contrato quer `date`: dia 1 do mês de referência.
        reference_month: `${meta.reference_month}-01`,
        files: parts,
      });
      onCreated({ sessionId: result.session_id, totalFiles: result.total_files });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.code === 'CONFLICT') {
        const existing = await findExistingSession();
        setConflictSessionId(existing);
        toast.error(err.userMessage);
      } else {
        toast.error(
          err instanceof ApiError
            ? err.userMessage
            : 'Não foi possível criar a conciliação. Tente novamente.',
        );
      }
    } finally {
      // Reabilita em sucesso OU erro — o botão nunca fica preso.
      setSubmitting(false);
    }
  }

  async function handleAttachToExisting() {
    if (conflictSessionId === null) return;
    setSubmitting(true);
    try {
      const result = await attachSessionFiles(conflictSessionId, pipeline.toParts());
      onCreated({ sessionId: result.session_id, totalFiles: result.total_files });
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.userMessage
          : 'Não foi possível anexar os arquivos à conciliação existente.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <SheetContent
      side="right"
      aria-label="Criar conciliação"
      // Foco inicial no PRIMÁRIO (design-system) em vez do primeiro campo.
      onOpenAutoFocus={(e) => {
        e.preventDefault();
        primaryRef.current?.focus();
      }}
      // Fechar com o processamento em voo perderia extrações já pagas à IA.
      onInteractOutside={(e) => {
        if (pipeline.isProcessing || submitting) e.preventDefault();
      }}
    >
      <SheetHeader>
        <SheetTitle>Criar conciliação</SheetTitle>
        <SheetDescription>
          Passo {step} de 2 —{' '}
          {step === 1 ? 'conta e mês de referência' : 'arquivos da conciliação'}
        </SheetDescription>
      </SheetHeader>

      <SheetBody>
        {/* O passo inativo é DESMONTADO (não escondido com `hidden`): markup
            focável dentro de um container `aria-hidden` é violação de a11y, e
            o RHF preserva os valores mesmo com os campos desmontados
            (`shouldUnregister: false` é o default), então "Voltar" volta com
            tudo no lugar. */}
        {step === 1 && (
          <Form {...form}>
            <form id="create-reconciliation-step1" onSubmit={form.handleSubmit(handleAdvance)}>
              <div className="space-y-6">
                <FormField
                control={form.control}
                name="omie_conta_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Conta bancária</FormLabel>
                    <Select
                      onValueChange={(v) => field.onChange(Number(v))}
                      value={field.value !== undefined ? String(field.value) : undefined}
                      disabled={!hasAccounts}
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
                    {!hasAccounts && (
                      <FormDescription>
                        Nenhuma conta sincronizada. Extraia as contas do Omie em &quot;Contas
                        Bancárias&quot; antes de criar uma conciliação.
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
                    <FormLabel>Mês de referência</FormLabel>
                    <FormControl>
                      <input
                        type="month"
                        lang="pt-BR"
                        max={currentMonth()}
                        disabled={!hasAccounts}
                        aria-label="Mês de referência"
                        className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full cursor-pointer rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              </div>
            </form>
          </Form>
        )}

        {step === 2 && (
        <div className="space-y-4">
          <div className="bg-muted/40 rounded-md border p-3 text-sm">
            <p className="font-medium">{selectedAccount?.name ?? '—'}</p>
            <p className="text-muted-foreground text-xs">
              {formatReferenceMonth(meta.reference_month)}
            </p>
          </div>

          <div className="space-y-2">
            <input
              id="reconciliation-files"
              type="file"
              multiple
              accept={FILE_ACCEPT}
              onChange={handleFilesChosen}
              disabled={pipeline.isProcessing || submitting}
              className="sr-only"
            />
            <label
              htmlFor="reconciliation-files"
              aria-disabled={pipeline.isProcessing || submitting || undefined}
              className={cn(
                buttonVariants({ variant: 'outline' }),
                'w-full cursor-pointer',
                (pipeline.isProcessing || submitting) && 'pointer-events-none opacity-50',
              )}
            >
              <Upload className="h-4 w-4" aria-hidden="true" />
              Adicionar arquivos
            </label>
            <p className="text-muted-foreground text-xs">
              Uma fatura grande pode vir quebrada em partes — envie todas aqui e elas viram um
              resumo só. Formatos: {ALLOWED_EXTENSIONS.join(', ').toUpperCase()} · máx.{' '}
              {MAX_FILE_SIZE_LABEL} por arquivo · até {MAX_FILES} arquivos.
            </p>
          </div>

          {pipeline.items.length > 0 && (
            <ul className="space-y-2" aria-label="Arquivos desta conciliação">
              {pipeline.items.map((item) => (
                <UploadItemRow
                  key={item.id}
                  item={item}
                  onRemove={() => pipeline.remove(item.id)}
                  disabled={submitting}
                />
              ))}
            </ul>
          )}

          {pipeline.isProcessing && (
            <p role="status" className="text-muted-foreground flex items-center gap-2 text-sm">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Extraindo movimentações com IA. Isso pode levar até 60 segundos por arquivo.
            </p>
          )}

          {selectedAccount !== undefined && isCreditCardAccount(selectedAccount.account_type) && (
            <p role="note" className="text-muted-foreground flex items-start gap-2 text-xs">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>
                Inclua somente arquivos da fatura do cartão. O pagamento da fatura aparece no
                extrato da conta corrente — não inclua aqui.
              </span>
            </p>
          )}

          {conflictSessionId !== null && (
            <div
              role="alert"
              className="border-info/40 bg-info-muted text-info flex items-start gap-3 rounded-md border p-3 text-sm"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <div className="space-y-2">
                <p>
                  Já existe uma conciliação para esta conta e mês. Você pode adicionar estes
                  arquivos a ela como novas partes.
                </p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void handleAttachToExisting()}
                  disabled={submitting}
                >
                  {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                  Adicionar à conciliação existente
                </Button>
              </div>
            </div>
          )}
        </div>
        )}
      </SheetBody>

      {/* Cancelar à ESQUERDA, primária à DIREITA (`justify-between` do shell). */}
      <SheetFooter>
        {step === 1 ? (
          <>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancelar
            </Button>
            <Button
              ref={primaryRef}
              type="submit"
              form="create-reconciliation-step1"
              disabled={!hasAccounts}
            >
              Avançar
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </>
        ) : (
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => setStep(1)}
              disabled={pipeline.isProcessing || submitting}
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              Voltar
            </Button>
            <Button
              ref={primaryRef}
              type="button"
              onClick={() => void handleConfirm()}
              disabled={!canConfirm}
              aria-live="polite"
            >
              {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {submitting ? 'Criando…' : `Confirmar (${parsedCount})`}
            </Button>
          </>
        )}
      </SheetFooter>
    </SheetContent>
  );
}

function formatAccountLabel(account: BankAccount): string {
  const base =
    account.bank_name && account.bank_name !== '—'
      ? `${account.name} — ${account.bank_name}`
      : account.name;
  return isCreditCardAccount(account.account_type) ? `${base} (Cartão)` : base;
}
