'use client';

/**
 * Input com toggle de visibilidade — usado pelos campos App Key / App Secret
 * dos modais de cliente. Doc §9.2/§9.3: "ícone de olho para mostrar/ocultar".
 *
 * `tabIndex={-1}` no botão de toggle: o foco do teclado nunca para nele,
 * apenas no input. Isso evita que Tab pule do nome direto pro toggle e
 * casse a ordem natural campos → "testar conexão" → salvar.
 */

import { Eye, EyeOff } from 'lucide-react';

import { Input } from '@/components/ui/input';

interface PasswordInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  visible: boolean;
  onToggle: () => void;
}

export function PasswordInput({ visible, onToggle, ...rest }: PasswordInputProps) {
  return (
    <div className="relative">
      <Input type={visible ? 'text' : 'password'} className="pr-10" {...rest} />
      <button
        type="button"
        onClick={onToggle}
        aria-label={visible ? 'Ocultar' : 'Mostrar'}
        aria-pressed={visible}
        tabIndex={-1}
        disabled={rest.disabled}
        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring absolute inset-y-0 right-0 flex items-center rounded-md pr-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:opacity-50"
      >
        {visible ? (
          <EyeOff className="h-4 w-4" aria-hidden="true" />
        ) : (
          <Eye className="h-4 w-4" aria-hidden="true" />
        )}
      </button>
    </div>
  );
}
