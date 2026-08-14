import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * Região rolável de conteúdo NÃO-tabular (design-system).
 *
 * Existe porque a decisão de acessibilidade de uma região rolável é sempre a
 * mesma e já tinha sido tomada uma vez, dentro do `<Table>`: um `div` com
 * `overflow` vira região rolável, e sem `tabIndex` o conteúdo só é alcançável
 * arrastando o mouse — o que reprova `scrollable-region-focusable` (SERIOUS,
 * WCAG 2.1.1/2.1.3) no gate `web_a11y`. `tabIndex={0}` sozinho satisfaz a
 * regra; `role`/`aria-label` entram porque um div focável sem papel nem nome é
 * anunciado como nada.
 *
 * Quem rende TABELA não precisa disto — o `<Table>` já embrulha o conteúdo (e
 * consome este mesmo componente). Isto aqui é para lista de cards e afins, que
 * não ganham a região de graça: foi exatamente esse buraco que deixou a Lista
 * de Conciliações transbordar por baixo da barra de paginação (86e2u4nxg).
 *
 * A altura NÃO vem daqui: quem chama decide (`min-h-0 flex-1` num flex column,
 * `max-h-*`, etc). Sem uma altura limitada não há o que rolar, e o componente
 * degrada para um `div` comum.
 */
export interface ScrollRegionProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Nome acessível da região — obrigatório: região sem nome não é anunciada. */
  label: string;
}

const ScrollRegion = React.forwardRef<HTMLDivElement, ScrollRegionProps>(
  ({ className, label, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('overflow-auto', className)}
      tabIndex={0}
      role="region"
      aria-label={label}
      {...props}
    />
  ),
);
ScrollRegion.displayName = 'ScrollRegion';

export { ScrollRegion };
