'use client';

/**
 * Campo "Compras do cartão no Omie" (86e3n70p0) — o MESMO no novo cliente e no
 * editar cliente. É configuração declarada: o sistema nunca adivinha o processo
 * do cliente pelo que vem do Omie.
 */
import type { Control, FieldValues, Path } from 'react-hook-form';

import {
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
  CARD_POSTING_DATE_MODES,
  CARD_POSTING_DATE_MODE_FIELD_LABEL,
  CARD_POSTING_DATE_MODE_HINT,
  CARD_POSTING_DATE_MODE_LABEL,
} from '@/lib/card-posting-date-mode';

interface CardPostingDateModeFieldProps<T extends FieldValues> {
  control: Control<T>;
  disabled: boolean;
}

export function CardPostingDateModeField<T extends FieldValues>({
  control,
  disabled,
}: CardPostingDateModeFieldProps<T>) {
  return (
    <FormField
      control={control}
      name={'card_posting_date_mode' as Path<T>}
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
              {CARD_POSTING_DATE_MODES.map((mode) => (
                <SelectItem key={mode} value={mode}>
                  {CARD_POSTING_DATE_MODE_LABEL[mode]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <FormDescription>{CARD_POSTING_DATE_MODE_HINT}</FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}
