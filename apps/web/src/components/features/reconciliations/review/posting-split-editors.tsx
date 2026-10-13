'use client';

/**
 * Peças da classificação de uma compra na gaveta "Lançar no Omie" (86e3n70q0,
 * bloco C): a FORMA do lançamento, a compra DIVIDIDA em categorias (em VALOR — é
 * o que fecha a subtask 86e3nb818, IOF + categoria) e o RATEIO por departamento
 * (em PERCENTUAL, como o pedido do servidor; o valor de cada parte aparece só
 * para conferência).
 *
 * Presentacionais: o estado mora na gaveta, e a regra (somas, tetos, repetição)
 * mora em `lib/validation/omie-posting.ts`. Aqui só se mostra quanto falta para
 * fechar, para a pessoa corrigir antes de enviar.
 *
 * Listas editáveis com chave estável por parte (`key`), nunca o índice.
 */

import { Plus, Trash2 } from 'lucide-react';
import { useId } from 'react';

import { Money } from '@/components/shared/money';
import { Button } from '@/components/ui/button';
import { Combobox, type ComboboxOption } from '@/components/ui/combobox';
import { Input } from '@/components/ui/input';
import type { OmiePostingMode } from '@/lib/contracts';
import { formatBRL } from '@/lib/format';
import { centsFromTyped, formatCentsForInput } from '@/lib/money-input';
import { cn } from '@/lib/utils';
import {
  MAX_CATEGORY_PARTS,
  MAX_DEPARTMENT_PARTS,
  MIN_CATEGORY_PARTS,
  categoryRemainderCents,
  departmentPartCents,
  departmentRemainderHundredths,
  newPartKey,
  type CategoryPart,
  type DepartmentPart,
} from '@/lib/validation/omie-posting';

/** Rótulo de cada forma, o MESMO no lote e na linha. */
export const POSTING_MODE_LABEL: Record<OmiePostingMode, string> = {
  provisionar: 'Provisionar no Contas a Pagar',
  direto: 'Lançar direto na conta do cartão',
};

const POSTING_MODE_HINT: Record<OmiePostingMode, string> = {
  provisionar: 'Título com fornecedor e vencimento, que aparece no fluxo de caixa.',
  direto: 'Lançamento avulso na conta do cartão, sem título.',
};

interface PostingModeChoiceProps {
  value: OmiePostingMode;
  onChange: (mode: OmiePostingMode) => void;
  /** Nome do grupo (um por linha): rádios de linhas diferentes não podem se misturar. */
  legend: string;
  disabled?: boolean;
}

/**
 * Rádio NATIVO (o mesmo padrão da convenção de sinal do mapeamento de entrada):
 * teclado, papel e estado vêm do navegador.
 */
