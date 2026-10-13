'use client';

/**
 * Classificação de UMA compra na gaveta "Lançar no Omie" (86e3n70q0, bloco C).
 *
 * Forma (provisionar ou direto), categoria única ou dividida, fornecedor e
 * rateio por departamento — os três últimos só na provisão, porque só ela tem
 * título. O estado é da gaveta (`LineDraft`); a regra é de
 * `lib/validation/omie-posting.ts`. Nenhuma sugestão automática: categoria,
 * fornecedor e departamento são só o que a pessoa escolhe (regra da gaveta desde a
 * Sprint 7).
 */

import { AlertTriangle, Lock, Loader2 } from 'lucide-react';
import { useId } from 'react';

import { OmieSupplierLookup } from '@/components/features/omie-suppliers/omie-supplier-lookup';
import { Button } from '@/components/ui/button';
import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import { Switch } from '@/components/ui/switch';
import { useOmieFornecedorByCode } from '@/hooks/use-omie-postings';
import type { OmiePostingMode, OmiePostingState } from '@/lib/contracts';
import {
  initialCategoryParts,
  initialDepartmentParts,
  type DepartmentPart,
  type LineDraft,
  type SupplierValue,
} from '@/lib/validation/omie-posting';

import { DIRECT_IN_DUE_DATE_PROCESS_MESSAGE } from './omie-posting-eligibility';
import {
  CategorySplitEditor,
  DepartmentSplitEditor,
  POSTING_MODE_LABEL,
  PostingModeChoice,
} from './posting-split-editors';

/** O que a linha precisa saber do cliente, da sessão e das listas do Omie. */
export interface PostingEditorContext {
  clientId: string;
  /** O processo da sessão permite o lançamento direto (só "na data da compra"). */
  allowDirect: boolean;
  departmentRequired: boolean;
  defaultSupplierCode: number | null;
  categoryOptions: ComboboxOption[];
  categoriesLoading: boolean;
  categoriesUnavailable: boolean;
  departmentOptions: ComboboxOption[];
  departmentsLoading: boolean;
  departmentsUnavailable: boolean;
  onRetryDepartments: () => void;
  retryingDepartments: boolean;
  disabled: boolean;
}

/** Nome do passo na frase da tela. */
export const POSTING_STEP_LABEL: Record<string, string> = {
  fornecedor: 'fornecedor',
  titulo: 'título',
  baixa: 'baixa',
  concluido: 'concluído',
};

interface PostingLineEditorProps {
  draft: LineDraft;
  onChange: (draft: LineDraft) => void;
  /** Valor da compra (`"-103.80"`). */
  amount: string;
  /** "15/10/2026 — Loja X": diz de qual compra é cada controle. */
  purchaseLabel: string;
  /** Intenção já gravada no servidor (retomada) ou `null`. */
  posting: OmiePostingState | null;
  /** Forma em que a linha está TRAVADA, ou `null` (a pessoa escolhe). */
  lockedMode: OmiePostingMode | null;
  context: PostingEditorContext;
}

export function PostingLineEditor({
  draft,
  onChange,
  amount,
  purchaseLabel,
  posting,
  lockedMode,
  context,
}: PostingLineEditorProps) {
  const isProvision = draft.modo === 'provisionar';
  const supplierSettled = posting?.kind === 'provisao' && posting.supplier_code !== null;
  const payableExists = posting?.kind === 'provisao' && posting.payable_id !== null;
  const disabled = context.disabled;

  return (
    <div className="space-y-3">
      {lockedMode !== null ? (
        <p className="bg-muted/40 text-muted-foreground flex items-start gap-2 rounded-md border p-2 text-xs">
          <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            <strong className="text-foreground font-medium">
              {POSTING_MODE_LABEL[lockedMode]}
            </strong>
            {posting?.kind === 'provisao' && posting.step
              ? ` (parou no passo ${POSTING_STEP_LABEL[posting.step] ?? posting.step})`
              : ''}
            . Esta compra já teve envio ao Omie nesta forma, e ela não pode ser trocada: trocar
            poderia lançar a mesma compra duas vezes.
          </span>
        </p>
      ) : context.allowDirect ? (
        <PostingModeChoice
          value={draft.modo}
          onChange={(modo) => onChange({ ...draft, modo })}
          legend={`Forma do lançamento — ${purchaseLabel}`}
          disabled={disabled}
        />
      ) : null}

      {payableExists && (
        <p className="text-muted-foreground text-xs">
          O título nº {posting?.payable_id} já existe no Omie com a classificação do primeiro envio;
          o que for escolhido agora não o altera.
        </p>
      )}

      <CategoryField
        draft={draft}
        onChange={onChange}
        amount={amount}
        purchaseLabel={purchaseLabel}
        context={context}
      />

      {isProvision && (
        <>
          {supplierSettled ? (
            <p className="text-muted-foreground text-xs">
              Fornecedor já definido neste lançamento (código {posting?.supplier_code} no Omie).
            </p>
          ) : (
            <SupplierField
              value={draft.supplier}
              onChange={(supplier) => onChange({ ...draft, supplier })}
              purchaseLabel={purchaseLabel}
              context={context}
            />
          )}
          <DepartmentsField
            parts={draft.departments}
            onChange={(departments) => onChange({ ...draft, departments })}
            amount={amount}
            purchaseLabel={purchaseLabel}
            context={context}
          />
        </>
      )}
    </div>
  );
}

