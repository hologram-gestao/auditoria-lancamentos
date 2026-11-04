'use client';

/**
 * A etapa "Diga o que é cada coluna" da importação do plano contábil (86e3ydjz4).
 *
 * Aparece quando a prévia responde 422 `MAPEAMENTO_NECESSARIO`: o servidor achou o
 * bloco de contas, mas o reconhecimento pelo conteúdo não decidiu código, nome ou tipo.
 * Em vez de um beco sem saída, uma linha por coluna com o cabeçalho (o único texto do
 * arquivo que o servidor devolve, exceção declarada), a forma detectada e o
 * preenchimento, e um seletor de papel pré-marcado com o que o servidor sugeriu. Valor de
 * célula nunca aparece: o servidor não o manda.
 *
 * O interruptor "não tem coluna de tipo" deriva o tipo pela hierarquia, e só por essa
 * escolha (nada é inferido em silêncio, §4.8); ele só fica disponível com a
 * classificação escolhida e nenhuma coluna de tipo. A prévia que vem depois diz quantas
 * contas ficaram sintéticas por isso.
 *
 * Os campos pertencem ao formulário que a gaveta criou (`useForm` com
 * `accountingChartMappingSchema`); a gaveta decide quando ele é enviado (não há
 * `<form>` aninhado).
 */

import { useEffect } from 'react';
import { useWatch, type UseFormReturn } from 'react-hook-form';

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
import { Switch } from '@/components/ui/switch';
import type { ChartMappingColumn } from '@/lib/accounting-chart-errors';
import {
  MAPPING_ROLE_LABELS,
  MAPPING_ROLES,
  columnFormLabel,
  type AccountingChartMappingValues,
} from '@/lib/validation/accounting-chart-mapping';

export const HIERARCHY_LABEL =
  'Este arquivo não tem coluna de tipo: marcar como sintética a conta que tem contas abaixo';

interface AccountingChartMappingStepProps {
  columns: ChartMappingColumn[];
  /** A frase do servidor: o que foi reconhecido e o que falta. */
  message: string;
  form: UseFormReturn<AccountingChartMappingValues>;
  disabled: boolean;
}

export function columnTitle(column: ChartMappingColumn): string {
  return `Coluna ${column.indice + 1}`;
}

export function AccountingChartMappingStep({
  columns,
  message,
  form,
  disabled,
}: AccountingChartMappingStepProps) {
  const roles = useWatch({ control: form.control, name: 'roles' });
  const hierarchyAvailable = roles.includes('classificacao') && !roles.includes('tipo');
  // Sem a condição (escolheram uma coluna de tipo, ou tiraram a classificação), o
  // interruptor desliga: a escolha não pode ficar valendo escondida.
  useEffect(() => {
    if (!hierarchyAvailable && form.getValues('tipoPelaHierarquia')) {
      form.setValue('tipoPelaHierarquia', false);
    }
  }, [hierarchyAvailable, form]);
  const rolesError =
    form.formState.errors.roles?.root?.message ?? form.formState.errors.roles?.message;

  return (
    <Form {...form}>
      <section
        aria-labelledby="accounting-chart-mapping-title"
        data-testid="accounting-chart-mapping-step"
        className="space-y-4"
      >
        <div className="space-y-1">
          <h3 id="accounting-chart-mapping-title" className="text-base font-semibold">
            Diga o que é cada coluna
          </h3>
          <p className="text-muted-foreground text-sm">{message}</p>
        </div>

        <ul className="space-y-2" aria-label="Colunas da planilha">
          {columns.map((column) => (
            <li
              key={column.indice}
              className="border-border flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0 space-y-0.5">
                <p className="text-sm font-medium">
                  {columnTitle(column)}
                  {column.cabecalho !== null && (
                    <span className="text-muted-foreground font-normal">
                      {' · '}
                      <span className="break-words">{column.cabecalho}</span>
                    </span>
                  )}
                </p>
                <p className="text-muted-foreground text-xs">
                  {columnFormLabel(column.formaDetectada)} · {column.preenchimento}% preenchida
                </p>
              </div>
              <FormField
                control={form.control}
                name={`roles.${column.indice}`}
                render={({ field }) => (
                  <FormItem className="sm:w-48 sm:shrink-0">
                    <Select value={field.value} onValueChange={field.onChange} disabled={disabled}>
                      <FormControl>
                        <SelectTrigger aria-label={`Papel da ${columnTitle(column).toLowerCase()}`}>
                          <SelectValue />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {MAPPING_ROLES.map((role) => (
                          <SelectItem key={role} value={role}>
                            {MAPPING_ROLE_LABELS[role]}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </li>
          ))}
        </ul>

        {rolesError && (
          <p role="alert" className="text-destructive text-sm font-medium">
            {rolesError}
          </p>
        )}

        <FormField
          control={form.control}
          name="tipoPelaHierarquia"
          render={({ field }) => (
            <FormItem>
              <div className="flex items-start gap-3">
                <FormControl>
                  <Switch
                    checked={field.value}
                    onCheckedChange={field.onChange}
                    disabled={disabled || !hierarchyAvailable}
                  />
                </FormControl>
                <div className="space-y-0.5">
                  <FormLabel className="cursor-pointer">{HIERARCHY_LABEL}</FormLabel>
                  <FormDescription>
                    Só com a classificação escolhida e nenhuma coluna de tipo. A prévia mostra
                    quantas contas ficaram sintéticas antes de você confirmar.
                  </FormDescription>
                </div>
              </div>
              <FormMessage />
            </FormItem>
          )}
        />
      </section>
    </Form>
  );
}
