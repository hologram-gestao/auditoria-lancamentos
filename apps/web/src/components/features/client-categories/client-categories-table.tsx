'use client';

/**
 * Tabela do catálogo de categorias de cliente (86e34jd8m).
 *
 * Recebe as linhas prontas do parent (ordem vem do backend: nome sem caixa).
 * Mostra o chip como ele aparece na lista de clientes, o tom, a contagem de
 * clientes vinculados — a informação que decide se dá para excluir — e as
 * ações. As mutações vivem nos diálogos do parent.
 */

import { SquarePen, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { ClientCategoryItem } from '@/lib/api/client-categories';

import { CLIENT_CATEGORY_TONE_LABELS, CategoryBadge } from './category-badge';

interface ClientCategoriesTableProps {
  rows: ClientCategoryItem[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onEdit: (category: ClientCategoryItem) => void;
  onDelete: (category: ClientCategoryItem) => void;
}

export function ClientCategoriesTable({
  rows,
  isLoading,
  isError,
  errorMessage,
  onEdit,
  onDelete,
}: ClientCategoriesTableProps) {
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Categoria</TableHead>
            <TableHead className="hidden sm:table-cell">Tom</TableHead>
            <TableHead className="text-right">Clientes</TableHead>
            <TableHead className="w-24 text-right">Ações</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading ? (
            <TableRow>
              <TableCell colSpan={4} className="text-muted-foreground py-10 text-center text-sm">
                Carregando categorias...
              </TableCell>
            </TableRow>
          ) : isError ? (
            <TableRow>
              <TableCell colSpan={4} className="text-destructive py-10 text-center text-sm">
                {errorMessage}
              </TableCell>
            </TableRow>
          ) : rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="text-muted-foreground py-10 text-center text-sm">
                Nenhuma categoria cadastrada. Crie a primeira em &apos;Nova categoria&apos;.
              </TableCell>
            </TableRow>
          ) : (
            rows.map((c) => (
              <TableRow key={c.id}>
                <TableCell>
                  <CategoryBadge name={c.name} tone={c.tone} />
                </TableCell>
                <TableCell className="text-muted-foreground hidden text-sm sm:table-cell">
                  {CLIENT_CATEGORY_TONE_LABELS[c.tone] ?? c.tone}
                </TableCell>
                <TableCell className="text-muted-foreground text-right tabular-nums">
                  {c.clients_count}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onEdit(c)}
                      aria-label={`Editar ${c.name}`}
                    >
                      <SquarePen className="h-4 w-4" aria-hidden="true" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onDelete(c)}
                      aria-label={`Excluir ${c.name}`}
                      className="text-destructive hover:text-destructive"
                    >
                      <Trash2 className="h-4 w-4" aria-hidden="true" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
