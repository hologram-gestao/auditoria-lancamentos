'use client';

/**
 * Tela de Clientes — Doc §9.1.
 *
 * RBAC visual:
 *   - Admin: vê todas as colunas (incluindo gerente responsável).
 *   - Manager: vê apenas a própria carteira; sem coluna de gerente.
 *
 * O backend já filtra por carteira no GET /clients (manager nunca recebe
 * dados de outro manager) — esta tela apenas oculta visualmente a coluna
 * para reduzir poluição visual.
 *
 * Click handler na linha leva pra /clientes/{id} (detalhe — S7). Os botões
 * de ação dentro da linha usam stopPropagation pra não disparar a navegação.
 */

import { format } from 'date-fns';
import { ptBR } from 'date-fns/locale';
import { ChevronLeft, ChevronRight, Eye, Plus, Search, SquarePen } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { ClientStatusBadge } from '@/components/features/clients/client-status-badge';
import { CreateClientModal } from '@/components/features/clients/create-client-modal';
import { EditClientModal } from '@/components/features/clients/edit-client-modal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useClientsList } from '@/hooks/use-clients';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { ApiError } from '@/lib/api/client';
import type { Client } from '@/lib/api/clients';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];

export default function ClientesPage() {
  const router = useRouter();
  const currentUser = useAuthStore((s) => s.user);

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, 300);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(20);
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);

  // Reseta a paginação quando a busca ou o pageSize mudam.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, pageSize]);

  const queryParams = useMemo(
    () => ({ page, pageSize, search: debouncedSearch || undefined }),
    [page, pageSize, debouncedSearch],
  );
  const { data, isLoading, isFetching, isError, error } = useClientsList(queryParams);

  if (currentUser === null) {
    // O layout pai já redireciona; este branch só satisfaz o type-checker.
    return null;
  }

  const isAdmin = currentUser.role === 'admin';
  const total = data?.pagination.total ?? 0;
  const rows = data?.data ?? [];
  const totalPages = data?.pagination.totalPages ?? 0;
  const colCount = isAdmin ? 6 : 5;
  const hasSearch = debouncedSearch.length > 0;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">Clientes</h1>
        <p className="text-muted-foreground text-sm">
          {isAdmin ? 'Gerencie todos os clientes BPO da Hologram.' : 'Clientes da sua carteira.'}
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search
            className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden="true"
          />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Buscar por nome..."
            className="pl-9"
            aria-label="Buscar clientes"
          />
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          Novo Cliente
        </Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nome</TableHead>
              {isAdmin && <TableHead>Gerente Responsável</TableHead>}
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Conciliações</TableHead>
              <TableHead>Cadastrado em</TableHead>
              <TableHead className="w-28 text-right">Ações</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows colCount={colCount} />
            ) : isError ? (
              <TableRow>
                <TableCell
                  colSpan={colCount}
                  className="text-destructive py-10 text-center text-sm"
                >
                  {error instanceof ApiError
                    ? error.userMessage
                    : 'Não foi possível carregar a lista.'}
                </TableCell>
              </TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={colCount}
                  className="text-muted-foreground py-10 text-center text-sm"
                >
                  {hasSearch
                    ? `Nenhum cliente encontrado para "${debouncedSearch}".`
                    : "Nenhum cliente cadastrado. Crie o primeiro cliente clicando em 'Novo Cliente'."}
                </TableCell>
              </TableRow>
            ) : (
              rows.map((c) => (
                <TableRow
                  key={c.id}
                  className={cn('cursor-pointer', !c.active && 'opacity-60')}
                  onClick={() => router.push(`/clientes/${c.id}`)}
                >
                  <TableCell className="font-medium">{c.name}</TableCell>
                  {isAdmin && (
                    <TableCell className="text-muted-foreground">
                      {c.responsible_manager?.name ?? '—'}
                    </TableCell>
                  )}
                  <TableCell>
                    <ClientStatusBadge active={c.active} />
                  </TableCell>
                  <TableCell className="text-muted-foreground text-right tabular-nums">
                    {c.reconciliation_count}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {format(new Date(c.created_at), "dd 'de' MMM 'de' yyyy", { locale: ptBR })}
                  </TableCell>
                  <TableCell className="text-right">
                    <div
                      className="flex items-center justify-end gap-1"
                      onClick={(e) => e.stopPropagation()}
                      role="presentation"
                    >
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => router.push(`/clientes/${c.id}`)}
                        aria-label={`Ver detalhe de ${c.name}`}
                      >
                        <Eye className="h-4 w-4" aria-hidden="true" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => setEditing(c)}
                        aria-label={`Editar ${c.name}`}
                      >
                        <SquarePen className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <p className="text-muted-foreground text-sm" aria-live="polite">
            {total === 0 ? 'Nenhum resultado.' : `${total} cliente${total === 1 ? '' : 's'}`}
            {isFetching && total > 0 ? ' · atualizando...' : ''}
          </p>
          <Select
            value={String(pageSize)}
            onValueChange={(v) => setPageSize(Number(v) as PageSize)}
          >
            <SelectTrigger className="h-8 w-[88px]" aria-label="Resultados por página">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZE_OPTIONS.map((opt) => (
                <SelectItem key={opt} value={String(opt)}>
                  {opt} / pág.
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || isLoading}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            Anterior
          </Button>
          <span className="text-muted-foreground text-sm">
            {totalPages > 0 ? `Página ${page} de ${totalPages}` : `Página ${page}`}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => p + 1)}
            disabled={page >= totalPages || isLoading || totalPages === 0}
          >
            Próxima
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>

      <CreateClientModal open={createOpen} onOpenChange={setCreateOpen} />
      <EditClientModal
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        client={editing}
        currentUserRole={currentUser.role}
      />
    </div>
  );
}

function SkeletonRows({ colCount }: { colCount: number }) {
  // 4 linhas é suficiente pra dar a impressão de "carregando" sem ocupar muito.
  return (
    <>
      {Array.from({ length: 4 }).map((_, i) => (
        <TableRow key={i}>
          {Array.from({ length: colCount }).map((__, j) => (
            <TableCell key={j} className="py-4">
              <div className="bg-muted h-3 w-full max-w-[180px] animate-pulse rounded" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}
