'use client';

/**
 * Configuração do cliente para o lançamento como Contas a Pagar (86e3n70q0,
 * bloco C), ao lado de "Compras do cartão no Omie" e com o MESMO gate: só para
 * cliente com conexão Omie (editar) ou com "Conectar com o Omie" ligado (criar).
 *
 * - "Exigir rateio por departamento nos títulos" (`omie_department_required`):
 *   ligado, a gaveta não lança a compra sem rateio (`departamento_pendente`).
 * - "Fornecedor padrão no Omie" (`omie_default_supplier_code`): escolhido pela
 *   busca por CNPJ (a mesma peça da gaveta), guardado como CÓDIGO, nunca digitado.
 *   Só no editar: na criação o cliente ainda não existe, e a busca pergunta ao Omie
 *   DELE (decisão do Pedro, 13/10/2026).
 */

import { Loader2, X } from 'lucide-react';
import { useState } from 'react';
import type { Control, FieldValues, Path } from 'react-hook-form';

import { OmieSupplierLookup } from '@/components/features/omie-suppliers/omie-supplier-lookup';
import { Button } from '@/components/ui/button';
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Switch } from '@/components/ui/switch';
import { useOmieFornecedorByCode } from '@/hooks/use-omie-postings';
import type { OmieFornecedorItem } from '@/lib/contracts';
import type { SupplierValue } from '@/lib/validation/omie-posting';

export const DEPARTMENT_REQUIRED_LABEL = 'Exigir rateio por departamento nos títulos';
export const DEFAULT_SUPPLIER_LABEL = 'Fornecedor padrão no Omie';

interface FieldProps<T extends FieldValues> {
  control: Control<T>;
  disabled: boolean;
}

export function DepartmentRequiredField<T extends FieldValues>({
  control,
  disabled,
}: FieldProps<T>) {
  return (
    <FormField
      control={control}
      name={'omie_department_required' as Path<T>}
      render={({ field }) => (
        <FormItem>
          <div className="flex items-start gap-3">
            <FormControl>
              <Switch
                checked={field.value === true}
                onCheckedChange={field.onChange}
                disabled={disabled}
              />
            </FormControl>
            <div className="space-y-0.5">
              <FormLabel className="cursor-pointer">{DEPARTMENT_REQUIRED_LABEL}</FormLabel>
              <FormDescription>
                Ligado, a compra provisionada no Contas a Pagar só é lançada com o rateio por
                departamento escolhido.
              </FormDescription>
            </div>
          </div>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

export function DefaultSupplierField<T extends FieldValues>({
  control,
  disabled,
  clientId,
}: FieldProps<T> & { clientId: string }) {
  return (
    <FormField
      control={control}
      name={'omie_default_supplier_code' as Path<T>}
      render={({ field }) => (
        <FormItem>
          {/* Texto, não `<label>`: o campo são botões e a busca, cada um com o próprio nome. */}
          <p className="text-sm font-medium">{DEFAULT_SUPPLIER_LABEL}</p>
          <DefaultSupplierPicker
            clientId={clientId}
            value={(field.value as number | null | undefined) ?? null}
            onChange={field.onChange}
            disabled={disabled}
          />
          <FormDescription>
            Usado na compra em que ninguém escolheu fornecedor na gaveta de lançamento (nem pelo
            nome, nem pelo CNPJ).
          </FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

/** Mostra o padrão gravado (pelo nome) e deixa trocar pela busca ou remover. */
function DefaultSupplierPicker({
  clientId,
  value,
  onChange,
  disabled,
}: {
  clientId: string;
  value: number | null;
  onChange: (codigo: number | null) => void;
  disabled: boolean;
}) {
  const [changing, setChanging] = useState(false);
  const [lookup, setLookup] = useState<SupplierValue>({ kind: 'none' });
  /** O que a busca acabou de achar: dispensa resolver o nome pelo código de novo. */
  const [chosen, setChosen] = useState<OmieFornecedorItem | null>(null);

  if (changing) {
    return (
      <div className="space-y-2 rounded-md border p-3">
        <OmieSupplierLookup
          clientId={clientId}
          value={lookup}
          allowRegister={false}
          label="Buscar o fornecedor padrão no Omie"
          disabled={disabled}
          onChange={(next) => {
            setLookup(next);
            if (next.kind === 'found') {
              setChosen(next.supplier);
              onChange(next.supplier.codigo);
              setChanging(false);
            }
          }}
        />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          onClick={() => setChanging(false)}
        >
          Cancelar a troca
        </Button>
      </div>
    );
  }

  if (value === null) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-sm">Nenhum fornecedor padrão.</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => {
            setLookup({ kind: 'none' });
            setChanging(true);
          }}
        >
          Escolher fornecedor padrão
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm">
        {chosen !== null && chosen.codigo === value ? (
          <strong className="font-medium">{chosen.razao_social ?? 'sem nome'}</strong>
        ) : (
          <SavedSupplierName clientId={clientId} codigo={value} />
        )}
      </span>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => {
          setLookup({ kind: 'none' });
          setChanging(true);
        }}
      >
        Trocar
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={disabled}
        onClick={() => {
          setChosen(null);
          onChange(null);
        }}
      >
        <X className="h-4 w-4" aria-hidden="true" />
        Remover
      </Button>
    </div>
  );
}

/** O nome do padrão GRAVADO, resolvido pela busca por código. */
function SavedSupplierName({ clientId, codigo }: { clientId: string; codigo: number }) {
  const query = useOmieFornecedorByCode(clientId, codigo);
  if (query.isLoading) {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        Carregando o fornecedor…
      </span>
    );
  }
  if (query.data) {
    return <strong className="font-medium">{query.data.razao_social ?? 'sem nome'}</strong>;
  }
  return (
    <span className="text-warning">
      Não foi possível mostrar o nome agora (código {codigo} no Omie).
    </span>
  );
}
