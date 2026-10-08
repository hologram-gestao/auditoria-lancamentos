/**
 * Card (86e3h579d): a moldura `bg-card rounded-lg border` que o app escrevia à mão em
 * cada tela, agora num componente só, no molde do shadcn.
 *
 * Duas variantes:
 *   - `default`: a moldura lisa de sempre.
 *   - `elevated`: o card da landing (`.lp-card`) em tokens. Borda em gradiente por
 *     `::before` e hover que sobe 2 px com sombra, tudo na classe `card-elevated` do
 *     `globals.css`. A cor vem de `--primary` (o verde só no tema Hologram; navy e
 *     índigo no claro e no escuro), nunca de `--brand`. O hover só existe com
 *     ponteiro (`hover: hover`), sobe só de `md` para cima e fica parado sob
 *     `prefers-reduced-motion`. Nada pinta atrás do texto: a borda é um anel de 1 px
 *     recortado por máscara e a sombra fica fora da caixa.
 *
 * `asChild` deixa a moldura vestir outro elemento (o `<li>` da landing, a `<section>`
 * de um bloco), sem `div` a mais. Server component: não tem estado nem efeito.
 */
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';

import { cn } from '@/lib/utils';

const cardVariants = cva('bg-card text-card-foreground rounded-lg border', {
  variants: {
    variant: {
      default: '',
      elevated: 'card-elevated',
    },
  },
  defaultVariants: {
    variant: 'default',
  },
});

export interface CardProps
  extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof cardVariants> {
  asChild?: boolean;
}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'div';
    return <Comp ref={ref} className={cn(cardVariants({ variant }), className)} {...props} />;
  },
);
Card.displayName = 'Card';

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex flex-col gap-1.5 p-4', className)} {...props} />
  ),
);
CardHeader.displayName = 'CardHeader';

const CardTitle = React.forwardRef<HTMLHeadingElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h3 ref={ref} className={cn('text-base font-semibold leading-none', className)} {...props} />
  ),
);
CardTitle.displayName = 'CardTitle';

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('p-4 pt-0', className)} {...props} />
  ),
);
CardContent.displayName = 'CardContent';

export { Card, CardContent, CardHeader, CardTitle, cardVariants };