function CategoryField({
  draft,
  onChange,
  amount,
  purchaseLabel,
  context,
}: {
  draft: LineDraft;
  onChange: (draft: LineDraft) => void;
  amount: string;
  purchaseLabel: string;
  context: PostingEditorContext;
}) {
  const splitId = useId();
  const canSplit = draft.modo === 'provisionar';
  const splitting = canSplit && draft.split;
  const disabled = context.disabled || context.categoriesUnavailable;
  return (
    <div className="space-y-2">
      {splitting ? (
        <CategorySplitEditor
          parts={draft.parts}
          onChange={(parts) => onChange({ ...draft, parts })}
          options={context.categoryOptions}
          amount={amount}
          purchaseLabel={purchaseLabel}
          loading={context.categoriesLoading}
          disabled={disabled}
        />
      ) : (
        <Combobox
          options={context.categoryOptions}
          value={draft.categoria}
          onValueChange={(categoria) => onChange({ ...draft, categoria })}
          label={`Categoria da compra de ${purchaseLabel}`}
          placeholder="Sem categoria"
          searchPlaceholder="Buscar categoria…"
          emptyMessage="Nenhuma categoria encontrada."
          loading={context.categoriesLoading}
          disabled={disabled}
        />
      )}
      {canSplit && (
        <div className="flex items-center gap-2">
          <Switch
            id={splitId}
            checked={draft.split}
            disabled={disabled}
            aria-label={`Dividir em categorias — ${purchaseLabel}`}
            onCheckedChange={(split) =>
              onChange({
                ...draft,
                split,
                parts:
                  split && draft.parts.length === 0
                    ? initialCategoryParts(draft.categoria, amount)
                    : draft.parts,
              })
            }
          />
          <label htmlFor={splitId} className="cursor-pointer text-sm">
            Dividir em categorias
          </label>
        </div>
      )}
    </div>
  );
}

function SupplierField({
  value,
  onChange,
  purchaseLabel,
  context,
}: {
  value: SupplierValue;
  onChange: (value: SupplierValue) => void;
  purchaseLabel: string;
  context: PostingEditorContext;
}) {
  const checkboxId = useId();
  const hasDefault = context.defaultSupplierCode !== null;
  const usingDefault = value.kind === 'default';
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">Fornecedor</p>
      {hasDefault && (
        <div className="flex items-start gap-2">
          <input
            id={checkboxId}
            type="checkbox"
            checked={usingDefault}
            disabled={context.disabled}
            onChange={(e) => onChange(e.target.checked ? { kind: 'default' } : { kind: 'none' })}
            className="accent-primary mt-0.5 size-4 cursor-pointer"
          />
          <label htmlFor={checkboxId} className="cursor-pointer text-sm">
            Usar o fornecedor padrão do cliente:{' '}
            <DefaultSupplierName clientId={context.clientId} codigo={context.defaultSupplierCode} />
          </label>
        </div>
      )}
      {!usingDefault && (
        <OmieSupplierLookup
          clientId={context.clientId}
          value={value}
          onChange={onChange}
          allowRegister
          label="CNPJ do fornecedor"
          labelContext={purchaseLabel}
          disabled={context.disabled}
        />
      )}
    </div>
  );
}

/** O nome do fornecedor padrão, resolvido pelo código (nunca o código como nome). */
export function DefaultSupplierName({
  clientId,
  codigo,
}: {
  clientId: string;
  codigo: number | null;
}) {
  const query = useOmieFornecedorByCode(clientId, codigo);
  if (query.isLoading) {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        carregando…
      </span>
    );
  }
  if (query.data) {
    return <strong className="font-medium">{query.data.razao_social ?? 'sem nome'}</strong>;
  }
  return (
    <span className="text-muted-foreground">nome indisponível agora (código {codigo} no Omie)</span>
  );
}

function DepartmentsField({
  parts,
  onChange,
  amount,
  purchaseLabel,
  context,
}: {
  parts: DepartmentPart[];
  onChange: (parts: DepartmentPart[]) => void;
  amount: string;
  purchaseLabel: string;
  context: PostingEditorContext;
}) {
  const switchId = useId();
  const required = context.departmentRequired;
  const open = required || parts.length > 0;
  return (
    <div className="space-y-2">
      {required ? (
        <p className="text-sm font-medium">
          Rateio por departamento{' '}
          <span className="text-muted-foreground font-normal">(obrigatório para este cliente)</span>
        </p>
      ) : (
        <div className="flex items-center gap-2">
          <Switch
            id={switchId}
            checked={parts.length > 0}
            disabled={context.disabled}
            aria-label={`Ratear por departamento — ${purchaseLabel}`}
            onCheckedChange={(on) => onChange(on ? initialDepartmentParts() : [])}
          />
          <label htmlFor={switchId} className="cursor-pointer text-sm">
            Ratear por departamento
          </label>
        </div>
      )}
      {open && context.departmentsUnavailable ? (
        <div role="alert" className="text-destructive space-y-1 text-xs">
          <p className="flex items-start gap-1">
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            Não foi possível carregar os departamentos do Omie.
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={context.onRetryDepartments}
            disabled={context.retryingDepartments}
          >
            {context.retryingDepartments && (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            )}
            Tentar novamente
          </Button>
        </div>
      ) : open ? (
        <DepartmentSplitEditor
          parts={parts}
          onChange={onChange}
          options={context.departmentOptions}
          amount={amount}
          purchaseLabel={purchaseLabel}
          loading={context.departmentsLoading}
          disabled={context.disabled}
          allowEmpty={!required}
        />
      ) : null}
    </div>
  );
}

/** Aviso fixo do processo "vencimento da fatura": só existe a provisão. */
export function DueDateProcessNote() {
  return (
    <p className="bg-muted/40 text-muted-foreground rounded-md border p-3 text-xs">
      Só a provisão no Contas a Pagar é oferecida. {DIRECT_IN_DUE_DATE_PROCESS_MESSAGE}
    </p>
  );
}
