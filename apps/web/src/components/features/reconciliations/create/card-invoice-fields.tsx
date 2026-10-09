'use client';

/**
 * Fatura de CARTÃO no passo 2 da gaveta (86e3n70p0).
 *
 * Dois campos, e os dois existem porque o cruzamento depende deles:
 *
 *   - **Compras do cartão no Omie**: o processo do cliente (configuração dele),
 *     com a troca SÓ nesta conciliação. Nunca é inferido: a pessoa vê o que vai
 *     valer antes de confirmar.
 *   - **Vencimento da fatura**: vem do parser ("como impresso") e a pessoa
 *     confirma ou corrige. No processo "no vencimento da fatura" é ele que diz
 *     onde está o lote no Omie, então é obrigatório (o servidor responde 422 sem
 *     ele); no outro processo é informativo.
 *
 * Campo de texto `DD/MM/AAAA` e não `<input type="date">`: o projeto não usa o
 * seletor nativo (design-system) e ainda não tem calendário (`ui/calendar`).
 */
import type { UseFormReturn } from 'react-hook-form';

import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  CARD_POSTING_DATE_MODES,
  CARD_POSTING_DATE_MODE_FIELD_LABEL,
  CARD_POSTING_DATE_MODE_LABEL,
} from '@/lib/card-posting-date-mode';
import type { CardPostingDateMode } from '@/lib/contracts';
import type { CardInvoiceValues } from '@/lib/validation/reconciliations';

/** O processo em frase, como a pessoa lê: "Lançadas no vencimento da fatura". */
const MODE_PHRASE: Record<CardPostingDateMode, string> = {
  purchase_date: 'Lançadas na data da compra',
  invoice_due_date: 'Lançadas no vencimento da fatura',
};

export function modeDescription(mode: CardPostingDateMode, clientMode: CardPostingDateMode) {
  if (mode === clientMode) return `${MODE_PHRASE[mode]}, configuração do cliente.`;
  return `${MODE_PHRASE[mode]}, só nesta conciliação. O cliente continua configurado como "${CARD_POSTING_DATE_MODE_LABEL[clientMode].toLowerCase()}".`;
}

interface CardInvoiceFieldsProps {
  form: UseFormReturn<CardInvoiceValues>;
  clientMode: CardPostingDateMode;
  mode: CardPostingDateMode;
  disabled: boolean;
}

export function CardInvoiceFields({ form, clientMode, mode, disabled }: CardInvoiceFieldsProps) {
  return (
    <Form {...form}>
      {/* O espaçamento mora no div de dentro: no fieldset, o `space-y` conta a
          legenda como primeiro filho e abre um vão antes do primeiro campo. */}
      <fieldset className="rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Fatura do cartão</legend>
        <div className="space-y-4">
          <FormField
            control={form.control}
            name="card_posting_date_mode"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{CARD_POSTING_DATE_MODE_FIELD_LABEL}</FormLabel>
                <Select value={field.value} onValueChange={field.onChange} disabled={disabled}>
                  <FormControl>
                    <SelectTrigger aria-label={CARD_POSTING_DATE_MODE_FIELD_LABEL}>
                      <SelectValue />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {CARD_POSTING_DATE_MODES.map((option) => (
                      <SelectItem key={option} value={option}>
                        {CARD_POSTING_DATE_MODE_LABEL[option]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>{modeDescription(mode, clientMode)}</FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="invoice_due_date"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  Vencimento da fatura{mode === 'purchase_date' ? ' (opcional)' : ''}
                </FormLabel>
                <FormControl>
                  <Input
                    inputMode="numeric"
                    autoComplete="off"
                    placeholder="DD/MM/AAAA"
                    maxLength={10}
                    disabled={disabled}
                    {...field}
                  />
                </FormControl>
                <FormDescription>
                  {mode === 'invoice_due_date'
                    ? 'As compras são procuradas no lote do vencimento no Omie, 3 dias para cada lado. Confira a data lida da fatura.'
                    : 'Informativo neste processo: cada compra é procurada pela própria data.'}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
      </fieldset>
    </Form>
  );
}
