'use client';

/**
 * Grupo de botões de filtro com "Todas" na frente (a tela do plano contábil e a aba de
 * clientes e fornecedores usam o MESMO). Ativo = `aria-pressed` + `accent`, com todo o
 * texto em `accent-foreground` para não criar par de contraste sem teste.
 */

import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export function FilterGroup<T extends string>({
  label,
  value,
  options,
  onChange,
  allLabel = 'Todas',
}: {
  label: string;
  value: T | null;
  options: { value: T; label: string }[];
  onChange: (value: T | null) => void;
  allLabel?: string;
}) {
  const all: { value: T | null; label: string }[] = [{ value: null, label: allLabel }, ...options];
  return (
    <div
      role="group"
      aria-label={label}
      className="bg-background inline-flex flex-wrap self-start rounded-md border p-0.5 xl:self-auto"
    >
      {all.map((option) => {
        const pressed = value === option.value;
        return (
          <Button
            key={option.label}
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={pressed}
            className={cn('h-8', pressed && 'bg-accent text-accent-foreground')}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </Button>
        );
      })}
    </div>
  );
}
