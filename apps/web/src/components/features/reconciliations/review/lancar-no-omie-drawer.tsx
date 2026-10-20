'use client';

/**
 * Gaveta "Lançar no Omie" (Sprint 7 / FRONT 07.6 · 07.7; provisão no Contas a
 * Pagar desde a 86e3n70q0, bloco C).
 *
 * É a última barreira antes de o sistema **gravar na contabilidade do cliente**:
 * o operador vê exatamente quais compras vão, escolhe a FORMA e classifica cada
 * uma, e confirma. Nunca auto-fire — decisão de segurança do PRD (S-2).
 *
 * **Duas formas, por linha** (padrão `provisionar`, igual ao servidor):
 *   - provisão: título no Contas a Pagar, com fornecedor (busca por CNPJ,
 *     cadastro na hora ou o padrão do cliente), categoria única ou dividida e
 *     rateio por departamento; no processo "na data da compra" o servidor dá a
 *     baixa na conta do cartão, no do vencimento o título fica a vencer;
 *   - direto: o `IncluirLancCC` da Sprint 7, só no processo "na data da compra".
 * A linha que já teve envio numa forma abre TRAVADA nela (`lockedPostingMode`,
 * espelho de `_kind_switch_allowed`).
 *
 * Estrutura do design-system (`ui/sheet.tsx`): header fixo · miolo rolável ·
 * footer com **Cancelar à esquerda** e a ação primária à direita.
 *
 * **Duas fases numa gaveta só.** Antes do envio, a gaveta classifica; depois,
 * ela vira o RESUMO por linha. Fechar no sucesso parcial seria esconder
 * justamente o que o operador precisa ler (quem entrou, onde parou, por quê) — e
 * é da mesma tela que ele reexecuta as que não terminaram.
 *
 * **Sem sugestão automática** de categoria, fornecedor ou departamento: só o que a
 * pessoa escolhe.
 */

import { AlertTriangle, CheckCircle2, Loader2, Upload, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';

import { OmieSupplierLookup } from '@/components/features/omie-suppliers/omie-supplier-lookup';
import { Money } from '@/components/shared/money';
import { Button } from '@/components/ui/button';
import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
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
  collectPosted,
  isPostingResultConcluded,
  useOmieCategorias,
  useOmieDepartamentos,
  useOmieSupplierSuggestions,
  usePostOmieLancamentos,
  withSeries,
} from '@/hooks/use-omie-postings';
import { ApiError, NetworkError } from '@/lib/api/client';
import type {
  OmieFornecedorItem,
  OmiePostingBatchPayload,
  OmiePostingInstallmentResult,
  OmiePostingLineRequest,
  OmiePostingLineResult,
  OmiePostingState,
} from '@/lib/api/omie-postings';
import type { FileEntryItem } from '@/lib/api/reconciliations';
import { PRODUCT_SHORT_NAME } from '@/lib/brand';
import type { OmiePostingMode } from '@/lib/contracts';
import { formatBRDate } from '@/lib/format';
import { cn } from '@/lib/utils';
import {
  LINE_ISSUE_MESSAGE,
  LINE_ISSUE_SHORT,
  buildPostingLine,
  emptyDraft,
  initialDepartmentParts,
  lineIssue,
  newPartKey,
  type DepartmentPart,
  type LineContext,
  type LineDraft,
  type LineIssue,
  type SupplierValue,
} from '@/lib/validation/omie-posting';

import { lockedPostingMode, processAllowsDirectPosting } from './omie-posting-eligibility';
import {
  DefaultSupplierName,
  DueDateProcessNote,
  POSTING_STEP_LABEL,
  PostingLineEditor,
  type PostingEditorContext,
} from './posting-line-editor';
import { DepartmentSplitEditor, PostingModeChoice } from './posting-split-editors';

/** O que a gaveta precisa saber do cliente e da conciliação. */
export interface PostingClientContext {
  clientId: string;
  /** Processo do cartão gravado na SESSÃO (`null` = sessão antiga, o de sempre). */
  cardPostingDateMode: string | null | undefined;
  /** Vencimento da fatura da sessão (ISO), no processo do vencimento. */
  invoiceDueDate: string | null | undefined;
  /** `clients.omie_department_required`. */
  departmentRequired: boolean;
  /** `clients.omie_default_supplier_code`. */
  defaultSupplierCode: number | null;
  /**
   * 86e3qhhtb — a sessão oferece provisionar as parcelas futuras de compra
   * parcelada (`installment_posting_enabled`, o kill-switch do servidor).
   */
  installmentPostingEnabled: boolean;
}

