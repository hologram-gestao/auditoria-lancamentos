'use client';

/**
 * Toggle Switch — variante minimalista compatível com o visual do shadcn.
 *
 * Não usa `@radix-ui/react-switch` (não está nas deps) — botão nativo com
 * `role="switch"`, `aria-checked` e teclado (Space/Enter via comportamento
 * padrão do <button>). Suficiente para o uso atual (toggle de Ativo/Inativo
 * em catálogo admin) e evita adicionar uma dep só para isso.
 */

import * as React from 'react';

import { cn } from '@/lib/utils';

interface SwitchProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean;
  onCheckedChange?: (checked: boolean) => void;
  /** Texto lido por leitores de tela. */
  'aria-label'?: string;
}

export const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ checked, onCheckedChange, disabled, className, ...rest }, ref) => {
    return (
      <button
        ref={ref}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={(e) => {
          if (rest.onClick) rest.onClick(e);
          if (!e.defaultPrevented) onCheckedChange?.(!checked);
        }}
        className={cn(
          'focus-visible:ring-ring inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
          checked ? 'bg-emerald-600' : 'bg-zinc-300 dark:bg-zinc-700',
          className,
        )}
        {...rest}
      >
        <span
          className={cn(
            'pointer-events-none block h-4 w-4 rounded-full bg-white shadow-lg ring-0 transition-transform',
            checked ? 'translate-x-4' : 'translate-x-0',
          )}
        />
      </button>
    );
  },
);
Switch.displayName = 'Switch';
