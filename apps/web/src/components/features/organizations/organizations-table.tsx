'use client';

/**
 * Tabela de organizações (86e36ecwa) — a visão da PLATAFORMA.
 *
 * Recebe as linhas prontas do parent (ordem e paginação vêm do backend). As
 * duas contagens são o que decide se dá para suspender sem quebrar operação:
 * `clients_count` diz quantos clientes ficam sem staff, `users_count` quantas
 * pessoas perdem acesso no request seguinte.
 *
 * `TableCard` + `Table fill` (design-system): a tabela rola DENTRO da própria
 * área e a barra de paginação nunca cobre a última linha.
 */

import { Power, PowerOff, SquarePen } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { OrganizationItem } from '@/lib/api/organizations';

import { OrganizationStatusBadge } from './organization-status-badge';

interface OrganizationsTableProps {
  rows: OrganizationItem[];
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
  onEdit: (organization: OrganizationItem) => void;
  onToggleActive: (organization: OrganizationItem) => void;
}

export function OrganizationsTable({
  rows,
  isLoading,
  isError,
  errorMessage,
  onEdit,
  onToggleActive,
}: OrganizationsTableProps) {
  return (
    <TableCard>
      <Table fill scrollRegionLabel="Organizações (rolável)">
        <TableHeader>
          <TableRow>
            <TableHead>Organização</TableHead>
            <TableHead>Situação</TableHead>
            <TableHead className="text-right">Clientes</TableHead>
            <TableHead className="text-right">Usuários</TableHead>
            <TableHead className="w-24 text-right">Ações</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading ? (
            <TableRow>
              <TableCell colSpan={5} className="text-muted-foreground py-10 text-center text-sm">
                Carregando organizações...
              </TableCell>
            </TableRow>
          ) : isError ? (
            <TableRow>
              <TableCell colSpan={5} className="text-destructive py-10 text-center text-sm">
                {errorMessage}
              </TableCell>
            </TableRow>
          ) : rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-muted-foreground py-10 text-center text-sm">
                Nenhuma organização encontrada. Crie a primeira em &apos;Nova organização&apos;.
              </TableCell>
            </TableRow>
          ) : (
            rows.map((org) => (
              <TableRow key={org.id}>
                <TableCell className="font-medium">{org.name}</TableCell>
                <TableCell>
                  <OrganizationStatusBadge active={org.active} />
                </TableCell>
                <TableCell className="text-muted-foreground text-right tabular-nums">
                  {org.clients_count}
                </TableCell>
                <TableCell className="text-muted-foreground text-right tabular-nums">
                  {org.users_count}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onEdit(org)}
                      aria-label={`Editar ${org.name}`}
                    >
                      <SquarePen className="h-4 w-4" aria-hidden="true" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onToggleActive(org)}
                      aria-label={`${org.active ? 'Suspender' : 'Reativar'} ${org.name}`}
                      className={org.active ? 'text-destructive hover:text-destructive' : undefined}
                    >
                      {org.active ? (
                        <PowerOff className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <Power className="h-4 w-4" aria-hidden="true" />
                      )}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </TableCard>
  );
}
