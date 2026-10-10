'use client';

/**
 * Lista dos layouts de exportação da organização — Sprint 13 (FRONT 13.5).
 *
 * Nome, sistema alvo, versão atual e data da última versão; para a plataforma,
 * a coluna Organização (o nome vem das opções de organização — o contrato da
 * lista traz só o `organizationId`). "Ver versões" abre a gaveta só leitura;
 * "Excluir" (86e3nuuub) só aparece para quem tem `manage_export_layouts` — a
 * regra de que só sai layout sem arquivo gerado é do servidor.
 *
 * `<TableCard>` + `<Table fill>` (a tela é uma lista só, a tabela enche a área)
 * e o estado vazio em `<TableEmpty>` DEPOIS do `<Table>` — nunca numa célula
 * `colSpan`, que em 390px fica com a metade direita fora da tela.
 */

import { Plus, Trash2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { ExportLayoutItem } from '@/lib/contracts';
import { formatCreatedAt } from '@/lib/format';

interface ExportLayoutsTableProps {
  rows: ExportLayoutItem[];
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  errorMessage: string;
  onRetry: () => void;
  showOrganization: boolean;
  organizationName: (organizationId: string) => string;
  onOpenVersions: (layout: ExportLayoutItem) => void;
  /** `manage_export_layouts`: sem ela a ação de excluir não é renderizada. */
  canDelete: boolean;
  onDelete: (layout: ExportLayoutItem) => void;
  /** Ação do estado vazio (a mesma do topo da tela). */
  onCreate: () => void;
  /** Com filtro de organização ativo, o vazio fala do recorte. */
  filtered: boolean;
}

export function ExportLayoutsTable({
  rows,
  isLoading,
  isFetching,
  isError,
  errorMessage,
  onRetry,
  showOrganization,
  organizationName,
  onOpenVersions,
  canDelete,
  onDelete,
  onCreate,
  filtered,
}: ExportLayoutsTableProps) {
  if (isError) {
    return (
      <div
        role="alert"
        className="bg-destructive-muted text-destructive ring-destructive/30 flex flex-col items-start gap-3 rounded-lg p-4 text-sm ring-1 ring-inset sm:flex-row sm:items-center sm:justify-between"
      >
        <span>{errorMessage}</span>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Tentar novamente
        </Button>
      </div>
    );
  }

  const colCount = showOrganization ? 6 : 5;

  return (
    <TableCard aria-busy={isFetching}>
      <Table fill scrollRegionLabel="Lista de layouts de exportação (rolável)">
        <TableHeader>
          <TableRow>
            <TableHead>Nome</TableHead>
            {showOrganization && <TableHead>Organização</TableHead>}
            <TableHead className="whitespace-nowrap">Sistema contábil</TableHead>
            <TableHead className="whitespace-nowrap text-right">Versão atual</TableHead>
            <TableHead className="whitespace-nowrap">Atualizado em</TableHead>
            <TableHead className="text-right">
              <span className="sr-only">Ações</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading
            ? Array.from({ length: 3 }).map((_, index) => (
                <TableRow key={index} aria-hidden="true">
                  {Array.from({ length: colCount }).map((__, cell) => (
                    <TableCell key={cell}>
                      <div className="bg-muted h-4 w-full animate-pulse rounded" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : rows.map((layout) => (
                <TableRow key={layout.id}>
                  <TableCell className="min-w-48 font-medium">{layout.name}</TableCell>
                  {showOrganization && (
                    <TableCell className="whitespace-nowrap">
                      {organizationName(layout.organizationId)}
                    </TableCell>
                  )}
                  <TableCell className="whitespace-nowrap">{layout.targetSystem}</TableCell>
                  <TableCell className="whitespace-nowrap text-right tabular-nums">
                    v{layout.latestVersion}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {formatCreatedAt(layout.updatedAt)}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onOpenVersions(layout)}
                        aria-label={`Ver versões de ${layout.name}`}
                      >
                        Ver versões
                      </Button>
                      {canDelete && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onDelete(layout)}
                          className="text-destructive hover:text-destructive hover:bg-destructive-muted"
                          aria-label={`Excluir ${layout.name}`}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                          Excluir
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
      {!isLoading && rows.length === 0 && (
        <TableEmpty className="flex flex-col items-center gap-3 text-center">
          <div className="space-y-1">
            <p className="text-sm font-medium">
              {filtered ? 'Nenhum layout nesta organização' : 'Nenhum layout ainda'}
            </p>
            <p className="text-muted-foreground text-sm">
              O layout define o formato do arquivo que o sistema contábil importa. Comece pelo
              modelo Domínio.
            </p>
          </div>
          <Button onClick={onCreate}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Criar a partir do modelo Domínio
          </Button>
        </TableEmpty>
      )}
    </TableCard>
  );
}
