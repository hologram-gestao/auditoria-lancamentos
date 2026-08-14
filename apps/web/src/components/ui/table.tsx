import * as React from 'react';

import { ScrollRegion } from '@/components/ui/scroll-region';
import { cn } from '@/lib/utils';

/**
 * Rótulo do wrapper rolável. Em viewport estreito (390px) a tabela transborda e
 * o wrapper vira uma região rolável: sem `tabIndex` o conteúdo só é alcançável
 * arrastando, o que reprova `scrollable-region-focusable` (SERIOUS, WCAG
 * 2.1.1/2.1.3). Essa decisão mora no `<ScrollRegion>` (`ui/scroll-region.tsx`) —
 * uma cópia só, usada também pelas listas de cards.
 */
export interface TableProps extends React.HTMLAttributes<HTMLTableElement> {
  /** Nome acessível da região rolável (use quando houver mais de uma tabela na tela). */
  scrollRegionLabel?: string;
  /**
   * A tabela passa a ser o SCROLLER VERTICAL da área em que vive, em vez de
   * crescer indefinidamente. Use sempre dentro de `<TableCard>` (defeito
   * 86e2uca1d): sem uma altura limitada acima, a tabela vaza da caixa e o que
   * vier depois — tipicamente a `PaginationBar`, que é opaca — cobre o que
   * vazou.
   *
   * Só com `fill` o cabeçalho gruda no topo. É opt-in porque nas tabelas de
   * altura livre o `sticky` não teria efeito vertical e o fundo opaco do `th`
   * mudaria a aparência à toa (ex.: a tabela dentro do modal de troca).
   */
  fill?: boolean;
}

const Table = React.forwardRef<HTMLTableElement, TableProps>(
  (
    { className, scrollRegionLabel = 'Tabela (rolável horizontalmente)', fill = false, ...props },
    ref,
  ) => (
    <ScrollRegion
      className={cn(
        'relative w-full',
        // `min-h-0`: como item flex do `<TableCard>`, é o que autoriza encolher
        // até a altura disponível em vez de esticar o card.
        // O `shadow` desenha a linha do cabeçalho: com `border-collapse:
        // collapse` (preflight do Tailwind) a borda pertence à TABELA, não à
        // célula, e não acompanha o `th` grudado — some ao rolar.
        fill &&
          '[&_thead_th]:bg-background min-h-0 [&_thead_th]:sticky [&_thead_th]:top-0 [&_thead_th]:shadow-[inset_0_-1px_0_hsl(var(--border))]',
      )}
      label={scrollRegionLabel}
    >
      <table ref={ref} className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </ScrollRegion>
  ),
);
Table.displayName = 'Table';

/**
 * Moldura da tabela: o card com borda que a envolve nas telas de lista.
 *
 * Existe para que a receita de altura seja UMA, e não uma cópia por tela
 * (defeito 86e2uca1d, em que as três telas de tabela repetiam
 * `div.rounded-lg.border` sem limite de altura e deixavam a tabela vazar).
 *
 * `max-h-full` em vez de `h-full` de propósito: com poucas linhas o card
 * continua abraçando o conteúdo — `h-full` deixaria uma moldura vazia e alta
 * numa tela com duas contas. O `flex flex-col` é o que permite ao
 * `<Table fill>` encolher até caber; o `overflow-hidden` faz o canto arredondado
 * recortar a tabela que rola por dentro.
 *
 * Depende de o PAI ter altura definida (`min-h-0 flex-1` dentro de uma seção
 * `flex h-full flex-col`, que é o padrão das telas do shell do cliente). Onde
 * essa altura não existe — abaixo de `lg`, em que o shell vira coluna — o
 * `max-h-full` não resolve, o card cresce e quem rola é o `<main>`. É o
 * comportamento mobile de hoje, preservado de propósito.
 */
const TableCard = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('flex max-h-full flex-col overflow-hidden rounded-lg border', className)}
      {...props}
    />
  ),
);
TableCard.displayName = 'TableCard';

const TableHeader = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <thead ref={ref} className={cn('[&_tr]:border-b', className)} {...props} />
));
TableHeader.displayName = 'TableHeader';

const TableBody = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tbody ref={ref} className={cn('[&_tr:last-child]:border-0', className)} {...props} />
));
TableBody.displayName = 'TableBody';

const TableFooter = React.forwardRef<
  HTMLTableSectionElement,
  React.HTMLAttributes<HTMLTableSectionElement>
>(({ className, ...props }, ref) => (
  <tfoot
    ref={ref}
    className={cn('bg-muted/50 border-t font-medium [&>tr]:last:border-b-0', className)}
    {...props}
  />
));
TableFooter.displayName = 'TableFooter';

const TableRow = React.forwardRef<HTMLTableRowElement, React.HTMLAttributes<HTMLTableRowElement>>(
  ({ className, ...props }, ref) => (
    <tr
      ref={ref}
      className={cn(
        'hover:bg-muted/50 data-[state=selected]:bg-muted border-b transition-colors',
        className,
      )}
      {...props}
    />
  ),
);
TableRow.displayName = 'TableRow';

const TableHead = React.forwardRef<
  HTMLTableCellElement,
  React.ThHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <th
    ref={ref}
    className={cn(
      'text-muted-foreground h-12 px-4 text-left align-middle font-medium [&:has([role=checkbox])]:pr-0',
      className,
    )}
    {...props}
  />
));
TableHead.displayName = 'TableHead';

const TableCell = React.forwardRef<
  HTMLTableCellElement,
  React.TdHTMLAttributes<HTMLTableCellElement>
>(({ className, ...props }, ref) => (
  <td
    ref={ref}
    className={cn('p-4 align-middle [&:has([role=checkbox])]:pr-0', className)}
    {...props}
  />
));
TableCell.displayName = 'TableCell';

const TableCaption = React.forwardRef<
  HTMLTableCaptionElement,
  React.HTMLAttributes<HTMLTableCaptionElement>
>(({ className, ...props }, ref) => (
  <caption ref={ref} className={cn('text-muted-foreground mt-4 text-sm', className)} {...props} />
));
TableCaption.displayName = 'TableCaption';

export {
  Table,
  TableBody,
  TableCaption,
  TableCard,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
};
