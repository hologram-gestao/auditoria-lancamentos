'use client';

/**
 * Campo de senha com alternância mostrar/ocultar.
 *
 * **Por que é um componente e não markup repetido:** a estrutura "input + botão
 * de olho dentro de uma `div.relative`" foi escrita duas vezes à mão (login e
 * criação de usuário) e as duas quebravam a acessibilidade do MESMO jeito. O
 * `<FormControl>` do shadcn é um `Slot` do Radix: ele repassa o `id` gerado
 * pelo `FormItem` para o PRIMEIRO filho. Com a `div.relative` no meio, o `id`
 * ia para a DIV — o `<label>Senha</label>` apontava para o nada, o input ficava
 * sem nome acessível ("edição", sem rótulo, no leitor de tela) e o axe reprovava
 * com `critical/label`.
 *
 * Aqui o `Slot` entrega as props a ESTE componente, que decide onde elas
 * pousam: `id`/`aria-*`/`ref` vão para o `<input>`, e a `div.relative` é
 * detalhe interno. Não há como montar errado do lado de fora.
 */

import { Eye, EyeOff } from 'lucide-react';
import * as React from 'react';

import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

export type PasswordInputProps = Omit<React.ComponentProps<'input'>, 'type'>;

const PasswordInput = React.forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className, disabled, ...props }, ref) => {
    const [visible, setVisible] = React.useState(false);
    const Icon = visible ? EyeOff : Eye;

    return (
      <div className="relative">
        <Input
          ref={ref}
          type={visible ? 'text' : 'password'}
          disabled={disabled}
          className={cn('pr-10', className)}
          {...props}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          disabled={disabled}
          aria-label={visible ? 'Ocultar senha' : 'Mostrar senha'}
          aria-pressed={visible}
          className="text-muted-foreground hover:text-foreground focus-visible:ring-ring absolute inset-y-0 right-0 flex cursor-pointer items-center rounded-md pr-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed"
        >
          <Icon className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    );
  },
);
PasswordInput.displayName = 'PasswordInput';

export { PasswordInput };
