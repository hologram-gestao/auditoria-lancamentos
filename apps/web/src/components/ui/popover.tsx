'use client';

/**
 * Popover do design-system, sobre o `@radix-ui/react-popover` que já estava nas
 * dependências (e sem uso até a Sprint 7).
 *
 * **`modal` é o default aqui, e isso não é detalhe.** Popover aberto DENTRO de
 * uma gaveta/diálogo herda o `react-remove-scroll` do pai, que engole o evento
 * `wheel` — a lista abre e não rola (learning do design-system). Com `modal`, o
 * conteúdo do popover ganha o próprio escopo e volta a rolar. Fora de gaveta o
 * `modal` não atrapalha: o overlay é invisível e o `Esc`/clique fora continuam
 * fechando.
 */

import * as PopoverPrimitive from '@radix-ui/react-popover';
import * as React from 'react';

import { cn } from '@/lib/utils';

const Popover = ({
  modal = true,
  ...props
}: React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Root>) => (
  <PopoverPrimitive.Root modal={modal} {...props} />
);
Popover.displayName = 'Popover';

const PopoverTrigger = PopoverPrimitive.Trigger;
const PopoverAnchor = PopoverPrimitive.Anchor;

const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content>
>(({ className, align = 'start', sideOffset = 4, ...props }, ref) => (
  <PopoverPrimitive.Portal>
    <PopoverPrimitive.Content
      ref={ref}
      align={align}
      sideOffset={sideOffset}
      className={cn(
        'bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 z-50 rounded-md border shadow-md outline-none',
        className,
      )}
      {...props}
    />
  </PopoverPrimitive.Portal>
));
PopoverContent.displayName = PopoverPrimitive.Content.displayName;

export { Popover, PopoverAnchor, PopoverContent, PopoverTrigger };