interface LancarNoOmieDrawerProps {
  sessionId: string;
  context: PostingClientContext;
  /** Compras selecionadas na tabela — sempre já filtradas por elegibilidade. */
  entries: FileEntryItem[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Linhas concluídas no Omie neste envio (id → estado). A tabela as tira da seleção. */
  onPosted: (results: Record<string, OmiePostingState>) => void;
}

/** O "aplicar a todas": o que a pessoa escolheu uma vez para valer em todas as linhas. */
interface BatchDraft {
  modo: OmiePostingMode;
  categoria: string | null;
  supplier: SupplierValue;
  departments: DepartmentPart[];
}

/** Teto de linhas por pedido de sugestão de fornecedor (o mesmo do servidor). */
const SUPPLIER_SUGGESTION_MAX_ENTRIES = 200;

export function LancarNoOmieDrawer({
  sessionId,
  context,
  entries,
  open,
  onOpenChange,
  onPosted,
}: LancarNoOmieDrawerProps) {
  const postMutation = usePostOmieLancamentos(sessionId);
  // Só busca quando a gaveta abre: no MISS do cache o backend vai ao Omie, e
  // ninguém paga essa ida por abrir a tela de revisão.
  const categoriasQuery = useOmieCategorias(sessionId, { enabled: open });
  const departamentosQuery = useOmieDepartamentos(context.clientId, { enabled: open });
  // 86e3qhhrd — sugestão de fornecedor pela descrição da compra, numa ida só para
  // as linhas que ainda vão escolher fornecedor (nem travadas no direto, nem com o
  // fornecedor já gravado numa provisão retomada). O teto é o do servidor.
  const suggestionEntryIds = useMemo(
    () =>
      entries
        .filter((e) => {
          const posting = e.omie_posting ?? null;
          if (posting?.kind === 'provisao' && posting.supplier_code !== null) return false;
          return lockedPostingMode(posting) !== 'direto';
        })
        .map((e) => e.id)
        .slice(0, SUPPLIER_SUGGESTION_MAX_ENTRIES),
    [entries],
  );
  const suggestionsQuery = useOmieSupplierSuggestions(sessionId, suggestionEntryIds, {
    enabled: open,
  });
  const suggestionsByEntry = useMemo(() => {
    const map = new Map<string, readonly OmieFornecedorItem[]>();
    suggestionsQuery.data?.forEach((s) => map.set(s.file_entry_id, s.sugestoes));
    return map;
  }, [suggestionsQuery.data]);
  const allowDirect = processAllowsDirectPosting({
    card_posting_date_mode: context.cardPostingDateMode,
  });

  const [drafts, setDrafts] = useState<Record<string, LineDraft>>({});
  const [batch, setBatch] = useState<BatchDraft>({
    modo: 'provisionar',
    categoria: null,
    supplier: { kind: 'none' },
    departments: [],
  });
  /** Resumo do último envio. Enquanto `null`, a gaveta está na fase de classificar. */
  const [result, setResult] = useState<OmiePostingBatchPayload | null>(null);

  const categoryOptions: ComboboxOption[] = useMemo(
    () =>
      (categoriasQuery.data?.data ?? []).map((c) => ({
        value: c.codigo,
        label: `${c.codigo} · ${c.descricao}`,
      })),
    [categoriasQuery.data],
  );
  const departmentOptions: ComboboxOption[] = useMemo(
    () =>
      (departamentosQuery.data?.data ?? []).map((d) => ({
        value: d.codigo,
        label: d.estrutura ? `${d.estrutura} · ${d.descricao}` : d.descricao,
      })),
    [departamentosQuery.data],
  );

  const resultByEntry = useMemo(() => {
    const map = new Map<string, OmiePostingLineResult>();
    result?.lines.forEach((line) => map.set(line.file_entry_id, line));
    return map;
  }, [result]);

  /** A forma travada de cada linha (pela intenção gravada no servidor). */
  /** A intenção da linha: a do servidor ao abrir, ou a que o último envio deixou. */
  function postingFor(entry: FileEntryItem): OmiePostingState | null {
    return postingFromResult(resultByEntry.get(entry.id), entry.omie_posting ?? null);
  }

  function lockedFor(entry: FileEntryItem): OmiePostingMode | null {
    return lockedPostingMode(postingFor(entry));
  }

  /** O rascunho da linha; a 1ª leitura nasce na forma travada ou no padrão. */
  function draftFor(entry: FileEntryItem): LineDraft {
    const existing = drafts[entry.id];
    if (existing !== undefined) return existing;
    return emptyDraft(lockedFor(entry) ?? 'provisionar');
  }

  function lineContextFor(entry: FileEntryItem): LineContext {
    const posting = postingFor(entry);
    return {
      amount: entry.amount,
      departmentRequired: context.departmentRequired,
      hasDefaultSupplier: context.defaultSupplierCode !== null,
      supplierSettled: posting?.kind === 'provisao' && posting.supplier_code !== null,
    };
  }

  /** No processo do vencimento só existe a provisão (salvo uma linha travada em direto). */
  function effectiveDraft(entry: FileEntryItem): LineDraft {
    const draft = draftFor(entry);
    const locked = lockedFor(entry);
    if (locked !== null) return { ...draft, modo: locked };
    if (!allowDirect) return { ...draft, modo: 'provisionar' };
    return draft;
  }

  const pendentes = entries.filter((e) => {
    const line = resultByEntry.get(e.id);
    return line === undefined || !isPostingResultConcluded(line);
  });

  const evaluated = pendentes.map((entry) => {
    const draft = effectiveDraft(entry);
    const ctx = lineContextFor(entry);
    return { entry, draft, ctx, issue: lineIssue(draft, ctx) };
  });

  /**
   * O que seria enviado agora: só as linhas completas. Depois de um envio, as que
   * já terminaram saem da lista — reenviá-las voltaria "já lançada" e poluiria o
   * resumo com uma linha que o operador não pediu de novo.
   */
  const lines: OmiePostingLineRequest[] = evaluated
    .filter((e) => e.issue === null)
    .map((e) => buildPostingLine(e.entry.id, e.draft, e.ctx));
  const deFora = evaluated.filter((e) => e.issue !== null);
  const issuesDeFora = new Set(deFora.map((e) => e.issue));

  const categoriasIndisponiveis = categoriasQuery.isError;

  function setDraft(entry: FileEntryItem, draft: LineDraft): void {
    setDrafts((prev) => ({ ...prev, [entry.id]: draft }));
  }

  function aplicarEmLote(): void {
    setDrafts((prev) => {
      const next = { ...prev };
      // Vale para as PENDENTES: reclassificar uma linha já concluída não faria
      // nada além de sugerir que ela pode mudar.
      pendentes.forEach((entry) => {
        const current = prev[entry.id] ?? emptyDraft(lockedFor(entry) ?? 'provisionar');
        const updated: LineDraft = {
          ...current,
          modo: lockedFor(entry) ?? (allowDirect ? batch.modo : 'provisionar'),
        };
        if (batch.categoria !== null) {
          updated.categoria = batch.categoria;
          updated.split = false;
        }
        if (batch.supplier.kind !== 'none') updated.supplier = batch.supplier;
        if (batch.departments.length > 0) {
          // Chaves novas por linha: a mesma parte em duas linhas não divide estado.
          updated.departments = batch.departments.map((d) => ({ ...d, key: newPartKey() }));
        }
        next[entry.id] = updated;
      });
      return next;
    });
  }

  async function handleConfirm(): Promise<void> {
    if (lines.length === 0 || postMutation.isPending) return;
    try {
      const payload = await postMutation.mutateAsync(lines);
      setResult(payload);
      onPosted(collectPosted(payload));
      notifyOutcome(payload);
    } catch (err) {
      // Erro do LOTE inteiro (recurso desligado, sessão que não é de cartão,
      // teto de linhas, Omie fora sem nada lançado). A gaveta continua aberta:
      // fechá-la levaria embora a classificação que o operador acabou de fazer.
      toast.error(resolvePostErrorMessage(err));
    }
  }

  const tudoConcluido = result !== null && pendentes.length === 0;
  const editorContext: PostingEditorContext = {
    clientId: context.clientId,
    allowDirect,
    departmentRequired: context.departmentRequired,
    defaultSupplierCode: context.defaultSupplierCode,
    categoryOptions,
    categoriesLoading: categoriasQuery.isLoading,
    categoriesUnavailable: categoriasIndisponiveis,
    departmentOptions,
    departmentsLoading: departamentosQuery.isLoading,
    departmentsUnavailable: departamentosQuery.isError,
    onRetryDepartments: () => void departamentosQuery.refetch(),
    retryingDepartments: departamentosQuery.isFetching,
    installmentPostingEnabled: context.installmentPostingEnabled,
    disabled: postMutation.isPending,
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) setResult(null);
        onOpenChange(next);
      }}
    >
      <SheetContent className="sm:max-w-2xl">
        <SheetHeader>
          <SheetTitle>Lançar no Omie</SheetTitle>
          <SheetDescription>
            {entries.length === 1
              ? 'A compra abaixo será lançada no Omie do cliente, na forma escolhida.'
              : `As ${entries.length} compras abaixo serão lançadas no Omie do cliente, cada uma na forma escolhida.`}{' '}
            O lançamento é gravado na contabilidade do cliente e não pode ser desfeito pelo{' '}
            {PRODUCT_SHORT_NAME}.
          </SheetDescription>
        </SheetHeader>

        <SheetBody>
          <div className="space-y-4">
            {/* 86e3n70qj — depois do envio, o desfecho fica DENTRO da gaveta, no
                topo: o toast some em segundos, e "cliquei e não sei o que
                aconteceu" foi o relato. Diz quantas entraram e onde. */}
            {result !== null && <ResultadoDoLote result={result} />}
            {!allowDirect && result === null && <DueDateProcessNote />}
            {categoriasIndisponiveis && (
              <div
                role="alert"
                className="bg-destructive-muted text-destructive flex items-start gap-2 rounded-md border p-3 text-sm"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <div className="space-y-2">
                  <p>
                    Não foi possível carregar as categorias do Omie. Sem elas não é possível
                    classificar as compras.
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void categoriasQuery.refetch()}
                    disabled={categoriasQuery.isFetching}
                  >
                    {categoriasQuery.isFetching && (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    )}
                    Tentar novamente
                  </Button>
                </div>
              </div>
            )}

            {!categoriasIndisponiveis && pendentes.length > 1 && (
              <BatchPanel
                batch={batch}
                onChange={setBatch}
                count={pendentes.length}
                onApply={aplicarEmLote}
                context={editorContext}
              />
            )}

            <ul className="divide-border divide-y">
              {entries.map((entry) => {
                const linha = resultByEntry.get(entry.id);
                const concluded = linha !== undefined && isPostingResultConcluded(linha);
                const purchaseLabel = `${formatBRDate(entry.transaction_date)} — ${entry.description}`;
                const ev = evaluated.find((e) => e.entry.id === entry.id);
                return (
                  <li key={entry.id} className="space-y-3 py-3">
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                      <span className="text-muted-foreground w-20 text-sm tabular-nums">
                        {formatBRDate(entry.transaction_date)}
                      </span>
                      <span className="min-w-[8rem] flex-1 text-sm">{entry.description}</span>
                      {/* `ml-auto` + `whitespace-nowrap`: em 390px o valor mais
                          largo não cabe na mesma linha e, sem isto, cai para a
                          linha de baixo ALINHADO À ESQUERDA — enquanto o valor
                          curto da linha vizinha fica à direita. */}
                      <Money value={entry.amount} tone="sign" className="ml-auto text-sm" />
                    </div>

                    {linha !== undefined && (
                      <LinhaResultado
                        line={linha}
                        purchaseDate={entry.transaction_date}
                        context={context}
                      />
                    )}

                    {!concluded && ev !== undefined && (
                      <div className="space-y-2 sm:pl-6">
                        <PostingLineEditor
                          draft={ev.draft}
                          onChange={(draft) => setDraft(entry, draft)}
                          amount={entry.amount}
                          purchaseLabel={purchaseLabel}
                          posting={postingFor(entry)}
                          lockedMode={lockedFor(entry)}
                          supplierSuggestions={suggestionsByEntry.get(entry.id)}
                          installment={entry.installment ?? null}
                          context={editorContext}
                        />
                        {ev.issue !== null && (
                          <p className="text-warning flex items-start gap-1 text-xs">
                            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                            <span>{issueMessage(ev.issue, context)}</span>
                          </p>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        </SheetBody>

        {/* `flex-wrap` só AQUI, não no `SheetFooter` compartilhado: o botão
            primário é largo ("Confirmar e lançar N de M"). Quando nem assim
            couber numa linha em 390px, ele desce inteiro para a linha de baixo
            em vez de ser clipado pela borda. */}
        <SheetFooter className="flex-wrap gap-y-3">
          <Button
            variant="outline"
            className="shrink-0"
            onClick={() => onOpenChange(false)}
            disabled={postMutation.isPending}
          >
            {/* "Concluir" e não "Fechar": o X do canto já se chama Fechar. */}
            {tudoConcluido ? 'Concluir' : 'Cancelar'}
          </Button>
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-2">
            {!tudoConcluido && deFora.length > 0 && (
              <span className="text-muted-foreground min-w-0 text-xs">
                {deFora.length === 1
                  ? '1 compra fica de fora'
                  : `${deFora.length} compras ficam de fora`}{' '}
                (
                {issuesDeFora.size === 1
                  ? LINE_ISSUE_SHORT[[...issuesDeFora][0] as LineIssue]
                  : 'veja o aviso em cada linha'}
                )
              </span>
            )}
            {!tudoConcluido && (
              <Button
                onClick={() => void handleConfirm()}
                disabled={postMutation.isPending || lines.length === 0}
              >
                {postMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    Lançando…
                  </>
                ) : (
                  <>
                    <Upload className="h-4 w-4" aria-hidden="true" />
                    {result === null
                      ? `Confirmar e lançar ${lines.length} de ${pendentes.length}`
                      : `Tentar novamente ${lines.length} de ${pendentes.length}`}
                  </>
                )}
              </Button>
            )}
          </div>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/** O aviso da linha, com a frase do fornecedor certa para quem tem (ou não) padrão. */
function issueMessage(issue: LineIssue, context: PostingClientContext): string {
  if (issue === 'fornecedor_pendente') {
    return context.defaultSupplierCode !== null
      ? 'Escolha o fornecedor: busque pelo nome ou pelo CNPJ, ou marque o fornecedor padrão do cliente. Sem ele esta compra fica de fora (fornecedor pendente).'
      : 'Sem fornecedor: busque pelo nome ou pelo CNPJ. Sem ele esta compra fica de fora (fornecedor pendente).';
  }
  return LINE_ISSUE_MESSAGE[issue];
}

/** O "aplicar a todas": forma, categoria, fornecedor e rateio, uma vez para o lote. */
function BatchPanel({
  batch,
  onChange,
  count,
  onApply,
  context,
}: {
  batch: BatchDraft;
  onChange: (batch: BatchDraft) => void;
  count: number;
  onApply: () => void;
  context: PostingEditorContext;
}) {
  const isProvision = !context.allowDirect || batch.modo === 'provisionar';
  const nothingChosen =
    batch.categoria === null &&
    batch.supplier.kind === 'none' &&
    batch.departments.length === 0 &&
    (!context.allowDirect || batch.modo === 'provisionar');
  return (
    <section
      aria-label="Classificar todas de uma vez"
      className="bg-muted/40 space-y-3 rounded-md border p-3"
    >
      <p className="text-sm font-medium">Classificar todas de uma vez</p>
      {context.allowDirect && (
        <PostingModeChoice
          value={batch.modo}
          onChange={(modo) => onChange({ ...batch, modo })}
          legend="Forma do lançamento para todas as compras"
          disabled={context.disabled}
        />
      )}
      <Combobox
        options={context.categoryOptions}
        value={batch.categoria}
        onValueChange={(categoria) => onChange({ ...batch, categoria })}
        label="Categoria para todas as compras"
        placeholder="Escolher categoria…"
        searchPlaceholder="Buscar categoria…"
        emptyMessage="Nenhuma categoria encontrada."
        loading={context.categoriesLoading}
        disabled={context.disabled}
      />
      {isProvision && (
        <>
          <BatchSupplier batch={batch} onChange={onChange} context={context} />
          <BatchDepartments batch={batch} onChange={onChange} context={context} />
        </>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-muted-foreground min-w-0 flex-1 text-xs">
          Depois de aplicar, cada linha ainda pode ser trocada — a da linha prevalece. Compra com
          envio anterior mantém a forma dela.
        </p>
        <Button variant="secondary" onClick={onApply} disabled={nothingChosen || context.disabled}>
          Aplicar a {count} compras
        </Button>
      </div>
    </section>
  );
}

function BatchSupplier({
  batch,
  onChange,
  context,
}: {
  batch: BatchDraft;
  onChange: (batch: BatchDraft) => void;
  context: PostingEditorContext;
}) {
  const usingDefault = batch.supplier.kind === 'default';
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">Fornecedor para todas</p>
      {context.defaultSupplierCode !== null && (
        <label className="flex cursor-pointer items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={usingDefault}
            disabled={context.disabled}
            onChange={(e) =>
              onChange({
                ...batch,
                supplier: e.target.checked ? { kind: 'default' } : { kind: 'none' },
              })
            }
            className="accent-primary mt-0.5 size-4 cursor-pointer"
          />
          <span>
            Usar o fornecedor padrão do cliente:{' '}
            <DefaultSupplierName clientId={context.clientId} codigo={context.defaultSupplierCode} />
          </span>
        </label>
      )}
      {!usingDefault && (
        <OmieSupplierLookup
          clientId={context.clientId}
          value={batch.supplier}
          onChange={(supplier) => onChange({ ...batch, supplier })}
          allowRegister
          label="Fornecedor para todas as compras"
          disabled={context.disabled}
        />
      )}
    </div>
  );
}

function BatchDepartments({
  batch,
  onChange,
  context,
}: {
  batch: BatchDraft;
  onChange: (batch: BatchDraft) => void;
  context: PostingEditorContext;
}) {
  const on = batch.departments.length > 0;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Switch
          checked={on}
          disabled={context.disabled || context.departmentsUnavailable}
          aria-label="Ratear todas por departamento"
          onCheckedChange={(next) =>
            onChange({ ...batch, departments: next ? initialDepartmentParts() : [] })
          }
        />
        <span className="text-sm">Ratear todas por departamento</span>
      </div>
      {on && (
        <DepartmentSplitEditor
          parts={batch.departments}
          onChange={(departments) => onChange({ ...batch, departments })}
          options={context.departmentOptions}
          amount={null}
          purchaseLabel="todas as compras"
          loading={context.departmentsLoading}
          disabled={context.disabled}
          allowEmpty
        />
      )}
    </div>
  );
}

/**
 * Desfecho de UMA linha, com o motivo do backend. O ícone/cor vêm do `status`
 * (enum fechado do contrato) e o texto é a mensagem que o servidor devolveu — em
 * `erro_omie` ela é a frase VERBATIM do provedor, que é o que a torna acionável.
 */
function LinhaResultado({
  line,
  purchaseDate,
  context,
}: {
  line: OmiePostingLineResult;
  purchaseDate: string;
  context: PostingClientContext;
}) {
  const concluded = isPostingResultConcluded(line);
  const isProvision = line.kind === 'provisao';
  // 86e3qhhtb: título (e baixa) feitos e alguma parcela futura não — não é um
  // passo parado, é a série incompleta; a lista de parcelas abaixo diz qual.
  const seriesIncomplete = line.reason === 'parcelas_incompletas';
  const stoppedMidway =
    !concluded &&
    !seriesIncomplete &&
    isProvision &&
    line.payable_id !== null &&
    line.payable_id !== undefined;
  const presentation = concluded
    ? RESULT_PRESENTATION.lancada
    : RESULT_PRESENTATION[line.status === 'lancada' ? 'bloqueada' : line.status];
  const Icon = presentation.icon;
  const dueProcess = context.cardPostingDateMode === 'invoice_due_date';
  const foundInPreviousInvoice =
    isProvision && line.status === 'lancada' && line.reason === 'ja_lancada';

  let rotulo: string;
  if (concluded && isProvision) {
    rotulo = `Provisionada no Omie · título nº ${line.payable_id ?? '—'}`;
    if (line.payment_id !== null && line.payment_id !== undefined) {
      rotulo += ` · baixa nº ${line.payment_id}`;
    }
  } else if (concluded) {
    rotulo =
      line.omie_lancamento_id !== null && line.omie_lancamento_id !== undefined
        ? `Lançada no Omie · lançamento nº ${line.omie_lancamento_id}`
        : 'Lançada no Omie';
  } else if (seriesIncomplete) {
    rotulo = `Provisionada no Omie · título nº ${line.payable_id ?? '—'} · parcelas futuras pendentes`;
  } else if (stoppedMidway) {
    rotulo = `Parou no passo: ${POSTING_STEP_LABEL[line.step ?? ''] ?? line.step ?? '—'}`;
  } else {
    rotulo = presentation.rotulo;
  }

  return (
    <div className="space-y-0.5 text-xs sm:pl-6">
      <p className={cn('flex items-start gap-2', presentation.tone)}>
        <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>
          <strong className="font-medium">{rotulo}</strong>
          {/* No concluído a frase do servidor ("já foi provisionada…") repete o rótulo;
              a exceção é a parcela ENCONTRADA numa fatura anterior (86e3qhhtb), que
              diz de onde veio o título. */}
          {(!concluded || foundInPreviousInvoice) &&
            line.message !== null &&
            line.message !== undefined && <> — {line.message}</>}
        </span>
      </p>
      {stoppedMidway && (
        <p className="text-muted-foreground pl-5">
          Já gravado no Omie: título nº {line.payable_id}
          {line.payment_id ? `, baixa nº ${line.payment_id}` : ''}. Tentar de novo continua deste
          passo, sem recriar o título.
        </p>
      )}
      {/* ONDE entrou, que é o que a pessoa foi conferir no Omie. */}
      {concluded && isProvision && (
        <p className="text-muted-foreground pl-5">
          {dueProcess
            ? `Título a vencer em ${context.invoiceDueDate ? formatBRDate(context.invoiceDueDate) : '—'} (vencimento da fatura), sem baixa: a baixa é feita quando a fatura for paga.`
            : `Título no Contas a Pagar, baixado na conta do cartão na data da compra (${formatBRDate(purchaseDate)}).`}
        </p>
      )}
      {concluded && !isProvision && (
        <p className="text-muted-foreground pl-5">
          Na conta do cartão no Omie, na data da compra ({formatBRDate(purchaseDate)}).
        </p>
      )}
      {(line.parcelas ?? []).length > 0 && <ParcelasResultado line={line} />}
    </div>
  );
}

/** 86e3qhhtb — cada parcela futura do envio: o título dela, ou onde parou. */
function ParcelasResultado({ line }: { line: OmiePostingLineResult }) {
  return (
    <ul className="space-y-0.5 pl-5" aria-label="Parcelas futuras">
      {(line.parcelas ?? []).map((p) => (
        <li
          key={p.numero}
          className={cn(
            'flex flex-wrap gap-x-2',
            p.status === 'erro'
              ? 'text-destructive'
              : p.status === 'pendente'
                ? 'text-warning'
                : 'text-muted-foreground',
          )}
        >
          <span className="text-foreground font-medium tabular-nums">
            Parcela {p.numero}/{p.total}
          </span>
          <span className="tabular-nums">vence {formatBRDate(p.vencimento)}</span>
          <span>{PARCELA_RESULT_LABEL[p.status](p)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Um rótulo por desfecho da parcela — os quatro do contrato, sem `default`. */
const PARCELA_RESULT_LABEL: Record<
  OmiePostingInstallmentResult['status'],
  (p: OmiePostingInstallmentResult) => string
> = {
  provisionada: (p) => `· título nº ${p.payable_id ?? '—'}`,
  ja_existia: (p) =>
    p.payable_id !== null && p.payable_id !== undefined
      ? `· já existia (título nº ${p.payable_id})`
      : '· já registrada por outra conciliação',
  pendente: (p) => `· sem confirmação: ${p.message ?? 'confira no Omie'}`,
  erro: (p) => `· não entrou: ${p.message ?? 'erro do Omie'}`,
};

/**
 * Contagens do lote do ponto de vista da pessoa: `ja_lancada` (direto vinculado,
 * provisão com título) conta como CONCLUÍDA, não como falha — o servidor a conta
 * em `bloqueadas`, mas para quem lê ela está no Omie.
 */
function batchCounts(payload: OmiePostingBatchPayload): {
  concluidas: number;
  falhas: number;
  /** Provisões que pararam no meio com o título JÁ criado (sucesso parcial). */
  parciais: number;
  kind: 'provisao' | 'direto' | 'misto';
} {
  const concluded = payload.lines.filter(isPostingResultConcluded);
  const parciais = payload.lines.filter(isStoppedMidway).length;
  const kinds = new Set(concluded.map((l) => l.kind ?? 'direto'));
  const kind = kinds.size === 1 ? (kinds.has('provisao') ? 'provisao' : 'direto') : 'misto';
  return {
    concluidas: concluded.length,
    falhas: payload.lines.length - concluded.length,
    parciais,
    kind,
  };
}

/** Provisão que parou depois de criar o título (o passo seguinte falhou). */
function isStoppedMidway(line: OmiePostingLineResult): boolean {
  return (
    !isPostingResultConcluded(line) &&
    line.kind === 'provisao' &&
    line.payable_id !== null &&
    line.payable_id !== undefined
  );
}

/**
 * O estado da intenção que este envio deixou na linha, na forma do `omie_posting`
 * da listagem. A gaveta guarda as linhas como estavam ao abrir; sem isto, a linha
 * que parou na baixa voltaria a oferecer a troca de forma e a pedir o fornecedor
 * até o refetch. Bloqueio sem envio (`bloqueada`, sem título) não muda o estado.
 */
function postingFromResult(
  line: OmiePostingLineResult | undefined,
  previous: OmiePostingState | null,
): OmiePostingState | null {
  if (line === undefined || line.kind === undefined || line.kind === null) return previous;
  if (line.status === 'bloqueada' && !isStoppedMidway(line)) return previous;
  const fault = line.reason === 'erro_omie' || line.reason === 'competencia_fechada';
  // 86e3qhhtb: título feito e parcela futura pendente é provisão CONCLUÍDA com a
  // série incompleta (o servidor grava `confirmed`), nunca um passo parado.
  const concludedStep = line.kind === 'provisao' && line.step === 'concluido';
  return {
    kind: line.kind,
    status: line.status === 'lancada' || concludedStep ? 'confirmed' : fault ? 'failed' : 'pending',
    step: line.step ?? null,
    attempts: fault ? (line.kind === 'direto' ? 1 : 0) : 1,
    supplier_code: line.supplier_code ?? null,
    payable_id: line.payable_id ?? null,
    payment_id: line.payment_id ?? null,
    omie_lancamento_id: line.omie_lancamento_id ?? null,
    error_code: fault ? 'OMIE_FAULT' : null,
    error_message: line.message ?? null,
    ...withSeries(line),
  };
}

function countPhrase(n: number, kind: 'provisao' | 'direto' | 'misto'): string {
  const verb = kind === 'provisao' ? 'provisionada' : 'lançada';
  return n === 1 ? `1 compra ${verb} no Omie.` : `${n} compras ${verb}s no Omie.`;
}

/**
 * Resumo do lote no topo da gaveta (86e3n70qj). `role="status"`: quem opera por
 * teclado ou leitor ouve o desfecho sem procurar o toast, que já foi embora.
 */
function ResultadoDoLote({ result }: { result: OmiePostingBatchPayload }) {
  const { concluidas: lancadas, falhas, parciais, kind } = batchCounts(result);
  // Título criado e baixa recusada NÃO é "nada lançado": é aviso, não erro.
  const nadaEscrito = lancadas === 0 && parciais === 0;
  const tone =
    falhas === 0
      ? 'bg-success-muted text-success'
      : nadaEscrito
        ? 'bg-destructive-muted text-destructive'
        : 'bg-warning-muted text-warning';
  const Icon = falhas === 0 ? CheckCircle2 : nadaEscrito ? XCircle : AlertTriangle;
  const where = {
    provisao:
      'Cada uma virou um título no Contas a Pagar; o número do título está na linha dela, abaixo.',
    direto:
      lancadas === 1
        ? 'Entrou na conta do cartão, na data da compra; o número do lançamento está na linha dela, abaixo.'
        : 'Entraram na conta do cartão, cada uma na data da compra; o número de cada lançamento está na linha dela, abaixo.',
    misto:
      'As provisionadas viraram título no Contas a Pagar e as diretas entraram na conta do cartão; o número de cada uma está na linha dela, abaixo.',
  }[kind];
  return (
    <div role="status" className={cn('flex items-start gap-2 rounded-md p-3 text-sm', tone)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-medium">
          {lancadas > 0
            ? countPhrase(lancadas, kind)
            : parciais > 0
              ? 'Nenhuma compra terminou no Omie.'
              : 'Nenhuma compra foi lançada no Omie.'}
        </p>
        <p>
          {lancadas > 0
            ? where
            : parciais > 0
              ? 'O título já foi criado e o passo seguinte não; cada linha diz onde parou.'
              : 'O motivo de cada compra está na linha dela, abaixo.'}
          {lancadas > 0 && falhas > 0 && ' As que não terminaram mostram onde pararam.'}
        </p>
      </div>
    </div>
  );
}

/** Um par por status — os três do contrato, sem `default` mágico. */
const RESULT_PRESENTATION = {
  lancada: { icon: CheckCircle2, tone: 'text-success', rotulo: 'Lançada no Omie' },
  bloqueada: { icon: AlertTriangle, tone: 'text-warning', rotulo: 'Não lançada' },
  erro: { icon: XCircle, tone: 'text-destructive', rotulo: 'Erro no lançamento' },
} as const;

/**
 * Sucesso verde, parcial em aviso, nada lançado em destrutivo — nunca a cor
 * primária para sucesso, e nunca `richColors` (ADR-017-FE: quem pinta é o
 * `<Toaster>` global, pelos tokens do tema).
 */
function notifyOutcome(payload: OmiePostingBatchPayload): void {
  const { concluidas, falhas, parciais, kind } = batchCounts(payload);
  if (falhas === 0) {
    toast.success(countPhrase(concluidas, kind));
    return;
  }
  if (concluidas === 0 && parciais > 0) {
    toast.warning('O título foi criado, mas o lançamento parou no meio. Veja onde em cada linha.');
    return;
  }
  if (concluidas === 0) {
    toast.error('Nenhuma compra foi lançada. Veja o motivo de cada linha.');
    return;
  }
  const verb = kind === 'provisao' ? 'provisionadas' : 'lançadas';
  toast.warning(
    `${concluidas} de ${payload.lines.length} compras ${verb}. Veja o motivo das demais.`,
  );
}

/** Reusa o `userMessage` do backend (já em PT-BR) quando existe. */
function resolvePostErrorMessage(err: unknown): string {
  if (err instanceof ApiError || err instanceof NetworkError) return err.userMessage;
  return 'Não foi possível lançar no Omie. Tente novamente em instantes.';
}