export function PostingModeChoice({
  value,
  onChange,
  legend,
  disabled = false,
}: PostingModeChoiceProps) {
  const name = useId();
  return (
    <fieldset className="space-y-1.5">
      <legend className="sr-only">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {(['provisionar', 'direto'] as const).map((mode) => {
          const id = `${name}-${mode}`;
          const checked = value === mode;
          return (
            <label
              key={mode}
              htmlFor={id}
              className={cn(
                'flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm',
                checked
                  ? 'border-primary bg-accent text-accent-foreground'
                  : 'border-input hover:bg-accent hover:text-accent-foreground',
                disabled && 'cursor-not-allowed opacity-50',
              )}
            >
              <input
                id={id}
                type="radio"
                name={name}
                value={mode}
                checked={checked}
                disabled={disabled}
                onChange={() => onChange(mode)}
                className="accent-primary mt-0.5 h-4 w-4 cursor-pointer"
              />
              <span className="space-y-0.5">
                <span className="block font-medium">{POSTING_MODE_LABEL[mode]}</span>
                <span className="block text-xs opacity-80">{POSTING_MODE_HINT[mode]}</span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

interface CategorySplitEditorProps {
  parts: CategoryPart[];
  onChange: (parts: CategoryPart[]) => void;
  options: ComboboxOption[];
  /** Valor da compra (`"-103.80"`): a soma das partes fecha com ele. */
  amount: string;
  /** Diz de qual compra é cada controle (a gaveta lista várias). */
  purchaseLabel: string;
  loading?: boolean;
  disabled?: boolean;
}

export function CategorySplitEditor({
  parts,
  onChange,
  options,
  amount,
  purchaseLabel,
  loading = false,
  disabled = false,
}: CategorySplitEditorProps) {
  const remainder = categoryRemainderCents(parts, amount);
  const update = (key: string, patch: Partial<CategoryPart>) =>
    onChange(parts.map((p) => (p.key === key ? { ...p, ...patch } : p)));
  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {parts.map((part, index) => (
          <li key={part.key} className="flex flex-wrap items-center gap-2">
            <div className="w-full sm:w-auto sm:min-w-[12rem] sm:flex-1">
              <Combobox
                options={options}
                value={part.codigo}
                onValueChange={(codigo) => update(part.key, { codigo })}
                label={`Categoria da parte ${index + 1} — ${purchaseLabel}`}
                placeholder="Categoria…"
                searchPlaceholder="Buscar categoria…"
                emptyMessage="Nenhuma categoria encontrada."
                loading={loading}
                disabled={disabled}
              />
            </div>
            <div className="flex items-center gap-1">
              <span className="text-muted-foreground text-sm" aria-hidden="true">
                R$
              </span>
              <Input
                inputMode="numeric"
                autoComplete="off"
                aria-label={`Valor da parte ${index + 1} — ${purchaseLabel}`}
                className="w-28 text-right tabular-nums"
                placeholder="0,00"
                value={formatCentsForInput(part.cents)}
                disabled={disabled}
                onChange={(e) =>
                  update(part.key, { cents: centsFromTyped(e.target.value.replace('-', '')) })
                }
              />
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remover a parte ${index + 1} — ${purchaseLabel}`}
              disabled={disabled || parts.length <= MIN_CATEGORY_PARTS}
              onClick={() => onChange(parts.filter((p) => p.key !== part.key))}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || parts.length >= MAX_CATEGORY_PARTS}
          onClick={() => onChange([...parts, { key: newPartKey(), codigo: null, cents: '' }])}
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Adicionar parte
        </Button>
        <RemainderNote cents={remainder} />
      </div>
      <p className="text-muted-foreground text-xs">
        Cada parte entra no título com a sua categoria (ex.: o gasto e o IOF). Se faltar ou sobrar 1
        centavo, ele vai para a última parte.
      </p>
    </div>
  );
}

function RemainderNote({ cents }: { cents: number }) {
  if (Math.abs(cents) <= 1) {
    return <span className="text-success text-xs">Fecha com o valor da compra.</span>;
  }
  // `aria-live`: quem digita o valor da parte ouve quanto falta sem procurar.
  return (
    <span aria-live="polite" className="text-warning whitespace-nowrap text-xs">
      {cents > 0 ? 'Falta ' : 'Sobra '}
      {formatBRL((Math.abs(cents) / 100).toFixed(2))}
    </span>
  );
}

interface DepartmentSplitEditorProps {
  parts: DepartmentPart[];
  onChange: (parts: DepartmentPart[]) => void;
  options: ComboboxOption[];
  /** Valor da compra, para mostrar o valor de cada parte; `null` no "aplicar a todas". */
  amount: string | null;
  purchaseLabel: string;
  loading?: boolean;
  disabled?: boolean;
  /** Pode ficar sem nenhuma parte (rateio opcional)? */
  allowEmpty: boolean;
}

export function DepartmentSplitEditor({
  parts,
  onChange,
  options,
  amount,
  purchaseLabel,
  loading = false,
  disabled = false,
  allowEmpty,
}: DepartmentSplitEditorProps) {
  const remainder = departmentRemainderHundredths(parts);
  const update = (key: string, patch: Partial<DepartmentPart>) =>
    onChange(parts.map((p) => (p.key === key ? { ...p, ...patch } : p)));
  const minParts = allowEmpty ? 0 : 1;
  return (
    <div className="space-y-2">
      <ul className="space-y-2">
        {parts.map((part, index) => (
          <li key={part.key} className="flex flex-wrap items-center gap-2">
            <div className="w-full sm:w-auto sm:min-w-[12rem] sm:flex-1">
              <Combobox
                options={options}
                value={part.codigo}
                onValueChange={(codigo) => update(part.key, { codigo })}
                label={`Departamento da parte ${index + 1} do rateio — ${purchaseLabel}`}
                placeholder="Departamento…"
                searchPlaceholder="Buscar departamento…"
                emptyMessage="Nenhum departamento encontrado."
                loading={loading}
                disabled={disabled}
              />
            </div>
            <div className="flex items-center gap-1">
              <Input
                inputMode="numeric"
                autoComplete="off"
                aria-label={`Percentual da parte ${index + 1} do rateio — ${purchaseLabel}`}
                className="w-20 text-right tabular-nums"
                placeholder="0,00"
                value={formatCentsForInput(part.hundredths)}
                disabled={disabled}
                onChange={(e) =>
                  update(part.key, {
                    hundredths: centsFromTyped(e.target.value.replace('-', '')).slice(0, 5),
                  })
                }
              />
              <span className="text-muted-foreground text-sm" aria-hidden="true">
                %
              </span>
            </div>
            {amount !== null && (
              <Money
                value={(departmentPartCents(part.hundredths, amount) / 100).toFixed(2)}
                tone="neutral"
                className="text-muted-foreground w-24 text-right text-xs"
              />
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={`Remover a parte ${index + 1} do rateio — ${purchaseLabel}`}
              disabled={disabled || parts.length <= minParts}
              onClick={() => onChange(parts.filter((p) => p.key !== part.key))}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </Button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || parts.length >= MAX_DEPARTMENT_PARTS}
          onClick={() =>
            onChange([
              ...parts,
              {
                key: newPartKey(),
                codigo: null,
                hundredths: remainder > 0 ? String(remainder) : '',
              },
            ])
          }
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Adicionar departamento
        </Button>
        {parts.length > 0 &&
          (Math.abs(remainder) <= 1 ? (
            <span className="text-success text-xs">Soma 100%.</span>
          ) : (
            <span aria-live="polite" className="text-warning whitespace-nowrap text-xs">
              {remainder > 0 ? 'Faltam ' : 'Sobram '}
              {formatCentsForInput(String(Math.abs(remainder)))}%
            </span>
          ))}
      </div>
    </div>
  );
}
