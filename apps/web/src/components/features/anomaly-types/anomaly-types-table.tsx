'use client';

/**
 * Tabela de tipos de anomalia — S15 FRONT 11.2.
 *
 * Recebe a lista já filtrada/ordenada do parent (busca client-side mora na
 * página). Renderiza Switch (Ativo/Inativo) + ações (Editar / Copiar código /
 * Excluir). Quando o Switch é clicado, NÃO altera state local — abre o modal
 * de confirmação que dispara o PATCH; o reflow da query refresca a linha.
 */

import { Copy, MoreHorizontal, SquarePen, Trash2 } from 'lucide-react';
import { toast } from 'sonner';

import {
  AnomalyTypeStatusBadge,
  SeverityBadge,
} from '@/components/features/anomaly-types/severity-badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { AnomalyType } from '@/lib/api/anomaly-types';
import { cn } from '@/lib/utils';

interface AnomalyTypesTableProps {
  rows: AnomalyType[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onEdit: (anomalyType: AnomalyType) => void;
  onToggle: (anomalyType: AnomalyType) => void;
  onDelete: (anomalyType: AnomalyType) => void;
}

export function AnomalyTypesTable({
  rows,
  isLoading,
  isError,
  errorMessage,
  onEdit,
  onToggle,
  onDelete,
}: AnomalyTypesTableProps) {
  async function copyCode(code: string) {
    try {
      await navigator.clipboard.writeText(code);
      toast.success('Código copiado.');
    } catch {
      toast.error('Não foi possível copiar o código.');
    }
  }

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Nome</TableHead>
            <TableHead>Código</TableHead>
            <TableHead>Severidade</TableHead>
            <TableHead className="hidden md:table-cell">Descrição</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="w-24 text-right">Ações</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading ? (
            <TableRow>
              <TableCell colSpan={6} className="text-muted-foreground py-10 text-center text-sm">
                Carregando tipos...
              </TableCell>
            </TableRow>
          ) : isError ? (
            <TableRow>
              <TableCell colSpan={6} className="text-destructive py-10 text-center text-sm">
                {errorMessage}
              </TableCell>
            </TableRow>
          ) : rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="text-muted-foreground py-10 text-center text-sm">
                Nenhum tipo encontrado.
              </TableCell>
            </TableRow>
          ) : (
            rows.map((t) => (
              <TableRow key={t.id} className={cn(!t.active && 'opacity-60')}>
                <TableCell className="font-medium">{t.name}</TableCell>
                <TableCell className="font-mono text-xs">{t.code}</TableCell>
                <TableCell>
                  <SeverityBadge severity={t.severity} />
                </TableCell>
                <TableCell className="hidden md:table-cell">
                  <p
                    className="text-muted-foreground line-clamp-1 max-w-md text-sm"
                    title={t.description}
                  >
                    {t.description}
                  </p>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <Switch
                      checked={t.active}
                      onCheckedChange={() => onToggle(t)}
                      aria-label={t.active ? `Desativar ${t.name}` : `Reativar ${t.name}`}
                    />
                    <AnomalyTypeStatusBadge active={t.active} />
                  </div>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onEdit(t)}
                      aria-label={`Editar ${t.name}`}
                    >
                      <SquarePen className="h-4 w-4" aria-hidden="true" />
                    </Button>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Mais ações para ${t.name}`}
                        >
                          <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => copyCode(t.code)}>
                          <Copy className="h-4 w-4" aria-hidden="true" />
                          <span className="ml-2">Copiar código</span>
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() => onDelete(t)}
                          className="text-destructive focus:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                          <span className="ml-2">Excluir</span>
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
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
