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
}

const Table = React.forwardRef<HTMLTableElement, TableProps>(
  ({ className, scrollRegionLabel = 'Tabela (rolável horizontalmente)', ...props }, ref) => (
    <ScrollRegion className="relative w-full" label={scrollRegionLabel}>
      <table ref={ref} className={cn('w-full caption-bottom text-sm', className)} {...props} />
    </ScrollRegion>
  ),
);
Table.displayName = 'Table';

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

export { Table, TableHeader, TableBody, TableFooter, TableHead, TableRow, TableCell, TableCaption };
