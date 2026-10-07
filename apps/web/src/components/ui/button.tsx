import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  // `cursor-pointer` mora AQUI, no componente base: a affordance não pode
  // depender de cada tela lembrar de reaplicar (learning do design-system).
  // `disabled:cursor-not-allowed` mantém o sinal correto no estado desabilitado.
  'inline-flex cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        // Hover por token sólido (86e3h578n), pela mesma regra do destrutivo. No
        // Hologram o primário é o verde da marca com texto navy; no claro e no
        // escuro `--primary-hover` é o valor que o antigo `/90` dava.
        default: 'bg-primary text-primary-foreground hover:bg-primary-hover',
        // Hover por TOKEN SÓLIDO, não `bg-destructive/90` (86e36ed1d): a
        // composição com alfa mistura o vermelho com a superfície e o par
        // resultante não é um token, então nenhum teste o trava. No escuro
        // reprovava de verdade: 3,95:1 contra o mínimo de 4,5.
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive-hover',
        // Verde da marca com texto navy (86e3h1h75): o primário das páginas
        // públicas (landing e login), onde o `default` é BRANCO (tema Hologram
        // fixo). Hover por token sólido e anel de foco no próprio verde;
        // `cursor-pointer` e `disabled:opacity-50` vêm da base: desabilitado é
        // o verde apagado, nunca um cinza sólido.
        brand: 'bg-brand text-brand-foreground hover:bg-brand-hover focus-visible:ring-brand',
        outline: 'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        // Link de ação em texto: `--link` (o `foreground` no claro e no escuro, o
        // verde no Hologram). Hoje sem uso; a variante segue o token do link.
        link: 'text-link underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 rounded-md px-3',
        lg: 'h-11 rounded-md px-8',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
